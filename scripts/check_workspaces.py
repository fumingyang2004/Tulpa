"""Isolated critical-path checks. Never mutate the user's messages or call a model."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from chatlocal.config import ROOT
from chatlocal.store import Store,tokens
from chatlocal.workspaces import Workspaces,dump
from chatlocal.workspace_runner import WorkspaceRunner
from chatlocal.changes import water
from chatlocal.agent_tools import ChatTools,SCHEMAS,tool
from chatlocal.workspace_tools import schemas
from chatlocal.retrieval import Plan


def denied(fn):
    try:fn()
    except ValueError:return
    raise AssertionError('Expected rejection')


def main():
  with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='workspace-check-') as folder:
    store=Store(Path(folder)/'chat.sqlite3');layer=Workspaces(store)
    def message(mid,cid='a',text='Router 讨论，提交报告',platform='qq'):
        with store.connect() as db:
            db.execute('''INSERT INTO messages(id,dedup_key,platform,conversation_id,conversation,sender_id,sender,timestamp,content,is_self,source_id,conversation_type)
              VALUES(?,?,?,?,?,'42','发言者',?, ?,0,?,'group')''',(mid,'key-'+str(mid),platform,cid,'测试群 '+cid,1790000000000+mid,text,str(mid)))
            db.execute('INSERT INTO message_fts(rowid,terms) VALUES(?,?)',(mid,tokens(text)))
    message(1);message(2,'b','秘密群内容')
    with store.connect() as db:mark=water(db)['high_water']
    with store.connect() as db:db.execute('UPDATE messages SET content=content WHERE id=1')
    with store.connect() as db:assert water(db)['high_water']==mark
    try:
        with store.connect() as db:db.execute("UPDATE messages SET content='rollback' WHERE id=1");raise RuntimeError()
    except RuntimeError:pass
    with store.connect() as db:assert water(db)['high_water']==mark
    wa=layer.create('Router 报告','A',dict(platforms=['qq'],conversations=[dump(['qq','a'])]));wb=layer.create('另一群','B',dict(platforms=['qq'],conversations=[dump(['qq','b'])]))
    wid=wa['id'];message(3);message(4,'b');layer.discover(wid);assert len(layer.candidates(wid))==1
    runner=WorkspaceRunner(store,agent=lambda *a,**k:(_ for _ in ()).throw(AssertionError('API must not run')))
    wc=layer.create('空','C',dict(platforms=['wechat']));assert runner.start(wc['id'],'整理变化',mode='changes')['api_called'] is False
    tid='fixture-task'
    with store.connect() as db:db.execute('INSERT INTO workspace_tasks(id,workspace_id,scope_epoch,question,mode,state,start_seq,created_at) VALUES(?,?,1,?,\'task\',\'running\',?,?)',(tid,wid,'生成报告',water(db)['high_water'],time.time()))
    tools=ChatTools(store,layer.plan(wid),max_calls=60,max_chars=60000);tools.schemas=SCHEMAS+schemas(tool)
    tools.workspace_context=dict(id=wid,task_id=tid,scope_epoch=1,start_seq=100,decisions={})
    outside=tools.execute('get_context',dict(message_id=2));assert outside.get('error')
    sql=tools.execute('query_communication_db',dict(sql='SELECT id,content FROM agent_messages',max_rows=20));assert '秘密群内容' not in dump(sql)
    assert tools.execute('evidence_collection',dict(action='list')).get('error')
    tools.execute('read_conversation',dict(platform='qq',conversation_id='a',limit=20))
    draft=tools.execute('workspace_draft',dict(name='报告',format='md',summary='首次调查',sections=[dict(kind='fact',text='记录中讨论了 Router 报告提交。',evidence_ids=[1,3])]))
    assert 'proposal_id' in draft,draft
    denied(lambda:layer.apply(wb['id'],[draft['proposal_id']],confirm=True))
    denied(lambda:layer.apply(wid,[draft['proposal_id']],confirm=True)) # task running
    with store.connect() as db:db.execute("UPDATE workspace_tasks SET state='completed' WHERE id=?",(tid,))
    denied(lambda:layer.apply(wid,[draft['proposal_id']],automatic=True))
    layer.apply(wid,[draft['proposal_id']],confirm=True);oid=draft['output_id'];assert layer.output(wid,oid)['version']==1
    denied(lambda:layer.output(wb['id'],oid))
    # Same output edit proposal, then manual edit: stale proposal may not overwrite.
    with store.connect() as db:db.execute("UPDATE workspace_tasks SET state='running' WHERE id=?",(tid,))
    tools.execute('workspace_read',dict(action='output',output_id=oid))
    patch=tools.execute('workspace_draft',dict(name='报告',format='md',output_id=oid,base_version=1,summary='续写',sections=[dict(kind='fact',text='Router 报告仍有提交讨论。',evidence_ids=[1])]))
    assert 'proposal_id' in patch,patch
    with store.connect() as db:db.execute("UPDATE workspace_tasks SET state='completed' WHERE id=?",(tid,))
    layer.edit(wid,oid,'用户保留的判断\n',1);denied(lambda:layer.apply(wid,[patch['proposal_id']],confirm=True))
    assert layer.output(wid,oid)['body']=='用户保留的判断\n'
    layer.restore(wid,oid,1,2,True);assert layer.output(wid,oid)['version']==3
    assert len(layer.history(wid)['versions'])==3
    # Excluded items survive rediscovery and repeat Agent collection attempts.
    ref=dict(source_type='message',source_id='1');layer.material(wid,ref,'excluded');layer.material(wid,ref,'accepted')
    assert next(r for r in layer.materials(wid) if r['source_id']=='1')['state']=='excluded'
    with store.connect() as db:
        db.execute("INSERT INTO voice_sources(message_id,locator_json) VALUES(1,'{}')")
        db.execute("UPDATE voice_sources SET transcript='三点开会',status='done' WHERE message_id=1")
        db.execute("INSERT INTO artifacts(sha256,size,mime_type,created_at,last_accessed_at) VALUES('sha',1,'text/plain',0,0)")
        db.execute("INSERT INTO artifact_sources(source_key,platform,source_type,conversation_id,conversation,sender,filename,extension,timestamp,sha256,discovered_at,last_seen_at) VALUES('source','qq','message','a','a','s','Router.txt','txt',1790000000001,'sha',0,0)")
        db.execute("UPDATE artifacts SET parse_status='PARSED' WHERE sha256='sha'")
        db.execute("INSERT INTO artifact_chunks(sha256,ordinal,text,locator) VALUES('sha',0,'正文','{}')")
        before=water(db)['high_water'];db.execute("UPDATE artifacts SET last_accessed_at=12 WHERE sha256='sha'");assert water(db)['high_water']==before
        db.execute('DELETE FROM messages WHERE id=3')
    layer.discover(wid);kinds={r['reason'] for r in layer.candidates(wid)};assert {'parse_changed','chunks_changed','deleted'}<=kinds,kinds
    with store.connect() as db:assert db.execute("SELECT decision FROM workspace_candidates WHERE workspace_id=? AND reason='voice_update'",(wid,)).fetchone()[0]=='excluded'
    # Reopening uses durable state; no stream events from workspace drafts/edits.
    assert Workspaces(Store(store.path)).output(wid,oid)['version']==3
    old=layer.get(wid);layer.update(wid,dict(revision=old['revision'],scope=dict(platforms=['qq'],conversations=[dump(['qq','b'])])))
    denied(lambda:layer.output(wid,oid));assert all(not r['available'] for r in layer.materials(wid))
    # Backfill is bounded, excludes old range, and does not silently declare full coverage.
    layer.backfill(wid,'2026-09-01','2026-09-30',limit=1);assert all(r['source_id'] in ('2','4') for r in layer.candidates(wid))
    with store.connect() as db:db.execute('DELETE FROM local_changes WHERE seq=(SELECT max(seq) FROM local_changes)')
    # Force a previously unprocessed gap, also catches cleared journals.
    with store.connect() as db:db.execute('UPDATE workspaces SET discovery_cursor=0 WHERE id=?',(wid,))
    assert layer.discover(wid)['gap']
    with store.connect() as db:before=db.execute('SELECT count(*) FROM messages').fetchone()[0]
    layer.delete(wid,True)
    with store.connect() as db:assert db.execute('SELECT count(*) FROM messages').fetchone()[0]==before
    assert layer.get(wb['id'])
    # Full task lifecycle with a deterministic agent driver: staged decisions
    # survive failure, but neither processing nor formal-output cursors advance.
    wd=layer.create('Router 报告','恢复测试',dict(platforms=['qq'],conversations=[dump(['qq','b'])]))
    message(5,'b','Router 最新安排，需要进一步核对。')
    fail=[True]
    def fixture_agent(store,plan,question,**kwargs):
        ctx=kwargs['workspace_context'];t=ChatTools(store,plan,max_calls=30,max_chars=30000);t.workspace_context=ctx;t.schemas=SCHEMAS+schemas(tool)
        changes=t.execute('workspace_read',dict(action='changes'))['candidates']
        t.execute('get_context',dict(message_id=5,radius=2))
        p=t.execute('workspace_draft',dict(name='恢复报告',format='md',summary='新增安排',sections=[dict(kind='fact',text='有一条新的Router安排，需要核对。',evidence_ids=[5])]))
        assert p.get('proposal_id'),p
        result=t.execute('workspace_read',dict(action='decide',candidate_ids=[r['id'] for r in changes],decision='processed',reason='读过新增正文，保留待确认安排'))
        assert not result.get('error'),result
        yield dict(type='done',status='error' if fail[0] else 'completed',record=dict(result=dict(claims=[]),requests=1,usage={},bundle=t.bundle()))
    runner=WorkspaceRunner(store,agent=fixture_agent)
    def wait_task(wid,tid):
        for _ in range(200):
            result=runner.task(wid,tid)
            if result['state']!='running' and tid not in runner.active:return result
            time.sleep(.02)
        raise AssertionError('Task did not settle')
    failed=runner.start(wd['id'],'检查新变化',mode='changes');assert wait_task(wd['id'],failed['task_id'])['state']=='error'
    assert layer.candidates(wd['id']) and layer.proposals(wd['id'])
    assert not layer.overview(wd['id'])['outputs']
    # Restart recovers a running row without manufacturing a completed result.
    with store.connect() as db:db.execute("UPDATE workspace_tasks SET state='running' WHERE id=?",(failed['task_id'],))
    WorkspaceRunner(store).recover();assert runner.task(wd['id'],failed['task_id'])['state']=='interrupted'
    # Reuse the saved draft on retry, never create a second identical artifact.
    saved=next(p for p in layer.proposals(wd['id']) if p['action']=='write')
    original=fixture_agent
    def retry_agent(store,plan,question,**kwargs):
        ctx=kwargs['workspace_context'];t=ChatTools(store,plan,max_calls=30,max_chars=30000);t.workspace_context=ctx;t.schemas=SCHEMAS+schemas(tool)
        rows=t.execute('workspace_read',dict(action='changes'))['candidates'];t.execute('get_context',dict(message_id=5,radius=2))
        assert not t.execute('workspace_read',dict(action='draft',draft_id=saved['id'])).get('error')
        p=t.execute('workspace_draft',dict(name='恢复报告',format='md',draft_id=saved['id'],summary='重试完成',sections=[dict(kind='fact',text='新安排仍需要核对。',evidence_ids=[5])]))
        assert p.get('proposal_id')==saved['id'],p
        t.execute('workspace_read',dict(action='decide',candidate_ids=[r['id'] for r in rows],decision='processed',reason='已读'))
        yield dict(type='done',status='completed',record=dict(result=dict(claims=[]),requests=1,usage={},bundle=t.bundle()))
    runner.agent=retry_agent;retried=runner.start(wd['id'],'继续检查',mode='changes');assert wait_task(wd['id'],retried['task_id'])['state']=='completed'
    assert not layer.candidates(wd['id']) and len([p for p in layer.proposals(wd['id']) if p['action']=='write'])==1
    assert layer.get(wd['id'])['applied_seq']<layer.get(wd['id'])['processed_seq']
    layer.apply(wd['id'],[saved['id']],confirm=True);assert len(layer.overview(wd['id'])['outputs'])==1
    assert runner.start(wd['id'],'再检查',mode='changes')['api_called'] is False
    # Same source changing after it was read rejects stale draft facts.
    tid='stale-task'
    with store.connect() as db:db.execute("INSERT INTO workspace_tasks(id,workspace_id,scope_epoch,question,mode,state,start_seq,created_at) VALUES(?,?,1,'stale','task','running',0,0)",(tid,wd['id']))
    t=ChatTools(store,layer.plan(wd['id']),max_calls=20);t.workspace_context=dict(id=wd['id'],task_id=tid,scope_epoch=1,start_seq=100);t.schemas=SCHEMAS+schemas(tool)
    t.execute('get_context',dict(message_id=5,radius=2))
    with store.connect() as db:db.execute("UPDATE messages SET content='已经变更' WHERE id=5")
    assert t.execute('workspace_draft',dict(name='禁止过期',format='md',summary='过期',sections=[dict(kind='fact',text='旧结论',evidence_ids=[5])])).get('error')
    print('PASS: transactional journal/no-op/rollback, scoped tools+SQL, drafts/apply/edit-conflict/restore/restart, exclusions, old ASR/parse/delete changes, bounded backfill, gap, owned deletion.')

if __name__=='__main__':main()
