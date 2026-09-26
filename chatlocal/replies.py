"""Durable drafts and exact, single-use human approval. Private SQLite only."""
import hashlib
import json
import secrets
import threading
import time
import uuid

from .message_sender import capability,sender_for,SendError,SendUncertain


def dump(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))
def uid():return uuid.uuid4().hex
TARGET_KEYS=('id','dedup_key','platform','conversation_id','conversation','sender_id','sender','timestamp','content','source_id')
def target_of(row):return {k:row[k] for k in TARGET_KEYS}
def fingerprint(target):return hashlib.sha256(dump(target).encode()).hexdigest()


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS reply_drafts(
        id TEXT PRIMARY KEY,message_id INTEGER NOT NULL,target_json TEXT NOT NULL,target_hash TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'DRAFT',revision INTEGER NOT NULL DEFAULT 0,instruction TEXT NOT NULL DEFAULT '',
        ai_text TEXT NOT NULL DEFAULT '',final_text TEXT NOT NULL DEFAULT '',result_json TEXT NOT NULL DEFAULT '{}',
        active_run TEXT,error TEXT NOT NULL DEFAULT '',approval_token TEXT,approval_json TEXT,approval_at REAL,
        receipt_json TEXT NOT NULL DEFAULT '{}',created_at REAL NOT NULL,updated_at REAL NOT NULL);
      CREATE INDEX IF NOT EXISTS reply_message ON reply_drafts(message_id,created_at);
      CREATE TABLE IF NOT EXISTS reply_generations(
        id TEXT PRIMARY KEY,draft_id TEXT NOT NULL REFERENCES reply_drafts(id),instruction TEXT NOT NULL,
        status TEXT NOT NULL,created_at REAL NOT NULL,finished_at REAL,events_json TEXT NOT NULL DEFAULT '[]',
        record_json TEXT NOT NULL DEFAULT '{}');
      CREATE TABLE IF NOT EXISTS reply_audit(
        id INTEGER PRIMARY KEY AUTOINCREMENT,draft_id TEXT NOT NULL,at REAL NOT NULL,state TEXT NOT NULL,detail_json TEXT NOT NULL);
    ''')


class Replies:
    def __init__(self,store,sender_factory=sender_for):self.store=store;self.sender_factory=sender_factory
    def audit(self,db,sid,state,detail=None):db.execute('INSERT INTO reply_audit(draft_id,at,state,detail_json) VALUES(?,?,?,?)',(sid,time.time(),state,dump(detail or {})))
    def recover(self):
        with self.store.connect() as db:
            db.execute("UPDATE reply_generations SET status='interrupted',finished_at=? WHERE status='running'",(time.time(),))
            db.execute("UPDATE reply_drafts SET active_run=NULL,error='生成中断，已保留先前草稿，可重新生成。' WHERE active_run IS NOT NULL")
            for r in db.execute("SELECT id,status FROM reply_drafts WHERE status IN ('SENDING','USER_APPROVED')").fetchall():
                status='UNKNOWN' if r['status']=='SENDING' else 'DRAFT'
                db.execute('UPDATE reply_drafts SET status=?,approval_token=NULL,error=? WHERE id=?',
                    (status,'上次发送结果未知，请到 QQ 核对，禁止自动重发。' if status=='UNKNOWN' else '未执行发送，请重新确认。',r['id']))
                self.audit(db,r['id'],status,dict(reason='process_restart'))
    def raw(self,db,sid):
        r=db.execute('SELECT * FROM reply_drafts WHERE id=?',(sid,)).fetchone()
        if not r:raise ValueError('回复草稿不存在。')
        return dict(r)
    def current_target(self,db,row):
        r=db.execute('SELECT * FROM messages WHERE id=?',(row['message_id'],)).fetchone()
        if not r or fingerprint(target_of(r))!=row['target_hash']:raise ValueError('原消息已被删除或修改，请从聊天窗口重新选择，未发送。')
        return target_of(r)
    def get(self,sid):
        with self.store.connect() as db:
            r=self.raw(db,sid)
            runs=db.execute('SELECT * FROM reply_generations WHERE draft_id=? ORDER BY created_at DESC LIMIT 10',(sid,)).fetchall()
        target=json.loads(r['target_json']);r.pop('target_json');r.pop('target_hash');r.pop('approval_token');r.pop('approval_json');r.pop('approval_at')
        target.pop('dedup_key',None)
        r.update(target=target,result=json.loads(r.pop('result_json')),receipt=json.loads(r.pop('receipt_json')),sender=capability(target['platform']),
            generations=[dict(id=x['id'],status=x['status'],instruction=x['instruction'],events=json.loads(x['events_json']),
                result=json.loads(x['record_json']).get('result',{}),requests=json.loads(x['record_json']).get('requests',0),
                usage=json.loads(x['record_json']).get('usage',{}),seconds=json.loads(x['record_json']).get('seconds',0)) for x in runs])
        return r
    def sent(self,platform,cid):
        if platform not in ('qq','wechat') or not isinstance(cid,str) or not cid or len(cid)>300:raise ValueError('会话无效。')
        with self.store.connect() as db:
            rows=db.execute('''SELECT id,final_text,updated_at,receipt_json FROM reply_drafts WHERE status='SENT'
              AND json_extract(target_json,'$.platform')=? AND json_extract(target_json,'$.conversation_id')=?
              ORDER BY updated_at DESC LIMIT 10''',(platform,cid)).fetchall()
        return dict(receipts=[dict(id=r['id'],content=r['final_text'],timestamp=r['updated_at'],receipt=json.loads(r['receipt_json'])) for r in rows],
            note='本工具的发送回执，独立于客户端原始记录。协议发送可能尚未写入桌面 QQ 数据库。')
    def create(self,mid,fresh=False):
        if type(mid) is not int:raise ValueError('请选择一条真实消息。')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
            if not row:raise ValueError('原消息已删除。')
            target=target_of(row);sha=fingerprint(target)
            # Reopening resumes the same draft, including any send receipt or
            # ambiguous result. It must not create a duplicate-send escape hatch.
            old=db.execute('SELECT id,status FROM reply_drafts WHERE message_id=? AND target_hash=? ORDER BY created_at DESC LIMIT 1',(mid,sha)).fetchone()
            if old and not (fresh is True and old['status'] in ('SENT','REJECTED')):return self.get(old['id'])
            sid=uid();now=time.time()
            db.execute('INSERT INTO reply_drafts(id,message_id,target_json,target_hash,created_at,updated_at) VALUES(?,?,?,?,?,?)',(sid,mid,dump(target),sha,now,now))
            self.audit(db,sid,'DRAFT')
        return self.get(sid)
    def edit(self,sid,text,revision):
        if not isinstance(text,str) or len(text)>2000 or any(ord(c)<32 and c not in '\n\t' for c in text):raise ValueError('回复最多2000字，不能含控制字符。')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');r=self.raw(db,sid)
            if r['status']!='DRAFT' or r['active_run'] or type(revision) is not int or r['revision']!=revision:raise ValueError('草稿状态已改变，请重新打开，不能覆盖新版本。')
            db.execute('UPDATE reply_drafts SET final_text=?,revision=revision+1,approval_token=NULL,approval_json=NULL,updated_at=? WHERE id=?',(text,time.time(),sid))
            self.audit(db,sid,'EDITED',dict(text=text,base_revision=revision))
        return self.get(sid)
    def reject(self,sid,revision):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');r=self.raw(db,sid)
            if r['status']=='REJECTED':return self.get(sid)
            if r['status']!='DRAFT' or type(revision) is not int or r['revision']!=revision:
                raise ValueError('草稿状态已改变，不能拒绝已批准或已发送的消息。')
            db.execute("UPDATE reply_drafts SET status='REJECTED',active_run=NULL,approval_token=NULL,approval_json=NULL,approval_at=NULL,error='',updated_at=? WHERE id=?",(time.time(),sid))
            if r['active_run']:
                db.execute("UPDATE reply_generations SET status='cancelled',finished_at=? WHERE id=?",(time.time(),r['active_run']))
            self.audit(db,sid,'REJECTED',dict(revision=revision))
        return self.get(sid)
    def preview(self,sid,revision,quote=False):
        if type(quote) is not bool:raise ValueError('引用选项无效。')
        with self.store.connect() as db:
            r=self.raw(db,sid);target=self.current_target(db,r)
        if r['status']!='DRAFT' or r['active_run'] or not r['ai_text'] or not r['final_text'].strip() or type(revision) is not int or r['revision']!=revision:raise ValueError('请等待生成并保存最终草稿后再发送。')
        prepared=self.sender_factory(target['platform']).prepare(target,quote)
        token=secrets.token_urlsafe(32)
        approval=dict(revision=revision,target_hash=r['target_hash'],text=r['final_text'],prepared=prepared,quote=quote)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');current=self.raw(db,sid);self.current_target(db,current)
            if current['revision']!=revision or current['status']!='DRAFT' or current['active_run']:raise ValueError('草稿已变化，请重新预览。')
            db.execute('UPDATE reply_drafts SET approval_token=?,approval_json=?,approval_at=? WHERE id=?',(token,dump(approval),time.time(),sid))
        return dict(token=token,text=approval['text'],destination=prepared,quote=quote,expires_in=300,revision=revision)
    def send(self,sid,token,confirm):
        if confirm is not True or not isinstance(token,str):raise ValueError('需要用户确认预览中的准确文字及目标会话。')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');r=self.raw(db,sid)
            if not r['approval_token'] or not secrets.compare_digest(token,r['approval_token']):raise ValueError('确认已失效，请重新预览。')
            if r['status'] in ('SENDING','SENT','UNKNOWN'):return self.get(sid)
            if r['status']!='DRAFT' or r['active_run'] or time.time()-(r['approval_at'] or 0)>300:raise ValueError('确认已过期或状态改变，请重新预览。')
            target=self.current_target(db,r);approval=json.loads(r['approval_json'])
            if approval['revision']!=r['revision'] or approval['text']!=r['final_text'] or approval['target_hash']!=r['target_hash']:raise ValueError('草稿已改变，请重新确认。')
            db.execute("UPDATE reply_drafts SET status='USER_APPROVED',error='' WHERE id=?",(sid,))
            self.audit(db,sid,'USER_APPROVED',approval)
        # Only the process handling this exact approval can claim the send.
        target_error=None
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');r=self.raw(db,sid)
            if r['status']!='USER_APPROVED':return self.get(sid)
            try:self.current_target(db,r)
            except ValueError as exc:target_error=str(exc)
            if target_error:
                db.execute("UPDATE reply_drafts SET status='DRAFT',approval_token=NULL,approval_json=NULL,error=? WHERE id=?",(target_error,sid))
                self.audit(db,sid,'DRAFT',dict(error=target_error))
            else:
                db.execute("UPDATE reply_drafts SET status='SENDING' WHERE id=?",(sid,));self.audit(db,sid,'SENDING')
        if target_error:raise ValueError(target_error)
        try:
            receipt=self.sender_factory(target['platform']).send(target,approval['text'],approval['prepared'])
            status='SENT';error=''
        except SendUncertain as exc:status='UNKNOWN';error=str(exc);receipt={}
        except SendError as exc:status='DRAFT';error=str(exc);receipt={}
        except Exception:status='UNKNOWN';error='发送结果未知，请到 QQ 核对，本条不会自动重发。';receipt={}
        with self.store.connect() as db:
            db.execute('UPDATE reply_drafts SET status=?,error=?,receipt_json=?,updated_at=? WHERE id=?',(status,error,dump(receipt),time.time(),sid))
            if status=='DRAFT':db.execute('UPDATE reply_drafts SET approval_token=NULL,approval_json=NULL WHERE id=?',(sid,))
            self.audit(db,sid,status,dict(receipt=receipt,error=error))
        return self.get(sid)


class ReplyRunner:
    def __init__(self,layer,agent=None):
        from .agent import run_agent
        self.layer=layer;self.agent=agent or run_agent;self.active={};self.lock=threading.Lock()
    def start(self,sid,instruction='',profile='standard',vision='ocr'):
        from .config import settings
        from .agent_profiles import profile_config
        from .vision import with_vision_mode
        if not isinstance(instruction,str) or len(instruction)>1000:raise ValueError('额外要求最多1000字。')
        config=with_vision_mode(profile_config(settings(),profile),vision)
        with self.layer.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');r=self.layer.raw(db,sid);target=self.layer.current_target(db,r)
            if r['status']!='DRAFT' or r['active_run']:raise ValueError('已有生成或发送操作，请先等待或停止。')
            rid=uid();now=time.time()
            db.execute('INSERT INTO reply_generations(id,draft_id,instruction,status,created_at) VALUES(?,?,?,?,?)',(rid,sid,instruction,'running',now))
            db.execute('UPDATE reply_drafts SET active_run=?,instruction=?,approval_token=NULL,approval_json=NULL,error=?,updated_at=? WHERE id=?',(rid,instruction,'',now,sid))
        cancel=threading.Event()
        with self.lock:self.active[rid]=cancel
        threading.Thread(target=self.work,args=(sid,rid,target,r['final_text'],instruction,config,cancel),daemon=True,name='reply-draft').start()
        return self.layer.get(sid)
    def work(self,sid,rid,target,previous,instruction,config,cancel):
        from .retrieval import Plan
        from .watch_check import TaskCancel
        events=[];record={};status='error';error='草稿生成失败，可重试；已有草稿保留。'
        try:
            plan=Plan(platforms=[target['platform']],conversations=[dump([target['platform'],target['conversation_id']])])
            for event in self.agent(self.layer.store,plan,'帮我回复这条消息。'+instruction,memory=dict(read_only=True),config=config,cancel=TaskCancel(cancel),
                reply_context=dict(message_id=target['id'],instruction=instruction,previous_draft=previous)):
                if event['type']=='done':record=event['record'];status=event['status'];break
                safe={k:event[k] for k in ('type','name','text','request','request_limit','characters') if k in event}
                events.append(safe)
                with self.layer.store.connect() as db:db.execute('UPDATE reply_generations SET events_json=? WHERE id=?',(dump(events),rid))
            if cancel.is_set():status='cancelled'
            if status=='completed' and not record.get('result',{}).get('reply_text'):status='error'
            error=record.get('result',{}).get('error') or ('已停止，先前草稿保留。' if status=='cancelled' else error)
        except Exception:pass
        with self.layer.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');r=self.layer.raw(db,sid)
            # Rejecting invalidates the run even if the provider ignores cancellation.
            if r['status']=='REJECTED':status='cancelled'
            try:self.layer.current_target(db,r)
            except ValueError as exc:status='error';error=str(exc)
            if r['active_run']==rid:
                if status=='completed':
                    result=record['result']
                    db.execute('UPDATE reply_drafts SET ai_text=?,final_text=?,result_json=?,revision=revision+1 WHERE id=?',
                        (result['reply_text'],result['reply_text'],dump(result),sid))
                db.execute('UPDATE reply_drafts SET active_run=NULL,error=?,updated_at=? WHERE id=?',('' if status=='completed' else error,time.time(),sid))
            db.execute('UPDATE reply_generations SET status=?,finished_at=?,record_json=? WHERE id=?',(status,time.time(),dump(record),rid))
            self.layer.audit(db,sid,'GENERATED' if status=='completed' else status.upper(),dict(run_id=rid))
        with self.lock:self.active.pop(rid,None)
    def stop(self,sid):
        r=self.layer.get(sid)
        with self.lock:
            cancel=self.active.get(r['active_run'])
            if cancel:cancel.set()
        return dict(stopping=bool(cancel))
    def reject(self,sid,revision):
        rid=self.layer.get(sid)['active_run']
        result=self.layer.reject(sid,revision)
        with self.lock:
            cancel=self.active.get(rid)
            if cancel:cancel.set()
        return result
