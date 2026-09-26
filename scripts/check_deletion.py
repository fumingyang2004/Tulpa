"""Focused deletion checks in disposable local databases; no client or cloud calls."""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from chatlocal.agent_sessions import Sessions
from chatlocal.store import Store
from chatlocal.watches import Watches
from chatlocal.watch_routes import install_watch_routes
from chatlocal.ui import build_app


def blocked(fn):
    try:fn()
    except (ValueError,RuntimeError):return
    raise AssertionError('Expected deletion to be rejected')


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='delete-check-') as folder:
        folder=Path(folder);store=Store(folder/'messages.sqlite3');sessions=Sessions(folder/'sessions.sqlite3')
        source=folder/'messages.json'
        source.write_text(json.dumps(dict(messages=[dict(platform='qq',conversation_id='course',conversation='课程群',
            sender='甲',timestamp='2026-09-21 18:00',content='周三交报告',source_id='1',is_self=False)])),encoding='utf-8')
        store.import_file(source)
        watches=Watches(store)
        card=watches.create('删除测试','报告',['qq'],[])
        keep=watches.create('保留测试','报告',['qq'],[])
        with store.connect() as db:
            db.execute('INSERT INTO watch_runs(card_id,created_at,status,record) VALUES(?,?,?,?)',(card['id'],'test','completed','{}'))
            # A failed parent delete must also roll back deletion of child runs.
            db.execute("CREATE TRIGGER deny_delete BEFORE DELETE ON watch_cards BEGIN SELECT RAISE(ABORT,'test'); END")
        import sqlite3
        try:watches.delete(card['id'])
        except sqlite3.IntegrityError:pass
        else:raise AssertionError('Expected rollback')
        with store.connect() as db:
            assert db.execute('SELECT count(*) FROM watch_runs WHERE card_id=?',(card['id'],)).fetchone()[0]==2
            db.execute('DROP TRIGGER deny_delete')
        with watches.lock:blocked(lambda:watches.delete(card['id']))
        assert watches.delete(card['id']) and not watches.delete(card['id'])
        assert watches.get(keep['id'])
        with store.connect() as db:
            assert db.execute('SELECT count(*) FROM watch_runs WHERE card_id=?',(card['id'],)).fetchone()[0]==0
            assert db.execute('SELECT count(*) FROM messages').fetchone()[0]==1
            assert db.execute('SELECT count(*) FROM sources').fetchone()[0]==1

        a=sessions.create();b=sessions.create()
        tid=sessions.start(a,'delete me',{});sessions.finish(tid,'completed',{})
        sessions.start(b,'keep me',{})
        assert sessions.delete(a) and not sessions.delete(a)
        assert not sessions.history(a) and len(sessions.history(b))==1

        app=build_app(store,sessions)
        callbacks={entry.fn.__name__:entry.fn for entry in app.fns.values() if entry.fn}
        c=sessions.create()
        result=callbacks['delete_session'](c,b)
        assert result[0]['cleared_current'] is False and result[1]==b
        assert all(x.get('__type__')=='update' for x in result[3:])
        # Register a live generator without ever reaching the Agent/cloud call.
        running=callbacks['agent_action']('测试',['qq'],[],'','','',b)
        next(running)
        blocked(lambda:callbacks['delete_session'](b,b))
        assert sessions.history(b)
        running.close()
        result=callbacks['delete_session'](b,b)
        assert result[0]['cleared_current'] and result[1] is None
        assert result[2]['value'] is None and result[3]==[] and result[6]==''
        assert sessions.choices()==[]

        # Exercise actual HTTP routes, job invalidation and a running-job race.
        api=FastAPI();install_watch_routes(api,store)
        entered=threading.Event();release=threading.Event()
        def fake_check(self,card_id,**kwargs):return self.get(card_id)
        def wait_check(self,card_id,**kwargs):
            entered.set();assert release.wait(5)
            return self.get(card_id)
        with TestClient(api) as client:
            def wait_job(job):
                for _ in range(200):
                    record=client.get('/api/watch-jobs/'+job).json()
                    if record['status']!='running':
                        assert record['status']=='completed',record
                        return record
                    time.sleep(.01)
                raise AssertionError('Job timeout')
            with patch.object(Watches,'check',fake_check):
                created=client.post('/api/watch-cards',json=dict(title='API delete',watch_query='报告',platforms=['qq'])).json()['job_id']
                target=wait_job(created)['result']['id']
                checked=client.post('/api/watch-cards/'+target+'/check',json={}).json()['job_id'];wait_job(checked)
            endpoint='/api/watch-cards/'+target
            assert client.delete(endpoint,headers={'Origin':'https://example.com'}).status_code==403
            with patch.object(Watches,'check',wait_check):
                pending=client.post(endpoint+'/check',json={}).json()['job_id']
                assert entered.wait(2)
                try:assert client.delete(endpoint).status_code==409
                finally:release.set()
                wait_job(pending)
            assert client.delete(endpoint).json()['deleted']
            assert client.delete(endpoint).json()['deleted'] is False
            assert client.get('/api/refresh-status').json()['running_job'] is None
            assert all(client.get('/api/watch-jobs/'+job).status_code==404 for job in (created,checked,pending))
            assert [c['id'] for c in client.get('/api/watch-cards').json()['cards']]==[keep['id']]
        with store.connect() as db:assert db.execute('SELECT count(*) FROM messages').fetchone()[0]==1
    print('PASS: transactional deletion, repeat delete, running guards, current/other session, job removal, origin check, source preservation; no cloud calls')


if __name__=='__main__':main()
