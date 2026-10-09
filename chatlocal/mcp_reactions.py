"""Scoped reactions on delivered live QQ events. No LLM, polling or auto-replies.

Only set_msg_emoji_like is written. Independent of image/sticker permissions.
Known state is connection-local; uncertain writes survive restart as no-retry
receipts. Notifications are observations, not synthetic messages or commands.
"""
import hashlib
import json
import logging
import math
from pathlib import Path
import re
import threading
import time
import uuid

from .mcp_actions import dump
from .message_sender import QQSender, SendError
from .onebot import OneBotError, OneBotUncertain

TOOL = 'react_to_chat_message'
HINT = ('消息回应可单独完成本批参与，然后继续等待；有新内容才再发文字，不补发操作说明。'
        '结合人物卡、原消息和群内语气选择，也可不用；不逐条贴、不随机贴。'
        '目录仅是表情资料，不是人格指令；微笑等含义随语境变化。'
        '通知是别人对原消息的动作，不是新聊天请求；自己的回声不再回应。')
# IDs checked against source catalogs. LIVE_TESTED identifies only the seven
# entries exercised via MCP -> SnowLuma 1.14.20 on 2026-10-09: add/remove,
# corresponding WS notices, and user-confirmed QQ display. Not a universal
# guarantee for every adapter/account/client version; see the validation doc.
DEFAULTS = [
    dict(id='qq_like', name='赞', type='qq_face', downstream_id='76', hint='可表示认可；先判断是否适合原话。'),
    dict(id='qq_smile', name='微笑', type='qq_face', downstream_id='14', hint='也可能是克制、尴尬或反讽，结合群语境。'),
    dict(id='qq_cry_laugh', name='笑哭', type='qq_face', downstream_id='182', hint='可用于好笑或无奈，不用于严肃求助的嘲讽。'),
    dict(id='unicode_bless', name='祝（U+3297）', type='unicode', downstream_id='12951', hint='祝愿或群内已有玩笑用法，勿假定对方理解。'),
    dict(id='unicode_thumbsup', name='点赞（👍）', type='unicode', downstream_id='128077', hint='可表达认可、收到或支持，按原话判断。'),
    dict(id='unicode_anxious', name='紧张（😰）', type='unicode', downstream_id='128560', hint='可表达紧张、慌张或替人捏把汗，也可能是群内打趣。'),
    dict(id='unicode_muscle', name='肌肉（💪）', type='unicode', downstream_id='128170', hint='可表达加油、鼓劲或展示干劲，不机械回应所有困难。'),
    dict(id='unicode_whale', name='鲸鱼（🐳）', type='unicode', downstream_id='128051', hint='适合与鲸鱼有关的内容或群内已有的梗，不作为固定签名。'),
    dict(id='unicode_question', name='问号（❔）', type='unicode', downstream_id='10068', hint='可表达疑惑、没看懂或轻微惊讶，留意是否会被理解成质问。'),
    dict(id='unicode_fist', name='拳头（👊）', type='unicode', downstream_id='128074', hint='可表示碰拳、鼓劲或熟人间打趣，不能脱离语境假定敌意。'),
    dict(id='unicode_loud_cry', name='大哭（😭）', type='unicode', downstream_id='128557', hint='可表示难过、感动或夸张感叹，依据聊天语气选择。'),
]
LIVE_TESTED = frozenset(('unicode_thumbsup', 'unicode_anxious', 'unicode_muscle',
                        'unicode_whale', 'unicode_question', 'unicode_fist', 'unicode_loud_cry'))


class Rejected(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def schema(tool, catalog):
    return tool(TOOL, '对当前持续群聊中已读取的真实消息添加/撤销小表情回应。'
        '只使用 event_id 和以下目录 id，不传 QQ 原始编号或任意表情。'
        '成功即可继续等待，不必补发文字；UNKNOWN 禁止换编号重试。候选资料：'+dump(catalog),
        dict(session_id=dict(type='string', minLength=32, maxLength=32),
             plan_id=dict(type='string', minLength=32, maxLength=32, description='先规划取得 READY 计划。'),
             event_id=dict(type='integer', minimum=1, maximum=2**63-1),
             reaction_id=dict(type='string', minLength=1, maxLength=48),
             operation=dict(type='string', enum=['add','remove']),
             idempotency_key=dict(type='string', minLength=8, maxLength=80)),
        ['session_id','event_id','reaction_id','operation','idempotency_key'])


def target_digest(target):
    # Optional event sequence fields must not turn the same uncertain write into
    # a new operation after reconnect. Sequence is verified separately at dispatch.
    fields = ('conversation_id', 'onebot_message_id', 'sender_id', 'timestamp')
    return hashlib.sha256(dump({k:target[k] for k in fields}).encode()).hexdigest()


class ChatReactions:
    def __init__(self, chat):
        self.chat, self.access, self.actions = chat, chat.access, chat.actions
        self.lock = threading.RLock()  # Never holds the chat/wait or media locks.
        self.epoch = uuid.uuid4().hex
        self.policy_path = Path(self.access.path).parent / 'mcp-reactions.json'
        self.log = logging.getLogger('tulpa.mcp.reactions')
        with self.access.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS chat_reaction_attempts(
                    operation_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                    identity TEXT NOT NULL, target_hash TEXT NOT NULL, emoji_id TEXT NOT NULL,
                    desired INTEGER NOT NULL, epoch TEXT NOT NULL, at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS reaction_target_attempts
                    ON chat_reaction_attempts(identity,target_hash,emoji_id,at);
                CREATE INDEX IF NOT EXISTS reaction_session_attempts ON chat_reaction_attempts(session_id,at);
                CREATE TABLE IF NOT EXISTS chat_reaction_state(
                    identity TEXT NOT NULL,target_hash TEXT NOT NULL,emoji_id TEXT NOT NULL,
                    desired INTEGER NOT NULL,epoch TEXT NOT NULL,at REAL NOT NULL,operation_id TEXT,
                    PRIMARY KEY(identity,target_hash,emoji_id));
                CREATE TABLE IF NOT EXISTS chat_reaction_notices(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,
                    target_id INTEGER NOT NULL,emoji_id TEXT NOT NULL,operator_id TEXT,
                    fingerprint TEXT NOT NULL,payload TEXT NOT NULL,epoch TEXT NOT NULL,at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS reaction_notice_session ON chat_reaction_notices(session_id,id);
            ''')
            if 'reaction_seen' not in {r['name'] for r in db.execute('PRAGMA table_info(chat_sessions)')}:
                db.execute('ALTER TABLE chat_sessions ADD COLUMN reaction_seen INTEGER NOT NULL DEFAULT 0')
            # No remote-state inference from a previous service's cache.
            db.execute('DELETE FROM chat_reaction_state')
            db.execute('DELETE FROM chat_reaction_notices')

    def reset_connection(self):
        # Invalidate references/cache without waiting behind a network write.
        self.epoch = uuid.uuid4().hex

    def policy(self):
        config = {}
        try:
            if self.policy_path.exists():
                if self.policy_path.is_symlink() or self.policy_path.stat().st_size > 65536:
                    raise ValueError()
                config = json.loads(self.policy_path.read_text('utf-8-sig'))
            if not isinstance(config, dict) or set(config)-{'enabled','cooldown_seconds','candidates'}:
                raise ValueError()
            enabled, cooldown = config.get('enabled', True), config.get('cooldown_seconds', 10)
            if type(enabled) is not bool or type(cooldown) not in (int,float) or not math.isfinite(cooldown) or not 0 <= cooldown <= 3600:
                raise ValueError()
            defaults = [dict(c, enabled=True, verification='live_tested' if c['id'] in LIVE_TESTED else 'source_checked') for c in DEFAULTS]
            candidates = config.get('candidates', defaults)
            if not isinstance(candidates, list) or len(candidates)>16:raise ValueError()
            result=[];ids=set();downstream=set()
            for item in candidates:
                if not isinstance(item,dict) or set(item)-{'id','name','type','downstream_id','hint','enabled','verification'}:raise ValueError()
                c=dict(item)
                if not isinstance(c.get('id'),str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,47}',c['id']) or c['id'] in ids:raise ValueError()
                for key,limit in (('name',40),('hint',120)):
                    if not isinstance(c.get(key),str) or not 1<=len(c[key])<=limit or any(ord(ch)<32 for ch in c[key]):raise ValueError()
                value=c.get('downstream_id')
                if not isinstance(value,str) or not re.fullmatch(r'0|[1-9][0-9]{0,6}',value):raise ValueError()
                n=int(value)
                if c.get('type')=='qq_face':
                    if not 0<=n<=999:raise ValueError()
                elif c.get('type')=='unicode':
                    if not 1000<=n<=0x10ffff or 0xd800<=n<=0xdfff or chr(n) in ('\u200d','\ufe0f'):raise ValueError()
                else:raise ValueError()
                if value in downstream:raise ValueError()
                c.setdefault('enabled',True);c.setdefault('verification','pending')
                if type(c['enabled']) is not bool or c['verification'] not in ('source_checked','live_tested','operator_verified','pending'):raise ValueError()
                if c['verification'] in ('source_checked','live_tested') and not any(c['id']==d['id'] and c['type']==d['type'] and value==d['downstream_id'] for d in DEFAULTS):raise ValueError()
                if c['verification']=='live_tested' and c['id'] not in LIVE_TESTED:raise ValueError()
                c['executable']=c['enabled'] and c['verification']!='pending'
                ids.add(c['id']);downstream.add(value);result.append(c)
            return dict(enabled=enabled,cooldown_seconds=cooldown,candidates=result,
                verification_note='source_checked 仅核对来源和接口；live_tested 已在 SnowLuma 1.14.20 完成添加/撤销、通知接收及用户目视确认，仅代表该环境样本。operator_verified 由本机使用者确认。')
        except (OSError,ValueError,TypeError):
            return dict(enabled=False,cooldown_seconds=10,candidates=[],error_code='reaction_config_invalid',
                        note='消息回应配置无效，已停止此子功能；其他聊天工具不受影响。')

    def catalog(self, scope):
        cfg=self.policy()
        return dict(cfg,authorized=bool(scope.get('chat_reactions')),hint=HINT,
                    enabled=cfg['enabled'] and bool(scope.get('chat_reactions')))

    def guard(self, gid, sid, cancel):
        cfg=self.policy()
        if not cfg['enabled']:raise Rejected(cfg.get('error_code','feature_disabled'),'消息表情回应已停用或配置无效。')
        grant,row=self.chat.validate(sid,gid)
        if not grant['scope'].get('chat_reactions'):raise Rejected('reaction_not_authorized','此连接未获消息回应权限，请由用户在 MCP 界面选择。')
        if cancel.is_set():raise Rejected('cancelled','已取消，未派发回应。')
        status=self.chat.receiver.status()
        if status['state']!='connected' or status['account']!=row['conversation_id'].split(':')[0]:
            raise Rejected('source_unavailable','实时连接未就绪或账号不一致，暂停回应。')
        return grant,row,cfg

    def target(self,row,eid):
        selected=self.chat.response_target(row,eid)
        with self.access.connect() as db:
            found=db.execute('SELECT payload FROM chat_inbox WHERE session_id=? AND id=?',(row['id'],eid)).fetchone()
        p=json.loads(found['payload']) if found else {}
        if p.get('source')!='onebot_websocket' or p.get('reaction_epoch')!=self.epoch:
            raise Rejected('target_expired','目标不是当前连接接收的真实消息，或引用已因断线/重启失效；请读取新的真实群消息。')
        if any(p.get(k) for k in ('virtual','is_notify','is_system','synthetic')):
            raise Rejected('target_not_message','通知、摘要或虚拟消息不能作为回应目标。')
        if 'onebot_message_seq' in p:selected['message_seq']=p['onebot_message_seq']
        return selected

    def request(self, grant, args, cancel):
        began=time.monotonic()
        try:
            with self.lock:return self.execute(grant,args,cancel)
        except Rejected as exc:
            self.log.info('reaction_rejected code=%s',exc.code)
            return self.rejected(args,exc.code,str(exc))
        except OneBotError as exc:
            return dict(self.rejected(args,exc.code,str(exc)),state='FAILED',retcode=exc.retcode,http_status=exc.http_status)
        except ValueError:
            return self.rejected(args,'target_or_scope_invalid','会话、授权或已读目标无效；未执行回应。')
        finally:
            self.log.info('reaction_call elapsed_ms=%s',round((time.monotonic()-began)*1000,1))

    @staticmethod
    def rejected(args,code,note):
        return dict(state='REJECTED',session_id=args.get('session_id'),target={'event_id':args.get('event_id')},
                    reaction_id=args.get('reaction_id'),operation=args.get('operation'),error_code=code,note=note)

    def execute(self, grant, args, cancel):
        sid,gid=args['session_id'],grant['id']
        grant,row,cfg=self.guard(gid,sid,cancel)
        candidate=next((c for c in cfg['candidates'] if c['id']==args['reaction_id']),None)
        if not candidate:raise Rejected('emoji_unknown','表情不在当前目录中，不会替换成默认表情。')
        if not candidate['executable']:raise Rejected('emoji_disabled_or_unverified','该候选被禁用或尚未验证，不能执行。')
        selected=self.target(row,args['event_id']);epoch=self.epoch
        desired=args['operation']=='add'
        params={k:args[k] for k in ('session_id','event_id','reaction_id','operation')}
        signature=hashlib.sha256(dump(params).encode()).hexdigest()
        key=hashlib.sha256(('reaction\0'+sid+'\0'+args['idempotency_key']).encode()).hexdigest()
        with self.access.connect() as db:
            old=db.execute('SELECT * FROM operations WHERE grant_id=? AND request_key=?',(gid,key)).fetchone()
        if old:
            if old['signature']!=signature:raise Rejected('idempotency_conflict','同一幂等编号不能用于不同回应操作。')
            return self.receipt(old['id'],gid,args,replayed=True)
        client=self.actions.client_factory()
        if not self.chat.receiver.matches_client(client):
            raise Rejected('instance_changed','HTTP 节点与当前实时连接绑定的实例不同，请先重连再读取新消息。')
        if client.login()!=row['conversation_id'].split(':')[0]:
            raise Rejected('account_changed','HTTP 节点当前 QQ 账号与原消息不一致。')
        identity=hashlib.sha256((client.url+'\0'+row['conversation_id'].split(':')[0]).encode()).hexdigest()
        target_hash=target_digest(selected)
        downstream=candidate['downstream_id']
        base=dict(session_id=sid,target=selected,reaction=candidate,operation=args['operation'],
                  client_display_confirmed=False)
        # Same endpoint/account/group/message identity across keys/sessions. An
        # unresolved attempt blocks BOTH add and remove, including after restart.
        with self.access.connect() as db:
            unknown=db.execute('''SELECT a.operation_id FROM chat_reaction_attempts a JOIN operations o ON o.id=a.operation_id
                WHERE a.identity=? AND a.target_hash=? AND a.emoji_id=? AND o.state IN ('UNKNOWN','EXECUTING') LIMIT 1''',
                (identity,target_hash,downstream)).fetchone()
        if unknown:
            return dict(base,state='UNKNOWN',operation_id=unknown[0],error_code='unresolved_previous_write',
                        note='同一目标和表情已有结果未知的操作；请在 QQ 核对，禁止换编号或反向操作重试。')
        sender=QQSender(dict(REPLY_ONEBOT_URL=client.url,REPLY_ONEBOT_TOKEN=client.token),transport=client.transport)
        try:
            address=sender.check_target(self.chat.live_target(grant,row['conversation_id']))
            info=sender.verify_chat_message(address,selected)
        except SendError:
            raise Rejected('target_unverified','无法通过 OneBot 核对原消息、账号、群和时间；可能是缓存缺失或目标失效。') from None
        seq=info.get('message_seq')
        if type(seq) is not int or seq<=0:
            raise Rejected('target_sequence_missing','原消息没有可核对的 QQ sequence，不猜测编号，不执行回应。')
        if ('message_seq' in selected and selected['message_seq']!=seq) or info.get('sequenceAuthoritative') is False or info.get('sequence_authoritative') is False:
            raise Rejected('target_sequence_changed','原消息的 QQ sequence 已变化或不可信，不执行回应。')
        payload=dict(message_id=int(selected['onebot_message_id']),emoji_id=downstream,set=desired)
        grant,row,cfg=self.guard(gid,sid,cancel)
        if epoch!=self.epoch or self.target(row,args['event_id'])!=selected:
            raise Rejected('target_expired','连接或原消息已变化，未执行回应。')
        if candidate not in cfg['candidates']:
            raise Rejected('catalog_changed','表情配置已改变，请重新读取当前目录。')
        oid=uuid.uuid4().hex;now=time.time()
        with self.access.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            pending=db.execute('''SELECT a.operation_id FROM chat_reaction_attempts a JOIN operations o ON o.id=a.operation_id
                WHERE a.identity=? AND a.target_hash=? AND a.emoji_id=? AND o.state IN ('UNKNOWN','EXECUTING') LIMIT 1''',
                (identity,target_hash,downstream)).fetchone()
            if pending:return dict(base,state='UNKNOWN',operation_id=pending[0],error_code='operation_in_flight',note='相同目标的操作正在执行或结果未知，不重复派发。')
            known=db.execute('SELECT * FROM chat_reaction_state WHERE identity=? AND target_hash=? AND emoji_id=?',
                             (identity,target_hash,downstream)).fetchone()
            if known and known['epoch']==epoch and known['desired']==int(desired):
                return dict(base,state='NOOP',operation_id=known['operation_id'],note='当前连接中已取得相同期望状态的成功回执，无须重复执行。')
            recent=db.execute('SELECT max(at) FROM chat_reaction_attempts WHERE session_id=?',(sid,)).fetchone()[0]
            if recent is not None and now-recent<cfg['cooldown_seconds']:
                raise Rejected('reaction_cooldown',f'本会话回应冷却中，请至少等待 {math.ceil(cfg["cooldown_seconds"]-(now-recent))} 秒。')
            summary=f'消息表情回应：{args["operation"]} {candidate["name"]} → 事件 {args["event_id"]}'
            db.execute('INSERT INTO operations(id,grant_id,request_key,signature,kind,conversation_id,params,target,summary,state,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (oid,gid,key,signature,'reaction',row['conversation_id'],dump(params),dump(selected),summary,'EXECUTING',now,now))
            db.execute('INSERT INTO operation_events(operation_id,state,at) VALUES(?,?,?)',(oid,'EXECUTING',now))
            db.execute('INSERT INTO chat_reaction_attempts VALUES(?,?,?,?,?,?,?,?)',(oid,sid,identity,target_hash,downstream,int(desired),epoch,now))
        dispatched=False
        try:
            self.guard(gid,sid,cancel)
            if self.epoch!=epoch or self.target(row,args['event_id'])!=selected:raise Rejected('target_expired','派发前目标失效。')
            if client.login()!=address['account']:raise Rejected('account_changed','派发前 QQ 账号已变化。')
            _,_,last_config=self.guard(gid,sid,cancel)
            if candidate not in last_config['candidates'] or self.epoch!=epoch or not self.chat.receiver.matches_client(client) or self.target(row,args['event_id'])!=selected:
                raise Rejected('preflight_changed','派发前连接、表情或消息引用变化，未执行回应。')
            self.chat.turns.before_dispatch()
            dispatched=True
            client.call('set_msg_emoji_like',payload,approved=True)
            state='SUCCEEDED'
            result=dict(base,retcode=0,onebot_status='ok',confirmation='api_receipt',turn_complete=True,
                        note='OneBot 接口已接受回应；未目视确认 QQ 显示。本批可以只回应，然后继续等待，不需补发文字。')
        except OneBotUncertain as exc:
            state='UNKNOWN';result=dict(base,error_code=exc.code,retcode=exc.retcode,http_status=exc.http_status,
                                      note='未取得确定回执，结果未知；请在 QQ 核对，禁止自动重试。')
        except OneBotError as exc:
            state='FAILED';result=dict(base,error_code=exc.code,retcode=exc.retcode,http_status=exc.http_status,note=str(exc))
        except Rejected as exc:
            state='REJECTED';result=dict(base,error_code=exc.code,note=str(exc))
        except Exception:
            state='UNKNOWN' if dispatched else 'REJECTED'
            result=dict(base,error_code='dispatch_unknown' if dispatched else 'preflight_failed',note='操作未完成；已派发时必须核对 QQ，不自动重试。')
        with self.access.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.actions.transition(db,oid,state,result)
            if state=='SUCCEEDED' and epoch==self.epoch:
                # A contradictory own echo may arrive while the HTTP call is in
                # flight. Keep the successful receipt, but do not cache a state
                # contradicted by a newer observation.
                echo=db.execute('''SELECT payload FROM chat_reaction_notices
                    WHERE session_id=? AND target_id=? AND emoji_id=? AND operator_id=? AND epoch=? AND at>=?
                    ORDER BY id DESC LIMIT 1''',
                    (sid,args['event_id'],downstream,address['account'],epoch,now)).fetchone()
                consistent=not echo or (json.loads(echo['payload'])['operation']=='add')==desired
                if consistent:
                    db.execute('INSERT OR REPLACE INTO chat_reaction_state VALUES(?,?,?,?,?,?,?)',
                               (identity,target_hash,downstream,int(desired),epoch,time.time(),oid))
                else:
                    db.execute('DELETE FROM chat_reaction_state WHERE identity=? AND target_hash=? AND emoji_id=?',
                               (identity,target_hash,downstream))
        self.log.info('reaction_result state=%s code=%s',state,result.get('error_code','ok'))
        return self.receipt(oid,gid,args)

    def receipt(self, oid, gid, args, replayed=False):
        result=self.actions.get(oid,gid)
        return dict(result,**{k:args[k] for k in ('session_id','reaction_id','operation')},
                    target={'event_id':args['event_id']},replayed=replayed,
                    state='UNKNOWN' if result['state']=='EXECUTING' else result['state'])

    def receipt_target(self,row,target):
        """Authenticate our own sent message for observation, never for writing.

        Some WS nodes suppress own-message echoes. The successful, scoped send
        receipt plus the authenticated notice still identifies a real message.
        This does NOT relax target(), native quote or reaction write checks.
        """
        if not target.get('is_self') or target.get('sender_id')!=row['conversation_id'].split(':')[0]:return False
        request=target.get('send_request')
        if not isinstance(request,dict):return False
        oid=request.get('operation_id')
        if not isinstance(oid,str):return False
        try:
            receipt=self.actions.get(oid,row['grant_id'])
            result=receipt['result']
            return (receipt['kind']=='send' and receipt['state']=='SUCCEEDED'
                    and receipt['conversation_id']==row['conversation_id']
                    and str(result.get('message_id'))==target['onebot_message_id']
                    and int(result.get('timestamp',0)*1000)==target['timestamp']
                    and self.chat.receiver.matches_client(self.actions.client_factory()))
        except (ValueError,TypeError,KeyError):return False

    def receive(self,event):
        """Normalize bounded observations; only new peer reactions to us wake wait."""
        if not self.policy()['enabled']:return
        account,group=str(event.get('self_id','')),str(event.get('group_id',''))
        if not account.isascii() or not account.isdigit() or not group.isascii() or not group.isdigit():return
        mid=event.get('message_id');op=event.get('sub_type')
        if type(mid) is not int or not -(2**31)<=mid<2**31 or mid==0 or op not in ('add','remove'):return
        cid=f'{account}:group:{group}'
        with self.access.connect() as db:
            row=db.execute('SELECT * FROM chat_sessions WHERE active=1 AND conversation_id=?',(cid,)).fetchone()
        if not row:return
        try:
            grant,row=self.chat.validate(row['id'],row['grant_id'])
        except ValueError:return
        # Observing notifications is part of reading this authorized live group;
        # the separate reaction flag controls writing, not ambient observations.
        status=self.chat.receiver.status()
        if status['state']!='connected' or status['account']!=account:return
        with self.access.connect() as db:
            found=db.execute('SELECT * FROM chat_inbox WHERE session_id=? AND message_id=?',(row['id'],str(mid))).fetchone()
        if not found:
            self.log.info('reaction_notice ignored=target_not_cached');return
        target=json.loads(found['payload'])
        if target.get('reaction_epoch')!=self.epoch or target.get('recalled') or target.get('synthetic'):
            self.log.info('reaction_notice ignored=target_expired_or_virtual');return
        source=target.get('source')
        if source!='onebot_websocket' and not (source=='send_receipt' and self.receipt_target(row,target)):
            self.log.info('reaction_notice ignored=target_source_untrusted');return
        sequence=event.get('message_seq')
        if (target.get('onebot_message_seq') is not None and sequence!=target['onebot_message_seq']) or (source=='send_receipt' and (type(sequence) is not int or sequence<=0)):
            self.log.info('reaction_notice ignored=sequence_invalid');return
        uid=event.get('operator_id',event.get('user_id'))
        uid=str(uid) if uid is not None and str(uid).isascii() and str(uid).isdigit() and int(str(uid))>0 else None
        stamp=event.get('time')
        stamp=stamp if type(stamp) in (int,float) and math.isfinite(stamp) and 0<stamp<100000000000 else None
        if stamp is not None and (stamp<row['created']-2 or stamp<target['timestamp']//1000):return
        likes=event.get('likes')
        if not isinstance(likes,list):return
        cfg=self.policy();catalog={c['downstream_id']:c for c in cfg['candidates']}
        for like in likes[:16]:
            if not isinstance(like,dict):continue
            emoji=str(like.get('emoji_id',''))
            if not re.fullmatch(r'[0-9]{1,7}',emoji):continue
            count=like.get('count');count=count if type(count) is int and 0<=count<2**31 else None
            item=dict(kind='message_reaction',conversation_id=cid,target_event_id=found['id'],onebot_message_id=str(mid),
                      operator_id=uid,is_self=uid==account,emoji_id=emoji,emoji_name=catalog.get(emoji,{}).get('name','未知表情 '+emoji),
                      operation=op,reported_count=count,timestamp=stamp,received_at=time.time(),
                      target_is_self=bool(target.get('is_self')),target_source=source,
                      count_note='原样保存通知计数；可能由接入端补默认值，不累加，不推断人数。',
                      source='onebot_notice',client_display_confirmed=False)
            fingerprint=hashlib.sha256(dump([cid,mid,uid,emoji,op,count,stamp,sequence]).encode()).hexdigest()
            with self.access.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                last=db.execute('''SELECT * FROM chat_reaction_notices WHERE session_id=? AND target_id=? AND emoji_id=? AND operator_id IS ? AND epoch=? ORDER BY id DESC LIMIT 1''',
                                (row['id'],found['id'],emoji,uid,self.epoch)).fetchone()
                if last:
                    previous=json.loads(last['payload'])
                    if last['fingerprint']==fingerprint and time.time()-last['at']<60:continue
                    if previous['operation']==op and previous['reported_count']==count and time.time()-last['at']<60:continue
                    if stamp is not None and previous['timestamp'] is not None and stamp<previous['timestamp']:continue
                db.execute('INSERT INTO chat_reaction_notices(session_id,target_id,emoji_id,operator_id,fingerprint,payload,epoch,at) VALUES(?,?,?,?,?,?,?,?)',
                           (row['id'],found['id'],emoji,uid,fingerprint,dump(item),self.epoch,time.time()))
                db.execute('DELETE FROM chat_reaction_notices WHERE session_id=? AND id NOT IN (SELECT id FROM chat_reaction_notices WHERE session_id=? ORDER BY id DESC LIMIT 200)',(row['id'],row['id']))
                # Own echoes can invalidate an API receipt's cached desired state,
                # but never convert UNKNOWN into success or provoke a write.
                if item['is_self']:
                    target_hash=target_digest(target)
                    db.execute('DELETE FROM chat_reaction_state WHERE target_hash=? AND emoji_id=? AND desired!=?',(target_hash,emoji,int(op=='add')))
            self.log.info('reaction_notice accepted=1 self_echo=%s',item['is_self'])
            if uid and not item['is_self'] and item['target_is_self'] and found['delivered']:
                signal=self.chat.events.get(row['id'])
                if signal:signal.set()

    def pending(self,row):
        # A tiny bounded query; do not rebuild the whole context every wait tick.
        with self.access.connect() as db:
            rows=db.execute('''SELECT n.id,n.payload FROM chat_reaction_notices n
                JOIN chat_inbox i ON i.id=n.target_id AND i.session_id=n.session_id
                JOIN chat_sessions s ON s.id=n.session_id
                WHERE n.session_id=? AND n.epoch=? AND i.delivered=1 AND n.id>s.reaction_seen
                ORDER BY n.id DESC LIMIT 12''',(row['id'],self.epoch)).fetchall()
        result=[]
        for r in reversed(rows):
            p=json.loads(r['payload'])
            if p.get('operator_id') and not p['is_self'] and p.get('target_is_self'):
                result.append(dict(p,notice_id=r['id']))
        return result

    def context(self,row,*,consume=False):
        with self.access.connect() as db:
            seen=db.execute('SELECT reaction_seen FROM chat_sessions WHERE id=?',(row['id'],)).fetchone()[0]
            rows=db.execute('''SELECT n.id,n.payload,i.payload original FROM chat_reaction_notices n JOIN chat_inbox i ON i.id=n.target_id AND i.session_id=n.session_id
                WHERE n.session_id=? AND n.epoch=? AND i.delivered=1 ORDER BY n.id DESC LIMIT 12''',(row['id'],self.epoch)).fetchall()
        items=[]
        for r in reversed(rows):
            p=json.loads(r['payload'])
            original=json.loads(r['original'])
            p['target']=dict(event_id=p['target_event_id'],sender_id=original.get('sender_id'),
                            content_excerpt=original.get('content','')[:160],recalled=bool(original.get('recalled')))
            verb='添加' if p['operation']=='add' else '撤销'
            p['notice_id']=r['id'];p['description']=f'{p["operator_id"] or "身份未知的成员"} 对已读消息事件 {p["target_event_id"]} {verb}了 {p["emoji_name"]} 回应'
            items.append(p)
        new=[p for p in items if p['notice_id']>seen and p.get('operator_id') and not p['is_self'] and p.get('target_is_self')]
        if consume and items:
            with self.access.connect() as db:
                db.execute('UPDATE chat_sessions SET reaction_seen=max(reaction_seen,?) WHERE id=?',(max(p['notice_id'] for p in items),row['id']))
        return dict(items=items,scope='仅当前连接期间收到、且目标在本授权会话已读缓存内的最近12条通知；不是群内全部回应或完整远端历史。',
                    new_items=new,wake_model=False,note='new_items 是本次新交付的他人对本人消息的回应，可按语境接话或沉默；不自动发言。items 是有限历史，勿重复处理；自己的回声不触发回复。')
