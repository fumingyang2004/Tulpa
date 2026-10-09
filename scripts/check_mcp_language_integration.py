"""Real MCP wire / local UI / WS synthetic integration; no QQ or LLM writes."""
import asyncio
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).parent))
from check_mcp_reactions import ReactionBot, Events, eventually, ROOT
from chatlocal.store import Store
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.mcp_language_learning import TOOLS
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import httpx


async def wire(service,token,sid):
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+token},trust_env=False,timeout=20) as http:
        async with streamable_http_client(service.status()['url'],http_client=http) as (r,w,_):
            async with ClientSession(r,w) as client:
                await client.initialize()
                assert TOOLS<={t.name for t in (await client.list_tools()).tools}
                out=(await client.call_tool('get_chat_learning',dict(session_id=sid,records=True))).structuredContent
                assert out['enabled'] and out['expression_count']==3 and not out['usable_expressions']
                assert 'source_id' not in json.dumps(out)
                return 'discovery and scoped state over real MCP HTTP passed'


def main():
    assert Path(__import__('chatlocal.mcp_language_learning',fromlist=['ChatLearning']).__file__).resolve()==ROOT/'chatlocal/mcp_language_learning.py'
    events=Events();ReactionBot.sent=[];ReactionBot.writes=[];ReactionBot.intercept=None
    server=ThreadingHTTPServer(('127.0.0.1',0),ReactionBot)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    ports={events.port,server.server_port};connect=socket.create_connection
    def restrict(address,*args,**kw):
        assert address[0] in ('127.0.0.1','localhost','::1') and address[1] in ports,address
        return connect(address,*args,**kw)
    try:
        with tempfile.TemporaryDirectory(prefix='tulpa-language-wire-') as tmp,patch.dict('os.environ',{},clear=True),patch('socket.create_connection',restrict):
            root=Path(tmp);store=Store(root/'chats.sqlite3')
            (root/'.env').write_text(f'REPLY_ONEBOT_URL=http://127.0.0.1:{server.server_port}\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:{events.port}\nREPLY_ONEBOT_WS_TOKEN=event-fixture\n','utf-8')
            with patch('chatlocal.onebot.ROOT',root),patch('chatlocal.message_sender.ROOT',root):
                service=install_mcp_routes(app:=FastAPI(),store);tools=service.tools;access=service.access
                with TestClient(app) as ui:
                    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                    ports.add(port);access.configure(True,port);service.start()
                    created=access.create(dict(name='learning-fixture',platforms=['qq'],chat=True,send=True,
                        conversations=[['qq','111:group:222'],['qq','111:group:223']]))
                    def call(name,**args):return tools.call(created['token'],name,args,threading.Event()).structuredContent
                    sid=call('start_chat_session',conversation_id='111:group:222',persona_preset='little_whale_v2',idempotency_key='fixture-start')['session']['id']
                    learning=tools.chat.learning;timer=[time.time()];learning.store.clock=lambda:timer[0]
                    path=f'/api/mcp/chats/{sid}/learning';headers={'X-ChatWeave-UI':'1'}
                    assert not call('get_chat_learning',session_id=sid)['enabled']
                    assert ui.put(path,json=dict(enabled=True,allow_degraded=False)).status_code==403
                    assert ui.put(path,json=dict(enabled=True,allow_degraded=False),headers=headers).json()['enabled']
                    def size():
                        with learning.store.db() as db:return db.execute('SELECT count(*) FROM observations').fetchone()[0]
                    def receive(mid,text,uid=333,**extra):
                        event=dict(post_type='message',message_type='group',self_id=111,group_id=222,message_id=mid,
                            user_id=uid,time=int(time.time()),sender={'user_id':uid},message=[dict(type='text',data={'text':text})])|extra
                        tools.chat.receive(event)
                    # Old history, SELF, synthetic and foreign-group are never evidence.
                    receive(-1,'SELF',111);receive(-2,'摘要',synthetic=True);receive(-3,'旧历史',time=int(time.time())-86400)
                    receive(-4,'别群机密',group_id=223);assert size()==0
                    with patch.object(store,'connect',side_effect=AssertionError('historical DB accessed')):
                        for i in range(10):receive(-100-i,f'云朵开机 合成实时内容{i}')
                        assert size()==10
                        timer[0]+=29;assert call('claim_chat_learning',session_id=sid,context_id='new-host-context')['state']=='no_task'
                        timer[0]+=1;t=call('claim_chat_learning',session_id=sid,context_id='new-host-context')['task']
                        assert t['stage']=='extract' and len(t['material']['messages'])==10
                        def submit(task,result,inv):return call('submit_chat_learning',session_id=sid,job_id=task['job_id'],lease=task['lease'],invocation_id=inv,result=result)
                        ids=[m['source_id'] for m in t['material']['messages']]
                        result=dict(expressions=[dict(situation='情境'+str(i),style='抽象方式'+str(i),source_id=ids[i]) for i in range(3)],jargon=[dict(term='云朵开机',source_id=ids[0])])
                        assert submit(t,result,'actual-call-1')['state']=='stage_completed'
                        t=call('claim_chat_learning',session_id=sid,context_id='new-host-context')['task']
                        assert t['stage']=='review'
                        assert submit(t,dict(reviews=[dict(index=i,accept=True,reason='来源确实支持且可复用') for i in range(3)]),'actual-call-2')['state']=='completed'
                        assert call('get_chat_learning',session_id=sid)['usable_expressions']==0
                    assert asyncio.run(wire(service,created['token'],sid))
                    # Server-authoritative binding: knowing another SID or
                    # choosing a new cid cannot inspect the old partition.
                    denied=call('start_chat_session',conversation_id='111:group:223',persona_preset='little_whale_v2',idempotency_key='fixture-other')
                    assert 'session' not in denied
                    bad=call('claim_chat_learning',session_id=sid,context_id='fresh-context',account_id='999')
                    assert bad['error_code']=='invalid_arguments'
                    data=ui.get(path).json();eid=data['expressions'][0]['id']
                    assert ui.post(path+'/record',json=dict(kind='expressions',id=eid,enabled=False),headers=headers).status_code==200
                    assert call('get_chat_learning',session_id='f'*32).get('expression_count') is None
                    # Disconnect invalidates any lease; the socket returning
                    # later cannot make old results valid again.
                    timer[0]+=31
                    for i in range(10):receive(-200-i,f'新批合成 {i}')
                    t=call('claim_chat_learning',session_id=sid,context_id='new-host-context')['task']
                    tools.chat.source_changed('disconnected')
                    denied=submit(t,result,'late-model-call')
                    assert denied['state']=='rejected' and denied['error_code']=='task_unavailable',denied
                    assert not ReactionBot.sent and not ReactionBot.writes
                    call('stop_chat_session',session_id=sid)
                    assert call('get_chat_learning',session_id=sid).get('expression_count') is None
                    print(json.dumps(dict(status='passed',transport='MCP HTTP + loopback WS',observations=10,
                        model_calls='2 simulated separate invocations',historical_db_reads=0,external_writes=0,
                        checks=['scope and grant pin','API opt-in and CSRF','SELF/history/synthetic/foreign filter','extract-review-persist',
                                'default degraded not used','tool discovery','disable record','disconnect/stop late result rejected']),ensure_ascii=False))
    finally:
        server.shutdown();events.close()


if __name__=='__main__':main()
