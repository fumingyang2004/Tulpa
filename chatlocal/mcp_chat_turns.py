"""Bounded Planner/Replyer protocol for one external Agent. No model calls.

The session lock serializes expressions, not receiving, waiting or stopping.
Freshness is checked again inside each existing adapter's dispatch guard.
"""
import hashlib
import json
import math
import threading
import time
import uuid

from .mcp_actions import dump

TOOLS = {'plan_chat_reply', 'send_chat_reply'}
DEFAULTS = dict(quiet_seconds=2., max_collect_seconds=8., reply_cooldown_seconds=3.,
                batch_ttl_seconds=120., plan_ttl_seconds=90., bubble_min_seconds=1.2,
                bubble_max_seconds=4., bubble_seconds_per_char=.035)
RANGES = dict(quiet_seconds=(.1,5), max_collect_seconds=(1,30), reply_cooldown_seconds=(0,60),
              batch_ttl_seconds=(10,300), plan_ttl_seconds=(5,180), bubble_min_seconds=(.1,5),
              bubble_max_seconds=(.1,10), bubble_seconds_per_char=(0,.2))


class TurnRejected(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details


def schema(tool):
    sid=dict(type='string',minLength=32,maxLength=32,description='原样复制工具返回的字符串；JSON 中必须带双引号。')
    key=dict(type='string',minLength=8,maxLength=80,description='可省略，由程序按当前批次/计划生成稳定编号；重试不能换参数。')
    event=dict(type='integer',minimum=1,maximum=2**63-1)
    return [tool('plan_chat_reply',
        'Planner 阶段：根据 get/wait 返回的 input_batch.id，决定等待、沉默或参与，指定接谁的话和表达通道。'
        '程序核对相关消息静默与新鲜度；READY 后按 next_call 先完成表达选择（有候选时可明确全不选），再进入 Replyer。只写简短沟通意图，不写思维链、不发进群。',
        dict(session_id=sid,idempotency_key=key,batch_id=sid,
             action=dict(type='string',enum=['wait','silence','reply']),
             target_event_id=event, topic_event_ids=dict(type='array',maxItems=5,uniqueItems=True,items=event),
             topic_terms=dict(type='array',maxItems=6,uniqueItems=True,items=dict(type='string',minLength=2,maxLength=24)),
             expressions=dict(type='array',maxItems=3,uniqueItems=True,items=dict(type='string',enum=['text','sticker','reaction'])),
             intent=dict(type='string',minLength=1,maxLength=200),
             wait_seconds=dict(type='number',minimum=0,maximum=30)),
        ['session_id','batch_id','action','intent']),
        tool('send_chat_reply',
        'Replyer 阶段：按 READY 计划一次提交 1–3 个语义气泡，不按标点机械拆句。'
        '有学习候选的文字回复须先 select_chat_expressions，选 0–5 条；未选择会返回 expression_selection_required。'
        '程序串行发送、气泡间留间隔并重新检查相关新消息/停止/授权。STALE 需补读重规划，已发不撤回，旧尾部不续发；UNKNOWN 禁止重试。'
        '纯 reaction、纯表情包均可结束本轮，不必配文。',
        dict(session_id=sid,plan_id=sid,idempotency_key=key,
             bubbles=dict(type='array',minItems=1,maxItems=3,items=dict(type='object',additionalProperties=False,
                properties=dict(kind=dict(type='string',enum=['text','sticker','reaction']),
                    text=dict(type='string',minLength=1,maxLength=4000),sticker_id=sid,
                    event_id=event,reaction_id=dict(type='string',maxLength=64),
                    operation=dict(type='string',enum=['add','remove']),
                    quote=dict(type='boolean'),mention_user_ids=dict(type='array',maxItems=5,uniqueItems=True,
                        items=dict(type='string',pattern='^[1-9][0-9]{0,19}$'))),required=['kind']))),
        ['session_id','plan_id','bubbles'])]


class ChatTurns:
    def __init__(self, chat):
        self.chat, self.access = chat, chat.access
        self.epoch=uuid.uuid4().hex
        self.locks={}; self.locks_guard=threading.Lock(); self.dispatch=threading.local()
        with self.access.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS chat_input_batches(
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, epoch TEXT NOT NULL,
                    created REAL NOT NULL, watermark INTEGER NOT NULL, ids TEXT NOT NULL, gap INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS chat_batch_session ON chat_input_batches(session_id,created);
                CREATE TABLE IF NOT EXISTS chat_turn_plans(
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, request_key TEXT NOT NULL, signature TEXT NOT NULL,
                    payload TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                    queue TEXT NOT NULL DEFAULT '[]', queue_signature TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '', UNIQUE(session_id,request_key));
                CREATE INDEX IF NOT EXISTS chat_plan_session ON chat_turn_plans(session_id,created);
            ''')
            # Never resume an old tail automatically. Dispatch uncertainty stays visible.
            for row in db.execute("SELECT * FROM chat_turn_plans WHERE state IN ('READY','SENDING','WAITING')").fetchall():
                queue=json.loads(row['queue'])
                for item in queue:
                    if item['state']=='DISPATCHING': item['state']='UNKNOWN'
                    elif item['state']=='PENDING': item['state']='CANCELLED'
                db.execute('UPDATE chat_turn_plans SET state=?,queue=?,reason=? WHERE id=?',
                    ('UNKNOWN' if any(x['state']=='UNKNOWN' for x in queue) else 'CANCELLED',dump(queue),'service_restarted',row['id']))
            db.execute('DELETE FROM chat_input_batches')

    def policy(self):
        path=self.access.path.parent/'mcp-chat-policy.json'
        try:
            cfg=json.loads(path.read_text('utf-8-sig')) if path.exists() else {}
            if not isinstance(cfg,dict) or set(cfg)-set(DEFAULTS): raise ValueError()
            policy=DEFAULTS|cfg
            for key,value in policy.items():
                low,high=RANGES[key]
                if type(value) not in (float,int) or not math.isfinite(value) or not low<=value<=high: raise ValueError()
            if policy['bubble_max_seconds']<policy['bubble_min_seconds']:raise ValueError()
            return policy
        except (ValueError,OSError):
            raise TurnRejected('chat_policy_invalid','聊天节奏配置无效，请修正本地 mcp-chat-policy.json。') from None

    def lock(self,sid):
        with self.locks_guard:return self.locks.setdefault(sid,threading.Lock())

    def rejected(self,exc,sid=None):
        result=dict(state='REJECTED',error_code=exc.code,note=str(exc),**exc.details)
        refresh={'stale','needs_refresh','batch_expired','plan_expired','target_expired','plan_missing','plan_not_ready','target_not_in_batch'}
        if exc.code in refresh:
            result['recovery']=dict(action='refresh_and_replan',automatic_write_retry=False,
                next_call=dict(tool='get_chat_session',arguments=dict(session_id=sid)))
        elif exc.code in {'needs_wait','reply_cooldown','source_unavailable','queue_busy'}:
            delay=min(30,max(1,exc.details.get('wait_seconds',2)))
            continuation=self.chat.continuation(sid)
            continuation['arguments']['minimum_wait_seconds']=delay
            result['recovery']=dict(action='wait',automatic_write_retry=False,
                retry_after_seconds=delay,next_call=continuation)
        elif exc.code=='expression_selection_required':
            result['recovery']=dict(action='select_expressions',automatic_write_retry=False,next_call=exc.details['next_call'])
        else:
            result['recovery']=dict(action='fix_request_or_stop',automatic_write_retry=False)
        return result

    def batch(self,row,items,collection=None):
        now=time.time();bid=uuid.uuid4().hex;ids=[m['id'] for m in items]
        with self.access.connect() as db:
            db.execute('INSERT INTO chat_input_batches VALUES(?,?,?,?,?,?,?)',
                       (bid,row['id'],self.epoch,now,max(ids,default=row['cursor']),dump(ids),row['gap_count']))
            db.execute('DELETE FROM chat_input_batches WHERE session_id=? AND id NOT IN (SELECT id FROM chat_input_batches WHERE session_id=? ORDER BY created DESC LIMIT 64)',(row['id'],row['id']))
        # Preserve individual IDs/time/sources; grouping is only a reading aid.
        bursts=[]
        for m in items:
            if not bursts or bursts[-1]['sender_id']!=m['sender_id'] or m['timestamp']-bursts[-1]['last_timestamp']>5000:
                bursts.append(dict(sender_id=m['sender_id'],event_ids=[],last_timestamp=m['timestamp']))
            bursts[-1]['event_ids'].append(m['id']);bursts[-1]['last_timestamp']=m['timestamp']
        return dict(id=bid,version=max(ids,default=row['cursor']),phase='DECIDING',
                    collection=collection or dict(reason='context_read',ready_to_send=False),bursts=bursts,
                    plan_call=dict(tool='plan_chat_reply',arguments=dict(session_id=row['id'],batch_id=bid),
                                   requires=['action','intent'],optional=['target_event_id','expressions']),
                    next='plan_chat_reply；普通读取不豁免静默或发送前检查。')

    def status(self,row):
        with self.access.connect() as db:
            p=db.execute('SELECT id,state,reason FROM chat_turn_plans WHERE session_id=? ORDER BY created DESC LIMIT 1',(row['id'],)).fetchone()
            pending=db.execute('SELECT 1 FROM chat_inbox WHERE session_id=? AND id>? LIMIT 1',(row['id'],row['cursor'])).fetchone()
        phase=('STOPPED' if not row['active'] else 'COLLECTING' if row['id'] in self.chat.waiters and pending
               else 'WAITING' if row['id'] in self.chat.waiters else 'REPLYING' if p and p['state']=='READY'
               else 'SENDING' if p and p['state']=='SENDING' else 'DECIDING')
        return dict(phase=phase,last_plan=dict(p) if p else None)

    def remaining_wait(self,sid):
        with self.access.connect() as db:
            p=db.execute("SELECT payload FROM chat_turn_plans WHERE session_id=? AND state='WAITING' ORDER BY created DESC LIMIT 1",(sid,)).fetchone()
        return max(0,json.loads(p[0])['not_before']-time.time()) if p else 0

    def data(self,sid):
        with self.access.connect() as db:
            rows=db.execute('SELECT id,payload,delivered FROM chat_inbox WHERE session_id=? ORDER BY id',(sid,)).fetchall()
        return [dict(json.loads(r['payload']),id=r['id'],delivered=bool(r['delivered'])) for r in rows]

    @staticmethod
    def related(message,focus,account):
        if message.get('is_self'):return False
        if focus.get('all'):return True
        if message['sender_id'] in focus['senders']:return True
        for seg in message.get('segments',[]):
            if seg.get('type')=='reply' and seg.get('onebot_message_id') in focus['message_ids']:return True
            if seg.get('type')=='at' and seg.get('qq')==account:return True
        text=message.get('content','').casefold()
        return any(term.casefold() in text for term in focus['terms'])

    def collection(self,row,pending,quiet,started):
        policy=self.policy();items=self.chat.messages(pending)
        peers=[m for m in items if not m['is_self']]
        if not peers:return dict(ready=True,reason='no_peer_input',remaining_seconds=0)
        # The oldest pending speaker anchors this bounded collection. Unrelated
        # hot-group traffic cannot slide its tail forever. @ is prioritized later.
        anchor=peers[0]
        focus=dict(senders=[anchor['sender_id']],message_ids=[anchor['onebot_message_id']],terms=[])
        relevant=[m for m in peers if self.related(m,focus,row['conversation_id'].split(':')[0])]
        remaining=max(0,quiet-(time.time()-max(m['received_at'] for m in relevant)))
        capped=time.monotonic()-started>=policy['max_collect_seconds']
        return dict(ready=remaining==0 or capped,reason='collection_limit' if capped and remaining else 'quiet' if not remaining else 'collecting',
                    remaining_seconds=round(remaining,2),ready_to_send=remaining==0)

    def load(self,pid,sid):
        with self.access.connect() as db:
            r=db.execute('SELECT * FROM chat_turn_plans WHERE id=? AND session_id=?',(pid,sid)).fetchone()
        if not r:raise TurnRejected('plan_missing','计划不属于当前会话，请补读并规划。')
        return dict(r)

    def record_language_context(self,p,context):
        """Audit prepared MCP reference material, never claim host prompt use.

        Keep only IDs/counts/digests in the existing bounded turn receipt. Full
        candidates remain in the short-lived learning store. No new raw chat log.
        """
        decision=context['expression_decision'];complete=context['selection_complete']
        candidates=context.get('candidates',[]);selected=context.get('selected',[])
        material={k:context.get(k,[]) for k in (('selected','jargon') if complete else ('candidates','jargon'))}
        fingerprint=hashlib.sha256(dump(material).encode()).hexdigest()
        with self.access.connect() as db:
            row=db.execute('SELECT payload,state,queue_signature FROM chat_turn_plans WHERE id=? AND session_id=?',
                           (p['id'],p['session_id'])).fetchone()
            if not row:return
            if row['state']!='READY' or row['queue_signature']:
                p['payload']=row['payload'];return
            payload=json.loads(row['payload']);old=payload.get('expression_selection',{})
            audit=dict(state=decision['state'],reason=decision['reason'],candidate_ids=[x['id'] for x in candidates],
                       selected_ids=[x['id'] for x in selected],jargon_ids=[x['id'] for x in context.get('jargon',[])],
                       delivery='tool_result_prepared',host_injection='unverified',model_use='unverified')
            if 'candidate_context' in old:audit['candidate_context']=old['candidate_context']
            key='selected_context' if complete else 'candidate_context'
            prior=old.get(key,{})
            audit[key]=dict(sha256=fingerprint,chars=len(dump(material)),
                            prepared_at=prior.get('prepared_at',time.time()) if prior.get('sha256')==fingerprint else time.time())
            if old!=audit:
                payload['expression_selection']=audit
                db.execute('UPDATE chat_turn_plans SET payload=? WHERE id=?',(dump(payload),p['id']))
            p['payload']=dump(payload)

    def public(self,p):
        data=json.loads(p['payload'])
        result=dict(plan_id=p['id'],state=p['state'],phase='REPLYING' if p['state']=='READY' else p['state'],
                    decision={k:data[k] for k in ('action','target_event_id','expressions','intent','batch_id','watermark','wait_seconds')},
                    bubbles=json.loads(p['queue']),reason=p['reason'],turn_complete=p['state'] in ('SUCCEEDED','SILENT'),
                    next='同一 Agent 组织表达后调用 send_chat_reply' if p['state']=='READY' else '继续 wait_chat_messages；旧队列尾部不会恢复')
        if p['state']=='READY':
            result['next_call']=dict(tool='send_chat_reply',arguments=dict(session_id=p['session_id'],plan_id=p['id']),requires=['bubbles'])
            if hasattr(self.chat,'learning'):
                context=self.chat.learning.plan_context(p)
                if context:
                    result['learned_language']=context
                    if context.get('selection_required'):
                        result['phase']='SELECTING_EXPRESSION'
                        result['next']='先选择本轮表达参考，expression_ids=[] 表示明确不用；完成后再组织文字。'
                        result['next_call']=context['select_call']
        elif p['state'] in ('WAITING','SILENT','SUCCEEDED'):
            result['next_call']=self.chat.continuation(p['session_id'],through=data['watermark'])
        else:
            # Neither a pending/unknown queue nor an interrupted tail is a new
            # write request. Reading the durable receipt is always the next step.
            result['recovery']=dict(action='inspect_result',automatic_write_retry=False,
                next_call=dict(tool='get_chat_session',arguments=dict(session_id=p['session_id'])))
        audit=json.loads(p['payload']).get('expression_selection')
        if audit:result['expression_selection']=audit
        return result

    def save(self,p,state,queue=None,reason=''):
        with self.access.connect() as db:
            db.execute('UPDATE chat_turn_plans SET state=?,queue=?,reason=?,updated=? WHERE id=?',
                       (state,dump(json.loads(p['queue']) if queue is None else queue),reason,time.time(),p['id']))
        return self.load(p['id'],p['session_id'])

    def check(self,p,cancel,*,cooldown=False):
        data=json.loads(p['payload']);sid=p['session_id']
        _,row=self.chat.validate(sid,data['grant_id'])
        if cancel.is_set():raise TurnRejected('cancelled','本次发送已取消。')
        policy=self.policy();now=time.time()
        if data['epoch']!=self.epoch or row['gap_count']!=data['gap']:
            raise TurnRejected('needs_refresh','实时连接曾中断或缓存缺失，请补读并重新规划。')
        if now-p['created']>policy['plan_ttl_seconds']:
            raise TurnRejected('plan_expired','计划已过期，请补读并重新规划。')
        source=self.chat.receiver.status()
        if source['state']!='connected' or source['account']!=row['conversation_id'].split(':')[0]:
            raise TurnRejected('source_unavailable','实时接收未连接，暂停发言。')
        rows=self.data(sid);indexed={m['id']:m for m in rows}
        for eid in data['focus_ids']:
            m=indexed.get(eid)
            if not m or m.get('recalled') or m.get('synthetic') or not m['delivered']:
                raise TurnRejected('target_expired','目标已失效，请重新读取。')
        focus=data['focus'];account=row['conversation_id'].split(':')[0]
        # Replies to a first bubble are relevant even from a different speaker.
        with self.access.connect() as db:
            queued=db.execute('SELECT queue FROM chat_turn_plans WHERE id=?',(p['id'],)).fetchone()
        if queued:
            for item in json.loads(queued[0]):
                mid=item.get('receipt',{}).get('result',{}).get('message_id')
                if mid:focus['message_ids'].append(str(mid))
        relevant=[m for m in rows if self.related(m,focus,account)]
        fresh=[m['id'] for m in relevant if m['id']>data['watermark']]
        if fresh:raise TurnRejected('stale','目标相关的新消息尚未纳入决策，请补读并重新规划。',refresh_event_ids=fresh[:12])
        last=max((m['received_at'] for m in relevant),default=data['read_at']-policy['quiet_seconds'])
        remaining=max(last+policy['quiet_seconds'],data['not_before'])-now
        if remaining>0:raise TurnRejected('needs_wait','相关消息仍在收集或计划要求等待；继续 wait 后重规划。',wait_seconds=round(remaining,2))
        if cooldown:
            with self.access.connect() as db:
                prev=db.execute("SELECT max(updated) FROM chat_turn_plans WHERE session_id=? AND id!=? AND queue!='[]'",(sid,p['id'])).fetchone()[0]
            if prev and now-prev<policy['reply_cooldown_seconds']:
                raise TurnRejected('reply_cooldown','上一轮表达刚结束，请留出接话空隙。',wait_seconds=round(policy['reply_cooldown_seconds']-(now-prev),2))

    def plan(self,grant,args,cancel):
        sid=args['session_id'];self.chat.validate(sid,grant['id'])
        args=dict(args)
        if 'idempotency_key' not in args:
            args['idempotency_key']='auto-'+hashlib.sha256(dump(args).encode()).hexdigest()
        lock=self.lock(sid)
        if not lock.acquire(False):return self.rejected(TurnRejected('queue_busy','本会话正在发送，不能并行规划。'),sid)
        try:
            signature=hashlib.sha256(dump(args).encode()).hexdigest()
            with self.access.connect() as db:
                old=db.execute('SELECT * FROM chat_turn_plans WHERE session_id=? AND request_key=?',(sid,args['idempotency_key'])).fetchone()
                batch=db.execute('SELECT * FROM chat_input_batches WHERE id=? AND session_id=?',(args['batch_id'],sid)).fetchone()
            if old:
                if old['signature']!=signature:raise TurnRejected('idempotency_conflict','计划编号已用于另一决定。')
                return self.public(dict(old))
            policy=self.policy();now=time.time()
            if not batch or batch['epoch']!=self.epoch or now-batch['created']>policy['batch_ttl_seconds']:
                raise TurnRejected('batch_expired','读取批次已失效，请 get/wait 后规划。')
            target=args.get('target_event_id');focus_ids=list(dict.fromkeys(([target] if target else [])+args.get('topic_event_ids',[])))
            if any(eid not in json.loads(batch['ids']) for eid in focus_ids):
                raise TurnRejected('target_not_in_batch','目标或话题消息必须来自所选已读批次。')
            rows=self.data(sid);index={m['id']:m for m in rows};focus_rows=[index[eid] for eid in focus_ids if eid in index]
            if len(focus_rows)!=len(focus_ids):raise TurnRejected('target_expired','目标已淘汰，请补读。')
            expressions=args.get('expressions',[])
            if (args['action']=='reply')!=bool(expressions):raise TurnRejected('expression_mismatch','参与时选择表达类型；等待或沉默不提交表达。')
            with self.access.connect() as db:
                waiting=db.execute("SELECT payload FROM chat_turn_plans WHERE session_id=? AND state='WAITING' ORDER BY created DESC LIMIT 1",(sid,)).fetchone()
            if waiting and args['action']=='reply':
                previous=json.loads(waiting[0]);delay=previous['not_before']-now
                if delay>0 and batch['watermark']<=previous['watermark']:
                    raise TurnRejected('needs_wait','已选择等待，尚无新输入或到期；继续 wait。',wait_seconds=round(delay,2))
            if 'reaction' in expressions and (not target or not grant['scope'].get('chat_reactions')):
                raise TurnRejected('reaction_permission','仅回应也需要真实目标和回应授权。')
            if 'sticker' in expressions and not grant['scope'].get('chat_sticker_send'):
                raise TurnRejected('sticker_permission','连接未获发送表情包权限。')
            focus=dict(senders=list({m['sender_id'] for m in focus_rows if not m['is_self']}),
                       message_ids=[m['onebot_message_id'] for m in focus_rows],terms=args.get('topic_terms',[]),all=not bool(focus_ids))
            # Untargeted proactive replies use all peer input as their subject.
            if not focus_ids:focus['senders']=list({m['sender_id'] for m in rows if not m['is_self']})
            row=self.chat.row(sid);pid=uuid.uuid4().hex
            data=dict(action=args['action'],target_event_id=target,focus_ids=focus_ids,focus=focus,
                expressions=expressions,intent=args['intent'],batch_id=batch['id'],watermark=batch['watermark'],
                read_at=batch['created'],epoch=self.epoch,gap=batch['gap'],grant_id=grant['id'],
                wait_seconds=args.get('wait_seconds',0),not_before=now+args.get('wait_seconds',0))
            if args['action']!='reply':
                data['expression_selection']=dict(state='not_applicable',reason='no_reply',delivery='none',
                    candidate_ids=[],selected_ids=[],host_injection='unverified',model_use='unverified')
            candidate=dict(id=pid,session_id=sid,payload=dump(data),created=now)
            if args['action']=='reply':self.check(candidate,cancel,cooldown=True)
            state={'reply':'READY','wait':'WAITING','silence':'SILENT'}[args['action']]
            with self.access.connect() as db:
                db.execute("UPDATE chat_turn_plans SET state='CANCELLED',reason='replanned' WHERE session_id=? AND state IN ('READY','WAITING')",(sid,))
                db.execute('INSERT INTO chat_turn_plans(id,session_id,request_key,signature,payload,state,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                    (pid,sid,args['idempotency_key'],signature,dump(data),state,now,now))
                # Retain uncertainty indefinitely; ordinary closed turn text is
                # bounded independently of immutable native operation receipts.
                db.execute("DELETE FROM chat_turn_plans WHERE session_id=? AND state IN ('SILENT','CANCELLED','SUCCEEDED','INTERRUPTED') AND id NOT IN (SELECT id FROM chat_turn_plans WHERE session_id=? ORDER BY created DESC LIMIT 200)",(sid,sid))
            return self.public(self.load(pid,sid))
        except TurnRejected as exc:return self.rejected(exc,sid)
        finally:lock.release()

    def before_dispatch(self):
        current=getattr(self.dispatch,'current',None)
        if current:self.check(current[0],current[1])

    def submit(self,grant,args,cancel):
        sid=args['session_id'];self.chat.validate(sid,grant['id'])
        if not args.get('plan_id'):return self.rejected(TurnRejected('plan_required','先用最新 input_batch 调用 plan_chat_reply；不能跳过 Planner 直接发送。'),sid)
        lock=self.lock(sid)
        if not lock.acquire(False):return self.rejected(TurnRejected('queue_busy','同一会话已有发送队列；稍后以原计划查询，勿换编号重发。'),sid)
        try:
            p=self.load(args['plan_id'],sid);data=json.loads(p['payload']);bubbles=args['bubbles']
            signature=hashlib.sha256(dump(bubbles).encode()).hexdigest()
            if p['queue_signature']:
                if p['queue_signature']!=signature:raise TurnRejected('idempotency_conflict','该计划已经提交了另一组表达，不能替换或重发。')
                return self.public(p)
            if p['state']!='READY':raise TurnRejected('plan_not_ready','该计划不可发送，请读取结果或补读重规划。')
            if not isinstance(bubbles,list) or not 1<=len(bubbles)<=3:raise TurnRejected('invalid_bubbles','一次提交 1–3 个语义气泡。')
            for i,b in enumerate(bubbles):
                kind=b['kind'];allowed={'text':{'kind','text','quote','mention_user_ids'},'sticker':{'kind','sticker_id'},'reaction':{'kind','event_id','reaction_id','operation'}}[kind]
                if set(b)-allowed or kind not in data['expressions']:raise TurnRejected('expression_mismatch','表达类型或字段与计划不一致。')
                required={'text':{'text'},'sticker':{'sticker_id'},'reaction':{'event_id','reaction_id','operation'}}[kind]
                if required-set(b):raise TurnRejected('expression_mismatch','表达缺少必要内容。')
                if kind=='reaction' and b['event_id']!=data['target_event_id']:raise TurnRejected('target_mismatch','回应目标与计划不一致。')
                if kind=='text' and i and (b.get('quote') or b.get('mention_user_ids')):raise TurnRejected('repeated_addressing','同一轮仅首条按需引用或 @；改变目标需重新规划。')
            self.check(p,cancel,cooldown=True)
            if any(b['kind']=='text' for b in bubbles) and hasattr(self.chat,'learning'):
                prepared=self.public(p)
                if prepared.get('learned_language',{}).get('selection_required'):
                    raise TurnRejected('expression_selection_required','先明确选择本轮的 0–5 条表达参考；全不选也可以。尚未发送。',
                        next_call=prepared['next_call'],learned_language=prepared['learned_language'])
            elif 'text' in data['expressions']:
                # A mixed plan may ultimately use only a sticker/reaction. Keep
                # the evidence of material already offered, but do not demand a
                # redundant selection or leave a successful turn marked pending.
                data=json.loads(p['payload'])
                if 'expression_selection' in data:
                    data['expression_selection'].update(state='not_applicable',reason='non_text_output')
                    p['payload']=dump(data)
                    with self.access.connect() as db:db.execute('UPDATE chat_turn_plans SET payload=? WHERE id=?',(p['payload'],p['id']))
            queue=[dict(index=i,expression=b,state='PENDING') for i,b in enumerate(bubbles)]
            with self.access.connect() as db:db.execute('UPDATE chat_turn_plans SET queue_signature=? WHERE id=?',(signature,p['id']))
            p=self.save(p,'SENDING',queue)
            self.dispatch.current=(p,cancel)
            try:
                for i,item in enumerate(queue):
                    try:
                        if i:
                            cfg=self.policy();previous=bubbles[i-1].get('text','')
                            delay=min(cfg['bubble_max_seconds'],cfg['bubble_min_seconds']+len(previous)*cfg['bubble_seconds_per_char'])
                            until=time.monotonic()+delay
                            while time.monotonic()<until:
                                self.check(p,cancel)
                                cancel.wait(min(.1,max(0,until-time.monotonic())))
                        self.check(p,cancel)
                        call=self.access.begin_call(grant['id'],'send_chat_sticker' if item['expression']['kind']=='sticker' else 'chat_expression')
                        b=item['expression'];key='turn-'+p['id']+'-'+str(i)
                        item['state']='DISPATCHING';self.save(p,'SENDING',queue)
                        try:
                            if b['kind']=='text':
                                params=dict(session_id=sid,idempotency_key=key,**{k:v for k,v in b.items() if k!='kind'})
                                if data['target_event_id']:params['reply_to_event_id']=data['target_event_id']
                                result=self.chat.send(grant,params,cancel)
                            elif b['kind']=='sticker':
                                result,_=self.chat.media.call(grant,'send_chat_sticker',dict(session_id=sid,idempotency_key=key,sticker_id=b['sticker_id']),cancel)
                            else:
                                result=self.chat.reactions.request(grant,dict(session_id=sid,idempotency_key=key,**{k:v for k,v in b.items() if k!='kind'}),cancel)
                            item['state']=result.get('state','UNKNOWN');item['receipt']=result
                            if item['state'] not in ('SUCCEEDED','NOOP'):
                                try:self.check(p,cancel)
                                except ValueError as exc:p['reason']=getattr(exc,'code','session_invalid')
                        except TurnRejected:
                            item['state']='CANCELLED'
                            raise
                        except ValueError:
                            item['state']='FAILED'
                            raise
                        except Exception:
                            item['state']='UNKNOWN'
                            raise
                        finally:self.access.finish_call(call,item['state'].lower(),0,0)
                        self.save(p,'SENDING',queue)
                        if item['state'] not in ('SUCCEEDED','NOOP'):break
                    except (TurnRejected,ValueError) as exc:
                        item['state']='CANCELLED' if item['state']=='PENDING' else item['state']
                        p['reason']=getattr(exc,'code','session_invalid');break
                    except Exception:
                        p['reason']='dispatch_unknown';break
                for item in queue:
                    if item['state']=='PENDING':item['state']='CANCELLED'
                state='SUCCEEDED' if all(x['state'] in ('SUCCEEDED','NOOP') for x in queue) else 'UNKNOWN' if any(x['state']=='UNKNOWN' for x in queue) else 'INTERRUPTED'
                return self.public(self.save(p,state,queue,p.get('reason','')))
            finally:self.dispatch.current=None
        except TurnRejected as exc:
            if 'p' in locals() and exc.code in ('stale','needs_refresh','plan_expired','target_expired'):
                self.save(p,'CANCELLED',reason=exc.code)
            return self.rejected(exc,sid)
        finally:lock.release()

    def single(self,grant,name,args,cancel):
        kinds={'send_chat_message':'text','send_chat_sticker':'sticker','react_to_chat_message':'reaction'}
        bubble={'kind':kinds[name],**{k:v for k,v in args.items() if k not in ('session_id','plan_id','idempotency_key','reply_to_event_id')}}
        if args.get('reply_to_event_id') and args.get('plan_id'):
            p=self.load(args['plan_id'],args['session_id'])
            if json.loads(p['payload'])['target_event_id']!=args['reply_to_event_id']:
                return dict(state='REJECTED',error_code='target_mismatch',note='回应对象与计划不同。')
        outcome=self.submit(grant,dict(session_id=args['session_id'],plan_id=args.get('plan_id'),
                          idempotency_key=args['idempotency_key'],bubbles=[bubble]),cancel)
        queue=outcome.get('bubbles',[])
        if len(queue)==1 and queue[0].get('receipt'):
            return dict(queue[0]['receipt'],chat_turn=outcome)
        return outcome
