"""Mandatory expression choice through real MCP HTTP; fake QQ, no LLM calls."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
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

sys.path.insert(0,str(Path(__file__).parent))
from check_mcp_reactions import ReactionBot, Events, eventually, ROOT
from check_mcp_language_learning import ground_fixture_rows
from chatlocal.store import Store
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.mcp_chat_turns import ChatTurns
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import httpx


async def wire(service,connection,sid,ui):
    tools=service.tools;learning=tools.chat.learning;scope='qq:111:group:222';serial=0
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+connection['token']},trust_env=False,timeout=20) as http:
        async with streamable_http_client(service.status()['url'],http_client=http) as (read,write,_):
            async with ClientSession(read,write) as client:
                await client.initialize()
                names={t.name for t in (await client.list_tools()).tools}
                assert {'plan_chat_reply','select_chat_expressions','send_chat_reply'}<=names
                async def call(name,**args):return (await client.call_tool(name,args)).structuredContent
                def seed(n):
                    with learning.store.db() as db:
                        db.execute('DELETE FROM expressions WHERE scope=?',(scope,))
                        db.execute('DELETE FROM expression_grounding WHERE scope=?',(scope,))
                        db.execute('DELETE FROM selections WHERE scope=?',(scope,))
                        for i in range(n):
                            db.execute('INSERT INTO expressions(id,scope,situation,style,count,updated,independence) VALUES(?,?,?,?,2,?,?)',
                                (f'{i:032x}',scope,f'合成情境{i}',f'合成群友形式{i}',time.time(),'degraded'))
                    ground_fixture_rows(learning.store,scope)
                async def plan(expressions=('text',),action='reply'):
                    nonlocal serial
                    serial+=1
                    packet=await call('get_chat_session',session_id=sid)
                    target=next(m for m in reversed(packet['context']['messages']) if not m['is_self'])
                    return await call('plan_chat_reply',session_id=sid,batch_id=packet['input_batch']['id'],
                        action=action,expressions=list(expressions),target_event_id=target['id'],
                        intent='合成测试意图',idempotency_key=f'usage-plan-{serial}')
                async def send(p,bubbles=None):
                    return await call('send_chat_reply',session_id=sid,plan_id=p['plan_id'],
                        bubbles=bubbles or [dict(kind='text',text='仅发送给本机模拟 OneBot')])
                async def select(p,ids):
                    return await call('select_chat_expressions',session_id=sid,plan_id=p['plan_id'],expression_ids=ids)
                def pause(value):
                    result=ui.put(f'/api/mcp/chats/{sid}/learning',json=dict(enabled=not value),headers={'X-ChatWeave-UI':'1'})
                    assert result.status_code==200
                seed(9);p=await plan()
                assert p['next_call']['tool']=='send_chat_reply' and p['expression_selection']['reason']=='insufficient_expressions'
                assert (await send(p))['state']=='SUCCEEDED'
                seed(10);p=await plan();before=len(ReactionBot.sent)
                assert p['phase']=='SELECTING_EXPRESSION' and p['next_call']['tool']=='select_chat_expressions'
                assert p['expression_selection']['state']=='pending'
                blocked=await send(p)
                assert blocked['error_code']=='expression_selection_required' and len(ReactionBot.sent)==before,blocked
                assert blocked['recovery']['next_call']['tool']=='select_chat_expressions'
                # The legacy single-send path uses the same gate.
                legacy=await call('send_chat_message',session_id=sid,plan_id=p['plan_id'],text='cannot bypass',idempotency_key='usage-legacy')
                assert legacy['error_code']=='expression_selection_required' and len(ReactionBot.sent)==before
                foreign=await select(p,['f'*32]);assert foreign['error_code']=='invalid_selection'
                chosen=await select(p,[])
                assert chosen['expression_decision']==dict(state='none',reason='explicit_none') and chosen['selected']==[]
                assert 'candidates' not in chosen and chosen['next_call']['tool']=='send_chat_reply'
                assert (await select(p,[]))['expression_selection']==chosen['expression_selection']
                completed=await send(p)
                assert completed['state']=='SUCCEEDED' and completed['expression_selection']['state']=='none'
                assert (await send(p))['state']=='SUCCEEDED' and len(ReactionBot.sent)==before+1
                print('PASS 9/10 threshold, wire discovery, explicit empty choice, legacy gate, unknown IDs and replay')

                p=await plan();ids=[x['id'] for x in p['learned_language']['candidates'][:2]]
                args=dict(session_id=sid,plan_id=p['plan_id'],expression_ids=ids)
                # Same choice can arrive twice concurrently without changing it.
                def choose():return tools.call(connection['token'],'select_chat_expressions',args,threading.Event()).structuredContent
                with ThreadPoolExecutor(2) as pool:
                    first=pool.submit(choose);second=pool.submit(choose);a=first.result(10);b=second.result(10)
                assert a['selected']==b['selected'] and a['expression_selection']==b['expression_selection']
                audit=a['expression_selection'];assert audit['selected_ids']==ids
                assert audit['delivery']=='tool_result_prepared' and audit['host_injection']==audit['model_use']=='unverified'
                assert audit['candidate_context']['sha256'] and audit['selected_context']['sha256']
                assert not any(k in json.dumps(audit,ensure_ascii=False) for k in ('合成群友形式','合成情境','合成测试意图'))
                assert (await select(p,[]))['error_code']=='idempotency_conflict'
                completed=await send(p);assert completed['state']=='SUCCEEDED'
                assert completed['expression_selection']==audit
                with service.access.connect() as db:
                    stored=json.loads(db.execute('SELECT payload FROM chat_turn_plans WHERE id=?',(p['plan_id'],)).fetchone()[0])
                    assert stored['expression_selection']==audit
                packet=await call('get_chat_session',session_id=sid)
                assert next(r for r in packet['recent_turns'] if r['plan_id']==p['plan_id'])['expression_selection']==audit
                print('PASS selected material audit, no private text in audit, atomic repeat, durable linked turn receipt')

                # Disabling a selected record invalidates the snapshot and requires a new choice.
                p=await plan();ids=[x['id'] for x in p['learned_language']['candidates'][:1]];await select(p,ids)
                learning.store.manage(learning.binding(sid,connection['id']),'expressions',ids[0],enabled=False)
                # Nine checked entries now means automatic not-applicable, not fabricated model refusal.
                out=await send(p);assert out['state']=='SUCCEEDED' and out['expression_selection']['state']=='not_applicable'
                seed(12);p=await plan();ids=[x['id'] for x in p['learned_language']['candidates'][:1]];await select(p,ids)
                learning.store.manage(learning.binding(sid,connection['id']),'expressions',ids[0],enabled=False)
                before=len(ReactionBot.sent);out=await send(p)
                assert out['error_code']=='expression_selection_required' and len(ReactionBot.sent)==before
                assert ids[0] not in [x['id'] for x in out['learned_language']['candidates']]
                await select(p,[]);assert (await send(p))['state']=='SUCCEEDED'
                p=await plan();pause(True)
                out=await send(p);assert out['state']=='SUCCEEDED' and out['expression_selection']['reason']=='learning_disabled'
                pause(False)
                # Learning-storage failure cannot silently masquerade as a model
                # choice or stop otherwise authorized ordinary conversation.
                with patch.object(learning.store,'context',side_effect=OSError('fixture storage failure')):
                    p=await plan();out=await send(p)
                    assert out['state']=='SUCCEEDED' and out['expression_selection']['state']=='unavailable'
                    assert out['expression_selection']['reason']=='learning_unavailable'
                silent=await plan(expressions=(),action='silence')
                assert silent['state']=='SILENT' and silent['expression_selection']['reason']=='no_reply'
                # A reaction-only round requires no expression selector or text filler.
                p=await plan(expressions=('reaction',))
                assert p['next_call']['tool']=='send_chat_reply' and p['expression_selection']['reason']=='non_text_plan'
                before=len(ReactionBot.sent)
                out=await send(p,[dict(kind='reaction',event_id=p['decision']['target_event_id'],reaction_id='qq_like',operation='add')])
                assert out['state']=='SUCCEEDED' and len(ReactionBot.sent)==before and len(ReactionBot.writes)==1,out
                p=await plan(expressions=('text','reaction'))
                assert p['expression_selection']['state']=='pending'
                out=await send(p,[dict(kind='reaction',event_id=p['decision']['target_event_id'],reaction_id='qq_like',operation='remove')])
                assert out['state']=='SUCCEEDED' and out['expression_selection']['reason']=='non_text_output',out
                assert len(ReactionBot.sent)==before and len(ReactionBot.writes)==2
                # Selection is never an exemption from stale-message/stop/revoke checks.
                p=await plan();await select(p,[])
                event=dict(post_type='message',message_type='group',self_id=111,group_id=222,user_id=333,
                    message_id=-9002,time=int(time.time()),sender={'user_id':333},message=[dict(type='text',data={'text':'合成补充更正'})])
                tools.chat.receive(event)
                out=await send(p);assert out['error_code']=='stale' and len(ReactionBot.sent)==before,out
                await asyncio.sleep(.15)
                p=await plan();await select(p,[])
                await call('stop_chat_session',session_id=sid)
                assert (await send(p))['error_code']=='chat_rejected'
                assert len(ReactionBot.sent)==before
                # Turn audit survives restart, but must never revive the cancelled queue.
                turns=ChatTurns(tools.chat)
                row=turns.load(p['plan_id'],sid)
                assert row['state']=='CANCELLED' and json.loads(row['payload'])['expression_selection']['state']=='none'
                print('PASS record changes, pause, silence/reaction-only, stale/stop, restart retains audit but no dispatch')
                return dict(status='passed',transport='real MCP HTTP + loopback OneBot',real_qq_writes=0,
                    model_calls=0,mocked_text_writes=len(ReactionBot.sent),mocked_reactions=len(ReactionBot.writes))


def main():
    assert Path(__import__('chatlocal.mcp_chat_turns',fromlist=['x']).__file__).resolve()==ROOT/'chatlocal/mcp_chat_turns.py'
    events=Events();ReactionBot.sent=[];ReactionBot.writes=[];ReactionBot.history={};ReactionBot.intercept=None
    server=ThreadingHTTPServer(('127.0.0.1',0),ReactionBot)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    ports={server.server_port,events.port};connect=socket.create_connection
    def restrict(address,*args,**kw):
        assert address[0] in ('127.0.0.1','localhost','::1') and address[1] in ports,address
        return connect(address,*args,**kw)
    try:
        with tempfile.TemporaryDirectory(prefix='tulpa-language-usage-') as tmp,patch.dict('os.environ',{},clear=True),patch('socket.create_connection',restrict):
            root=Path(tmp);store=Store(root/'chats.sqlite3')
            (root/'.env').write_text(f'REPLY_ONEBOT_URL=http://127.0.0.1:{server.server_port}\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:{events.port}\nREPLY_ONEBOT_WS_TOKEN=event-fixture\n','utf-8')
            with patch('chatlocal.onebot.ROOT',root),patch('chatlocal.message_sender.ROOT',root):
                service=install_mcp_routes(app:=FastAPI(),store);access=service.access
                with TestClient(app) as ui:
                    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                    ports.add(port);access.configure(True,port);service.start()
                    connection=access.create(dict(name='usage-fixture',platforms=['qq'],conversations=[['qq','111:group:222']],chat=True,send=True,chat_reactions=True))
                    (access.path.parent/'mcp-chat-policy.json').write_text('{"quiet_seconds":0.1,"reply_cooldown_seconds":0}','utf-8')
                    service.tools.chat.reactions.policy_path.write_text('{"cooldown_seconds":0}','utf-8')
                    sid=service.tools.call(connection['token'],'start_chat_session',dict(conversation_id='111:group:222',persona_preset='little_whale_v2',idempotency_key='usage-start'),threading.Event()).structuredContent['session']['id']
                    event=dict(post_type='message',message_type='group',self_id=111,group_id=222,user_id=333,
                        message_id=-9001,message_seq=9001,time=int(time.time()),sender={'user_id':333},
                        message=[dict(type='text',data={'text':'合成测试消息，不来自真实群'})])
                    ReactionBot.history['-9001']=deepcopy(event);events.send(event)
                    def received():
                        with access.connect() as db:return db.execute('SELECT 1 FROM chat_inbox WHERE session_id=? AND message_id=?',(sid,'-9001')).fetchone()
                    eventually(received)
                    time.sleep(.15)
                    print(json.dumps(asyncio.run(wire(service,connection,sid,ui)),ensure_ascii=False))
    finally:server.shutdown();events.close()


if __name__=='__main__':main()
