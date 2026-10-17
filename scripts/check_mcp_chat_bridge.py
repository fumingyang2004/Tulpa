"""Bridge behavior and real stdio/HTTP interoperability; temporary accounts only."""
import asyncio
from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from test_environment import system_environment

sys.path.insert(0,str(Path(__file__).resolve().parent))
from check_mcp_reactions import ReactionBot, Events, eventually, ROOT
from chatlocal.mcp_chat_bridge import ChatBridge, compact_result, result
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.store import Store
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

SID='a'*32
CID='111:group:222'


def idle(**extra):
    return dict(event='idle',session_id=SID,messages=[],gap_count=0,
        receiver={'state':'connected'},prompt_changed=False,
        next_call=dict(tool='wait_chat_messages',arguments=dict(session_id=SID,known_prompt_version='fixture',acknowledge_through_id=7)),**extra)


class Upstream:
    def __init__(self,packets=()):
        self.packets=list(packets);self.calls=[];self.cancelled=False
    async def call_tool(self,name,args,**kwargs):
        self.calls.append((name,deepcopy(args)))
        if name=='get_chat_session':return result(dict(session=dict(id=SID,conversation_id=CID,active=True,gap_count=0)))
        if name=='list_chat_sessions':return result(dict(sessions=[dict(id=SID,conversation_id=CID,active=True,gap_count=0)]))
        if name=='stop_chat_session':return result(dict(event='stopped',session=dict(id=SID,active=False)))
        if name=='wait_chat_messages':
            if self.packets:return result(self.packets.pop(0))
            try:await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled=True;raise
        raise AssertionError('Unexpected write '+name)


async def checks():
    packet=dict(event='messages',messages=[dict(id=8,content='不能丢的原文',segments=[{'type':'image','image_index':0}])],gap_count=0)
    upstream=Upstream([idle() for _ in range(5)]+[packet]);bridge=ChatBridge(upstream,CID)
    progress=[]
    async def notify(polls,elapsed):progress.append(polls)
    out=await bridge.call('wait_chat_messages',dict(session_id=SID,known_prompt_version='fixture',acknowledge_through_id=7,
        note='one update',quiet_seconds=0,limit=3,timeout_seconds=2),notify)
    assert out.structuredContent==packet and progress==[1,2,3,4,5]
    waits=[args for name,args in upstream.calls if name=='wait_chat_messages']
    assert len(waits)==6 and all(a['acknowledge_through_id']==7 for a in waits)
    assert all(a['quiet_seconds']==0 and a['limit']==3 and a['timeout_seconds']==2 for a in waits)
    assert sum('note' in a for a in waits)==1
    await bridge.close()
    assert not any(name=='stop_chat_session' for name,_ in upstream.calls),'Inspection alone acquired ownership'

    wakes=[dict(event='reactions',messages=[]),dict(event='learning_ready',learning_ready={'next_call':{'tool':'claim_chat_learning'}}),
           dict(event='source_unavailable'),dict(event='stopped'),dict(error='revoked',error_code='revoked'),
           idle(chat_prompt={'persona':'first or changed'}),idle(chat_guidance={'reaction_notices':{'new_items':[{'notice_id':4}]}}),
           dict(idle(),gap_count=1)]
    for packet in wakes:
        upstream=Upstream([packet]);bridge=ChatBridge(upstream,CID)
        out=await bridge.call('wait_chat_messages',dict(session_id=SID,known_prompt_version='fixture'))
        assert out.structuredContent==packet and len(upstream.calls)==2
        await bridge.close()

    upstream=Upstream();bridge=ChatBridge(upstream,CID,idle_wait_seconds=.12)
    began=time.monotonic();out=await bridge.call('wait_chat_messages',dict(session_id=SID))
    assert out.structuredContent['event']=='idle_window_elapsed' and time.monotonic()-began<.5 and upstream.cancelled
    task=asyncio.create_task(bridge.call('wait_chat_messages',dict(session_id=SID)))
    await asyncio.sleep(.02)
    busy=await bridge.call('wait_chat_messages',dict(session_id=SID));assert busy.structuredContent['error_code']=='chat_busy'
    task.cancel()
    try:await task
    except asyncio.CancelledError:pass
    assert not bridge.waiting
    await bridge.close()

    upstream=Upstream();bridge=ChatBridge(upstream,CID);bridge.sessions.add(SID)
    failures=[]
    async def broken(*args,**kwargs):
        failures.append(1);raise TimeoutError('synthetic upstream timeout')
    upstream.call_tool=broken
    try:await bridge.call('wait_chat_messages',dict(session_id=SID))
    except TimeoutError:pass
    else:raise AssertionError('Upstream timeout was hidden')
    assert len(failures)==1 and not bridge.waiting
    await bridge.close()

    upstream=Upstream();bridge=ChatBridge(upstream,CID,duration_seconds=.08)
    await bridge.call('get_chat_session',dict(session_id=SID))
    # Independent timer also stops while the host is thinking, without wait.
    await asyncio.sleep(.12)
    assert len([n for n,_ in upstream.calls if n=='stop_chat_session'])==1
    out=await bridge.call('send_chat_reply',dict(session_id=SID,plan_id='b'*32,bubbles=[]))
    assert out.structuredContent['event']=='duration_elapsed'
    await bridge.close()

    upstream=Upstream();bridge=ChatBridge(upstream,CID)
    out=await bridge.call('start_chat_session',dict(conversation_id='111:group:223'))
    assert out.isError and not upstream.calls
    out=await bridge.call('get_chat_session',dict(session_id='invalid'))
    assert out.isError and not upstream.calls
    out=await bridge.call('run_sql',dict(sql='private fixture'))
    assert out.isError and not upstream.calls
    # A well-formed ID from another scope must not leak its returned context.
    upstream.call_tool=lambda *a,**kw:asyncio.sleep(0,result=result(dict(sessions=[dict(id=SID,conversation_id='111:group:223')],context='private')))
    out=await bridge.call('get_chat_session',dict(session_id=SID))
    assert out.isError and 'private' not in str(out)
    await bridge.close()

    payload=dict(plan_id='b'*32,state='SUCCEEDED',bubbles=[dict(index=0,state='SUCCEEDED',expression={'kind':'text','text':'x'*4000},
        receipt={'id':'receipt','state':'SUCCEEDED','summary':'x'*4000,'result':{'message_id':'88'}})])
    reply=result(payload);small=compact_result(reply)
    assert len(str(small))<len(str(reply))*.15
    assert small.structuredContent['bubbles'][0]['receipt']['message_id']=='88'
    pixels=types.ImageContent(type='image',data='AA==',mimeType='image/png')
    reply.content.append(pixels)
    assert compact_result(reply).content[-1]==pixels
    for state in ('REVIEW_REQUIRED','UNKNOWN'):
        special=result(dict(payload,state=state,bubbles=[dict(state='UNKNOWN',expression={'kind':'text','text':'keep'},receipt={'state':'UNKNOWN'})]))
        assert compact_result(special).structuredContent==special.structuredContent
    print('PASS idle coalescing, all wake conditions, cancellation, deadline, scope, multimodal/uncertainty preservation')


async def wire(service,token,ui,root):
    config=root/'bridge.json'
    config.write_text(json.dumps({'mcpServers':{'fixture':{'url':service.status()['url'],'headers':{'Authorization':'Bearer '+token}}}}))
    config.chmod(0o600)
    params=StdioServerParameters(command=sys.executable,cwd=str(ROOT),args=['-m','chatlocal.mcp_chat_bridge','--config',str(config),
        '--conversation',CID,'--idle-wait-seconds','5','--duration-seconds','3'])
    with (root/'bridge.stderr').open('w+') as errors:
        async with stdio_client(params,errlog=errors) as (read,write):
            async with ClientSession(read,write) as host:
                info=await host.initialize();assert info.serverInfo.name=='tulpa-chat'
                tools={t.name:t for t in (await host.list_tools()).tools}
                assert 'review_chat_reply' in tools and tools['review_chat_reply'].annotations.openWorldHint
                assert 'run_sql' not in tools and 'read_message' not in tools
                started=(await host.call_tool('start_chat_session',dict(conversation_id=CID,persona='仅用于隔离测试的人格',idempotency_key='bridge-fixture'))).structuredContent
                sid=started['session']['id'];version=started['chat_prompt']['prompt_version']
                assert 'persona' not in started['session'] and 'instructions' in started
                response=ui.put(f'/api/mcp/chats/{sid}/learning',json={'enabled':False},headers={'X-ChatWeave-UI':'1'})
                assert response.status_code==200
                reduced=(await host.call_tool('get_chat_session',dict(session_id=sid,known_prompt_version=version))).structuredContent
                assert not reduced['prompt_changed'] and 'persona' not in reduced['chat_prompt'] and 'instructions' not in reduced
                out=(await host.call_tool('wait_chat_messages',dict(session_id=sid,known_prompt_version=version))).structuredContent
                assert out.get('event') in ('duration_elapsed','stopped'),out
                eventually(lambda:not service.tools.chat.row(sid)['active'])
                assert not ReactionBot.sent and not ReactionBot.writes
        errors.seek(0);assert token not in errors.read(),'Credential leaked to stderr'
    print('PASS real stdio → persistent HTTP → temporary Tulpa; prompt delta and independent expiry, no QQ writes')


def main():
    asyncio.run(checks())
    events=Events();ReactionBot.sent=[];ReactionBot.writes=[];ReactionBot.history={};ReactionBot.intercept=None
    bot=ThreadingHTTPServer(('127.0.0.1',0),ReactionBot)
    threading.Thread(target=bot.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='tulpa-bridge-') as tmp,patch.dict('os.environ',system_environment(),clear=True):
            root=Path(tmp);store=Store(root/'chats.sqlite3')
            (root/'.env').write_text(f'REPLY_ONEBOT_URL=http://127.0.0.1:{bot.server_port}\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:{events.port}\nREPLY_ONEBOT_WS_TOKEN=event-fixture\n')
            with patch('chatlocal.onebot.ROOT',root),patch('chatlocal.message_sender.ROOT',root):
                app=FastAPI();service=install_mcp_routes(app,store)
                with TestClient(app) as ui:
                    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                    service.access.configure(True,port);service.start()
                    grant=service.access.create(dict(name='bridge-fixture',platforms=['qq'],conversations=[['qq',CID]],chat=True,send=True))
                    asyncio.run(wire(service,grant['token'],ui,root))
    finally:
        events.close();bot.shutdown();bot.server_close()


if __name__=='__main__':main()
