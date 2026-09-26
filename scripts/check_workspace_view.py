"""Small isolated regression of the read-only Workspace V1.1 projection. No API calls."""
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chatlocal.config import ROOT
from chatlocal.store import Store
from chatlocal.workspaces import Workspaces, dump
from chatlocal.workspace_view import home_view, sections, source_counts
from chatlocal.changes import water


with tempfile.TemporaryDirectory(dir=ROOT/'.tmp', prefix='workspace-view-') as folder:
    store = Store(Path(folder)/'fixture.sqlite3'); layer = Workspaces(store)
    def message(mid, cid='a'):
        with store.connect() as db:
            db.execute('''INSERT INTO messages(id,dedup_key,platform,conversation_id,conversation,sender_id,sender,timestamp,content,is_self,source_id)
                VALUES(?,?,'qq',?,'测试群','42','同学',1790000000000,?,0,?)''',
                (mid, f'key-{mid}', cid, f'Router 通知 {mid}', str(mid)))
    message(1); message(2,'b')
    w = layer.create('Router 报告', scope=dict(platforms=['qq'],conversations=[dump(['qq','a'])])); wid=w['id']
    empty=home_view(layer,wid)
    assert not empty['understanding'] and not empty['recent']['items'] and not empty['tasks']
    ref=layer.reference('message','1',layer.plan(wid));layer.material(wid,ref,'accepted','确认的起点')
    tid='task'
    with store.connect() as db:
        db.execute('''INSERT INTO workspace_tasks(id,workspace_id,scope_epoch,question,mode,state,start_seq,created_at)
            VALUES(?,?,1,'报告','task','running',?,?)''',(tid,wid,water(db)['high_water'],time.time()))
    text='# 报告\n\n已确认 Router 安排。\n\n来源：[message 1](http://127.0.0.1:7860/?anchor=1)\n\n**待确认／覆盖限制：** 截止日期仍需核实。\n\n来源：[message 1](/?anchor=1)\n\n**用户要求：** 不得成为已证实事实。\n\n---\n覆盖说明。'
    p=layer.draft(wid,tid,name='报告',format='md',body=text,refs=[ref],summary='首次调查')
    assert not home_view(layer,wid)['understanding']  # running/draft never formal
    with store.connect() as db:db.execute("UPDATE workspace_tasks SET state='error' WHERE id=?",(tid,))
    assert not home_view(layer,wid)['understanding']  # failed still not formal
    with store.connect() as db:db.execute("UPDATE workspace_tasks SET state='completed' WHERE id=?",(tid,))
    layer.apply(wid,[p['proposal_id']],confirm=True);oid=p['output_id']
    with store.connect() as db:before=(water(db),tuple(db.execute('SELECT discovery_cursor,processed_seq,applied_seq FROM workspaces WHERE id=?',(wid,)).fetchone()),db.total_changes)
    view=home_view(layer,wid)
    assert [p['text'] for p in view['understanding']]==['已确认 Router 安排。']
    assert view['questions'][0]['text']=='截止日期仍需核实。' and view['questions'][0]['refs'][0]['source_id']=='1'
    assert view['outputs'][0]['sources']['messages']==1 and view['confirmed_materials']==1
    assert view['materials'][0]['conversation']=='测试群' and view['materials'][0]['reason']=='确认的起点'
    with store.connect() as db:assert before==(water(db),tuple(db.execute('SELECT discovery_cursor,processed_seq,applied_seq FROM workspaces WHERE id=?',(wid,)).fetchone()),db.total_changes)
    # Manual edits are visible products, but are not promoted to Agent conclusions.
    layer.edit(wid,oid,'# 人工编辑\n\n未经核实的判断',1)
    assert not home_view(layer,wid)['understanding']
    layer.restore(wid,oid,1,2,True)
    assert home_view(layer,wid)['understanding'][0]['version']==3
    # Repeated journal events count a source once; outside scope and backfills don't become new messages.
    message(3);message(4,'b');layer.discover(wid)
    view=home_view(layer,wid);assert view['recent']['items'][0]['count']==1
    with store.connect() as db:db.execute("UPDATE workspace_candidates SET decision='irrelevant' WHERE source_id='3' AND workspace_id=?",(wid,))
    assert not home_view(layer,wid)['recent']['items']
    with store.connect() as db:db.execute("UPDATE workspace_candidates SET decision='pending' WHERE source_id='3' AND workspace_id=?",(wid,))
    layer.material(wid,dict(source_type='message',source_id='3'),'excluded')
    assert not home_view(layer,wid)['recent']['items']
    with store.connect() as db:db.execute("UPDATE messages SET content='原文已改变' WHERE id=1")
    view=home_view(layer,wid);assert not view['understanding'] and view['questions'][0]['stale']
    assert view['outputs'][0]['sources']['changed']==1
    w=layer.get(wid);layer.update(wid,dict(revision=w['revision'],scope=dict(platforms=['qq'],conversations=[dump(['qq','b'])])))
    sealed=home_view(layer,wid)
    assert not sealed['outputs'] and not sealed['understanding'] and not sealed['questions'] and not sealed['tasks']
    assert all(not r['available'] and not r['reason'] for r in sealed['materials'])
    # CSV quotes/newlines and multiple chunks in one file are handled without invented counts.
    refs=[dict(source_type='artifact_chunk',source_id='7:2',available=True,url='/?file=7&chunk=2'),
          dict(source_type='artifact_chunk',source_id='7:3',available=True,url='/?file=7&chunk=3')]
    rows=sections(dict(format='csv',body='类型,说明,原始证据\nfact,"引号,换行\n保留",http://127.0.0.1:7860/?file=7&chunk=2\n',refs=refs))
    assert len(rows)==1 and rows[0]['refs'][0]['source_id']=='7:2' and source_counts(refs)['files']==1
print('PASS: isolated UI projection: applied-only, questions/citations, no writes, restore/manual edit, scoped change feed, exclusion, stale sources, CSV/counts.')
