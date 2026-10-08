"""Direct QQ operations under explicit UI grants, with durable no-retry receipts."""
import hashlib
import json
import threading
import time
import uuid

from .group_admin import GroupAdmin
from .message_sender import QQSender, SendError, SendUncertain
from .onebot import Client, OneBotError, OneBotUncertain
from .retrieval import scope_sql

_execution = threading.RLock()


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class MCPActions:
    def __init__(self, access, *, sender_factory=QQSender, client_factory=Client):
        self.access, self.store = access, access.store
        self.sender_factory, self.client_factory = sender_factory, client_factory
        with access.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS operations(
                    id TEXT PRIMARY KEY,grant_id TEXT NOT NULL,request_key TEXT NOT NULL,
                    signature TEXT NOT NULL,kind TEXT NOT NULL,conversation_id TEXT NOT NULL,
                    params TEXT NOT NULL,target TEXT NOT NULL,summary TEXT NOT NULL,
                    state TEXT NOT NULL,created REAL NOT NULL,
                    updated REAL NOT NULL,result TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(grant_id,request_key));
                CREATE TABLE IF NOT EXISTS operation_events(
                    id INTEGER PRIMARY KEY,operation_id TEXT NOT NULL,state TEXT NOT NULL,at REAL NOT NULL);
            ''')

    def recover(self):
        # Called once when a new owning service starts, never during a request.
        with self.access.connect() as db:
            rows = db.execute("SELECT id FROM operations WHERE state='EXECUTING'").fetchall()
            for row in rows:
                self.transition(db, row['id'], 'UNKNOWN', {'note': '服务中断，结果未知；请到 QQ 核对，不会自动重试。'})

    def transition(self, db, oid, state, result=None):
        db.execute('UPDATE operations SET state=?,updated=?,result=? WHERE id=?',
                   (state, time.time(), dump(result or {}), oid))
        db.execute('INSERT INTO operation_events(operation_id,state,at) VALUES(?,?,?)', (oid, state, time.time()))

    def scoped_target(self, grant, cid, kind):
        if not grant['scope'].get(kind):
            raise ValueError('此连接未获该操作权限，请在 Tulpa 界面重新授权。')
        where, values = scope_sql(self.access.plan(grant))
        with self.store.connect() as db:
            row = db.execute(f"SELECT * FROM messages m WHERE {where} AND platform='qq' AND conversation_id=? LIMIT 1", values+[cid]).fetchone()
        if not row:
            raise ValueError('目标不在连接允许的会话和日期范围内。')
        return dict(row)

    def prepare(self, grant, cid, kind, params, *, target_resolver=None, send_preparer=None):
        row = (target_resolver or self.scoped_target)(grant, cid, kind)
        if kind == 'send':
            text = params.get('text')
            if not isinstance(text, str) or not text.strip() or len(text) > 4000 or any(ord(c)<32 and c not in '\n\t' for c in text):
                raise ValueError('请提供 1–4000 字的实际发送文本。')
            if send_preparer:
                address = send_preparer(self.sender_factory(), row, params)
            elif set(params) != {'text'}:
                raise ValueError('发送参数无效。')
            else:
                address = self.sender_factory().prepare(row, False)
            labels = []
            if address.get('quote_id') is not None:labels.append('引用 QQ 消息 '+str(address['quote_id']))
            if address.get('mention_user_ids'):labels.append('@ '+', '.join(address['mention_user_ids']))
            detail = ('\n'+'；'.join(labels)) if labels else ''
            return address, f'QQ {address["account"]} → {"群" if address["kind"]=="group" else "好友"}「{address["name"]}」（{address["peer"]}）{detail}\n发送：\n{text}', row
        allowed = {'action','user_id','duration_seconds','group_name','request_id','approve','reason'}
        if set(params)-allowed or params.get('action') not in ('mute','unmute','kick','rename','request'):
            raise ValueError('群管理参数无效。')
        fields = {'mute':{'user_id','duration_seconds'}, 'unmute':{'user_id'}, 'kick':{'user_id'},
                  'rename':{'group_name'}, 'request':{'request_id','approve','reason'}}[params['action']]
        if set(params)-fields-{'action'}:
            raise ValueError('参数不属于所选管理操作。')
        target, api, payload, summary = GroupAdmin(self.store).validate(self.client_factory(), cid, params['action'], params)
        return dict(target=target, api=api, payload=payload), summary, row

    def public(self, row):
        result = {k: row[k] for k in ('id','grant_id','kind','conversation_id','summary','state','created','updated')}
        result['result'] = json.loads(row['result'])
        result['note'] = '按用户在 Tulpa 界面授予本连接的直接操作权限执行；UNKNOWN 不得自动重试。'
        return result

    def get(self, oid, gid=None):
        with self.access.connect() as db:
            row = db.execute('SELECT * FROM operations WHERE id=?', (oid,)).fetchone()
            if not row or (gid is not None and row['grant_id'] != gid):
                raise ValueError('操作不存在。')
            return self.public(row)

    def list(self):
        with self.access.connect() as db:
            rows = db.execute('SELECT o.id,g.name FROM operations o JOIN grants g ON o.grant_id=g.id ORDER BY o.created DESC LIMIT 100').fetchall()
        return [dict(self.get(r['id']), connection_name=r['name']) for r in rows]

    def execute(self, grant, kind, args, cancel, *, guard=None, target_resolver=None, send_preparer=None):
        cid, key = args['conversation_id'], args['idempotency_key']
        params = {k:v for k,v in args.items() if k not in ('conversation_id','idempotency_key')}
        signature = hashlib.sha256(dump([kind,cid,params]).encode()).hexdigest()
        with _execution:
            if guard:guard()
            grant = self.access.by_id(grant['id'], source=target_resolver is None)
            (target_resolver or self.scoped_target)(grant,cid,kind)
            with self.access.connect() as db:
                old = db.execute('SELECT * FROM operations WHERE grant_id=? AND request_key=?', (grant['id'],key)).fetchone()
            if old:
                if old['signature'] != signature:
                    raise ValueError('同一个幂等编号不能用于不同内容。')
                return self.get(old['id'], grant['id'])
            prepared, summary, target = self.prepare(grant, cid, kind, params, target_resolver=target_resolver, send_preparer=send_preparer)
            self.access.by_id(grant['id'], source=target_resolver is None)
            if guard:guard()
            if cancel.is_set():
                raise ValueError('调用已取消，未执行操作。')
            oid, now = uuid.uuid4().hex, time.time()
            with self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('INSERT INTO operations(id,grant_id,request_key,signature,kind,conversation_id,params,target,summary,state,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (oid,grant['id'],key,signature,kind,cid,dump(params),dump(prepared),summary,'EXECUTING',now,now))
                db.execute('INSERT INTO operation_events(operation_id,state,at) VALUES(?,?,?)',(oid,'EXECUTING',now))
            dispatched = False
            try:
                self.access.by_id(grant['id'], source=target_resolver is None)
                if guard:guard()
                if cancel.is_set():raise ValueError('调用已取消，未执行。')
                if kind=='send':
                    dispatched = True
                    sender=self.sender_factory()
                    result = sender.send(target,params['text'],prepared,guard=guard) if target_resolver else sender.send(target,params['text'],prepared)
                else:
                    dispatched = True
                    self.client_factory().call(prepared['api'],prepared['payload'],approved=True)
                    result = {'note':'QQ 接口返回操作成功。'}
                state = 'SUCCEEDED'
            except (SendUncertain,OneBotUncertain):
                state, result = 'UNKNOWN', {'note':'未取得确定回执，请在 QQ 核对，不会自动重试。'}
            except (ValueError,SendError,OneBotError) as exc:
                state, result = 'FAILED', {'note':str(exc)}
            except Exception:
                state, result = ('UNKNOWN' if dispatched else 'FAILED'), {'note':'操作未取得结果，请核对；不会自动重试。'}
            with self.access.connect() as db:
                self.transition(db,oid,state,result)
            return self.get(oid)

    def revoke(self,gid):
        with _execution:
            self.access.revoke(gid)
