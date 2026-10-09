"""Persistent chat acceptance: real MCP HTTP + isolated OneBot, never real QQ."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from websockets.sync.server import serve
from websockets.exceptions import ConnectionClosed

ROOT=Path(sys.argv[sys.argv.index('--package')+1]).resolve() if '--package' in sys.argv else Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from chatlocal.store import Store
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.mcp_chat import MCPChat, CHAT_TOOLS
from chatlocal import mcp_chat_prompts as prompts
from chatlocal.onebot import invalidate_availability


class OneBot(BaseHTTPRequestHandler):
    sent=[]
    block=False
    uncertain=False
    entered=threading.Event()
    release=threading.Event()
    def log_message(self,*args):pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        action=self.path[1:]
        if action=='get_login_info':data={'user_id':111}
        elif action=='get_group_list':data=[{'group_id':g,'group_name':'Fixture group '+str(g)} for g in range(222,231)]
        elif action=='get_group_info':
            if self.block:self.entered.set();self.release.wait(8)
            data={'group_id':body['group_id'],'group_name':'Fixture group '+str(body['group_id'])}
        elif action=='send_group_msg':
            self.sent.append(body)
            if self.uncertain:self.send_response(503);self.end_headers();return
            data={'message_id':700+len(self.sent)}
        else:raise AssertionError(action)
        raw=json.dumps({'status':'ok','retcode':0,'data':data}).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)


class Events:
    def __init__(self):
        self.connections=set();self.lock=threading.Lock();self.last=None
        self.server=serve(self.handle,'127.0.0.1',0)
        self.port=self.server.socket.getsockname()[1]
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def handle(self,ws):
        if ws.request.headers.get('Authorization')!='Bearer event-fixture':ws.close();return
        with self.lock:self.connections.add(ws)
        try:
            ws.send(json.dumps(dict(post_type='meta_event',meta_event_type='lifecycle',self_id=111)))
            for _ in ws:pass
        finally:
            with self.lock:self.connections.discard(ws)
    def send(self,event):
        self.last=event
        with self.lock:connections=list(self.connections)
        for ws in connections:
            try:ws.send(json.dumps(event))
            except ConnectionClosed:pass
    def close_connections(self):
        with self.lock:connections=list(self.connections)
        for ws in connections:ws.close()
    def close(self):
        self.close_connections();self.server.shutdown();self.thread.join(3)


def eventually(check):
    deadline=time.monotonic()+5
    while not check():
        if time.monotonic()>deadline:raise AssertionError('Timed out waiting for fixture condition')
        time.sleep(.02)


async def protocol(service, token, session_ids, add, ui):
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+token},timeout=65,trust_env=False) as http:
        async with streamable_http_client(service.status()['url'],http_client=http) as (read,write,_):
            async with ClientSession(read,write) as client:
                await client.initialize()
                names={t.name:t for t in (await client.list_tools()).tools}
                assert CHAT_TOOLS-{'react_to_chat_message'}<=names.keys()
                assert 'react_to_chat_message' not in names
                assert names['send_chat_message'].annotations.openWorldHint
                waits=[asyncio.create_task(client.call_tool('wait_chat_messages',{'session_id':sid,'timeout_seconds':30,'quiet_seconds':0})) for sid in session_ids]
                for _ in range(100):
                    if len(service.tools.chat.waiters)==len(session_ids):break
                    await asyncio.sleep(.02)
                assert len(service.tools.chat.waiters)==len(session_ids)
                # More than the four normal slots wait simultaneously; all ordinary
                # reads, direct sends and stop controls must still be available.
                started=time.monotonic()
                result=await client.call_tool('get_data_status',{})
                assert not result.isError and time.monotonic()-started<3,result
                result=await client.call_tool('send_qq_message',dict(conversation_id='111:group:230',text='independent tool',idempotency_key='ordinary-during-wait'))
                assert result.structuredContent['state']=='SUCCEEDED',result
                add(222,'http-new')
                result=await asyncio.wait_for(waits[0],5)
                assert result.structuredContent['messages'][-1]['content']=='http-new',result
                start=time.monotonic()
                result=await client.call_tool('stop_chat_session',{'session_id':session_ids[1]})
                assert result.structuredContent['event']=='stopped'
                assert (await asyncio.wait_for(waits[1],3)).structuredContent['event']=='stopped'
                assert time.monotonic()-start<2
                response=await asyncio.to_thread(ui.post,'/api/mcp/chats/all/stop',json={},headers={'X-ChatWeave-UI':'1'})
                assert response.status_code==200
                for task in waits[2:]:assert (await asyncio.wait_for(task,3)).structuredContent['event']=='stopped'


async def hot_personas_protocol(service, token, directory):
    # A single initialized MCP client keeps its original tool schema while cards
    # are added/changed/deleted. Neither the service nor the client is restarted.
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+token},timeout=20,trust_env=False) as http:
        async with streamable_http_client(service.status()['url'],http_client=http) as (read,write,_):
            async with ClientSession(read,write) as client:
                await client.initialize()
                schemas={tool.name:tool for tool in (await client.list_tools()).tools}
                assert 'enum' not in schemas['start_chat_session'].inputSchema['properties']['persona_preset']
                async def call(name, *, expect_error=False, **args):
                    response=await client.call_tool(name,args)
                    assert bool(response.isError)==expect_error,response
                    return response.structuredContent
                baseline=await call('list_chat_personas')
                assert baseline['count']==1 and not baseline['warnings']
                card=directory/'夜猫子.md'
                card.write_text('# 夜猫子\n\n电影迷，话不多。\n','utf-8')
                latest=await call('list_chat_personas')
                assert latest['count']==2 and {p['id'] for p in latest['personas']}=={'little_whale','夜猫子'}
                args=dict(conversation_id='111:group:229',persona_preset='夜猫子',idempotency_key='hot-persona-original')
                first=await call('start_chat_session',**args)
                sid=first['session']['id'];snapshot=first['chat_prompt']['persona'];version=first['chat_prompt']['prompt_version']
                assert snapshot==card.read_text('utf-8') and first['session']['persona_name']=='夜猫子'
                card.write_text('# 白鹭君\n\n音乐迷，慢慢说。\n','utf-8')
                latest=await call('list_chat_personas')
                assert next(p for p in latest['personas'] if p['id']=='夜猫子')['name']=='白鹭君'
                second=await call('start_chat_session',**(args|dict(conversation_id='111:group:230',idempotency_key='hot-persona-updated')))
                assert second['chat_prompt']['persona']==card.read_text('utf-8') and second['session']['persona_name']=='白鹭君'
                assert second['chat_prompt']['prompt_version']!=version
                card.unlink()
                assert (await call('list_chat_personas'))['count']==1
                resumed=await call('start_chat_session',**args)
                assert resumed['session']['id']==sid and resumed['chat_prompt']['persona']==snapshot
                assert resumed['session']['persona_name']=='夜猫子' and resumed['chat_prompt']['prompt_version']==version
                saved=await call('get_chat_session',session_id=sid)
                assert saved['chat_prompt']['persona']==snapshot and saved['chat_prompt']['persona_name']=='夜猫子'
                rejected=await call('start_chat_session',expect_error=True,**(args|dict(idempotency_key='hot-persona-deleted')))
                assert rejected['error_code']=='chat_rejected'
                await call('stop_chat_session',session_id=sid)
                await call('stop_chat_session',session_id=second['session']['id'])


def main():
    (ROOT/'.tmp').mkdir(exist_ok=True)
    events=Events()
    upstream=ThreadingHTTPServer(('127.0.0.1',0),OneBot)
    threading.Thread(target=upstream.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='mcp-chat-') as tmp,patch.dict('os.environ',{},clear=True):
            folder=Path(tmp);store=Store(folder/'chats.sqlite3');serial=0
            persona_dir=folder/'personas';persona_dir.mkdir()
            for name in ('little_whale.md','little_whale.source.json','behavior.md'):
                shutil.copy2(prompts.ROOT/name,persona_dir/name)
            def native_add(group,content,*,own=False,old=False):
                nonlocal serial
                serial+=1
                record=dict(platform='qq',conversation_id=f'111:group:{group}',conversation='Fixture group',conversation_type='group',
                            sender='Member',sender_id='111' if own else '333',is_self=own,source_id='row-'+str(serial),
                            timestamp='2020-01-01' if old else int(time.time()*1000),content=content)
                file=folder/'input.json';file.write_text(json.dumps([record]),'utf-8');store.import_file(file)
                with store.connect() as db:return db.execute('SELECT max(id) FROM messages').fetchone()[0]
            for group in range(222,231):native_add(group,'initial')
            native_add(999,'FORBIDDEN')
            (folder/'.env').write_text(f'REPLY_ONEBOT_URL=http://127.0.0.1:{upstream.server_port}\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:{events.port}\nREPLY_ONEBOT_WS_TOKEN=event-fixture\n','utf-8')
            app=FastAPI();service=install_mcp_routes(app,store);access=service.access;tools=service.tools;headers={'X-ChatWeave-UI':'1'}
            with patch('chatlocal.onebot.ROOT',folder),patch('chatlocal.message_sender.ROOT',folder),patch.object(prompts,'ROOT',persona_dir),TestClient(app) as ui:
                invalidate_availability()
                with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                assert ui.put('/api/mcp',json=dict(enabled=True,port=port),headers=headers).json()['running']
                config=dict(name='chat-fixture',platforms=['qq'],conversations=[['qq',f'111:group:{g}'] for g in range(222,231)])
                def grant(**flags):
                    response=ui.post('/api/mcp/connections',json=config|flags,headers=headers)
                    assert response.status_code==200,response.text
                    return response.json()
                readonly=grant();writer=grant(send=True);g=grant(send=True,chat=True);other=grant(send=True,chat=True)
                assert ui.post('/api/mcp/connections',json=config|dict(chat=True),headers=headers).status_code==400
                def call(name,*,connection=g,cancel=None,**args):return tools.call(connection['token'],name,args,cancel or threading.Event()).structuredContent
                for connection in (readonly,writer):
                    assert not CHAT_TOOLS & {s['name'] for s in tools.schemas(access.authorize(connection['token']))}
                from chatlocal.agent_tools import SCHEMAS
                assert not CHAT_TOOLS & {s['name'] for s in SCHEMAS},'Internal Harness received chat tools'
                from chatlocal.mcp_connect import codex_config
                import tomllib
                entries=tomllib.loads(codex_config('http://127.0.0.1:18777/mcp','fixture',{'chat':True,'send':True}))['mcp_servers']['tulpa']['tools']
                assert CHAT_TOOLS-{'react_to_chat_message'}<=entries.keys()
                assert 'react_to_chat_message' not in entries
                hot=grant(send=True,chat=True)
                asyncio.run(hot_personas_protocol(service,hot['token'],persona_dir))
                presets=call('list_chat_personas')['personas']
                assert [p['id'] for p in presets]==['little_whale']
                assert presets[0]['name']=='小鲸鱼'
                assert call('start_chat_session',conversation_id='111:group:223',idempotency_key='missing-persona')['error_code']=='chat_rejected'
                whale_args=dict(conversation_id='111:group:223',persona_preset='little_whale',persona='少用问号',idempotency_key='whale-preset')
                whale=call('start_chat_session',**whale_args);wid=whale['session']['id']
                assert whale['session']['persona_name']=='小鲸鱼' and '少用问号' in whale['chat_prompt']['persona']
                assert 'behavior' in whale['chat_prompt']
                # Repeated start keeps the original snapshot even after packaged
                # preset text changes, including its idempotency signature.
                with patch.object(prompts,'persona_catalog',side_effect=AssertionError('Retry must use snapshot, not files')):
                    resumed=call('start_chat_session',**whale_args)
                assert resumed['session']['id']==wid and resumed['chat_prompt']['persona']==whale['chat_prompt']['persona']
                compact=call('wait_chat_messages',session_id=wid,timeout_seconds=1,known_prompt_version=whale['chat_prompt']['prompt_version'])
                assert not compact['prompt_changed'] and 'chat_prompt' not in compact and compact['chat_guidance']['persona_name']=='小鲸鱼'
                refreshed=call('wait_chat_messages',session_id=wid,timeout_seconds=1,known_prompt_version='old-version')
                assert refreshed['prompt_changed'] and refreshed['chat_prompt']['behavior']
                assert not OneBot.sent,'Prompt setup sent messages'
                call('stop_chat_session',session_id=wid)
                def add(group,content,*,own=False,old=False):
                    nonlocal serial
                    serial+=1;mid=str(serial)
                    event=dict(post_type='message',message_type='group',self_id=111,group_id=group,message_id=serial,
                               user_id=111 if own else 333,sender=dict(nickname='Member'),time=time.time(),
                               message=[dict(type='text',data=dict(text=content))])
                    events.send(event)
                    def recorded():
                        with access.connect() as db:return db.execute('SELECT id FROM chat_inbox WHERE message_id=? ORDER BY id DESC LIMIT 1',(mid,)).fetchone()
                    eventually(recorded)
                    return recorded()[0]
                payload=dict(conversation_id='111:group:222',persona='用简短、自然的中文聊天，不主动总结。',idempotency_key='start-fixture-1')
                with ThreadPoolExecutor(2) as pool:
                    starts=list(pool.map(lambda _:call('start_chat_session',**payload),range(2)))
                assert not any('error' in r for r in starts),starts
                sid=starts[0]['session']['id'];assert starts[1]['session']['id']==sid
                assert call('list_chat_groups')['items'][0]['conversation_id']=='111:group:222'
                with patch.object(store,'connect',side_effect=AssertionError('Live chat touched historical database')):
                    assert call('get_chat_session',session_id=sid)['session']['source']=='onebot_websocket'
                    assert call('wait_chat_messages',session_id=sid,timeout_seconds=1)['event']=='idle'
                    assert call('list_chat_groups')['items']
                assert starts[0]['session']['expires_at'] is None and not OneBot.sent
                assert 'FORBIDDEN' not in json.dumps(starts)
                assert starts[0]['session']['persona_preset']=='' and starts[0]['chat_prompt']['persona']==payload['persona']
                assert call('start_chat_session',**(payload|dict(persona='changed')))['error_code']=='chat_rejected'
                assert call('start_chat_session',**(payload|dict(conversation_id='111:group:999',idempotency_key='forbidden-start')))['error_code']=='chat_rejected'
                assert call('get_chat_session',connection=other,session_id=sid)['error_code']=='chat_rejected'
                assert call('stop_chat_session',connection=other,session_id=sid)['error_code']=='chat_rejected'
                assert call('wait_chat_messages',session_id=sid,timeout_seconds=1)['event']=='idle'
                assert call('get_chat_session',session_id=sid)['session']['active']
                # Pagination, interruption and restart must not acknowledge unseen data.
                ids=[add(222,'batch-'+str(i)) for i in range(53)]
                first=call('wait_chat_messages',session_id=sid,quiet_seconds=0,limit=30)
                assert first['has_more'] and len(first['messages'])==30
                assert first['chat_guidance']['social_context']['observed_messages']==30,'Guidance peeked at unread next page'
                assert any('先接着读' in hint for hint in first['chat_guidance']['reminders'])
                assert call('wait_chat_messages',session_id=sid,acknowledge_through_id=ids[-1])['error_code']=='chat_rejected'
                assert call('wait_chat_messages',session_id=sid,quiet_seconds=0)['read_through_id']==first['read_through_id']
                tools.chat.suspend()
                # Simulate a pre-preset schema: migration must preserve persona,
                # unread cursor and legacy idempotency, without opting into whale.
                with access.connect() as db:
                    db.execute('ALTER TABLE chat_sessions DROP COLUMN persona_preset')
                    db.execute('ALTER TABLE chat_sessions DROP COLUMN persona_name')
                tools.chat=MCPChat(access,tools.actions)
                assert tools.chat.listed(g['id'])[0]['state']=='waiting_agent'
                legacy=call('start_chat_session',**payload)
                assert legacy['session']['id']==sid and legacy['session']['persona_preset']==''
                assert legacy['session']['persona_name']=='自定义人格'
                assert legacy['chat_prompt']['persona']==payload['persona']
                second=call('wait_chat_messages',session_id=sid,acknowledge_through_id=first['read_through_id'],note='等群友补充',quiet_seconds=0)
                assert [m['id'] for m in first['messages']+second['messages']]==ids
                eventually(lambda:tools.chat.receiver.status()['state']=='connected')
                native_add(222,'old-import',old=True)
                third=call('wait_chat_messages',session_id=sid,acknowledge_through_id=second['read_through_id'],timeout_seconds=1,quiet_seconds=0)
                assert third['event']=='idle','Native DB leaked into live chat'
                own=add(222,'own-message',own=True)
                own_result=call('wait_chat_messages',session_id=sid,acknowledge_through_id=second['read_through_id'],quiet_seconds=0)
                assert own_result['messages'][0]['is_self']==1
                call('wait_chat_messages',session_id=sid,acknowledge_through_id=own,timeout_seconds=1)
                # Duplicate upstream events must not create duplicate deliveries.
                events.send(events.last);time.sleep(.15)
                assert call('wait_chat_messages',session_id=sid,timeout_seconds=1)['event']=='idle'
                # Reconnect reports a gap; unread cursor survives and no old data is imported.
                events.close_connections()
                eventually(lambda:tools.chat.receiver.status()['state']!='connected')
                assert call('send_chat_message',session_id=sid,text='must not send offline',idempotency_key='offline-send').get('error')
                eventually(lambda:tools.chat.receiver.status()['state']=='connected')
                assert call('get_chat_session',session_id=sid)['session']['gap_count']>=1
                # No cross-group/date or wrong-account event can become context.
                events.send(dict(post_type='message',message_type='group',self_id=111,group_id=999,message_id=99881,time=time.time(),message='FORBIDDEN-LIVE'))
                events.send(dict(post_type='message',message_type='group',self_id=111,group_id=222,message_id=99882,time=1,message='HISTORICAL-REPLAY'))
                time.sleep(.1)
                assert 'FORBIDDEN-LIVE' not in json.dumps(call('get_chat_session',session_id=sid))
                assert call('wait_chat_messages',session_id=sid,timeout_seconds=1)['event']=='idle'
                send=dict(session_id=sid,text='fixture answer' ,idempotency_key='session-send-1')
                with ThreadPoolExecutor(2) as pool:sent=list(pool.map(lambda _:call('send_chat_message',**send),range(2)))
                assert all(s['state']=='SUCCEEDED' for s in sent) and len(OneBot.sent)==1,sent
                OneBot.uncertain=True
                uncertain=call('send_chat_message',**(send|dict(idempotency_key='unknown-send')))
                assert uncertain['state']=='UNKNOWN'
                before=len(OneBot.sent)
                assert call('send_chat_message',**(send|dict(idempotency_key='unknown-send')))['state']=='UNKNOWN' and len(OneBot.sent)==before
                OneBot.uncertain=False
                # Stop remains prompt while a preflight OneBot lookup is blocked.
                with ThreadPoolExecutor(2) as pool:
                    OneBot.block=True;OneBot.entered.clear();OneBot.release.clear()
                    pending=pool.submit(lambda:call('send_chat_message',**(send|dict(idempotency_key='stop-race'))))
                    assert OneBot.entered.wait(3)
                    began=time.monotonic();assert ui.post('/api/mcp/chats/'+sid+'/stop',json={},headers=headers).status_code==200
                    assert time.monotonic()-began<1
                    OneBot.release.set();OneBot.block=False
                    assert pending.result(5).get('error') and len(OneBot.sent)==before
                assert call('send_chat_message',**(send|dict(idempotency_key='after-stop')))['error_code']=='chat_rejected'
                assert call('start_chat_session',**payload)['session']['active'] is False,'Retry resurrected stopped session'
                sessions=[]
                for group in range(222,228):
                    row=call('start_chat_session',**(payload|dict(conversation_id=f'111:group:{group}',idempotency_key='parallel-'+str(group))))
                    sessions.append(row['session']['id'])
                asyncio.run(protocol(service,g['token'],sessions,add,ui))
                # Revoke wakes active waiters and prevents any later sends.
                row=call('start_chat_session',**(payload|dict(idempotency_key='revoke-session')));sid=row['session']['id']
                with ThreadPoolExecutor(1) as pool:
                    pending=pool.submit(lambda:call('wait_chat_messages',session_id=sid,timeout_seconds=30))
                    eventually(lambda:sid in tools.chat.waiters)
                    assert ui.post('/api/mcp/connections/'+g['id']+'/revoke',json={},headers=headers).status_code==200
                    revoked=pending.result(3)
                    assert revoked.get('error') or revoked.get('event')=='stopped',revoked
                assert not tools.chat.row(sid)['active']
                # Cancelling a transport wait does not stop or acknowledge the session.
                row=call('start_chat_session',connection=other,**(payload|dict(idempotency_key='cancel-session')));sid=row['session']['id']
                cursor=row['session']['cursor'];cancel=threading.Event()
                with ThreadPoolExecutor(1) as pool:
                    pending=pool.submit(lambda:call('wait_chat_messages',connection=other,session_id=sid,timeout_seconds=30,cancel=cancel))
                    eventually(lambda:sid in tools.chat.waiters)
                    duplicate=call('wait_chat_messages',connection=other,session_id=sid,timeout_seconds=1)
                    assert duplicate['error_code']=='chat_rejected',duplicate
                    cancel.set();assert pending.result(3)['event']=='cancelled'
                assert tools.chat.row(sid)['cursor']==cursor and tools.chat.row(sid)['active']
                # Stop is available even after a client exhausts its request quota.
                with access.connect() as db:
                    db.executemany('INSERT INTO calls(grant_id,tool,at,status) VALUES(?,?,?,?)',[(other['id'],'get_data_status',time.time(),'ok')]*120)
                assert call('get_chat_session',connection=other,session_id=sid)['error_code']=='rate_limited'
                with patch.object(tools,'onebot',side_effect=AssertionError('Stop must not probe OneBot')):
                    assert call('stop_chat_session',connection=other,session_id=sid)['event']=='stopped'
                expired=grant(send=True,chat=True,end='2020-01-02')
                assert call('start_chat_session',connection=expired,**(payload|dict(idempotency_key='expired-start')))['error_code']=='chat_rejected'
                # Cross-origin browser requests cannot stop another user's session.
                assert ui.post('/api/mcp/chats/all/stop',json={}).status_code==403
                assert ui.post('/api/mcp/chats/all/stop',json={},headers=headers|{'Origin':'https://evil.invalid'}).status_code==403
                assert ui.post('/api/mcp/chats/not-found/stop',json={},headers=headers).status_code==404
                assert ui.get('/api/mcp/chats').status_code==200
                # A brand-new portable app has zero imported messages. Live chat
                # still works, including a real MCP call while Store is forbidden.
                empty=Store(folder/'empty.sqlite3');app2=FastAPI();svc2=install_mcp_routes(app2,empty)
                with socket.socket() as sock:sock.bind(('127.0.0.1',0));port2=sock.getsockname()[1]
                svc2.access.configure(True,port2)
                with TestClient(app2) as ui2:
                    g2=ui2.post('/api/mcp/connections',json=config|dict(chat=True,send=True),headers=headers).json()
                    assert 'token' in g2,g2
                    start2=svc2.tools.call(g2['token'],'start_chat_session',payload,threading.Event()).structuredContent
                    sid2=start2['session']['id']
                    with patch.object(empty,'connect',side_effect=AssertionError('Native chat DB was accessed')):
                        assert svc2.tools.call(g2['token'],'get_chat_session',dict(session_id=sid2),threading.Event()).structuredContent['context']['messages']==[]
                        # End-to-end HTTP authentication and dispatch also bypass native DB.
                        async def live_only_http():
                            async with httpx.AsyncClient(headers={'Authorization':'Bearer '+g2['token']},timeout=15,trust_env=False) as http:
                                async with streamable_http_client(svc2.status()['url'],http_client=http) as (read,write,_):
                                    async with ClientSession(read,write) as client:
                                        await client.initialize()
                                        pending=asyncio.create_task(client.call_tool('wait_chat_messages',dict(session_id=sid2,timeout_seconds=10,quiet_seconds=0)))
                                        await asyncio.sleep(.2)
                                        events.send(dict(post_type='message',message_type='group',self_id=111,group_id=222,message_id=99883,user_id=333,time=time.time(),message=[dict(type='text',data={'text':'zero-import-push'})]))
                                        received=await asyncio.wait_for(pending,5)
                                        assert received.structuredContent['messages'][0]['content']=='zero-import-push',received
                                        sent=await client.call_tool('send_chat_message',dict(session_id=sid2,text='zero-import-send',idempotency_key='zero-import-send'))
                                        assert sent.structuredContent['state']=='SUCCEEDED',sent
                                        await client.call_tool('stop_chat_session',dict(session_id=sid2))
                        asyncio.run(live_only_http())
    finally:
        OneBot.release.set();events.close();upstream.shutdown();upstream.server_close();invalidate_availability()
    print('PASS: hot persona discovery over one MCP connection, immutable snapshots after edit/deletion, external-only indefinite sessions, real MCP HTTP, isolated OneBot sends, 6 independent waits + ordinary tools, WebSocket push/dedupe/reconnect/native-reader independence/pagination/restart/ack, idempotency/UNKNOWN, stop race/revoke/UI origin. No real QQ sends or internal model calls.')


if __name__=='__main__':main()
