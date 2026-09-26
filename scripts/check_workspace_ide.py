"""IDE safety/workflow regression using isolated data and a fake Agent only."""
import json,sys,tempfile,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from chatlocal.config import ROOT
from chatlocal.store import Store
from chatlocal.workspaces import Workspaces,dump
from chatlocal.workspace_ide import task_intent
from chatlocal.workspace_runner import WorkspaceRunner
from chatlocal.agent_tools import ChatTools,SCHEMAS,tool
from chatlocal.workspace_tools import schemas
from chatlocal.workspace_view import home_view

def denied(fn):
    try:fn()
    except ValueError:return
    raise AssertionError('Operation should be denied')

with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='workspace-ide-') as folder:
    store=Store(Path(folder)/'fixture.sqlite3');layer=Workspaces(store)
    wid=layer.create('示例网络资料整理',scope=dict(platforms=['qq']))['id']
    with store.connect() as db:db.execute("INSERT INTO messages(id,dedup_key,platform,conversation_id,conversation,sender_id,sender,timestamp,content,is_self,source_id) VALUES(1,'a','qq','a','测试群','42','测试',1790000000000,'示例网络配置已修复',0,'1')")
    assert task_intent('之前那个方案怎么样？')=='ask'
    assert task_intent('这个问题已经解决了吗？')=='ask'
    assert task_intent('不要更新工作区，仅回答问题。')=='ask'
    assert task_intent('这个问题昨天已经解决。')=='fact'
    assert task_intent('继续整理最近进展。')=='task'
    seen=[]
    def agent(store,plan,question,**kw):
        ctx=kw['workspace_context'];seen.append(ctx['intent'])
        t=ChatTools(store,plan,max_calls=30,max_chars=50000);t.workspace_context=ctx;t.schemas=SCHEMAS+schemas(tool)
        t.execute('get_context',dict(message_id=1,radius=2))
        if ctx['intent']=='ask':
            assert t.execute('workspace_draft',dict(name='禁止写入',format='md',summary='普通提问',sections=[dict(kind='uncertainty',text='禁止')])).get('error')
            assert t.execute('workspace_material',dict(source_type='message',source_id='1',reason='普通提问')).get('error')
        else:
            kind='user_fact' if ctx['intent']=='fact' else 'fact'
            ref=dict(kind=kind,text='模型试图替换用户说法' if kind=='user_fact' else '记录中说明配置已修复。',evidence_ids=[] if kind=='user_fact' else [1])
            result=t.execute('workspace_draft',dict(name='当前状态.md',format='md',resource_kind='state',summary='核对状态',sections=[ref]))
            assert result.get('proposal_id'),result
        yield dict(type='done',status='completed',record=dict(result=dict(claims=[]),requests=1,usage={},bundle=t.bundle()))
    runner=WorkspaceRunner(store,agent=agent)
    def wait(tid):
        for _ in range(200):
            t=runner.task(wid,tid)
            if t['state']!='running' and tid not in runner.active:return t
            time.sleep(.02)
        raise AssertionError('Task did not finish')
    r=runner.start(wid,'之前那个方案怎么样？');assert wait(r['task_id'])['state']=='completed'
    assert not layer.proposals(wid) and not layer.materials(wid) and not layer.overview(wid)['outputs']
    r=runner.start(wid,'这个问题昨天已经解决。');t=wait(r['task_id']);assert t['state']=='completed',t
    proposed=layer.proposals(wid);assert len(proposed)==1
    assert not home_view(layer,wid)['current_state']
    assert '这个问题昨天已经解决。' in proposed[0]['body'] and '模型试图' not in proposed[0]['body']
    assert proposed[0]['refs'][0]['source_type']=='user_statement'
    denied(lambda:layer.apply(wid,[proposed[0]['id']],automatic=True))
    layer.apply(wid,[proposed[0]['id']],confirm=True)
    view=home_view(layer,wid);assert view['current_state'] and not view['understanding'] and view['user_supplements']
    oid=proposed[0]['output_id'];v=layer.output(wid,oid)
    edit=layer.manual_draft(wid,oid,'# 手动状态\n用户编辑',v['version']);assert layer.output(wid,oid)['body']==v['body']
    layer.reject(wid,[edit['proposal_id']]);assert not layer.proposals(wid)
    edit=layer.manual_draft(wid,oid,'# 手动状态\n用户编辑',v['version']);layer.apply(wid,[edit['proposal_id']],confirm=True)
    layer.restore(wid,oid,1,2,True);assert layer.output(wid,oid)['version']==3
    assert Workspaces(Store(store.path)).output(wid,oid)['version']==3
    # Another workspace cannot borrow this user-supplied assertion.
    other=layer.create('另一个主题',scope=dict(platforms=['wechat']))['id']
    assert not layer.ref_status(proposed[0]['refs'][0],layer.plan(other))['available']
    # Automatic maintenance defaults off, is durable, budgets are explicit, no
    # candidate means zero model calls. A due check never applies anything.
    assert layer.schedule(other)['minutes']==0
    settings=layer.set_schedule(other,dict(minutes=10080,profile='quick',vision='ocr'))
    assert Workspaces(Store(store.path)).schedule(other)['minutes']==10080
    with store.connect() as db:db.execute('UPDATE workspace_schedules SET next_at=1 WHERE workspace_id=?',(other,))
    before=len(seen);runner.maintain(now=2);assert len(seen)==before
    task=runner.task(other,layer.schedule(other)['last_task_id']);assert task['record']['requests']==0
    layer.delete(wid,True)
    wid=layer.create('示例网络',scope=dict(platforms=['qq'],conversations=[dump(['qq','a'])]))['id']
    with store.connect() as db:db.execute("UPDATE messages SET content='示例网络今天新增指南' WHERE id=1")
    layer.set_schedule(wid,dict(minutes=1440,profile='quick'))
    with store.connect() as db:db.execute('UPDATE workspace_schedules SET next_at=1 WHERE workspace_id=?',(wid,))
    runner.maintain(now=2);tid=layer.schedule(wid)['last_task_id'];assert wait(tid)['state']=='completed'
    assert layer.proposals(wid) and not layer.overview(wid)['outputs'] and not layer.materials(wid)
    layer.apply(wid,[p['id'] for p in layer.proposals(wid)],confirm=True)
    assert layer.overview(wid)['outputs'] and layer.materials(wid)
    # User scope changes revoke old user assertions and proposals.
    old=layer.get(wid);layer.update(wid,dict(revision=old['revision'],scope=dict(platforms=['wechat'])))
    denied(lambda:layer.output(wid,layer.overview(wid)['outputs'][0]['id']))
    print('PASS: read-only ask, fact provenance, all-proposal writes, staged materials, user edit/reject/apply/restore, durable schedule, zero-model no-change, scheduled candidate-only update, scope isolation. Synthetic driver; no cloud/client messages.')
