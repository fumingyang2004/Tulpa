"""Refresh/chat concurrency and opt-in migration. Isolated DB, no client or cloud."""
import json,sys,tempfile,threading,time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from chatlocal.store import Store
from chatlocal.watches import Watches
from chatlocal.live import LiveManager
from chatlocal.agent import run_agent
from chatlocal.agent_tools import ChatTools
from chatlocal.retrieval import Plan
from chatlocal.data_delete import preview,purge
from chatlocal.activity import evidence_access
from chatlocal.watch_routes import install_watch_routes


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='refresh-chat-') as tmp:
        folder=Path(tmp);store=Store(folder/'chats.sqlite3')
        def put(n):
            path=folder/f'{n}.json'
            path.write_text(json.dumps([dict(platform='qq',conversation_id='c',conversation='课程',sender='甲',
                sender_id='a',timestamp='2026-09-22 12:00',content=f'小测通知 {n}',is_self=False,source_id=str(n))]),'utf-8')
            return store.import_file(path,load_stickers=False)
        put(1);watches=Watches(store);card=watches.create('小测','关注小测',['qq'],[],load_stickers=True)
        # Upgrade resets old opt-ins once, then preserves new explicit opt-ins.
        with store.connect() as db:
            db.execute("DELETE FROM client_settings WHERE name='automatic_stickers_opt_in_v1'")
            db.execute("INSERT OR REPLACE INTO client_settings VALUES('live_ingestion',?)",(json.dumps(dict(qq='database',wechat='off',load_stickers=True,reconcile_minutes=5)),))
        Watches(store)
        assert not Watches(store).get(card['id'])['load_stickers']
        live=LiveManager(store);assert live.settings==dict(qq='database',wechat='off',load_stickers=False)
        live.configure(dict(load_stickers=True));Watches(store)
        assert LiveManager(store).settings['load_stickers'] is True
        assert not hasattr(live,'reconcile')
        # While a writer has an uncommitted modification, reads see committed data.
        with store.connect() as writer:
            writer.execute("UPDATE messages SET content='uncommitted' WHERE id=1")
            with store.connect() as reader:
                assert reader.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
                assert reader.execute('SELECT content FROM messages WHERE id=1').fetchone()[0]=='小测通知 1'
            writer.rollback()
        deleting=dict(platform='qq',conversations=[],start='2026-09-01',end='2026-09-30')
        fingerprint=preview(store,deleting)['fingerprint']
        sync_entered=threading.Event();sync_release=threading.Event()
        agent_entered=threading.Event();agent_release=threading.Event();calls=[];seen=[]
        def syncer(*args,**kwargs):
            calls.append(kwargs);sync_entered.set();assert sync_release.wait(20)
            result=put(2)
            return [dict(platform='qq',status='ok',added=result['imported'])]
        def analyze(store,plan,*args,**kwargs):
            tools=ChatTools(store,plan);seen.append(plan.snapshot_max_id);agent_entered.set()
            yield dict(type='status',text='分析已入库消息')
            assert agent_release.wait(20)
            result=tools.execute('read_conversation',dict(platform='qq',conversation_id='c',limit=20))
            assert [m['id'] for m in result['messages']]==[1],result
            yield dict(type='done',status='completed',record=dict(result=dict(claims=[],insufficient=True),bundle=tools.bundle(),events=[],requests=0))
        app=FastAPI();install_watch_routes(app,store)
        def wait_job(client,jid):
            until=time.monotonic()+10
            while time.monotonic()<until:
                job=client.get('/api/watch-jobs/'+jid).json()
                if job['status']!='running':return job
                time.sleep(.02)
            raise AssertionError('Job did not finish')
        with patch('chatlocal.watch_routes.sync_messages',syncer),patch('chatlocal.agent._run_agent',analyze),TestClient(app) as client:
            try:
                first=client.post('/api/sync',json={});assert first.status_code==200
                assert sync_entered.wait(2)
                response=client.post('/api/watch-cards/'+card['id']+'/ask',json=dict(question='小测是什么',vision_mode='ocr'))
                assert response.status_code==200,response.text
                assert agent_entered.wait(2),'Question was queued behind refresh'
                assert {j['kind'] for j in client.get('/api/refresh-status').json()['running_jobs']}=={'sync','question'}
                assert client.post('/api/sync',json={}).status_code==409
                assert client.delete('/api/watch-cards/'+card['id']).status_code==409
                try:purge(store,deleting,fingerprint);raise AssertionError('Deleted active evidence')
                except ValueError as exc:assert '聊天正在' in str(exc)
                sync_release.set();assert wait_job(client,first.json()['job_id'])['status']=='completed'
                assert seen==[1] and store.message(2)
                agent_release.set();assert wait_job(client,response.json()['job_id'])['status']=='completed'
                assert calls[0]['load_stickers'] is False
                # Next round sees the newly committed upper bound; generators
                # release their deletion guard even when closed before completion.
                def next_turn(store,plan,*args,**kw):
                    assert plan.snapshot_max_id==2;yield dict(type='status')
                with patch('chatlocal.agent._run_agent',next_turn):
                    turn=run_agent(store,Plan(),'test');next(turn);turn.close()
                with evidence_access(store,delete=True):pass
                # Exercise the actual auto timer with a virtual clock; no live clients.
                assert client.post('/api/auto-refresh',json=dict(minutes=30)).json()['load_stickers'] is False
                calls.clear()
                with patch('chatlocal.watch_routes.time',SimpleNamespace(time=lambda:time.time()+1801)):
                    until=time.monotonic()+8
                    while not calls and time.monotonic()<until:time.sleep(.05)
                assert len(calls)==1 and calls[0]['load_stickers'] is False
                for job in client.get('/api/refresh-status').json()['running_jobs']:wait_job(client,job['id'])
                assert client.post('/api/auto-refresh',json=dict(minutes=30,load_stickers=True)).json()['load_stickers'] is True
                assert not client.get('/api/live-status').json()['settings'].get('reconcile_minutes')
            finally:sync_release.set();agent_release.set()
    print('PASS: refresh + watch dialogue run concurrently, WAL committed reads, frozen turn IDs, deletion protection, close releases lock, timer opt-in defaults/migration, no reconcile scheduler. Isolated fixtures; no cloud.')


if __name__=='__main__':main()
