"""Critical transaction/cursor/coverage tests. Synthetic data; no cloud or clients."""
import json
import hashlib
import zlib
from array import array
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from chatlocal.store import Store
from chatlocal.sync import sync_messages
from chatlocal.watches import Watches
from chatlocal.agent_tools import ChatTools
from chatlocal.retrieval import Plan
from chatlocal.llm import validate_answer
from chatlocal.import_scope import save_scope,get_scope
from chatlocal.watch_view import entry_view
from incremental_common import boundary
from incremental_qq import delta_rows


def expect_error(fn):
    try:fn()
    except ValueError:return
    raise AssertionError('Expected failure')


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='watch-check-') as tmp:
        folder=Path(tmp);store=Store(folder/'messages.sqlite3');file=folder/'messages.json'
        def message(source,content,platform='qq',conversation='course',time='2026-09-21 18:00'):
            return dict(platform=platform,conversation_id=conversation,conversation=conversation,sender='甲',sender_id='a',
                timestamp=time,content=content,is_self=False,source_id=source)
        def put(rows,**kwargs):file.write_text(json.dumps(dict(messages=rows)),encoding='utf-8');return store.import_file(file,**kwargs)
        put([message('1','PPT 周三交')])
        blob=zlib.compress(array('q',[1,2]).tobytes())
        checkpoint=dict(method='native-id-inventory-v1',account='test',cursors={name:dict(inventory_sha256=hashlib.sha256(blob).hexdigest()) for name in ('c2c_msg_table','group_msg_table')})
        def runner(command,**kwargs):
            platform='qq' if 'export_qq.py' in command[1] else 'wechat'
            if platform=='wechat':return SimpleNamespace(returncode=1,stderr='ValueError: 测试故障')
            out=Path(command[command.index('--output')+1])
            for name in checkpoint['cursors']:(out.parent/(name+'.next.bin')).write_bytes(blob)
            out.write_text(json.dumps(dict(messages=[message('2','改为周五提交')],sync_checkpoint=checkpoint)),encoding='utf-8')
            return SimpleNamespace(returncode=0,stderr='')
        synced=sync_messages(store,runner=runner)
        assert [r['status'] for r in synced]==['ok','error']
        assert synced[0]['added']==1
        assert sync_messages(store,['qq'],runner=runner)[0]['added']==0
        with store.connect() as db:
            assert db.execute("SELECT last_sync_at FROM sync_state WHERE platform='wechat'").fetchone()[0] is None
            before=db.execute('SELECT count(*) FROM messages').fetchone()[0]
        # A stale checkpoint cannot commit any of its message rows.
        expect_error(lambda:put([message('3','不应入库')],sync=dict(platform='qq',previous={},next=checkpoint,detail='')))
        with store.connect() as db:assert db.execute('SELECT count(*) FROM messages').fetchone()[0]==before
        # Native IDs reused after DB reset must be detected.
        native=sqlite3.connect(':memory:');native.execute('CREATE TABLE source(id INTEGER PRIMARY KEY,source_id TEXT,time INT)')
        native.execute("INSERT INTO source VALUES(1,'a',10)")
        cursor=boundary(native,'source','id',['source_id','time'],None)
        native.execute("UPDATE source SET source_id='other'")
        expect_error(lambda:boundary(native,'source','id',['source_id','time'],cursor));native.close()
        # Actual QQ UIDs can DECREASE while new messages arrive. The adapter
        # must use membership, never max(rowid) or a timestamp-only cursor.
        native=sqlite3.connect(':memory:')
        native.execute('CREATE TABLE qq("40001" INTEGER PRIMARY KEY,"40003" INT,"40050" INT,"40090" TEXT,"40033" INT,"40800" BLOB,"40021" TEXT,"40020" TEXT,"40011" INT,"40012" INT)')
        native.execute("INSERT INTO qq VALUES(100,1,1000,'a',1,X'','g','u',2,1)")
        spec=SimpleNamespace(table='qq',conversation_column='40021',table_rank=1,conversation_type='group')
        request_path=folder/'native-request.json'
        rows,cursor=delta_rows(native,spec,dict(checkpoint={},bootstrap_since=0),request_path)
        assert [r[0] for r in rows]==[100]
        request=dict(checkpoint=dict(method='native-id-inventory-v1',cursors={'qq':cursor}),inventory={'qq':str(folder/'qq.next.bin')})
        native.execute("INSERT INTO qq VALUES(50,2,900,'a',1,X'','g','u',2,1)")
        rows,_=delta_rows(native,spec,request,request_path)
        assert [r[0] for r in rows]==[50], 'Lower UID / older timestamp must still be imported'
        native.close()
        watches=Watches(store);card=watches.create('课程','截止时间',['qq'],[json.dumps(['qq','course'])])
        def agent(s,plan,q,**kwargs):
            assert 'watch_context' not in kwargs
            tools=ChatTools(s,plan,max_messages=100,max_chars=24000)
            tools.execute('search_messages',dict(query='PPT 提交 周五',limit=5))
            claim=dict(text='当前安排',evidence_ids=list(tools.messages))
            checked=validate_answer(dict(claims=[claim]),list(tools.messages.values()))
            yield dict(type='done',status='completed',record=dict(result=checked,bundle=tools.bundle(),requests=0))
        c=watches.check(card['id'],refresh=False,agent=agent)
        baseline_cursor=c['cursor'];baseline_at=c['last_checked_at']
        # Late-arriving older timestamps are new by ingestion ID; other scopes excluded.
        put([message('late','补到的旧通知，周五','qq','course','2026-09-01 10:00'),message('other','别群通知','qq','other')])
        def failed(*args,**kwargs):yield dict(type='done',status='error',record={'result':{'error':'测试失败'}})
        expect_error(lambda:watches.check(c['id'],refresh=False,agent=failed))
        assert watches.get(c['id'])['cursor']==baseline_cursor and watches.get(c['id'])['last_checked_at']==baseline_at
        # Client refresh failure doesn't discard locally ingested progress.
        c=watches.check(c['id'],agent=agent,syncer=lambda *a,**k:[dict(platform='qq',status='error')])
        assert c['cursor']>baseline_cursor and c['last_result']['answer']['claims']
        assert c['last_result']['refresh_notice'] and c['last_result']['data_status'][0]['status']=='error'
        assert 'watch-refresh-note' in entry_view(store,watches.timeline(c['id'])[0][-1])['html']
        c=watches.check(c['id'],refresh=False,agent=agent)
        assert c['last_result']['mode']=='scheduled_question'
        put([message(str(100+i),'后续消息 '+str(i)) for i in range(41)])
        # Reproduce UI bug: import selection is a completely different group.
        read_scope=dict(qq=dict(enabled=True,conversations=['other']),wechat=dict(enabled=False,conversations=None))
        save_scope(store,read_scope)
        unexpected_refresh=[]
        def no_refresh(*args,**kwargs):unexpected_refresh.append(args);raise AssertionError('unrelated client scope must not be read')
        c=watches.check(c['id'],agent=agent,syncer=no_refresh,trigger='scheduled')
        assert watches.message_counts(c)['pending_messages']==0 and c['last_result']['mode']=='scheduled_question'
        assert c['last_result']['data_status']==[dict(platform='qq',coverage='none',status='skipped')]
        assert get_scope(store)==read_scope
        assert watches.timeline(c['id'])[0][-1]['trigger']=='scheduled'
        c=watches.check(c['id'],refresh=False,agent=agent)
        assert watches.message_counts(c)['pending_messages']==0
        c=watches.check(c['id'],syncer=no_refresh,agent=agent)
        assert c['last_result']['refresh_notice']
        # A late client arrival, even with old sent time, is checked next time.
        put([message('after-offline','离线后补到的旧消息','qq','course','2026-08-01 10:00')])
        c=watches.check(c['id'],agent=agent,syncer=no_refresh)
        assert watches.message_counts(c)['pending_messages']==0
        assert not unexpected_refresh
        # Partial coverage and one failed platform still preserve both local scopes.
        put([message('wechat-local','微信课程通知','wechat','wx-course')])
        read_scope=dict(qq=dict(enabled=True,conversations=['other']),wechat=dict(enabled=True,conversations=None));save_scope(store,read_scope)
        combined=watches.create('跨平台','课程通知',['qq','wechat'],[])
        sync_calls=[]
        def mixed_sync(s,platforms,**kwargs):
            sync_calls.append((platforms,kwargs['load_stickers']))
            return [dict(platform='qq',status='ok'),dict(platform='wechat',status='error')]
        combined=watches.check(combined['id'],syncer=mixed_sync,agent=agent)
        assert sync_calls==[(['qq','wechat'],False)]
        assert combined['last_result']['data_status']==[dict(platform='qq',coverage='partial',status='ok'),dict(platform='wechat',coverage='full',status='error')]
        # Refresh exceptions and Agent failure: only successful local analysis moves the cursor.
        put([message('fail-after-refresh','等待分析的消息')]);before=watches.get(c['id'])
        save_scope(store,dict(qq=dict(enabled=True,conversations=None),wechat=dict(enabled=False,conversations=None)))
        def busy(*args,**kwargs):raise ValueError('busy')
        expect_error(lambda:watches.check(c['id'],agent=failed,syncer=busy))
        assert watches.get(c['id'])['cursor']==before['cursor'] and watches.get(c['id'])['last_checked_at']==before['last_checked_at']
        assert watches.timeline(c['id'])[0][-1]['result']['refresh_notice']
        c=watches.check(c['id'],agent=agent,syncer=busy)
        assert watches.message_counts(c)['pending_messages']==0 and c['last_result']['data_status'][0]['status']=='error'
        # Future IDs still cannot be read beyond this query snapshot.
        tools=ChatTools(store,Plan(platforms=['qq'],snapshot_max_id=baseline_cursor))
        assert 'error' in tools.execute('get_context',dict(message_id=baseline_cursor+1))
        tools.execute('get_context',dict(message_id=baseline_cursor,radius=8))
        assert all(mid<=baseline_cursor for mid in tools.messages)
    print('PASS: idempotent delta, atomic checkpoint rollback, source reset, platform isolation, late arrivals, query failure retry, independent import scope and frozen snapshot. Synthetic only.')


if __name__=='__main__':main()
