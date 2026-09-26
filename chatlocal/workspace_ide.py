"""Workspace interaction policy and small durable IDE extensions."""
import hashlib
import json
import re
import time


def task_intent(question,mode='auto'):
    if mode!='auto':
        if mode not in ('ask','task','fact','changes'):raise ValueError('工作区交互模式无效。')
        return mode
    # Ambiguous text remains a read-only question. Classification never grants
    # application authority: even explicit writing only creates proposals.
    if re.search(r'仅回答|只(?:需要)?回答|(?:不要|不必|无需|不需要|先别).{0,8}(?:整理|写入|更新|修改|生成|保存)',question):return 'ask'
    if re.search(r'(?:已经|已)(?:解决|取消|完成).{0,2}(?:吗|么|[？?])',question):return 'ask'
    if re.search(r'^(补充(?:一下)?|刚确认|我确认|告诉你|更新一下信息)[：:，,\s]|^(?:这个|该)(?:问题|方案).*(?:已经|已)(?:解决|取消|完成)',question):return 'fact'
    if not re.search(r'^(?:怎么|如何|为什么|是否|能否)',question) and re.search(r'(整理|更新当前状态|更新到工作区|写入|记下|记录一下|生成|撰写|创建|修改|补入|补上|删掉|删除|保存|建立|做一份|做成索引)',question):return 'task'
    return 'ask'


def initialize_ide(db):
    for table in ('workspace_outputs','workspace_proposals'):
        if 'resource_kind' not in {r[1] for r in db.execute('PRAGMA table_info('+table+')')}:
            db.execute("ALTER TABLE "+table+" ADD COLUMN resource_kind TEXT NOT NULL DEFAULT 'artifact'")
    db.execute('''CREATE TABLE IF NOT EXISTS workspace_schedules(
      workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
      minutes INTEGER NOT NULL DEFAULT 0,profile TEXT NOT NULL DEFAULT 'standard',
      vision TEXT NOT NULL DEFAULT 'ocr',next_at REAL,last_at REAL,last_task_id TEXT,
      error TEXT NOT NULL DEFAULT '')''')


class WorkspaceIDE:
    def _writable_task(self,db,wid,tid):
        task=db.execute('SELECT * FROM workspace_tasks WHERE workspace_id=? AND id=?',(wid,tid)).fetchone()
        w=self._get(db,wid)
        if not task or task['state']!='running' or task['scope_epoch']!=w['scope_epoch']:
            raise ValueError('任务已结束或范围已变化。')
        if task['mode']=='ask':raise ValueError('本轮是只读提问；请明确要求整理，或选择“整理 / 修改”后发送。')
        return task

    def user_statement(self,wid,tid):
        with self.store.connect() as db:
            w=self._get(db,wid)
            task=db.execute("SELECT question,scope_epoch FROM workspace_tasks WHERE workspace_id=? AND id=? AND mode='fact'",(wid,tid)).fetchone()
        if not task or task['scope_epoch']!=w['scope_epoch']:raise ValueError('用户补充不存在或属于旧范围。')
        signature=hashlib.sha256(task['question'].encode()).hexdigest()
        return dict(source_type='user_statement',source_id=tid,workspace_id=wid,
            label='用户补充（未独立核实）',text=task['question'],url='/?workspace='+wid+'&task='+tid,
            fingerprint=signature,content_version=signature)

    def propose_material(self,wid,tid,ref,state,reason):
        from .workspaces import dump,uid
        if state not in ('accepted','pending'):raise ValueError('资料关联状态无效。')
        ref=self.reference(ref['source_type'],str(ref['source_id']),self.plan(wid))
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');self._writable_task(db,wid,tid);w=self._get(db,wid)
            if self.excluded(db,wid,ref['source_type'],ref['source_id']):raise ValueError('资料已由用户排除。')
            existing=db.execute('SELECT * FROM workspace_materials WHERE workspace_id=? AND source_type=? AND source_id=?',(wid,ref['source_type'],ref['source_id'])).fetchone()
            if existing and existing['state']==state and existing['content_version']==ref['content_version']:return dict(already_collected=True)
            key='material:'+ref['source_type']+':'+ref['source_id']
            prior=db.execute("SELECT id FROM workspace_proposals WHERE workspace_id=? AND output_id=? AND scope_epoch=? AND state IN ('draft','pending')",(wid,key,w['scope_epoch'])).fetchone()
            if prior:return dict(proposal_id=prior[0],requires_confirmation=True)
            pid=uid();payload=dump(dict(state=state,reason=reason[:500]))
            db.execute('''INSERT INTO workspace_proposals(id,workspace_id,task_id,output_id,name,format,body,refs_json,base_version,scope_epoch,summary,action,created_at)
                VALUES(?,?,?,?,?,'md',?,?,0,?,?,'material',?)''',
                (pid,wid,tid,key,(ref['label'] or '收录资料')[:90],payload,dump([ref]),w['scope_epoch'],reason[:500],time.time()))
        return dict(proposal_id=pid,requires_confirmation=True,note='资料关联建议；确认前不加入正式资料集合。')

    def reject(self,wid,ids):
        from .workspaces import dump
        if not isinstance(ids,list) or not 1<=len(ids)<=100:raise ValueError('请选择要忽略的改动。')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');w=self._get(db,wid)
            for pid in ids:
                p=db.execute("SELECT p.* FROM workspace_proposals p WHERE p.id=? AND p.workspace_id=? AND p.scope_epoch=? AND p.state IN ('draft','pending')",(pid,wid,w['scope_epoch'])).fetchone()
                if not p:raise ValueError('提案不存在或已处理。')
                task=db.execute('SELECT state FROM workspace_tasks WHERE id=?',(p['task_id'],)).fetchone()
                if task and task[0]=='running':raise ValueError('请先停止或等待任务结束。')
                db.execute("UPDATE workspace_proposals SET state='rejected' WHERE id=?",(pid,))
            self.audit(db,wid,'proposals_rejected',dict(ids=ids))
        return dict(rejected=len(ids))

    def manual_draft(self,wid,oid,body,base_version):
        from .workspaces import uid
        if not isinstance(body,str) or len(body)>100000:raise ValueError('正文超出10万字符。')
        current=self.output(wid,oid);tid=uid();w=self.get(wid)
        with self.store.connect() as db:
            if db.execute("SELECT 1 FROM workspace_tasks WHERE workspace_id=? AND state='running'",(wid,)).fetchone():raise ValueError('请等待当前任务结束后再提出手动编辑。')
            db.execute("INSERT INTO workspace_tasks(id,workspace_id,scope_epoch,question,mode,state,start_seq,created_at) VALUES(?,?,?,'用户手动编辑','manual','running',?,?)",(tid,wid,w['scope_epoch'],w['processed_seq'],time.time()))
        try:
            result=self.draft(wid,tid,name=current['name'],format=current['format'],body=body,refs=[],summary='用户手动编辑',output_id=oid,base_version=base_version,resource_kind=current['resource_kind'])
        finally:
            with self.store.connect() as db:db.execute("UPDATE workspace_tasks SET state='completed',finished_at=? WHERE id=?",(time.time(),tid))
        return result

    def schedule(self,wid):
        self.get(wid)
        with self.store.connect() as db:r=db.execute('SELECT * FROM workspace_schedules WHERE workspace_id=?',(wid,)).fetchone()
        return dict(r) if r else dict(workspace_id=wid,minutes=0,profile='standard',vision='ocr',next_at=None,last_at=None,last_task_id=None,error='')

    def set_schedule(self,wid,body):
        from .agent_profiles import profile_config
        minutes=body.get('minutes',0);profile=body.get('profile','standard');vision=body.get('vision','ocr')
        if type(minutes)is not int or minutes!=0 and not 30<=minutes<=525600:raise ValueError('自动维护间隔为30分钟至一年，0表示关闭。')
        profile_config({},profile)
        if vision not in ('ocr','native'):raise ValueError('识图设置无效。')
        with self.store.connect() as db:
            self._get(db,wid)
            db.execute('''INSERT INTO workspace_schedules(workspace_id,minutes,profile,vision,next_at) VALUES(?,?,?,?,?)
              ON CONFLICT(workspace_id) DO UPDATE SET minutes=excluded.minutes,profile=excluded.profile,
              vision=excluded.vision,next_at=excluded.next_at,error='' ''',(wid,minutes,profile,vision,time.time()+minutes*60 if minutes else None))
            self.audit(db,wid,'maintenance_settings',dict(minutes=minutes,profile=profile,vision=vision))
        return self.schedule(wid)
