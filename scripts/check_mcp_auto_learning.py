"""Automatic chat/learning loop, migration and scope isolation. Synthetic only.

The simulated host follows returned next_call links; it never enables learning
or manually chooses a learning stage. A fake clock drives batch/retry deadlines.
All network targets are loopback fixtures; model results are deterministic.
"""
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).parent))
from check_mcp_reactions import ReactionBot,Events,ROOT
from check_mcp_language_learning import Clock,expression_fixture,review_fixture,ground_fixture_rows
from chatlocal.store import Store
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.mcp_language_store import LanguageStore,LearningError
from fastapi import FastAPI
from fastapi.testclient import TestClient


def migration():
    clock=Clock();scope='qq:111:group:222'
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/'mcp-language.sqlite3';store=LanguageStore(path,clock=clock)
        b=dict(scope=scope,gid='old-a',sid='old-session',epoch='old-process')
        store.attach(b)
        with store.db() as db:
            db.execute("UPDATE settings SET enabled=0,allow_degraded=0,last_learned=0")
            db.execute("INSERT INTO settings(scope,gid,enabled,allow_degraded,since,last_batch) VALUES(?,'old-b',1,1,1,1)",(scope,))
            for i in range(9):
                db.execute("INSERT INTO expressions(id,scope,situation,style,count,updated,independence) VALUES(?,?,?,?,7,1,'degraded')",(str(i),scope,'场景'+str(i),'表达'+str(i)))
            db.execute("INSERT INTO jargon(id,scope,term,folded,count,meaning,is_jargon,manual,enabled,updated) VALUES('word',?,'合成词','合成词',8,'人工释义',1,1,0,1)",(scope,))
            for table in ('language_libraries','language_runs','language_migrations','learning_failures'):db.execute('DROP TABLE '+table)
        store=LanguageStore(path,clock=clock)
        assert len(list(Path(tmp).glob('*.before-auto-*.sqlite3')))==1
        b.update(gid='new-grant',sid='new-session',epoch='new-process');store.attach(b)
        saved=store.status(b,records=True)
        assert not saved['enabled'] and saved['library']['last_completed_at'] is None
        assert len(saved['expressions'])==9 and all(e['count']==7 for e in saved['expressions'])
        assert saved['jargon'][0]['manual'] and not saved['jargon'][0]['enabled']
        store.configure(b,enabled=True)
        assert not store.context(b,'nine',[],'')['candidates']
        assert saved['library']['checked_expressions']==0 and saved['library']['pending_expressions']==9
        ground_fixture_rows(store,scope)
        assert not store.context(b,'nine-verified',[],'')['candidates']
        with store.db() as db:db.execute("INSERT INTO expressions(id,scope,situation,style,count,updated,independence) VALUES('tenth',?,'第十个','方式',2,1,'degraded')",(scope,))
        ground_fixture_rows(store,scope)
        candidates=store.context(b,'ten',[],'')['candidates'];assert 0<len(candidates)<=10
        store.select(b,'ten',[e['id'] for e in candidates[:5]])
        for i in range(10):store.observe(b,dict(source_id=i,sent_at=clock(),text='合成材料',at=clock(),peer=True))
        clock.advance(30);wake=store.readiness(b);assert wake['ready']
        with ThreadPoolExecutor(2) as pool:claims=list(pool.map(lambda _:store.claim(b,'context-a',wake_id=wake['wake_id']),range(2)))
        assert sum(t is not None for t in claims)==1
        t=next(t for t in claims if t)
        failed=store.submit(b,t['job_id'],t['lease'],'fake-failure',failure='timeout')
        assert store.submit(b,t['job_id'],t['lease'],'fake-failure',failure='timeout')['already_recorded']
        assert store.readiness(b)['reason']=='retry_cooldown'
        clock.advance(15);new_wake=store.readiness(b);assert new_wake['ready'] and new_wake['wake_id']!=wake['wake_id']
        assert not store.claim(b,'context-a',wake_id=wake['wake_id'])
        retry=store.claim(b,'context-a',wake_id=new_wake['wake_id']);assert retry
        store.cancel(dict(sid=b['sid']))
        try:store.submit(b,retry['job_id'],retry['lease'],'late-call',dict(expressions=[],jargon=[]))
        except LearningError as e:assert e.code=='task_unavailable'
        else:raise AssertionError('late result accepted')
        store=LanguageStore(path,clock=clock);b.update(gid='third-grant',sid='third-session',epoch='third-process');store.attach(b)
        state=store.status(b,records=True)
        assert state['enabled'] and state['expression_count']==10 and state['jargon']==saved['jargon']
        assert len(list(Path(tmp).glob('*.before-auto-*.sqlite3')))==1
        for changed in ('qq:999:group:222','qq:111:group:223'):
            foreign=dict(b,scope=changed,gid=changed,sid=changed);store.attach(foreign)
            assert store.status(foreign)['expression_count']==0 and store.status(foreign)['jargon_count']==0
        # Same native event+time in a new grant never increments a second time.
        for i in range(10):store.observe(b,dict(source_id=i,sent_at=1000.,text='合成材料',at=clock(),peer=True))
        assert store.status(b)['buffered_messages']==0
        print('PASS migration backup/rerun, 9/10 threshold, human changes/pause, new grant/restart, scope isolation, atomic claim, retry receipts')


def main():
    assert Path(__import__('chatlocal.mcp_language_learning',fromlist=['ChatLearning']).__file__).resolve()==ROOT/'chatlocal/mcp_language_learning.py'
    migration()
    events=Events();ReactionBot.sent=[];ReactionBot.writes=[];ReactionBot.intercept=None
    upstream=ThreadingHTTPServer(('127.0.0.1',0),ReactionBot)
    threading.Thread(target=upstream.serve_forever,daemon=True).start()
    ports={events.port,upstream.server_port};connect=socket.create_connection
    def restrict(address,*args,**kwargs):
        assert address[0] in ('localhost','127.0.0.1','::1') and address[1] in ports,address
        return connect(address,*args,**kwargs)
    try:
        with tempfile.TemporaryDirectory() as tmp,patch.dict('os.environ',{},clear=True),patch('socket.create_connection',restrict):
            root=Path(tmp);data=Store(root/'chats.sqlite3')
            (root/'.env').write_text(f'REPLY_ONEBOT_URL=http://127.0.0.1:{upstream.server_port}\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:{events.port}\nREPLY_ONEBOT_WS_TOKEN=event-fixture\n','utf-8')
            with patch('chatlocal.onebot.ROOT',root),patch('chatlocal.message_sender.ROOT',root):
                app=FastAPI();service=install_mcp_routes(app,data);tools=service.tools;access=service.access;chat=tools.chat
                with TestClient(app) as ui:
                    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                    ports.add(port);access.configure(True,port);service.start()
                    timer=[time.time()];chat.learning.store.clock=lambda:timer[0]
                    cfg=dict(name='auto-fixture',platforms=['qq'],chat=True,send=True,conversations=[['qq','111:group:222']])
                    g=access.create(cfg);headers={'X-ChatWeave-UI':'1'};serial=0
                    def call(name,**args):return tools.call(g['token'],name,args,threading.Event()).structuredContent
                    def key():
                        nonlocal serial
                        serial+=1;return f'fixture-{serial:04}'
                    body=dict(connection_id=g['id'],conversation_id='111:group:222',persona_preset='little_whale_v2',idempotency_key=key())
                    assert ui.post('/api/mcp/chats/start',json=body).status_code==403
                    click=ui.post('/api/mcp/chats/start',json=body,headers=headers).json();sid=click['session_id']
                    overview=ui.get('/api/mcp/chat-overview').json()['groups'][0]
                    assert overview['learning_state']=='等待 Agent',overview
                    # Usual host entrance resumes the UI session, without any learning command.
                    start=call('start_chat_session',conversation_id='111:group:222',persona_preset='little_whale_v2',idempotency_key=key())
                    assert start['session']['id']==sid and call('get_chat_learning',session_id=sid)['enabled']
                    def receive(n,base=100):
                        for i in range(n):chat.receive(dict(post_type='message',message_type='group',self_id=111,group_id=222,message_id=-base-i,user_id=333,
                            time=int(time.time()),sender={'user_id':333},message=[dict(type='text',data={'text':f'合成表达 云朵开机 {i}'})]))
                    def follow(link):
                        args=dict(link['arguments'])
                        if link['tool']=='wait_chat_messages':args.update(timeout_seconds=1,quiet_seconds=0)
                        return call(link['tool'],**args)
                    def silence(packet):
                        assert packet['event']=='messages'
                        decision=call('plan_chat_reply',session_id=sid,batch_id=packet['input_batch']['id'],idempotency_key=key(),action='silence',intent='合成场景潜水',expressions=[])
                        assert decision['state']=='SILENT' and decision['next_call']['tool']=='wait_chat_messages',decision
                        return decision['next_call']
                    receive(10)
                    packet=call('wait_chat_messages',session_id=sid,timeout_seconds=1,quiet_seconds=0)
                    link=silence(packet);timer[0]+=29
                    assert follow(link)['event']=='idle'
                    # Clock, not claim or an eleventh message, seals the batch.
                    timer[0]+=1;chat.learning.tick()
                    with chat.learning.store.db() as db:assert db.execute('SELECT count(*) FROM batches').fetchone()[0]==1
                    began=time.perf_counter();wake=follow(link);wake_ms=(time.perf_counter()-began)*1000
                    assert wake['event']=='learning_ready' and wake['continue_waiting']
                    t=follow(wake['next_call'])['task'];assert t['stage']=='extract'
                    assert follow(wake['next_call'])['state']=='no_task'
                    began=time.perf_counter();busy=follow(link);lease_ms=(time.perf_counter()-began)*1000
                    assert busy['event']=='idle' and lease_ms>=900
                    # Model works on one stage while a fresh message arrives.
                    receive(1,500)
                    ids=[m['source_id'] for m in t['material']['messages']]
                    result=dict(expressions=[expression_fixture(t['material']['messages'][i],'情境'+str(i),'方式'+str(i)) for i in range(3)],jargon=[dict(term='云朵开机',source_id=ids[0])])
                    submitted=call('submit_chat_learning',**t['submit_call']['arguments'],invocation_id=key(),result=result)
                    assert call('claim_chat_learning',session_id=sid)['reason']=='return_to_wait'
                    packet=follow(submitted['next_call']);assert packet['event']=='messages'
                    link=silence(packet);wake=follow(link);assert wake['event']=='learning_ready'
                    t2=follow(wake['next_call'])['task'];assert t2['stage']=='review'
                    checked=review_fixture(t2)
                    final=call('submit_chat_learning',**t2['submit_call']['arguments'],invocation_id=key(),result=checked)
                    assert call('submit_chat_learning',**t2['submit_call']['arguments'],invocation_id=key(),result=checked)['state']=='already_completed'
                    assert final['state']=='completed';idle=follow(final['next_call']);assert idle['event']=='idle'
                    # Empty wait is bounded, no immediate no-task spin.
                    began=time.perf_counter();idle=follow(final['next_call']);idle_ms=(time.perf_counter()-began)*1000
                    assert idle['event']=='idle' and 900<=idle_ms<2500
                    library=ui.get('/api/mcp/chat-library',params={'conversation_id':'111:group:222'}).json()
                    assert library['library']['checked_expressions']==3 and not library['library']['expressions_in_use']
                    assert library['library']['known_jargon']==0 and library['library']['observing_jargon']==1
                    assert library['run']['completed_batches']==1 and library['library']['last_completed_at']
                    # Budget cannot be reset by reconnecting/new grant.
                    budget_change=dict(conversation_id='111:group:222',change=dict(hourly_calls=2))
                    assert ui.post('/api/mcp/chat-library',json=budget_change,headers=headers).json()['hourly_call_budget']==2
                    timer[0]+=31;receive(10,600)
                    packet=follow(final['next_call']);link=silence(packet)
                    began=time.perf_counter();idle=follow(link);budget_ms=(time.perf_counter()-began)*1000
                    assert idle['event']=='idle' and budget_ms>=900
                    assert ui.get('/api/mcp/chat-overview').json()['groups'][0]['learning_state']=='预算暂停'
                    budget_change['change']['hourly_calls']=8
                    assert ui.post('/api/mcp/chat-library',json=budget_change,headers=headers).json()['hourly_call_budget']==8
                    wake=follow(link);t=follow(wake['next_call'])['task']
                    failed=call('submit_chat_learning',**t['submit_call']['arguments'],invocation_id=key(),failure='timeout')
                    began=time.perf_counter();cool=follow(failed['next_call']);cooldown_ms=(time.perf_counter()-began)*1000
                    assert cool['event']=='idle' and cooldown_ms>=900
                    timer[0]+=15;wake=follow(failed['next_call']);t=follow(wake['next_call'])['task']
                    call('stop_chat_session',session_id=sid)
                    assert call('submit_chat_learning',**t['submit_call']['arguments'],invocation_id=key(),result=result).get('state')!='completed'
                    chat.receiver.stop()
                    retained=ui.get('/api/mcp/chat-library',params={'conversation_id':'111:group:222'}).json()
                    assert retained['expression_count']==3 and retained['jargon_count']==1
                    assert ui.get('/api/mcp/chat-overview').json()['groups'][0]['learning_state']=='积累已保留'
                    access.revoke(g['id']);old=g;g=access.create(cfg)
                    start=call('start_chat_session',conversation_id='111:group:222',persona_preset='little_whale_v2',idempotency_key=key());sid=start['session']['id']
                    current=call('get_chat_learning',session_id=sid)
                    assert current['enabled'] and current['expression_count']==3 and current['jargon_count']==1 and current['run']['completed_batches']==0
                    assert current['hourly_call_budget']==8
                    from chatlocal.mcp_access import AccessDenied
                    try:tools.call(old['token'],'get_chat_learning',dict(session_id=sid),threading.Event())
                    except AccessDenied:pass
                    else:raise AssertionError('revoked token regained access')
                    # UI pause persists; same-account new grants cannot undo it.
                    change=dict(conversation_id='111:group:222',change=dict(enabled=False))
                    assert ui.post('/api/mcp/chat-library',json=change,headers=headers).json()['enabled'] is False
                    call('stop_chat_session',session_id=sid);g=access.create(cfg)
                    sid=call('start_chat_session',conversation_id='111:group:222',persona_preset='little_whale_v2',idempotency_key=key())['session']['id']
                    assert not call('get_chat_learning',session_id=sid)['enabled']
                    assert not ReactionBot.sent and not ReactionBot.writes
                    cost=dict(wake_ms=round(wake_ms,2),empty_wait_ms=round(idle_ms,2),budget_wait_ms=round(budget_ms,2),lease_wait_ms=round(lease_ms,2),cooldown_wait_ms=round(cooldown_ms,2),completed_model_stages=2,
                              expression_count=3,jargon_candidates=1,extra_model_calls=0,qq_writes=0,
                              host_boundary='one in-flight stage; lease 120s, host generation cannot be interrupted')
                    print('PASS start→silence→timer→wait→one stage→new chat first→review→persist→stop/revoke/new grant')
                    print(json.dumps(cost,ensure_ascii=False))
    finally:upstream.shutdown();events.close()


if __name__=='__main__':main()
