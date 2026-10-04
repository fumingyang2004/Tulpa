"""Local MCP grants. Only token hashes persist; settings are never model tools."""
import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager

from .client_accounts import bound_account
from .retrieval import Plan, date_bound

CHAT_MEDIA_FLAGS = ('chat_images', 'chat_sticker_send', 'chat_sticker_collect')
CHAT_MEDIA_TOOLS = ('read_chat_image','list_chat_stickers','read_chat_sticker','note_chat_sticker','send_chat_sticker','collect_chat_sticker')

DEFAULT_PORT = 18777


class AccessDenied(ValueError):
    pass


class RateLimited(ValueError):
    pass


class MCPAccess:
    def __init__(self, store, *, path=None):
        self.store = store
        self.path = path or store.path.parent / 'mcp-access.sqlite3'
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL, port INTEGER NOT NULL);
                INSERT OR IGNORE INTO settings VALUES(1,0,18777);
                CREATE TABLE IF NOT EXISTS grants(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
                    scope TEXT NOT NULL, epoch TEXT NOT NULL, accounts TEXT NOT NULL,
                    created_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS calls(
                    id INTEGER PRIMARY KEY, grant_id TEXT NOT NULL, tool TEXT NOT NULL,
                    at REAL NOT NULL, status TEXT NOT NULL, seconds REAL NOT NULL DEFAULT 0,
                    messages INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS calls_budget ON calls(grant_id,tool,at);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def settings(self):
        with self.connect() as db:
            row = dict(db.execute('SELECT enabled,port FROM settings WHERE id=1').fetchone())
        row['enabled'] = bool(row['enabled'])
        return row

    def configure(self, enabled, port):
        if type(enabled) is not bool or type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError('启用状态或端口无效；端口范围 1024–65535。')
        with self.connect() as db:
            db.execute('UPDATE settings SET enabled=?,port=? WHERE id=1', (enabled, port))

    def epoch(self):
        with self.store.connect() as db:
            return db.execute('SELECT epoch FROM local_change_meta WHERE id=1').fetchone()[0]

    def accounts(self, platforms):
        return {p: bound_account(self.store, p) or '' for p in platforms}

    def create(self, body):
        fields = {'name', 'platforms', 'conversations', 'all_conversations', 'start', 'end', 'media', 'prepare', 'voice', 'onebot', 'send', 'manage', 'chat', *CHAT_MEDIA_FLAGS}
        if not isinstance(body, dict) or set(body) - fields:
            raise ValueError('连接配置字段无效。')
        name = body.get('name', '')
        if not isinstance(name, str) or not name.strip() or len(name) > 60 or any(ord(c) < 32 for c in name):
            raise ValueError('请填写不超过 60 字的连接名称。')
        platforms = body.get('platforms')
        if not isinstance(platforms, list) or not platforms or any(p not in ('qq', 'wechat') for p in platforms):
            raise ValueError('请选择允许访问的平台。')
        conversations = body.get('conversations', [])
        # Live chat can be authorized before importing any native chat history.
        chat_account = None
        live_groups = set()
        if body.get('chat') is True and body.get('send') is True and 'qq' in platforms:
            from .onebot import Client
            client = Client(timeout=3)
            chat_account = client.login()
            if not body.get('all_conversations'):
                live_groups = {f'{chat_account}:group:{g["group_id"]}' for g in client.call('get_group_list', {})
                               if isinstance(g,dict) and str(g.get('group_id','')).isdigit()}
        if not isinstance(conversations, list) or len(conversations) > 500:
            raise ValueError('请选择最多 500 个会话。')
        selections = []
        for pair in conversations:
            if not isinstance(pair, list) or len(pair) != 2 or pair[0] not in platforms or not isinstance(pair[1], str) or not 1 <= len(pair[1]) <= 200:
                raise ValueError('会话选择无效。')
            with self.store.connect() as db:
                if not db.execute('SELECT 1 FROM messages WHERE platform=? AND conversation_id=? LIMIT 1', pair).fetchone() and not (pair[0]=='qq' and pair[1] in live_groups):
                    raise ValueError('所选会话已不存在，请重新选择。')
            selection = json.dumps(pair, ensure_ascii=False)
            if selection not in selections:
                selections.append(selection)
        for flag in ('all_conversations', 'media', 'prepare', 'voice', 'onebot', 'send', 'manage', 'chat', *CHAT_MEDIA_FLAGS):
            if flag in body and type(body[flag]) is not bool:
                raise ValueError('权限开关必须为布尔值。')
        if body.get('all_conversations'):
            selections = []
        elif not selections:
            raise ValueError('请选择会话，或明确允许所选平台的全部会话。')
        dates = {}
        for key in ('start', 'end'):
            value = body.get(key, '')
            if not isinstance(value, str) or len(value) > 10:
                raise ValueError('日期应为 YYYY-MM-DD。')
            if value:
                from datetime import date
                try:
                    if date.fromisoformat(value).isoformat() != value:
                        raise ValueError()
                except ValueError:
                    raise ValueError('日期应为 YYYY-MM-DD。') from None
            dates[key] = value
        start, end = date_bound(dates['start']), date_bound(dates['end'], True)
        if start is not None and end is not None and start >= end:
            raise ValueError('开始日期不能晚于截止日期。')
        scope = dict(platforms=sorted(set(platforms)), conversations=selections, **dates,
                     **{k: body.get(k, False) for k in ('media', 'prepare', 'voice', 'onebot', 'send', 'manage', 'chat', *CHAT_MEDIA_FLAGS)})
        if (scope['send'] or scope['manage']) and 'qq' not in platforms:
            raise ValueError('发送和群管理需要选择 QQ 平台。')
        if scope['chat'] and not scope['send']:
            raise ValueError('持续聊天需要同时允许直接发送 QQ 消息。')
        if any(scope[k] for k in CHAT_MEDIA_FLAGS) and not scope['chat']:
            raise ValueError('表情包子功能需要开启持续群聊。')
        if (scope['chat_sticker_send'] or scope['chat_sticker_collect']) and not scope['chat_images']:
            raise ValueError('发送和收藏表情需要先允许持续聊天看图。')
        if chat_account:scope['chat_account'] = chat_account
        token = secrets.token_urlsafe(32)
        gid = uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO grants(id,name,token_hash,scope,epoch,accounts,created_at) VALUES(?,?,?,?,?,?,?)',
                       (gid, name.strip(), self.digest(token), json.dumps(scope), self.epoch(), json.dumps(self.accounts(platforms)), time.time()))
        return dict(id=gid, token=token)

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode('utf-8')).hexdigest()

    def authorize(self, token, *, source=True):
        if not isinstance(token, str) or not 20 <= len(token) <= 200 or not self.settings()['enabled']:
            raise AccessDenied('MCP 未启用或连接凭据已失效。')
        with self.connect() as db:
            row = db.execute('SELECT * FROM grants WHERE token_hash=? AND revoked=0', (self.digest(token),)).fetchone()
        if not row:
            raise AccessDenied('连接凭据无效或已撤销。')
        return self.validate_grant(row, source=source)

    def by_id(self, gid, *, source=True):
        if not self.settings()['enabled']:
            raise AccessDenied('MCP 服务已停用。')
        with self.connect() as db:
            row = db.execute('SELECT * FROM grants WHERE id=? AND revoked=0', (gid,)).fetchone()
        if not row:
            raise AccessDenied('连接凭据无效或已撤销。')
        return self.validate_grant(row, source=source)

    def validate_grant(self, row, *, source=True):
        grant = dict(row)
        grant['scope'] = json.loads(grant['scope'])
        if not source:return grant  # Authentication only. Tool-specific checks still apply.
        try:
            valid = grant['epoch'] == self.epoch() and json.loads(grant['accounts']) == self.accounts(grant['scope']['platforms'])
        except ValueError:
            valid = False
        if not valid:
            raise AccessDenied('数据来源或账号已改变，请在 Tulpa 重新授权。')
        return grant

    def revoke(self, gid):
        with self.connect() as db:
            db.execute('UPDATE grants SET revoked=1 WHERE id=?', (gid,))

    def list(self):
        with self.connect() as db:
            rows = [dict(r) for r in db.execute('SELECT id,name,scope,accounts,epoch,created_at,revoked FROM grants ORDER BY created_at DESC')]
            for row in rows:
                row['scope'] = json.loads(row['scope'])
                row['accounts'] = json.loads(row['accounts'])
                try:row['valid']=not row['revoked'] and row.pop('epoch')==self.epoch() and row['accounts']==self.accounts(row['scope']['platforms'])
                except ValueError:row['valid']=False
                row.pop('epoch',None)
                row['calls'] = db.execute('SELECT count(*) FROM calls WHERE grant_id=?', (row['id'],)).fetchone()[0]
            recent = [dict(r) for r in db.execute('SELECT c.*,g.name FROM calls c JOIN grants g ON c.grant_id=g.id ORDER BY c.id DESC LIMIT 30')]
        return dict(connections=rows, recent=recent)

    def begin_call(self, gid, tool):
        # Per-page processing limits, not an artificial task-wide message cap.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            limit = {'prepare_file': 12, 'download_file': 12, 'transcribe_voice': 12, 'read_image': 60,
                     'read_chat_image':60,'read_chat_sticker':60,'collect_chat_sticker':12}.get(tool)
            group = ('prepare_file','download_file') if tool in ('prepare_file','download_file') else ('read_chat_image','read_chat_sticker') if tool in ('read_chat_image','read_chat_sticker') else (tool,)
            if limit and db.execute('SELECT count(*) FROM calls WHERE grant_id=? AND tool IN ('+','.join('?' for _ in group)+') AND at>?',
                                    (gid, *group, time.time()-3600)).fetchone()[0] >= limit:
                raise RateLimited('本连接已达到该按需处理工具的每小时上限；请稍后继续。')
            # A busy Agent must always be able to stop its own session.
            family = 'IN' if tool in CHAT_MEDIA_TOOLS else 'NOT IN'
            placeholders = ','.join('?' for _ in CHAT_MEDIA_TOOLS)
            if tool != 'stop_chat_session' and db.execute(f"SELECT count(*) FROM calls WHERE grant_id=? AND at>? AND tool!='stop_chat_session' AND tool {family} ({placeholders})", (gid, time.time()-60, *CHAT_MEDIA_TOOLS)).fetchone()[0] >= 120:
                raise RateLimited('本连接请求过于频繁，请稍后继续。')
            if tool=='send_chat_sticker' and db.execute("SELECT count(*) FROM calls WHERE grant_id=? AND tool=? AND at>?",(gid,tool,time.time()-60)).fetchone()[0]>=12:
                raise RateLimited('表情发送过于频繁，请稍后继续；普通聊天不受影响。')
            return db.execute('INSERT INTO calls(grant_id,tool,at,status) VALUES(?,?,?,?)',
                              (gid, tool, time.time(), 'running')).lastrowid

    def finish_call(self, call_id, status, seconds, messages=0):
        with self.connect() as db:
            db.execute('UPDATE calls SET status=?,seconds=?,messages=? WHERE id=?', (status, round(seconds, 3), messages, call_id))

    @staticmethod
    def plan(grant):
        scope = grant['scope']
        return Plan(platforms=scope['platforms'], conversations=scope['conversations'],
                    start=date_bound(scope['start']), end=date_bound(scope['end'], True))
