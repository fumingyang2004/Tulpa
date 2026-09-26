"""Durable, scoped work products. All writes are SQLite-owned, never model paths."""
import difflib
import json
import re
import time
import uuid

from .changes import water
from .collections import Collections, digest
from .retrieval import Plan, date_bound, scope_sql
from .workspace_ide import WorkspaceIDE,initialize_ide


def dump(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))
def uid():return uuid.uuid4().hex


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS workspaces(
        id TEXT PRIMARY KEY,title TEXT NOT NULL,goal TEXT NOT NULL,scope_json TEXT NOT NULL,
        scope_epoch INTEGER NOT NULL DEFAULT 1,revision INTEGER NOT NULL DEFAULT 1,
        collection_id TEXT NOT NULL,change_epoch TEXT NOT NULL,discovery_cursor INTEGER NOT NULL,
        processed_seq INTEGER NOT NULL DEFAULT 0,applied_seq INTEGER NOT NULL DEFAULT 0,
        gap INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,updated_at REAL NOT NULL,
        watch_ids TEXT NOT NULL DEFAULT '[]');
      CREATE TABLE IF NOT EXISTS workspace_materials(
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        source_type TEXT NOT NULL,source_id TEXT NOT NULL,state TEXT NOT NULL,
        reason TEXT NOT NULL,fingerprint TEXT NOT NULL,content_version TEXT NOT NULL,added_at REAL NOT NULL,
        PRIMARY KEY(workspace_id,source_type,source_id));
      CREATE TABLE IF NOT EXISTS workspace_candidates(
        id INTEGER PRIMARY KEY AUTOINCREMENT,workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        seq INTEGER NOT NULL,source_type TEXT NOT NULL,source_id TEXT NOT NULL,
        reason TEXT NOT NULL,decision TEXT NOT NULL DEFAULT 'pending',scope_epoch INTEGER NOT NULL,
        UNIQUE(workspace_id,seq,source_type,source_id,scope_epoch));
      CREATE TABLE IF NOT EXISTS workspace_tasks(
        id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        scope_epoch INTEGER NOT NULL,question TEXT NOT NULL,mode TEXT NOT NULL,state TEXT NOT NULL,
        start_seq INTEGER NOT NULL,created_at REAL NOT NULL,finished_at REAL,
        events_json TEXT NOT NULL DEFAULT '[]',record_json TEXT NOT NULL DEFAULT '{}');
      CREATE UNIQUE INDEX IF NOT EXISTS workspace_one_task ON workspace_tasks(workspace_id) WHERE state='running';
      CREATE TABLE IF NOT EXISTS workspace_outputs(
        id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        name TEXT NOT NULL,format TEXT NOT NULL,head INTEGER NOT NULL DEFAULT 0,deleted INTEGER NOT NULL DEFAULT 0,
        UNIQUE(workspace_id,name));
      CREATE TABLE IF NOT EXISTS workspace_versions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,output_id TEXT NOT NULL REFERENCES workspace_outputs(id) ON DELETE CASCADE,
        version INTEGER NOT NULL,scope_epoch INTEGER NOT NULL,body TEXT NOT NULL,refs_json TEXT NOT NULL,
        summary TEXT NOT NULL,origin TEXT NOT NULL,diff TEXT NOT NULL,through_seq INTEGER NOT NULL,
        created_at REAL NOT NULL,UNIQUE(output_id,version));
      CREATE TABLE IF NOT EXISTS workspace_proposals(
        id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        task_id TEXT REFERENCES workspace_tasks(id) ON DELETE CASCADE,output_id TEXT NOT NULL,
        name TEXT NOT NULL,format TEXT NOT NULL,body TEXT NOT NULL,refs_json TEXT NOT NULL,
        base_version INTEGER NOT NULL,scope_epoch INTEGER NOT NULL,summary TEXT NOT NULL,
        action TEXT NOT NULL DEFAULT 'write',state TEXT NOT NULL DEFAULT 'draft',created_at REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS workspace_audit(
        id INTEGER PRIMARY KEY AUTOINCREMENT,workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,detail_json TEXT NOT NULL,created_at REAL NOT NULL);
    ''')
    initialize_ide(db)


def clean_scope(value):
    if not isinstance(value,dict):raise ValueError('需要指定访问范围。')
    platforms=value.get('platforms',['qq','wechat']);conversations=value.get('conversations',[])
    if not isinstance(platforms,list) or not platforms or set(platforms)-{'qq','wechat'}:raise ValueError('平台范围无效。')
    if not isinstance(conversations,list) or len(conversations)>300:raise ValueError('会话范围过多。')
    chosen=[]
    for item in conversations:
        try:p,c=json.loads(item)
        except (ValueError,TypeError):raise ValueError('会话范围格式无效。') from None
        if p not in platforms or not isinstance(c,str) or not c or len(c)>300:raise ValueError('会话不在平台范围内。')
        chosen.append(dump([p,c]))
    start,end=value.get('start'),value.get('end')
    start=date_bound(start) if not isinstance(start,int) else start
    end=date_bound(end,True) if not isinstance(end,int) else end
    if start is not None and end is not None and start>=end:raise ValueError('开始日期必须早于截止日期。')
    return dict(platforms=list(dict.fromkeys(platforms)),conversations=list(dict.fromkeys(chosen)),start=start,end=end)


class Workspaces(WorkspaceIDE):
    def __init__(self,store):self.store=store;self.collections=Collections(store)
    def _get(self,db,wid):
        row=db.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()
        if not row:raise ValueError('工作区不存在。')
        return dict(row)
    def get(self,wid):
        with self.store.connect() as db:row=self._get(db,wid)
        row['scope']=json.loads(row.pop('scope_json'));row['watch_ids']=json.loads(row['watch_ids'])
        return row
    def plan(self,wid):return Plan(**self.get(wid)['scope'])
    def audit(self,db,wid,kind,detail):
        db.execute('INSERT INTO workspace_audit(workspace_id,kind,detail_json,created_at) VALUES(?,?,?,?)',(wid,kind,dump(detail),time.time()))
    def list(self):
        with self.store.connect() as db:
            return [dict(r) for r in db.execute('''SELECT w.id,w.title,w.updated_at,w.gap,
              (SELECT count(*) FROM workspace_candidates c WHERE c.workspace_id=w.id AND c.scope_epoch=w.scope_epoch AND c.decision='pending') pending
              FROM workspaces w ORDER BY w.updated_at DESC''')]
    def create(self,goal,title='',scope=None,seeds=None,collection_id=None):
        goal=str(goal or '').strip();title=str(title or goal[:32]).strip()
        if not goal or len(goal)>2000 or not title or len(title)>100:raise ValueError('请填写目标（最多2000字）和名称（最多100字）。')
        scope=clean_scope(scope or {});plan=Plan(**scope);refs=[]
        if seeds:
            if not isinstance(seeds,list) or len(seeds)>30:raise ValueError('起始资料最多30项。')
            for item in seeds:refs.append(self.reference(item['source_type'],str(item['source_id']),plan))
        if collection_id:
            # Copy references, not the shared collection or its mutable decisions.
            page=self.collections.get(collection_id,limit=100,plan=plan)
            for item in page['items']:
                if item['status']=='available':refs.append(self.reference(item['source_type'],item['source_id'],plan))
        wid,cid=uid(),uid();now=time.time()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');mark=water(db)
            db.execute('INSERT INTO evidence_collections VALUES(?,?,?,?)',(cid,'工作区 · '+title,now,now))
            db.execute('''INSERT INTO workspaces(id,title,goal,scope_json,collection_id,change_epoch,discovery_cursor,processed_seq,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?)''',(wid,title,goal,dump(scope),cid,mark['epoch'],mark['high_water'],mark['high_water'],now,now))
            for ref in refs:self._material(db,wid,cid,ref,'accepted','用户选择的起始资料')
            self.audit(db,wid,'created',dict(start_seq=mark['high_water'],scope=scope,imported_refs=len(refs),collection_page_limit=100))
        return self.get(wid)
    def reference(self,kind,sid,plan):
        source=self.collections.resolve(kind,str(sid),plan)
        with self.store.connect() as db:
            if kind in ('message','media','voice'):
                mid=int(str(sid).split(':')[0]);r=db.execute('SELECT content,media_json,reply_to,sender,timestamp FROM messages WHERE id=?',(mid,)).fetchone()
                v=db.execute('SELECT transcript,profile,audio_sha256 FROM voice_sources WHERE message_id=?',(mid,)).fetchone()
                version=digest([tuple(r),tuple(v) if v else None])
            elif kind.startswith('artifact_'):
                row=db.execute('SELECT source_key,sha256,filename,metadata_json FROM artifact_sources WHERE id=?',(int(str(sid).split(':')[0]),)).fetchone()
                version=digest([tuple(row),source['fingerprint']])
            else:
                row=db.execute('SELECT content,metadata_json FROM qq_knowledge WHERE id=?',(int(sid),)).fetchone();version=digest(tuple(row))
        return dict(source_type=kind,source_id=str(sid),fingerprint=source['fingerprint'],content_version=version,
                    label=source.get('title',''),url=source['url'])
    def ref_status(self,ref,plan):
        if ref.get('source_type')=='statistic':return dict(ref,available=True,notice='统计回执快照；不是原消息，不代表当前数据。')
        if ref.get('source_type')=='user_statement':
            try:
                current=self.user_statement(ref['workspace_id'],ref['source_id'])
                w=self.get(ref['workspace_id'])
                if any(w['scope'].get(k)!=getattr(plan,k) for k in ('platforms','conversations','start','end')):raise ValueError('范围不符。')
                if current['fingerprint']!=ref['fingerprint']:raise ValueError('用户补充发生变化。')
                return dict(current,available=True,changed=False,notice='用户提供的信息，未经独立原始资料核实。')
            except (ValueError,KeyError):return dict(source_type='user_statement',source_id=ref['source_id'],available=False,notice='用户补充不可用或属于旧范围。')
        try:
            current=self.reference(ref['source_type'],ref['source_id'],plan)
            if current['fingerprint']!=ref['fingerprint']:raise ValueError('原引用身份已变化。')
            return dict(current,available=True,changed=current['content_version']!=ref['content_version'])
        except ValueError:return dict(source_type=ref['source_type'],source_id=ref['source_id'],available=False,notice='来源已删除、改变身份或超出当前范围。')
    def _material(self,db,wid,cid,ref,state,reason):
        key=(wid,ref['source_type'],ref['source_id'])
        if self.excluded(db,wid,ref['source_type'],ref['source_id']):return False
        old=db.execute('SELECT state FROM workspace_materials WHERE workspace_id=? AND source_type=? AND source_id=?',key).fetchone()
        if old and old[0]=='excluded':return False
        db.execute('''INSERT INTO workspace_materials VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(workspace_id,source_type,source_id)
          DO UPDATE SET reason=excluded.reason,fingerprint=excluded.fingerprint,content_version=excluded.content_version,
          state=CASE WHEN workspace_materials.state='accepted' THEN 'accepted' ELSE excluded.state END''',
          (*key,state,reason,ref['fingerprint'],ref['content_version'],time.time()))
        if state=='accepted':
            db.execute('INSERT OR IGNORE INTO evidence_collection_items VALUES(?,?,?,?,?,?)',
                       (uid(),cid,ref['source_type'],ref['source_id'],ref['fingerprint'],time.time()))
        return True
    def excluded(self,db,wid,kind,sid):
        kinds=[(kind,str(sid))]
        if kind in ('media','voice'):kinds.append(('message',str(sid).split(':')[0]))
        if kind=='artifact_chunk':kinds.append(('artifact_source',str(sid).split(':')[0]))
        return any(db.execute("SELECT 1 FROM workspace_materials WHERE workspace_id=? AND source_type=? AND source_id=? AND state='excluded'",(wid,k,s)).fetchone() for k,s in kinds)
    def materials(self,wid):
        plan=self.plan(wid)
        with self.store.connect() as db:rows=db.execute('SELECT * FROM workspace_materials WHERE workspace_id=? ORDER BY added_at DESC LIMIT 2000',(wid,)).fetchall()
        return [dict(state=r['state'],reason=r['reason'] if self.ref_status(dict(r),plan)['available'] else '',**self.ref_status(dict(r),plan)) for r in rows]
    def material(self,wid,ref,state='pending',reason='Agent 收集',epoch=None):
        if state not in ('accepted','pending','excluded'):raise ValueError('资料状态无效。')
        plan=self.plan(wid)
        try:ref=self.reference(ref['source_type'],str(ref['source_id']),plan)
        except ValueError:
            if state!='excluded':raise
            with self.store.connect() as db:
                old=db.execute('SELECT * FROM workspace_materials WHERE workspace_id=? AND source_type=? AND source_id=?',(wid,ref['source_type'],str(ref['source_id']))).fetchone()
            if not old:raise ValueError('资料不存在，不能排除未知来源。') from None
            ref=dict(old)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');w=self._get(db,wid)
            if epoch is not None and epoch!=w['scope_epoch']:raise ValueError('范围已变化，请重新执行任务。')
            if state=='excluded':
                self._material(db,wid,w['collection_id'],ref,'pending',reason)
                db.execute("UPDATE workspace_materials SET state='excluded' WHERE workspace_id=? AND source_type=? AND source_id=?",(wid,ref['source_type'],ref['source_id']))
                db.execute('DELETE FROM evidence_collection_items WHERE collection_id=? AND source_type=? AND source_id=?',(w['collection_id'],ref['source_type'],ref['source_id']))
            else:self._material(db,wid,w['collection_id'],ref,state,reason[:500])
            self.audit(db,wid,'material_decision',dict(type=ref['source_type'],id=ref['source_id'],state=state))
        return dict(saved=True)
    def release(self,wid,kind,sid):
        with self.store.connect() as db:
            self._get(db,wid)
            db.execute("UPDATE workspace_materials SET state='pending' WHERE workspace_id=? AND source_type=? AND source_id=? AND state='excluded'",(wid,kind,str(sid)))
            self.audit(db,wid,'exclusion_released',dict(type=kind,id=sid))
    def _event_allowed(self,event,scope):
        return (event['platform'] in scope['platforms'] and
            (not scope['conversations'] or dump([event['platform'],event['conversation_id']]) in scope['conversations']) and
            (scope['start'] is None or event['source_timestamp'] is not None and event['source_timestamp']>=scope['start']) and
            (scope['end'] is None or event['source_timestamp'] is not None and event['source_timestamp']<scope['end']))
    def discover(self,wid,limit=1000):
        # Bounded cheap discovery. Nonmatches are retained as filtered, not deleted.
        import jieba
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');w=self._get(db,wid);mark=water(db);cursor=w['discovery_cursor'];scope=json.loads(w['scope_json'])
            upper=min(mark['high_water'],cursor+limit)
            rows=db.execute('SELECT * FROM local_changes WHERE seq>? AND seq<=? ORDER BY seq',(cursor,upper)).fetchall()
            if mark['epoch']!=w['change_epoch'] or mark['high_water']<cursor or len(rows)!=upper-cursor:
                db.execute('UPDATE workspaces SET gap=1 WHERE id=?',(wid,));return dict(gap=True,scanned=0)
            words=[t for t in jieba.cut(w['goal']) if len(t.strip())>=2 and t not in ('工作区','整理','报告','资料','聊天','相关','建立','生成','关于','研究','现状','索引')][:20]
            for r in rows:
                if not self._event_allowed(r,scope):continue
                old=db.execute('SELECT state FROM workspace_materials WHERE workspace_id=? AND source_type=? AND source_id=?',(wid,r['source_type'],r['source_id'])).fetchone()
                text=''
                if r['source_type'] in ('message','voice'):
                    m=db.execute('SELECT m.content,m.conversation,m.sender,v.transcript FROM messages m LEFT JOIN voice_sources v ON m.id=v.message_id WHERE m.id=?',(r['source_id'],)).fetchone()
                    if m:text=' '.join(str(v or '') for v in m)
                elif r['source_type']=='artifact_source':
                    m=db.execute('SELECT filename,conversation,sender FROM artifact_sources WHERE id=?',(r['source_id'],)).fetchone()
                    if m:text=' '.join(str(v or '') for v in m)
                chosen=bool(old or scope['conversations'] or any(t.lower() in text.lower() for t in words) or r['kind'] in ('deleted','delete'))
                decision='excluded' if self.excluded(db,wid,r['source_type'],r['source_id']) else 'pending' if chosen else 'filtered'
                db.execute('''INSERT OR IGNORE INTO workspace_candidates(workspace_id,seq,source_type,source_id,reason,decision,scope_epoch)
                  VALUES(?,?,?,?,?,?,?)''',(wid,r['seq'],r['source_type'],r['source_id'],r['kind'],decision,w['scope_epoch']))
            db.execute('UPDATE workspaces SET discovery_cursor=? WHERE id=?',(upper,wid))
        return dict(gap=False,scanned=len(rows),remaining=mark['high_water']-upper)
    def candidates(self,wid,limit=60):
        w=self.get(wid);plan=Plan(**w['scope'])
        with self.store.connect() as db:rows=db.execute("SELECT * FROM workspace_candidates WHERE workspace_id=? AND scope_epoch=? AND decision='pending' ORDER BY id LIMIT ?",(wid,w['scope_epoch'],limit)).fetchall()
        out=[]
        for r in rows:
            item=dict(r)
            try:item['source']=self.collections.resolve(r['source_type'],r['source_id'],plan)
            except ValueError:item['source']=dict(available=False,note='原资料已删除或超出范围；不得继续当作可用来源。')
            out.append(item)
        return out
    def backfill(self,wid,start,end,limit=200,offset=0,new_scope_only=False):
        if not start or not end:raise ValueError('补查需明确起止日期。')
        w=self.get(wid);plan=Plan(**w['scope']);a,b=date_bound(start),date_bound(end,True)
        if a>=b or not 1<=limit<=500 or not 0<=offset<=100000:raise ValueError('补查范围或页码无效。')
        plan.start=max(a,plan.start) if plan.start is not None else a;plan.end=min(b,plan.end) if plan.end is not None else b
        from .artifacts import source_scope
        from .group_knowledge import scope as knowledge_scope
        filters=[scope_sql(plan),source_scope(plan),knowledge_scope(plan)]
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if new_scope_only:
                prior=db.execute("SELECT detail_json FROM workspace_audit WHERE workspace_id=? AND kind='scope_changed' ORDER BY id DESC LIMIT 1",(wid,)).fetchone()
                if not prior:raise ValueError('没有范围变化；请使用普通日期补查。')
                old=Plan(**json.loads(prior[0])['old']);oldfilters=[scope_sql(old),source_scope(old),knowledge_scope(old)]
                filters=[(f'({a}) AND NOT ({b})',aa+bb) for (a,aa),(b,bb) in zip(filters,oldfilters)]
            (mw,ma),(fw,fa),(kw,ka)=filters
            rows=db.execute(f'''SELECT 'message' source_type,CAST(m.id AS TEXT) source_id FROM messages m WHERE {mw}
              UNION ALL SELECT 'artifact_source',CAST(s.id AS TEXT) FROM artifact_sources s WHERE {fw}
              UNION ALL SELECT 'qq_'||k.kind,CAST(k.id AS TEXT) FROM qq_knowledge k WHERE {kw}
              ORDER BY source_type,source_id LIMIT ? OFFSET ?''',ma+fa+ka+[limit+1,offset]).fetchall()
            for r in rows[:limit]:
                if not self.excluded(db,wid,r['source_type'],r['source_id']):db.execute("INSERT OR IGNORE INTO workspace_candidates(workspace_id,seq,source_type,source_id,reason,scope_epoch) VALUES(?,0,?,?,'bounded_backfill',?)",(wid,r['source_type'],r['source_id'],w['scope_epoch']))
            self.audit(db,wid,'bounded_backfill',dict(start=a,end=b,offset=offset,count=min(limit,len(rows)),has_more=len(rows)>limit,new_scope_only=new_scope_only,scope_epoch=w['scope_epoch']))
        return dict(count=min(limit,len(rows)),has_more=len(rows)>limit,next_offset=offset+limit)
    def update(self,wid,body):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');w=self._get(db,wid)
            if body.get('revision')!=w['revision']:raise ValueError('设置已变更，请重新打开。')
            if db.execute("SELECT 1 FROM workspace_tasks WHERE workspace_id=? AND state='running'",(wid,)).fetchone():raise ValueError('请先停止当前任务再修改范围。')
            scope=clean_scope(body.get('scope',json.loads(w['scope_json'])));goal=str(body.get('goal',w['goal'])).strip();title=str(body.get('title',w['title'])).strip()
            if not goal or len(goal)>2000 or not title or len(title)>100:raise ValueError('名称或目标无效。')
            changed=scope!=json.loads(w['scope_json']) or goal!=w['goal']
            links=body.get('watch_ids',json.loads(w['watch_ids']))
            if not isinstance(links,list) or len(links)>20:raise ValueError('关注卡关联过多。')
            for link in links:
                if not db.execute('SELECT 1 FROM watch_cards WHERE id=?',(link,)).fetchone():raise ValueError('关联关注卡不存在。')
            db.execute('UPDATE workspaces SET title=?,goal=?,scope_json=?,scope_epoch=scope_epoch+?,revision=revision+1,updated_at=?,watch_ids=? WHERE id=?',
                       (title,goal,dump(scope),int(changed),time.time(),dump(links),wid))
            if changed:
                db.execute('UPDATE workspaces SET processed_seq=0,applied_seq=0 WHERE id=?',(wid,))
                db.execute("UPDATE workspace_proposals SET state='scope_changed' WHERE workspace_id=? AND state IN ('pending','draft')",(wid,))
                self.audit(db,wid,'scope_changed',dict(old=json.loads(w['scope_json']),new=scope,note='旧成果、任务正文不再进入Agent；新范围需指定日期有界补查。'))
        return self.get(wid)
    def output(self,wid,oid,version=None):
        with self.store.connect() as db:
            w=self._get(db,wid);o=db.execute('SELECT * FROM workspace_outputs WHERE workspace_id=? AND id=?',(wid,oid)).fetchone()
            if not o:raise ValueError('成果不属于当前工作区。')
            v=db.execute('SELECT * FROM workspace_versions WHERE output_id=? AND version=?',(oid,version or o['head'])).fetchone()
            if not v:raise ValueError('成果尚未发布。')
            if v['scope_epoch']!=w['scope_epoch']:raise ValueError('该版本来自旧范围，正文已隔离。请在当前范围重新调查生成修订。')
        refs=[self.ref_status(r,Plan(**json.loads(w['scope_json']))) for r in json.loads(v['refs_json'])]
        return dict(o,body=v['body'],version=v['version'],origin=v['origin'],summary=v['summary'],refs=refs,created_at=v['created_at'])
    def output_for_agent(self,wid,oid):
        """Old-range bytes stay sealed; allow a new scoped revision of the same ID."""
        with self.store.connect() as db:
            w=self._get(db,wid);o=db.execute('SELECT * FROM workspace_outputs WHERE workspace_id=? AND id=?',(wid,oid)).fetchone()
            if not o:raise ValueError('成果不属于当前工作区。')
            v=db.execute('SELECT scope_epoch FROM workspace_versions WHERE output_id=? AND version=?',(oid,o['head'])).fetchone()
        if v and v[0]!=w['scope_epoch']:
            return dict(id=oid,name='旧范围成果',format=o['format'],version=o['head'],body='',refs=[],
                note='旧正文/名称已隔离。可依据当前范围重新调查，指定同一个output_id/base_version提出修订；不能恢复或引用旧正文。')
        return self.output(wid,oid)
    def overview(self,wid):
        self.discover(wid);w=self.get(wid)
        with self.store.connect() as db:
            outputs=[dict(r) for r in db.execute('''SELECT o.*,v.scope_epoch,v.summary,v.created_at FROM workspace_outputs o
              LEFT JOIN workspace_versions v ON v.output_id=o.id AND v.version=o.head WHERE o.workspace_id=?''',(wid,))]
            tasks=[dict(r) for r in db.execute('SELECT id,scope_epoch,question,mode,state,start_seq,created_at,finished_at FROM workspace_tasks WHERE workspace_id=? ORDER BY created_at DESC LIMIT 50',(wid,))]
            pending=db.execute("SELECT count(*) FROM workspace_candidates WHERE workspace_id=? AND scope_epoch=? AND decision='pending'",(wid,w['scope_epoch'])).fetchone()[0]
            filtered=db.execute("SELECT count(*) FROM workspace_candidates WHERE workspace_id=? AND scope_epoch=? AND decision='filtered'",(wid,w['scope_epoch'])).fetchone()[0]
            mark=water(db)
        for row in tasks:
            if row['scope_epoch']!=w['scope_epoch']:row['question']='旧范围任务（正文隔离）'
        for row in outputs:
            if row['scope_epoch']!=w['scope_epoch']:row['summary']='旧范围成果，需在新范围修订'
        return dict(workspace=w,outputs=outputs,tasks=tasks,pending=pending,filtered=filtered,local_seq=mark['high_water'],
                    output_updated_at=max((o['created_at'] or 0 for o in outputs if o['scope_epoch']==w['scope_epoch']),default=0),
                    note='已发现候选不等于已分析；已分析不等于成果已应用。关键词筛选可能遗漏，可指定日期补查。首次调查为预算内样本。')
    def draft(self,wid,tid,*,name,format,body,refs,summary,output_id=None,base_version=0,action='write',draft_id=None,resource_kind='artifact'):
        if not re.fullmatch(r'[^<>:"/\\|?*\x00-\x1f]{1,90}',name) or name in ('.','..'):raise ValueError('成果名称无效，请勿输入路径。')
        if format not in ('md','csv') or len(body)>100000 or len(summary)>600:raise ValueError('成果类型或大小超限。')
        if action not in ('write','delete'):raise ValueError('改动类型无效。')
        if resource_kind not in ('artifact','state','note') or resource_kind=='state' and format!='md':raise ValueError('资源类型无效。')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');w=self._get(db,wid);task=db.execute('SELECT * FROM workspace_tasks WHERE id=? AND workspace_id=?',(tid,wid)).fetchone()
            self._writable_task(db,wid,tid)
            if not task or task['state']!='running' or task['scope_epoch']!=w['scope_epoch']:raise ValueError('任务已停止或范围变化，不能再写草稿。')
            if output_id:
                o=db.execute('SELECT * FROM workspace_outputs WHERE id=? AND workspace_id=?',(output_id,wid)).fetchone()
                if not o or o['head']!=base_version:raise ValueError('成果版本已变化，请重新读取；没有覆盖用户编辑。')
                prior_epoch=db.execute('SELECT scope_epoch FROM workspace_versions WHERE output_id=? AND version=?',(output_id,o['head'])).fetchone()[0]
                name,format=(o['name'] if prior_epoch==w['scope_epoch'] else name),o['format']
                resource_kind=o['resource_kind']
            else:
                if action=='delete':raise ValueError('删除必须指定已有成果。')
                o=db.execute('SELECT * FROM workspace_outputs WHERE workspace_id=? AND name=?',(wid,name)).fetchone()
                if o:raise ValueError('同名成果已存在，请读取并按版本提出修改。')
                pending=db.execute("SELECT id FROM workspace_proposals WHERE workspace_id=? AND name=? AND task_id!=? AND state IN ('draft','pending')",(wid,name,tid)).fetchone()
                if pending and pending[0]!=draft_id:raise ValueError('已有同名草稿，请workspace_read(action=draft)后传draft_id继续，避免重复创建。')
                output_id=uid()
                if resource_kind=='state' and db.execute("SELECT 1 FROM workspace_outputs WHERE workspace_id=? AND resource_kind='state' AND deleted=0",(wid,)).fetchone():raise ValueError('当前状态已存在，请读取并修订原成果。')
            old=db.execute('SELECT id,output_id FROM workspace_proposals WHERE task_id=? AND name=?',(tid,name)).fetchone()
            if draft_id:
                prior=db.execute("SELECT * FROM workspace_proposals WHERE id=? AND workspace_id=? AND scope_epoch=? AND state IN ('draft','pending')",(draft_id,wid,w['scope_epoch'])).fetchone()
                if not prior or prior['name']!=name or prior['base_version']!=base_version:raise ValueError('草稿已变化，请重新读取。')
                old=prior
            pid=old['id'] if old else uid();output_id=old['output_id'] if old else output_id
            db.execute('''INSERT INTO workspace_proposals(id,workspace_id,task_id,output_id,name,format,body,refs_json,base_version,scope_epoch,summary,action,created_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,refs_json=excluded.refs_json,summary=excluded.summary,action=excluded.action,task_id=excluded.task_id,state='draft',base_version=excluded.base_version''',
              (pid,wid,tid,output_id,name,format,body,dump(refs),base_version,w['scope_epoch'],summary,action,time.time()))
            db.execute('UPDATE workspace_proposals SET resource_kind=? WHERE id=?',(resource_kind,pid))
        return dict(proposal_id=pid,output_id=output_id,state='draft',requires_confirmation=True,note='仅提案；用户确认前不会改变正式状态。')
    def _commit(self,db,w,oid,name,format,body,refs,summary,origin,through_seq,deleted=False):
        row=db.execute('SELECT * FROM workspace_outputs WHERE id=? AND workspace_id=?',(oid,w['id'])).fetchone()
        if not row:
            db.execute('INSERT INTO workspace_outputs(id,workspace_id,name,format) VALUES(?,?,?,?)',(oid,w['id'],name,format));head=0;before=''
        else:
            head=row['head'];old=db.execute('SELECT body FROM workspace_versions WHERE output_id=? AND version=?',(oid,head)).fetchone();before=old[0] if old else ''
        diff=''.join(difflib.unified_diff(before.splitlines(True),body.splitlines(True),fromfile=f'v{head}',tofile=f'v{head+1}'))
        db.execute('INSERT INTO workspace_versions(output_id,version,scope_epoch,body,refs_json,summary,origin,diff,through_seq,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (oid,head+1,w['scope_epoch'],body,dump(refs),summary,origin,diff,through_seq,time.time()))
        db.execute('UPDATE workspace_outputs SET head=?,deleted=?,name=? WHERE id=?',(head+1,int(deleted),name,oid))
        self.audit(db,w['id'],'version_applied',dict(output_id=oid,version=head+1,origin=origin,deleted=deleted))
        db.execute('UPDATE workspaces SET updated_at=? WHERE id=?',(time.time(),w['id']))
        return head+1
    def proposals(self,wid):
        w=self.get(wid)
        with self.store.connect() as db:rows=db.execute("SELECT * FROM workspace_proposals WHERE workspace_id=? AND scope_epoch=? AND state IN ('draft','pending') ORDER BY created_at",(wid,w['scope_epoch'])).fetchall()
        out=[]
        for r in rows:
            p=dict(r);before=''
            if p['base_version']:
                try:before=self.output(wid,p['output_id'],p['base_version'])['body']
                except ValueError:before='（旧范围正文已隔离）\n'
            p['diff']=''.join(difflib.unified_diff(before.splitlines(True),p['body'].splitlines(True),fromfile='当前成果',tofile='拟议版本'))
            p['refs']=[self.ref_status(ref,Plan(**w['scope'])) for ref in json.loads(p.pop('refs_json'))];out.append(p)
        return out
    def apply(self,wid,ids,*,confirm=False,automatic=False):
        if not confirm or automatic:raise ValueError('所有正式改动均需用户确认，禁止自动应用。')
        if not isinstance(ids,list) or not 1<=len(ids)<=20:raise ValueError('请选择1至20项改动。')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');w=self._get(db,wid);rows=[]
            for pid in dict.fromkeys(ids):
                r=db.execute("SELECT * FROM workspace_proposals WHERE workspace_id=? AND id=? AND state IN ('pending','draft')",(wid,pid)).fetchone()
                if not r or r['scope_epoch']!=w['scope_epoch']:raise ValueError('改动已应用或范围已变化。')
                if r['task_id'] and db.execute('SELECT state FROM workspace_tasks WHERE id=?',(r['task_id'],)).fetchone()[0]=='running':raise ValueError('请等待任务结束或停止后再应用草稿。')
                if r['action']=='material':
                    for ref in json.loads(r['refs_json']):
                        status=self.ref_status(ref,Plan(**json.loads(w['scope_json'])))
                        if not status['available'] or status.get('changed') or self.excluded(db,wid,ref['source_type'],ref['source_id']):raise ValueError('资料已变化、不可用或已排除。')
                    rows.append(r);continue
                old=db.execute('SELECT head FROM workspace_outputs WHERE id=? AND workspace_id=?',(r['output_id'],wid)).fetchone()
                if (old[0] if old else 0)!=r['base_version']:raise ValueError('成果已被编辑，旧任务不能覆盖。请重新调查或手工合并。')
                if db.execute('SELECT 1 FROM workspace_outputs WHERE workspace_id=? AND name=? AND id!=?',(wid,r['name'],r['output_id'])).fetchone():raise ValueError('同名成果已经存在，请修改草稿名称或修订已有成果。')
                for ref in json.loads(r['refs_json']):
                    if ref.get('source_type')=='user_statement' and ref.get('workspace_id')!=wid:raise ValueError('不能引用其他工作区的用户补充。')
                    status=self.ref_status(ref,Plan(**json.loads(w['scope_json'])))
                    if not status['available'] or status.get('changed'):raise ValueError('引用来源已变化或删除；请重新取证后再应用。')
                rows.append(r)
            if len({r['output_id'] for r in rows})!=len(rows):raise ValueError('一批不能包含同一成果的两个修订，请选择其中一个。')
            state_ids={r[0] for r in db.execute("SELECT id FROM workspace_outputs WHERE workspace_id=? AND resource_kind='state' AND deleted=0",(wid,))}
            for r in rows:
                if r['resource_kind']=='state':
                    if r['action']=='delete':state_ids.discard(r['output_id'])
                    else:state_ids.add(r['output_id'])
            if len(state_ids)>1:raise ValueError('只能有一份当前状态，请修订已有状态文档。')
            for r in rows:
                if r['action']=='material':
                    payload=json.loads(r['body'])
                    for ref in json.loads(r['refs_json']):self._material(db,wid,w['collection_id'],ref,payload['state'],payload['reason'])
                    self.audit(db,wid,'material_proposal_applied',dict(proposal_id=r['id']))
                    db.execute("UPDATE workspace_proposals SET state='applied' WHERE id=?",(r['id'],));continue
                task=db.execute('SELECT start_seq FROM workspace_tasks WHERE id=?',(r['task_id'],)).fetchone();seq=task[0] if task else w['processed_seq']
                mode=db.execute('SELECT mode FROM workspace_tasks WHERE id=?',(r['task_id'],)).fetchone()
                self._commit(db,w,r['output_id'],r['name'],r['format'],r['body'],json.loads(r['refs_json']),r['summary'],'user' if mode and mode[0]=='manual' else 'agent',seq,r['action']=='delete')
                db.execute('UPDATE workspace_outputs SET resource_kind=? WHERE id=?',(r['resource_kind'],r['output_id']))
                db.execute("UPDATE workspace_proposals SET state='applied' WHERE id=?",(r['id'],))
            # A conservative watermark: unresolved proposals never count as formal progress.
            waiting=db.execute("SELECT 1 FROM workspace_proposals WHERE workspace_id=? AND scope_epoch=? AND state IN ('pending','draft')",(wid,w['scope_epoch'])).fetchone()
            if not waiting:db.execute('UPDATE workspaces SET applied_seq=processed_seq WHERE id=?',(wid,))
        return dict(applied=len(rows))
    def edit(self,wid,oid,body,base_version,summary='用户手动编辑'):
        if not isinstance(body,str) or len(body)>100000:raise ValueError('正文超出10万字符。')
        previous=self.output(wid,oid)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');w=self._get(db,wid);o=db.execute('SELECT * FROM workspace_outputs WHERE id=? AND workspace_id=?',(oid,wid)).fetchone()
            if not o or o['head']!=base_version:raise ValueError('版本冲突，请重新打开成果。')
            self._commit(db,w,oid,o['name'],o['format'],body,[],summary,'user',w['applied_seq'])
        return self.output(wid,oid)
    def restore(self,wid,oid,version,base_version,confirm):
        if not confirm:raise ValueError('回退需要确认。')
        old=self.output(wid,oid,version)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');w=self._get(db,wid);o=db.execute('SELECT * FROM workspace_outputs WHERE id=? AND workspace_id=?',(oid,wid)).fetchone()
            if o['head']!=base_version:raise ValueError('版本冲突，请重新打开。')
            v=db.execute('SELECT refs_json,through_seq FROM workspace_versions WHERE output_id=? AND version=?',(oid,version)).fetchone()
            self._commit(db,w,oid,o['name'],o['format'],old['body'],json.loads(v[0]),f'恢复版本 {version}（来源状态以当前核对为准）','restore',v[1])
            db.execute('UPDATE workspaces SET applied_seq=min(applied_seq,?) WHERE id=?',(v[1],wid))
        return self.output(wid,oid)
    def history(self,wid):
        w=self.get(wid)
        with self.store.connect() as db:
            rows=[dict(r) for r in db.execute('''SELECT v.id,v.output_id,v.version,v.scope_epoch,v.summary,v.origin,v.diff,v.created_at,o.name
              FROM workspace_versions v JOIN workspace_outputs o ON o.id=v.output_id WHERE o.workspace_id=? ORDER BY v.id DESC LIMIT 150''',(wid,))]
            audit=[]
            for r in db.execute('SELECT id,kind,created_at,detail_json FROM workspace_audit WHERE workspace_id=? ORDER BY id DESC LIMIT 100',(wid,)):
                item=dict(r);detail=json.loads(item.pop('detail_json'))
                if r['kind']=='bounded_backfill' and detail.get('scope_epoch')==w['scope_epoch']:item['coverage']=detail
                audit.append(item)
        for r in rows:
            if r['scope_epoch']!=w['scope_epoch']:r['diff']='旧范围差异正文已隔离';r['summary']='旧范围版本'
        return dict(versions=rows,audit=audit)
    def delete(self,wid,confirm):
        if not confirm:raise ValueError('删除工作区需要确认。')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');w=self._get(db,wid)
            if db.execute("SELECT 1 FROM workspace_tasks WHERE workspace_id=? AND state='running'",(wid,)).fetchone():raise ValueError('请先停止任务。')
            db.execute('DELETE FROM workspaces WHERE id=?',(wid,));db.execute('DELETE FROM evidence_collections WHERE id=?',(w['collection_id'],))
        return dict(deleted=True)
