"""Critical Watch conversation/schedule checks, with fake Agent and source sync."""
import inspect
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
from chatlocal.agent import run_agent
from chatlocal.store import Store
from chatlocal.watches import Watches
from chatlocal.watch_routes import install_watch_routes
from chatlocal.watch_view import entry_view


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='watch-dialogue-') as folder:
        folder=Path(folder);store=Store(folder/'messages.sqlite3');source=folder/'source.json'
        source.write_text(json.dumps({'messages':[dict(platform='qq',conversation_id=c,conversation=c,sender='老师',
            timestamp='2026-09-22 10:00',content='小测周三进行',source_id='1',is_self=False) for c in ('course','other')]}),encoding='utf-8')
        store.import_file(source);watches=Watches(store);scope=[json.dumps(['qq','course'])]
        card=watches.create('小测','关注小测时间',['qq'],scope,schedule_minutes=10080)
        original_due=card['next_run_at']
        seen=[]
        def fake(*args,**kwargs):
            # Bind the actual Agent signature to catch integration drift.
            inspect.signature(run_agent).bind(*args,**kwargs)
            seen.append(kwargs);plan=args[1]
            claim=dict(text='小测周三进行',evidence_ids=[1])
            result=dict(claims=[claim],insufficient=False,rejected=0)
            assert 'watch_context' not in kwargs
            bundle=dict(messages=[store.message(1)],contexts={},context_chars=8,plan=plan.__dict__,seed_ids=[1],scope_count=1,match_count=1)
            try:yield dict(type='done',status='completed',record=dict(result=result,bundle=bundle,requests=0))
            finally:
                if kwargs.get('cancel') is not None:kwargs['cancel'].set()
        cancel=threading.Event()
        card=watches.check(card['id'],refresh=False,agent=fake,cancelled=cancel)
        cursor=card['cursor'];baseline=card['baseline'];checked=card['last_checked_at']
        assert not cancel.is_set(),'Runtime cleanup must not cancel the parent query'
        watches.ask(card['id'],'小测是哪天？',agent=fake,cancelled=threading.Event())
        watches.ask(card['id'],'那是哪一天？',agent=fake)
        assert any(t['question']=='小测是哪天？' for t in seen[-1]['memory']['previous_turns'])
        assert watches.get(card['id'])['cursor']==cursor
        rows,_=watches.timeline(card['id'])
        assert [r['kind'] for r in rows]==['setup','check','question','question']
        assert '小测周三进行' in entry_view(store,rows[1])['html'] and '查看上下文' in entry_view(store,rows[1])['html']
        card=watches.update(card['id'],'课堂小测','关注小测时间',['qq'],scope,schedule_minutes=1440)
        assert card['revision']==1 and card['baseline']==baseline and card['last_checked_at']==checked
        assert card['next_run_at']<original_due
        card=watches.update(card['id'],'课堂小测','关注小测时间',['qq'],[json.dumps(['qq','other'])],schedule_minutes=10080)
        assert card['revision']==2 and card['cursor']==0 and not card['baseline'] and card['last_checked_at'] is None
        watches.ask(card['id'],'新群呢？',agent=fake)
        assert len(seen[-1]['memory']['previous_turns'])==1,'Old-scope memory leaked'
        assert watches.get(card['id'])['last_checked_at'] is None,'Follow-up changed baseline'
        # Failure and cancellation are visible, never advance evidence cursors.
        def failed(*args,**kwargs):yield dict(type='done',status='error',record=dict(result=dict(error='测试分析失败')))
        try:watches.check(card['id'],syncer=lambda *a,**k:[dict(platform='qq',status='error')],agent=failed)
        except ValueError:pass
        else:raise AssertionError('Expected analysis failure')
        assert watches.timeline(card['id'])[0][-1]['status']=='error'
        assert watches.get(card['id'])['cursor']==0
        watches.pause(card['id'],True);assert watches.get(card['id'])['next_run_at'] is None
        watches.pause(card['id'],False);card=watches.get(card['id'])
        # Schedule survives a new service object and missed slots fire once.
        assert Watches(store).get(card['id'])['schedule_minutes']==10080
        at=card['next_run_at']+4*10080*60
        assert watches.due(at)==[card['id']] and watches.claim_due(card['id'],at)
        assert not watches.claim_due(card['id'],at) and not watches.due(at)
        assert watches.get(card['id'])['cursor']==0
        # Run the actual background timer once, with fake data/Agent only.
        with store.connect() as db:db.execute('UPDATE watch_cards SET next_run_at=? WHERE id=?',(time.time()-1,card['id']))
        finished=threading.Event();calls=[];real_check=Watches.check
        def scheduled(self,card_id,**kwargs):
            calls.append(kwargs.copy())
            result=real_check(self,card_id,**kwargs,agent=fake,syncer=lambda *a,**k:[dict(platform='qq',status='ok')])
            finished.set();return result
        api=FastAPI();install_watch_routes(api,store)
        with patch.object(Watches,'check',scheduled),TestClient(api) as client:
            assert finished.wait(9),'Actual timer did not run'
            assert len(calls)==1 and calls[0]['trigger']=='scheduled' and calls[0]['refresh']
            page=client.get('/api/watch-cards/'+card['id']+'/thread').json()
            assert page['entries'][-1]['trigger']=='scheduled'
            assert page['entries'][-1]['status']=='completed'
            assert client.put('/api/watch-cards/'+card['id'],json={},headers={'Origin':'https://example.com'}).status_code==403
            # Paging leaves all older runs available.
            with store.connect() as db:
                for i in range(43):watches.add_run(db,card['id'],'settings',str(i),2,result=dict(note='<script>not executable</script>'))
            page=client.get('/api/watch-cards/'+card['id']+'/thread').json()
            assert len(page['entries'])==40 and page['older']
            older=client.get('/api/watch-cards/'+card['id']+'/thread',params={'before':page['older']}).json()
            assert not {r['id'] for r in page['entries']}&{r['id'] for r in older['entries']}
            assert '&lt;script&gt;' in page['entries'][-1]['html']
        with store.connect() as db:assert db.execute('SELECT count(*) FROM messages').fetchone()[0]==2
    print('PASS: persisted daily/weekly schedule, actual timer with fake source/Agent, single overdue run, pause/resume, scope revision reset, bounded follow-up memory, evidence rendering, pagination, failure checkpoint, source preservation. No cloud calls.')


if __name__=='__main__':main()
