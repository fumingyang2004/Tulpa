"""Watch is an ordinary scheduled question, not a message-audit batch. Isolated fixtures."""
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
from chatlocal.agent import run_agent
from chatlocal.store import Store
from chatlocal.watches import Watches
from chatlocal.agent_tools import ChatTools
from chatlocal.llm import validate_answer
from chatlocal.watch_view import entry_view
from chatlocal.watch_routes import install_watch_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='watch-query-') as tmp:
        folder=Path(tmp);store=Store(folder/'messages.sqlite3');watches=Watches(store);serial=0
        def put(n,group='course'):
            nonlocal serial
            rows=[]
            for _ in range(n):
                serial+=1
                rows.append(dict(platform='qq',conversation_id=group,conversation=group,sender='老师',sender_id='a',
                    timestamp='2026-09-01 10:00',source_id=str(serial),content='课程消息 '+str(serial),is_self=False))
            path=folder/'messages.json';path.write_text(json.dumps(dict(messages=rows)),encoding='utf-8');store.import_file(path)
        put(1);scope=[json.dumps(['qq','course'])];seen=[];after=None;fail=False
        def agent(s,plan,q,**kwargs):
            inspect.signature(run_agent).bind(s,plan,q,**kwargs)
            assert 'watch_context' not in kwargs and 'new_ids' not in json.dumps(kwargs['memory'])
            assert kwargs['memory']['read_only']
            seen.append((plan,q,kwargs))
            tools=ChatTools(s,plan)
            tools.execute('read_conversation',dict(platform='qq',conversation_id='course',order='newest',limit=1))
            claim=dict(text='查询所得的课程情况',evidence_ids=list(tools.messages))
            output=validate_answer(dict(claims=[claim]),list(tools.messages.values()))
            if after:after()
            try:
                yield dict(type='done',status='error' if fail else 'completed',record=dict(
                    result=dict(error='模拟失败') if fail else output,bundle=tools.bundle(),requests=2,usage=dict(total_tokens=100)))
            finally:kwargs['cancel'].set()
        card=watches.create('课程','关注课程',['qq'],scope)
        card=watches.check(card['id'],refresh=False,agent=agent)
        seen.clear();put(95);parent=threading.Event();after=lambda:put(1)
        card=watches.check(card['id'],refresh=False,agent=agent,cancelled=parent)
        assert not parent.is_set() and len(seen)==1
        row=watches.timeline(card['id'])[0][-1]
        assert row['record']['bundle']['message_count']==1,'Question should not require all 95 messages'
        assert card['last_result']['mode']=='scheduled_question' and 'checked' not in card['last_result']
        assert watches.message_counts(card)['pending_messages']==1,'Arrivals during a query belong to next trigger window'
        assert '查询所得的课程情况' in entry_view(store,row)['html']
        # Failed, cancelled and restarted queries preserve the last successful answer and watermark.
        after=None;fail=True;before=card.copy()
        try:watches.check(card['id'],refresh=False,agent=agent)
        except ValueError:pass
        else:raise AssertionError('Expected failure')
        card=watches.get(card['id']);assert card['cursor']==before['cursor'] and card['baseline']==before['baseline']
        fail=False;watches=Watches(Store(store.path));seen.clear()
        card=watches.check(card['id'],refresh=False,agent=agent)
        assert len(seen)==1 and watches.message_counts(card)['pending_messages']==0
        parent=threading.Event();after=parent.set;before=card.copy()
        try:watches.check(card['id'],refresh=False,agent=agent,cancelled=parent)
        except ValueError:pass
        else:raise AssertionError('Expected cancellation')
        assert watches.get(card['id'])['last_checked_at']==before['last_checked_at']
        after=None;seen.clear()
        # A time/manual trigger asks again even without new input (e.g. 'today').
        card=watches.check(card['id'],refresh=False,agent=agent)
        assert len(seen)==1 and watches.message_counts(card)['pending_messages']==0
        assert '再查一次' in seen[0][1] and seen[0][2]['memory']['previous_turns']
        # Count is only a trigger, scoped and persistent; time/count conditions are OR.
        card=watches.update(card['id'],'课程','关注课程',['qq'],scope,message_threshold=3)
        put(20,'other');put(2);assert not watches.due()
        put(1);assert watches.due()==[card['id']]
        assert watches.claim_due(card['id'])=='message_count' and not watches.claim_due(card['id'])
        watches=Watches(store);assert not watches.due() and watches.message_counts(watches.get(card['id']))['pending_messages']==3
        put(3);assert watches.claim_due(card['id'])=='message_count'
        card=watches.update(card['id'],'课程','关注课程',['qq'],scope,message_threshold=3,schedule_minutes=60)
        at=card['next_run_at']
        put(3);assert watches.claim_due(card['id'],at-10)=='message_count'
        assert not watches.due(at),'Count trigger resets timer; no duplicate at old deadline'
        assert watches.claim_due(card['id'],at+3600)=='scheduled' and not watches.claim_due(card['id'],at+3600)
        watches.pause(card['id'],True);put(3);assert not watches.due(at+99999)
        watches.pause(card['id'],False);assert watches.claim_due(card['id'])=='message_count'
        card=watches.update(card['id'],'课程','关注课程',['qq'],scope,message_threshold=3);put(3)
        finished=threading.Event();calls=[];real_check=Watches.check
        def scheduled(self,cid,**kwargs):
            calls.append(kwargs.copy());result=real_check(self,cid,**kwargs,agent=agent,syncer=lambda *a,**k:[]);finished.set();return result
        app=FastAPI();install_watch_routes(app,store)
        with patch.object(Watches,'check',scheduled),TestClient(app) as client:
            assert finished.wait(9),'Count timer did not fire'
            assert len(calls)==1 and calls[0]['trigger']=='message_count' and calls[0]['load_stickers'] is False
            for _ in range(40):
                if not client.get('/api/refresh-status').json()['running_jobs']:break
                time.sleep(.05)
            body=dict(title='课程',watch_query='关注课程',platforms=['qq'],conversations=scope,schedule_minutes=10080,message_threshold=100)
            response=client.put('/api/watch-cards/'+card['id'],json=body);assert response.status_code==200,response.text
            saved=response.json();assert saved['message_threshold']==100 and saved['schedule_minutes']==10080
            for bad in (-1,1.5,True,100001):
                assert client.put('/api/watch-cards/'+card['id'],json=dict(body,message_threshold=bad)).status_code==409
    print('PASS: ordinary query reads only relevant evidence, one call, stable scope/snapshot, failure/cancel/restart, repeat question without new input, count/time triggers and actual timer/API. Synthetic only.')


if __name__=='__main__':main()
