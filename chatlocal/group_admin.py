"""Immutable group-operation proposals, explicit UI decisions and durable receipts."""
import hashlib
import json
import secrets
import threading
import time
import uuid

from .onebot import Client, OneBotError, OneBotUncertain


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS group_admin_policy(
        session_id TEXT PRIMARY KEY,mode TEXT NOT NULL DEFAULT 'ask',revision INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS group_admin_operations(
        id TEXT PRIMARY KEY,session_id TEXT NOT NULL,turn_id TEXT NOT NULL,signature TEXT NOT NULL,
        action TEXT NOT NULL,target TEXT NOT NULL,params TEXT NOT NULL,summary TEXT NOT NULL,
        status TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,updated REAL NOT NULL,
        approval_token TEXT NOT NULL,decision TEXT NOT NULL DEFAULT '',detail TEXT NOT NULL DEFAULT '',
        UNIQUE(session_id,turn_id,signature));
      CREATE INDEX IF NOT EXISTS group_admin_session ON group_admin_operations(session_id,created);
      CREATE TABLE IF NOT EXISTS group_admin_audit(
        id INTEGER PRIMARY KEY AUTOINCREMENT,operation_id TEXT,session_id TEXT NOT NULL,
        event TEXT NOT NULL,at REAL NOT NULL,detail TEXT NOT NULL);
    ''')


def number(value, label):
    if not isinstance(value, (str, int)) or isinstance(value, bool) or not str(value).isdigit() or not 0 < int(value) < 2**53:
        raise OneBotError(label + '无效。')
    return str(int(value))


def group_identity(client, cid):
    parts = str(cid).split(':')
    if len(parts) != 3 or parts[1] != 'group':
        raise OneBotError('需要完整的 QQ 群会话编号。')
    account, group = number(parts[0], 'QQ 账号'), number(parts[2], '群号')
    if client.login() != account:
        raise OneBotError('OneBot 登录账号与所选聊天的本人账号不一致。')
    info = client.call('get_group_info', dict(group_id=int(group), no_cache=True))
    if not isinstance(info, dict) or str(info.get('group_id')) != group:
        raise OneBotError('无法核对目标群。')
    return dict(conversation_id=cid, account=account, group_id=group,
                group_name=str(info.get('group_name') or group)[:120])


def member(client, target, uid):
    uid = number(uid, '成员账号')
    data = client.call('get_group_member_info', dict(group_id=int(target['group_id']), user_id=int(uid), no_cache=True))
    if not isinstance(data, dict) or str(data.get('user_id')) != uid or str(data.get('group_id')) != target['group_id']:
        raise OneBotError('无法核对该成员在目标群中的身份。')
    if data.get('role') not in ('owner', 'admin', 'member'):
        raise OneBotError('接口没有提供可靠的群权限信息。')
    return dict(user_id=uid, name=str(data.get('card') or data.get('nickname') or uid)[:120], role=data['role'])


def requests(client, target):
    data = client.call('get_group_system_msg', dict(group_id=int(target['group_id']), only_pending=True, count=100))
    if not isinstance(data, list):
        raise OneBotError('入群申请接口格式不兼容。')
    result = []
    for item in data:
        if not isinstance(item, dict) or str(item.get('group_id')) != target['group_id'] or item.get('checked') is not False:
            continue
        # Never construct a protocol flag from an unverified numeric ID.
        flag = item.get('flag')
        if not isinstance(flag, str) or not 1 <= len(flag) <= 800:
            continue
        uid = str(item.get('requester_uin') or item.get('user_id') or '')
        if not uid.isdigit() or int(uid) <= 0:
            continue
        subtype = item.get('sub_type') or item.get('subtype') or 'add'
        # Invitations for this account are distinct from applications to a
        # group it administers. V1 exposes only verified join applications.
        if subtype != 'add' or str(item.get('invitor_uin') or '0') != '0':
            continue
        result.append(dict(request_id=hashlib.sha256(flag.encode()).hexdigest()[:24], flag=flag,
                           user_id=uid, name=str(item.get('requester_nick') or uid)[:120],
                           comment=str(item.get('message') or '')[:1000], sub_type='add'))
    return result


_execution_lock = threading.RLock()


class GroupAdmin:
    def __init__(self, store, client_factory=None):
        self.store = store
        self.client_factory = client_factory or Client

    def _audit(self, db, sid, oid, event, detail=''):
        db.execute('INSERT INTO group_admin_audit(operation_id,session_id,event,at,detail) VALUES(?,?,?,?,?)',
                   (oid, sid, event, time.time(), detail))

    def policy(self, sid):
        with self.store.connect() as db:
            row = db.execute('SELECT mode,revision FROM group_admin_policy WHERE session_id=?', (sid,)).fetchone()
        return dict(row) if row else dict(mode='ask', revision=0)

    def set_policy(self, sid, mode):
        if mode not in ('ask', 'allow', 'deny'):
            raise ValueError('审批模式无效。')
        with self.store.connect() as db:
            db.execute('''INSERT INTO group_admin_policy VALUES(?,?,1) ON CONFLICT(session_id)
                DO UPDATE SET mode=excluded.mode,revision=group_admin_policy.revision+1''', (sid, mode))
            self._audit(db, sid, None, 'policy', mode)
        # A setting change never silently applies existing pending proposals.
        return self.policy(sid)

    def _public(self, row, ui=False):
        value = {k: row[k] for k in ('id', 'session_id', 'turn_id', 'action', 'summary', 'status',
                                     'created', 'expires', 'updated', 'decision', 'detail')}
        value['target'] = json.loads(row['target'])
        if ui:
            value['approval_token'] = row['approval_token']
        return value

    def get(self, oid, sid=None, ui=False):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM group_admin_operations WHERE id=?', (oid,)).fetchone()
        if not row or (sid is not None and row['session_id'] != sid):
            raise ValueError('操作不存在或不属于当前对话。')
        return self._public(row, ui)

    def list(self, sid, ui=False):
        with self.store.connect() as db:
            db.execute("UPDATE group_admin_operations SET status='EXPIRED',updated=? WHERE session_id=? AND status='PENDING' AND expires<?",
                       (time.time(), sid, time.time()))
            rows = db.execute('SELECT * FROM group_admin_operations WHERE session_id=? ORDER BY created DESC LIMIT 50', (sid,)).fetchall()
        return [self._public(row, ui) for row in rows]

    def validate(self, client, cid, action, params):
        target = group_identity(client, cid)
        actor = member(client, target, target['account'])
        if actor['role'] not in ('owner', 'admin'):
            raise OneBotError('当前账号在该群不是群主或管理员，无法执行此管理操作。')
        target['actor_role'] = actor['role']
        group = target['group_id']
        label = f'群「{target["group_name"]}」（{group}）'
        if action in ('mute', 'unmute', 'kick'):
            selected = member(client, target, params.get('user_id'))
            rank = {'member': 0, 'admin': 1, 'owner': 2}
            if selected['user_id'] == target['account'] or rank[selected['role']] >= rank[actor['role']]:
                raise OneBotError('不能管理本人、群主或权限不低于当前账号的成员。')
            target.update(member=selected)
            who = f'「{selected["name"]}」（{selected["user_id"]}）'
            if action == 'kick':
                payload = dict(group_id=int(group), user_id=int(selected['user_id']), reject_add_request=False)
                return target, 'set_group_kick', payload, label + '：移出成员 ' + who + '；不禁止再次申请'
            duration = 0 if action == 'unmute' else params.get('duration_seconds')
            if type(duration) is not int or (action == 'mute' and not 1 <= duration <= 2592000):
                raise OneBotError('请明确禁言时长，范围为 1 秒至 30 天。')
            text = '解除禁言 ' if duration == 0 else f'禁言 {duration // 60} 分钟 ' if duration % 60 == 0 else f'禁言 {duration} 秒 '
            return target, 'set_group_ban', dict(group_id=int(group), user_id=int(selected['user_id']), duration=duration), label + '：' + text + who
        if action == 'rename':
            name = params.get('group_name')
            if not isinstance(name, str) or not name.strip() or len(name) > 60 or any(ord(c) < 32 for c in name):
                raise OneBotError('请提供 1–60 字、无换行或控制字符的新群名。')
            if name == target['group_name']:
                raise OneBotError('群名已是这个名称，无需修改。')
            return target, 'set_group_name', dict(group_id=int(group), group_name=name), label + f'：群名改为「{name}」'
        if action == 'request':
            if type(params.get('approve')) is not bool:
                raise OneBotError('请明确同意还是拒绝这条入群申请。')
            matches = [r for r in requests(client, target) if r['request_id'] == params.get('request_id')]
            if len(matches) != 1:
                raise OneBotError('该入群申请已变化、已处理或无法唯一核对，请重新读取。')
            r = matches[0]
            target['request'] = {k: r[k] for k in ('request_id', 'user_id', 'name', 'sub_type')}
            reason = params.get('reason', '')
            if not isinstance(reason, str) or len(reason) > 200 or any(ord(c) < 32 for c in reason):
                raise OneBotError('申请处理理由无效。')
            summary = label + f'：{"同意" if params["approve"] else "拒绝"}「{r["name"]}」（{r["user_id"]}）入群'
            if reason and not params['approve']:
                summary += '；理由：' + reason
            return target, 'set_group_add_request', dict(flag=r['flag'], sub_type=r['sub_type'], approve=params['approve'], reason=reason), summary
        raise OneBotError('不支持此群管理操作。')

    def propose(self, context, cid, action, params, cancel=None):
        sid, tid = context['session_id'], context['turn_id']
        signature = hashlib.sha256(dump([cid, action, params]).encode()).hexdigest()
        with self.store.connect() as db:
            old = db.execute('SELECT * FROM group_admin_operations WHERE session_id=? AND turn_id=? AND signature=?', (sid, tid, signature)).fetchone()
        if old:
            return self._public(old)
        target, _, _, summary = self.validate(self.client_factory(), cid, action, params)
        if cancel and cancel.is_set():
            raise OneBotError('本轮已停止，未创建管理操作。')
        oid, now = uuid.uuid4().hex, time.time()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT count(*) FROM group_admin_operations WHERE turn_id=?', (tid,)).fetchone()[0] >= 5:
                raise OneBotError('本轮最多提出 5 项管理操作，请分批核对。')
            policy = db.execute('SELECT mode,revision FROM group_admin_policy WHERE session_id=?', (sid,)).fetchone()
            policy = dict(policy) if policy else dict(mode='ask', revision=0)
            # Auto permission must already have been granted before this turn.
            mode = policy['mode'] if policy == context.get('policy') else 'ask'
            state = 'REJECTED' if mode == 'deny' else 'PENDING'
            db.execute('''INSERT INTO group_admin_operations(id,session_id,turn_id,signature,action,target,params,summary,
                status,created,expires,updated,approval_token,decision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (oid, sid, tid, signature, action, dump(target), dump(params), summary, state, now, now + 900, now,
                 secrets.token_urlsafe(32), 'default_deny' if mode == 'deny' else ''))
            self._audit(db, sid, oid, state, summary)
        if mode == 'allow':
            return self.decide(oid, sid, True, auto_policy=policy, cancel=cancel)
        return self.get(oid)

    def decide(self, oid, sid, allow, token=None, *, auto_policy=None, cancel=None):
        if type(allow) is not bool:
            raise ValueError('需要明确允许或拒绝。')
        with _execution_lock:
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT * FROM group_admin_operations WHERE id=? AND session_id=?', (oid, sid)).fetchone()
                if not row:
                    raise ValueError('操作不存在。')
                if auto_policy is None and (not isinstance(token, str) or not secrets.compare_digest(row['approval_token'], token)):
                    raise ValueError('审批凭据无效，请重新载入当前操作。')
                if row['status'] != 'PENDING':
                    return self._public(row)
                if row['expires'] < time.time():
                    state = 'EXPIRED'
                elif cancel and cancel.is_set():
                    state = 'CANCELLED'
                elif not allow:
                    state = 'REJECTED'
                else:
                    if auto_policy is not None:
                        saved = db.execute('SELECT mode,revision FROM group_admin_policy WHERE session_id=?', (sid,)).fetchone()
                        if not saved or dict(saved) != auto_policy or saved['mode'] != 'allow':
                            return self._public(row)
                    state = 'EXECUTING'
                decision = 'default_allow' if auto_policy else 'user_allow' if allow else 'user_deny'
                db.execute('UPDATE group_admin_operations SET status=?,updated=?,decision=? WHERE id=?', (state, time.time(), decision, oid))
                self._audit(db, sid, oid, state, decision)
            if state != 'EXECUTING':
                return self.get(oid)
            try:
                client = self.client_factory()
                target, api, payload, summary = self.validate(client, json.loads(row['target'])['conversation_id'], row['action'], json.loads(row['params']))
                if dump(target) != row['target'] or summary != row['summary']:
                    raise OneBotError('群名、成员身份或权限已变化，旧审批已失效，请重新提出操作。')
                if cancel and cancel.is_set():
                    raise OneBotError('本轮已停止，未执行管理操作。')
                client.call(api, payload, approved=True)
                state, detail = 'SUCCEEDED', 'QQ 接口返回操作成功。'
            except OneBotUncertain as exc:
                state, detail = 'UNKNOWN', str(exc)
            except OneBotError as exc:
                state, detail = 'FAILED', str(exc)
            except Exception:
                state, detail = 'UNKNOWN', '操作中断，结果未知；请在 QQ 核对，不会自动重试。'
            with self.store.connect() as db:
                db.execute('UPDATE group_admin_operations SET status=?,updated=?,detail=? WHERE id=?', (state, time.time(), detail, oid))
                self._audit(db, sid, oid, state, detail)
            return self.get(oid)

    def cancel(self, sid, tid=None):
        with self.store.connect() as db:
            db.execute("UPDATE group_admin_operations SET status='CANCELLED',updated=? WHERE session_id=? AND status='PENDING'" + (' AND turn_id=?' if tid else ''),
                       [time.time(), sid] + ([tid] if tid else []))

    def recover(self):
        with self.store.connect() as db:
            db.execute("UPDATE group_admin_operations SET status='UNKNOWN',detail='服务在操作中重启，结果未知；请在 QQ 核对。',updated=? WHERE status='EXECUTING'", (time.time(),))
