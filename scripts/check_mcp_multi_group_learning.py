"""One credential, two group libraries: synthetic MCP/WS and durable store checks.

No real QQ writes, history access or model requests. --package checks an installed
runtime with temporary state, using the same fixtures as the existing MCP suite.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from test_environment import system_environment
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
from check_mcp_language_learning import Clock,ROOT,expression_fixture,review_fixture,jargon_fixture
from check_mcp_reactions import ReactionBot,Events
from chatlocal.mcp_language_store import LanguageStore,LearningError,digest
from chatlocal.mcp_access import AccessDenied
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.store import Store
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def rejected(code,fn):
    try:fn()
    except LearningError as exc:assert exc.code==code,(exc.code,code)
    else:raise AssertionError('expected '+code)


def durable():
    clock=Clock()
    a=dict(scope='qq:111:group:222',gid='one-credential',sid='session-a',epoch='epoch-1')
    b=dict(a,scope='qq:111:group:223',sid='session-b')
    with tempfile.TemporaryDirectory(prefix='tulpa-multigroup-store-') as tmp:
        path=Path(tmp)/'language.sqlite3';store=LanguageStore(path,clock=clock)
        with store.db() as db:
            db.execute('INSERT INTO pins VALUES(?,?)',(a['gid'],a['scope']))
            db.execute('INSERT INTO contexts VALUES(?,?,?)',(digest('same-host'),a['scope'],a['gid']))
        for binding in (a,b):store.attach(binding)
        def feed(binding,tag='initial'):
            for i in range(10):store.observe(binding,dict(peer=True,source_id=f'{tag}-{i}',sent_at=1000,
                at=clock(),text=f'云朵开机 {binding["sid"]} 合成消息{i}'))
        for binding in (a,b):feed(binding)
        clock.advance(30)
        with ThreadPoolExecutor(4) as pool:
            claims=list(pool.map(lambda binding:(binding,store.claim(binding,'same-host')),[a,b,a,b]))
        assert sum(t is not None for _,t in claims)==2
        ta=next(t for binding,t in claims if binding==a and t)
        tb=next(t for binding,t in claims if binding==b and t)
        assert ta['library_key']==a['scope'] and tb['library_key']==b['scope']
        assert ta['material']['messages'][0]['source_id']!=tb['material']['messages'][0]['source_id']
        def submit(binding,t,result):return store.submit(binding,t['job_id'],t['lease'],'call-'+t['stage'],result)
        ra=dict(expressions=[expression_fixture(m,'情境'+str(i),'描述'+str(i)) for i,m in enumerate(ta['material']['messages'][:3])])
        rb=dict(expressions=[expression_fixture(m,'别群情境'+str(i),'别群描述'+str(i)) for i,m in enumerate(tb['material']['messages'][:3])])
        rejected('task_unavailable',lambda:submit(b,ta,ra))
        rejected('invalid_source',lambda:submit(b,tb,ra))
        for binding,t,result in ((a,ta,ra),(b,tb,rb)):
            submit(binding,t,result)
            assert submit(binding,t,result)['state']=='already_completed'
            word=store.claim(binding,'same-host');assert word['stage']=='extract_jargon'
            submit(binding,word,jargon_fixture(word))
            review=store.claim(binding,'same-host');assert review['stage']=='review'
            submit(binding,review,review_fixture(review))
        original=store.status(a,records=True);other=store.status(b,records=True)
        assert original['expression_count']==other['expression_count']==3
        assert original['jargon'][0]['count']==other['jargon'][0]['count']==1
        eid=original['expressions'][0]['id'];jid=original['jargon'][0]['id']
        store.manage(a,'expressions',eid,enabled=False)
        store.manage(a,'jargon',jid,meaning='合成群专用释义')
        rejected('record_unavailable',lambda:store.manage(b,'expressions',eid,enabled=False))
        store.cancel(dict(sid=a['sid']));a2=dict(a,gid='new-credential',sid='session-a2')
        store.attach(a2);feed(a2)
        assert store.status(a2)['buffered_messages']==0
        assert store.status(a2,records=True)['expressions']==store.status(a,records=True)['expressions']
        store.configure(a2,enabled=False)
        assert store.status(b)['enabled']
        clock.advance(31);feed(b,'second');job=store.claim(b,'same-host')
        store.cancel(dict(sid=a2['sid']))
        assert submit(b,job,dict(expressions=[]))['state']=='completed'
        new_account=dict(a,scope='qq:999:group:222',sid='other-account')
        store.attach(new_account);assert store.status(new_account)['expression_count']==0
        before=store.status(a2,records=True)
        for _ in range(2):
            store=LanguageStore(path,clock=clock)
            a2['epoch']='restarted';b['epoch']='restarted'
            store.attach(a2);store.attach(b)
            saved=store.status(a2,records=True)
            assert not saved['enabled'] and saved['expressions']==before['expressions'] and saved['jargon']==before['jargon']
            assert saved['calls_last_hour']==before['calls_last_hour']
            rejected('task_unavailable',lambda:submit(b,job,dict(expressions=[])))
        print('PASS shared credential, legacy pins ignored, simultaneous leases, cross-group rejection, shared library, dedupe, account isolation, restart/pause/manual state')


async def wire(service,token):
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+token},trust_env=False,timeout=15) as http:
        async with streamable_http_client(service.status()['url'],http_client=http) as (read,write,_):
            async with ClientSession(read,write) as client:
                await client.initialize()
                schema=next(t for t in (await client.list_tools()).tools if t.name=='get_chat_learning')
                assert 'conversation_id' in schema.inputSchema['properties']
                for group in (222,223):
                    result=(await client.call_tool('get_chat_learning',dict(conversation_id=f'111:group:{group}',records=True))).structuredContent
                    assert result['library_key']==f'qq:111:group:{group}' and result['expression_count']==3


def integration():
    events=Events();ReactionBot.sent=[];ReactionBot.writes=[];ReactionBot.intercept=None
    server=ThreadingHTTPServer(('127.0.0.1',0),ReactionBot)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    ports={events.port,server.server_port};connect=socket.create_connection
    def restrict(address,*args,**kw):
        assert address[0] in ('127.0.0.1','localhost','::1') and address[1] in ports,address
        return connect(address,*args,**kw)
    try:
        with tempfile.TemporaryDirectory(prefix='tulpa-multigroup-mcp-') as tmp,patch.dict('os.environ',system_environment(),clear=True),patch('socket.create_connection',restrict):
            root=Path(tmp);history=Store(root/'chats.sqlite3')
            (root/'.env').write_text(f'REPLY_ONEBOT_URL=http://127.0.0.1:{server.server_port}\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:{events.port}\nREPLY_ONEBOT_WS_TOKEN=event-fixture\n','utf-8')
            with patch('chatlocal.onebot.ROOT',root),patch('chatlocal.message_sender.ROOT',root):
                service=install_mcp_routes(app:=FastAPI(),history);tools=service.tools;chat=tools.chat;access=service.access
                with TestClient(app) as ui:
                    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                    ports.add(port);access.configure(True,port);service.start()
                    cfg=dict(name='shared-connection',platforms=['qq'],chat=True,send=True,conversations=[['qq',f'111:group:{g}'] for g in (222,223)])
                    credential=access.create(cfg);serial=0
                    def call(name,**args):return tools.call(credential['token'],name,args,threading.Event()).structuredContent
                    def key():
                        nonlocal serial
                        serial+=1;return f'multi-fixture-{serial}'
                    assert call('get_chat_learning',conversation_id='111:group:222')['expression_count']==0
                    assert not chat.listed()
                    assert call('get_chat_learning')['error_code']=='invalid_learning_target'
                    sessions={g:call('start_chat_session',conversation_id=f'111:group:{g}',persona_preset='little_whale_v2',idempotency_key=key())['session']['id'] for g in (222,223)}
                    timer=[time.time()];chat.learning.store.clock=lambda:timer[0]
                    with patch.object(history,'connect',side_effect=AssertionError('history accessed')):
                        for group in sessions:
                            for i in range(10):chat.receive(dict(post_type='message',message_type='group',self_id=111,group_id=group,message_id=-100-i,user_id=333,
                                time=int(time.time()),sender={'user_id':333},message=[dict(type='text',data={'text':f'云朵开机 合成群{group} {i}'})]))
                        timer[0]+=31;chat.learning.tick()
                        for group,sid in sessions.items():
                            packet=call('wait_chat_messages',session_id=sid,timeout_seconds=1,quiet_seconds=0)
                            decision=call('plan_chat_reply',session_id=sid,batch_id=packet['input_batch']['id'],action='silence',intent='只观察合成群友',expressions=[],idempotency_key=key())
                            assert decision['state']=='SILENT'
                            args=dict(decision['next_call']['arguments'],timeout_seconds=1,quiet_seconds=0)
                            for expected in ('extract','extract_jargon','review'):
                                wake=call('wait_chat_messages',**args);assert wake['event']=='learning_ready',wake
                                task=call('claim_chat_learning',**wake['next_call']['arguments'],context_id='one-host-context')['task']
                                assert task['stage']==expected and task['library_key']==f'qq:111:group:{group}'
                                if expected=='extract':result=dict(expressions=[expression_fixture(m,'合成场景'+str(i),'表达'+str(i)) for i,m in enumerate(task['material']['messages'][:3])])
                                elif expected=='extract_jargon':result=jargon_fixture(task)
                                else:result=review_fixture(task)
                                foreign=call('submit_chat_learning',session_id=sessions[223 if group==222 else 222],job_id=task['job_id'],lease=task['lease'],invocation_id=key(),result=result)
                                assert foreign['error_code']=='task_unavailable'
                                completed=call('submit_chat_learning',**task['submit_call']['arguments'],invocation_id=key(),result=result)
                                assert completed['state'] in ('stage_completed','completed')
                    groups=ui.get('/api/mcp/chat-overview').json()['groups']
                    assert len(groups)==2 and all(g['learning']['expression_count']==3 and not g.get('error') for g in groups)
                    asyncio.run(wire(service,credential['token']))
                    assert call('get_chat_learning',conversation_id='999:group:222').get('expression_count') is None
                    assert call('get_chat_learning',conversation_id='111:group:999').get('expression_count') is None
                    assert call('get_chat_learning',session_id=sessions[222],conversation_id='111:group:222')['error_code']=='invalid_learning_target'
                    for sid in sessions.values():call('stop_chat_session',session_id=sid)
                    chat.receiver.stop()
                    saved=call('get_chat_learning',conversation_id='111:group:222',records=True)
                    old=credential;access.revoke(old['id']);credential=access.create(cfg)
                    assert call('get_chat_learning',conversation_id='111:group:222',records=True)['expressions']==saved['expressions']
                    try:tools.call(old['token'],'get_chat_learning',dict(conversation_id='111:group:222'),threading.Event())
                    except AccessDenied:pass
                    else:raise AssertionError('revoked credential regained access')
                    assert not ReactionBot.sent and not ReactionBot.writes
                    print(json.dumps(dict(status='passed',groups=2,shared_grants=1,synthetic_peer_messages=20,model_stages=6,
                        historical_reads=0,qq_writes=0,mcp_http='passed',local_overview='both libraries visible',offline_read='passed',revoke='denied'),ensure_ascii=False))
    finally:server.shutdown();events.close()


if __name__=='__main__':
    assert Path(__import__('chatlocal.mcp_language_learning',fromlist=['x']).__file__).resolve()==ROOT/'chatlocal/mcp_language_learning.py'
    durable();integration()
