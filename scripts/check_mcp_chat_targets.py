"""Native reply/@ acceptance via isolated OneBot HTTP/WS and real MCP HTTP.

No real QQ calls, private data, external models or environment credentials.
Use --package <portable root> to execute that release's actual modules/runtime.
"""
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

sys.path.insert(0,str(Path(__file__).resolve().parent))
from check_mcp_chat import Events, OneBot, eventually, ROOT
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from chatlocal.store import Store
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.mcp_chat import MCPChat
from chatlocal.onebot import invalidate_availability


class TargetBot(OneBot):
    sent=[]
    calls=[]
    history={}
    login=111
    bad_member=False
    uncertain=False
    intercept=None

    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        action=self.path[1:]
        self.calls.append((action,deepcopy(body)))
        if type(self).intercept:type(self).intercept(action,body)
        if action=='get_login_info':data={'user_id':self.login}
        elif action=='get_group_list':data=[{'group_id':g,'group_name':'Fixture '+str(g)} for g in (222,223)]
        elif action=='get_group_info':data={'group_id':body['group_id'],'group_name':'Fixture '+str(body['group_id'])}
        elif action=='get_msg':data=self.history.get(str(body['message_id']))
        elif action=='get_group_member_info':
            data={'group_id':999 if self.bad_member else body['group_id'],'user_id':body['user_id'],'nickname':'Fixture'}
        elif action=='send_group_msg':
            self.sent.append(deepcopy(body))
            if self.uncertain:self.send_response(503);self.end_headers();return
            data={'message_id':100000+len(self.sent)}
        else:raise AssertionError(action)
        raw=json.dumps(dict(status='ok',retcode=0,data=data)).encode()
        self.send_response(200);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)


def text_segment(text):return {'type':'text','data':{'text':text}}
def reply_segment(mid):return {'type':'reply','data':{'id':str(mid)}}
def at_segment(uid):return {'type':'at','data':{'qq':str(uid)}}


async def wire(service,token,sid,eid):
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+token},timeout=20,trust_env=False) as http:
        async with streamable_http_client(service.status()['url'],http_client=http) as (read,write,_):
            async with ClientSession(read,write) as client:
                await client.initialize()
                schemas={t.name:t.inputSchema for t in (await client.list_tools()).tools}
                fields=schemas['send_chat_message']['properties']
                assert fields['quote']['default'] is False and fields['mention_user_ids']['default']==[]
                assert set(schemas['send_chat_message']['required'])=={'session_id','text','idempotency_key'}
                assert 'reply_to_event_id' not in schemas['send_qq_message']['properties']
                args=dict(session_id=sid,text='wire reply',reply_to_event_id=eid,quote=True,mention_user_ids=['333'],idempotency_key='wire-both-options')
                result=await client.call_tool('send_chat_message',args)
                assert not result.isError and result.structuredContent['state']=='SUCCEEDED',result
                assert result.structuredContent['result']['message_segments']==[reply_segment(9001),at_segment(333),text_segment('wire reply')]
                before=len(TargetBot.sent)
                bad=await client.call_tool('send_chat_message',args|dict(mention_user_ids=['all'],idempotency_key='wire-at-all-denied'))
                assert bad.isError and bad.structuredContent['error_code']=='invalid_arguments'
                assert len(TargetBot.sent)==before


def main():
    for name in ('mcp_chat','mcp_chat_prompts','mcp_actions','message_sender'):
        module=__import__('chatlocal.'+name,fromlist=[name])
        assert Path(module.__file__).resolve()==ROOT/'chatlocal'/f'{name}.py', 'Loaded wrong installation'
    print('Testing installed modules:',ROOT,flush=True)
    events=Events();upstream=ThreadingHTTPServer(('127.0.0.1',0),TargetBot)
    threading.Thread(target=upstream.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='tulpa-chat-targets-') as tmp,patch.dict('os.environ',{},clear=True):
            folder=Path(tmp);store=Store(folder/'chats.sqlite3')
            (folder/'.env').write_text(f'REPLY_ONEBOT_URL=http://127.0.0.1:{upstream.server_port}\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:{events.port}\nREPLY_ONEBOT_WS_TOKEN=event-fixture\n','utf-8')
            with patch('chatlocal.onebot.ROOT',folder),patch('chatlocal.message_sender.ROOT',folder):
                app=FastAPI();service=install_mcp_routes(app,store);access=service.access;tools=service.tools
                with TestClient(app) as ui:
                    headers={'X-ChatWeave-UI':'1'}
                    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                    assert ui.put('/api/mcp',json=dict(enabled=True,port=port),headers=headers).json()['running']
                    config=dict(name='targets-fixture',platforms=['qq'],conversations=[['qq',f'111:group:{g}'] for g in (222,223)],chat=True,send=True)
                    def grant():
                        result=ui.post('/api/mcp/connections',json=config,headers=headers)
                        assert result.status_code==200,result.text
                        return result.json()
                    g=grant();other=grant()
                    def call(name,*,connection=g,**args):
                        return tools.call(connection['token'],name,args,threading.Event()).structuredContent
                    def start(group,key,connection=g):
                        result=call('start_chat_session',connection=connection,conversation_id=f'111:group:{group}',persona='简短自然，不每句引用。',idempotency_key=key)
                        assert 'session' in result,result
                        return result['session']['id']
                    sid=start(222,'start-native-targets');sid2=start(223,'start-other-targets')
                    def cached(mid,session=sid):
                        with access.connect() as db:
                            r=db.execute('SELECT id,payload FROM chat_inbox WHERE session_id=? AND message_id=?',(session,str(mid))).fetchone()
                            return dict(json.loads(r['payload']),id=r['id']) if r else None
                    def add(mid,group=222,uid=333):
                        event=dict(post_type='message',message_type='group',self_id=111,group_id=group,message_id=mid,
                                   user_id=uid,sender={'user_id':uid,'nickname':'Member'},time=int(time.time()),message=[text_segment('fixture '+str(mid))])
                        TargetBot.history[str(mid)]=deepcopy(event);events.send(event)
                        target_sid=sid if group==222 else sid2
                        eventually(lambda:cached(mid,target_sid))
                        return cached(mid,target_sid)['id']
                    def read(session=sid):
                        return call('get_chat_session',session_id=session)['context']['messages']
                    def send(key,*,session=sid,**options):
                        return call('send_chat_message',session_id=session,text='接一句',idempotency_key=key,**options)
                    def rejected(result,before):
                        assert result.get('error') or result.get('state')=='FAILED',result
                        assert len(TargetBot.sent)==before,result

                    # Unread next pages, raw OneBot IDs and another session are not targets.
                    eid=add(9001);foreign=add(9002,223)
                    rejected(send('unread-target',reply_to_event_id=eid,quote=True),0)
                    read();read(sid2)
                    rejected(send('foreign-session',reply_to_event_id=foreign,quote=True),0)
                    rejected(send('raw-onebot-id',reply_to_event_id=9001,quote=True),0)
                    rejected(send('quote-missing-target',quote=True),0)
                    for i,options in enumerate((dict(reply_to_event_id=True),dict(quote='true'),dict(mention_user_ids=['all']),
                            dict(mention_user_ids=['0333']),dict(mention_user_ids=['333','333']),dict(mention_user_ids=[333]),dict(mention_user_ids=['0']),
                            dict(reply_to_event_id=2**63))):
                        rejected(send('invalid-options-'+str(i),**options),0)
                    denied=call('send_chat_message',connection=other,session_id=sid,text='forbidden',idempotency_key='wrong-grant')
                    rejected(denied,0)

                    # Old text-only caller, inert CQ text, explicit defaults and the old signature.
                    args=dict(session_id=sid,text='literal [CQ:at,qq=333] @Member',idempotency_key='legacy-text')
                    plain=call('send_chat_message',**args)
                    assert plain['state']=='SUCCEEDED',plain
                    assert TargetBot.sent[-1]['message']==[text_segment(args['text'])]
                    same=call('send_chat_message',**args,quote=False,mention_user_ids=[])
                    assert same['id']==plain['id'] and len(TargetBot.sent)==1
                    with access.connect() as db:
                        from chatlocal.mcp_actions import dump
                        import hashlib
                        signature=db.execute('SELECT signature FROM operations WHERE id=?',(plain['id'],)).fetchone()[0]
                        assert signature==hashlib.sha256(dump(['send','111:group:222',{'text':args['text']}]).encode()).hexdigest()
                    internal=send('internal-response-only',reply_to_event_id=eid)
                    assert internal['state']=='SUCCEEDED' and TargetBot.sent[-1]['message']==[text_segment('接一句')]
                    assert internal['result']['reply_to_event_id']==eid and internal['result']['quote_message_id'] is None
                    quoted=send('quote-only',reply_to_event_id=eid,quote=True)
                    assert quoted['state']=='SUCCEEDED' and TargetBot.sent[-1]['message']==[reply_segment(9001),text_segment('接一句')]
                    assert quoted['result']['mention_user_ids']==[]
                    at=send('at-only-test',mention_user_ids=['444'])
                    assert at.get('state')=='SUCCEEDED' and TargetBot.sent[-1]['message']==[at_segment(444),text_segment('接一句')],at
                    both=send('both-options',reply_to_event_id=eid,quote=True,mention_user_ids=['444','333'])
                    assert both['state']=='SUCCEEDED' and TargetBot.sent[-1]['message']==[reply_segment(9001),at_segment(444),at_segment(333),text_segment('接一句')]
                    next_part=send('split-second-part')
                    assert next_part['state']=='SUCCEEDED' and TargetBot.sent[-1]['message']==[text_segment('接一句')]
                    before=len(TargetBot.sent)
                    for options in (dict(reply_to_event_id=eid,quote=False),dict(mention_user_ids=['333']),dict(reply_to_event_id=foreign,quote=True)):
                        rejected(send('quote-only',**options),before)
                    negative=add(-9003);read()
                    assert send('negative-onebot-id',reply_to_event_id=negative,quote=True)['state']=='SUCCEEDED'
                    assert TargetBot.sent[-1]['message'][0]==reply_segment(-9003)

                    # IDs can be recycled after a bridge restart. Verify scope/sender/time,
                    # not just that get_msg happened to return something with that number.
                    original=deepcopy(TargetBot.history['9001']);before=len(TargetBot.sent)
                    mutations=[dict(group_id=223),dict(self_id=999),dict(sender={'user_id':999}),dict(time=original['time']-10),
                               dict(message_id=9002),dict(message_type='private'),dict(recalled=True)]
                    for i,change in enumerate(mutations):
                        TargetBot.history['9001']=original|change
                        rejected(send('bad-mapping-'+str(i),reply_to_event_id=eid,quote=True),before)
                    TargetBot.history['9001']=None
                    rejected(send('missing-remote-target',reply_to_event_id=eid,quote=True),before)
                    TargetBot.history['9001']=original
                    TargetBot.bad_member=True
                    rejected(send('wrong-member',mention_user_ids=['444']),before);TargetBot.bad_member=False
                    TargetBot.login=999
                    rejected(send('http-wrong-account',reply_to_event_id=eid,quote=True),before);TargetBot.login=111
                    with patch.object(tools.chat.receiver,'status',return_value={'state':'connected','account':'999'}):
                        rejected(send('ws-wrong-account',mention_user_ids=['333']),before)

                    # The receiver's periodic HTTP identity probe can observe the
                    # intentional 999 login above. Reconnect before testing echoes,
                    # otherwise this fixture races a valid account-change disconnect.
                    with events.lock:previous_connections=set(events.connections)
                    events.close_connections()
                    eventually(lambda:tools.chat.receiver.status()['state']=='connected'
                               and bool(events.connections-previous_connections))

                    # Receipt fallback has the actual requested segments, then the real
                    # WS echo wins (including provider-added @) without another inbox row.
                    own=quoted['result']['message_id'];fallback=cached(own)
                    assert fallback['source']=='send_receipt' and fallback['send_request']['reply_to_event_id']==eid
                    assert fallback['segments']==[{'type':'reply','onebot_message_id':'9001'},{'type':'text','text':'接一句'}]
                    echo=dict(post_type='message_sent',message_type='group',self_id=111,group_id=222,message_id=own,user_id=111,
                              sender={'user_id':111},time=int(time.time()),message=[reply_segment(9001),at_segment(333),text_segment('接一句')])
                    events.send(echo);eventually(lambda:cached(own)['source']=='onebot_websocket')
                    assert cached(own)['id']==fallback['id'] and cached(own)['segments'][1]=={'type':'at','qq':'333'}
                    assert cached(own)['send_request']['mention_user_ids']==[], 'Implicit provider @ mislabeled as our request'
                    retry=send('quote-only',reply_to_event_id=eid,quote=True)
                    assert retry['id']==quoted['id'] and cached(own)['source']=='onebot_websocket'
                    assert cached(own)['segments'][1]['type']=='at'
                    # Also handle WS arriving before the HTTP receipt.
                    def echo_early(action,body):
                        if action=='send_group_msg':
                            tools.chat.receive(dict(echo,message_id=100000+len(TargetBot.sent)+1,message=body['message']+[at_segment(444)]))
                    TargetBot.intercept=echo_early
                    early=send('early-echo',reply_to_event_id=eid,quote=True);TargetBot.intercept=None
                    assert cached(early['result']['message_id'])['source']=='onebot_websocket'
                    assert cached(early['result']['message_id'])['segments'][-1]['type']=='at'
                    assert cached(early['result']['message_id'])['send_request']['quote_message_id']==9001

                    # Real MCP transport exposes and executes the optional contract.
                    with patch.object(store,'connect',side_effect=AssertionError('Live send touched native chat DB')):
                        asyncio.run(wire(service,g['token'],sid,eid))

                    # Concurrent retries send once. Removing a previously used target
                    # never changes a durable success/UNKNOWN into another send.
                    before=len(TargetBot.sent)
                    with ThreadPoolExecutor(2) as pool:
                        answers=list(pool.map(lambda _:send('concurrent-quote',reply_to_event_id=eid,quote=True),range(2)))
                    assert answers[0]['id']==answers[1]['id'] and len(TargetBot.sent)==before+1
                    TargetBot.uncertain=True
                    unknown=send('unknown-quote',reply_to_event_id=eid,quote=True);TargetBot.uncertain=False
                    assert unknown['state']=='UNKNOWN',unknown
                    before=len(TargetBot.sent)
                    events.send(dict(post_type='notice',notice_type='group_recall',self_id=111,group_id=222,message_id=9001,time=int(time.time())))
                    eventually(lambda:cached(9001).get('recalled'))
                    rejected(send('recalled-target',reply_to_event_id=eid,quote=True),before)
                    assert send('unknown-quote',reply_to_event_id=eid,quote=True)['id']==unknown['id']
                    assert send('quote-only',reply_to_event_id=eid,quote=True)['id']==quoted['id']
                    with access.connect() as db:db.execute('DELETE FROM chat_inbox WHERE id=?',(eid,))
                    assert send('unknown-quote',reply_to_event_id=eid,quote=True)['id']==unknown['id']
                    rejected(send('pruned-target',reply_to_event_id=eid,quote=True),before)
                    assert len(TargetBot.sent)==before

                    # Waiting delivers only its returned page. Recalled/stop/cancel or
                    # revoke occurring in the final target check prevent the POST.
                    fresh=add(9010);later=add(9011)
                    with access.connect() as db:
                        db.execute('UPDATE chat_sessions SET cursor=?,offered=? WHERE id=?',(fresh-1,fresh-1,sid))
                    page=call('wait_chat_messages',session_id=sid,limit=1,quiet_seconds=0,timeout_seconds=1)
                    assert [m['id'] for m in page['messages']]==[fresh] and page['has_more']
                    assert all(m['event_id']<=fresh for m in page['chat_guidance']['response_targets']['items'])
                    rejected(send('undelivered-page',reply_to_event_id=later,quote=True),before)
                    read()
                    def during_lookup(callback):
                        count=0
                        def intercept(action,body):
                            nonlocal count
                            if action=='get_msg':
                                count+=1
                                if count==2:callback()
                        return intercept
                    TargetBot.intercept=during_lookup(lambda:tools.chat.recall(dict(self_id=111,group_id=222,message_id=9010)))
                    rejected(send('recall-during-check',reply_to_event_id=fresh,quote=True),before);TargetBot.intercept=None
                    cancel=threading.Event()
                    TargetBot.intercept=during_lookup(cancel.set)
                    result=tools.call(g['token'],'send_chat_message',dict(session_id=sid,text='cancel',idempotency_key='cancel-during-check',reply_to_event_id=later,quote=True),cancel).structuredContent
                    rejected(result,before);TargetBot.intercept=None
                    TargetBot.intercept=during_lookup(lambda:tools.chat.stop(sid,g['id']))
                    rejected(send('stop-during-check',reply_to_event_id=later,quote=True),before);TargetBot.intercept=None
                    rejected(send('already-stopped'),before)

                    # A surviving session uses the same delivered-target check after
                    # restart. Legacy offered events are migrated without moving ack.
                    offered=call('wait_chat_messages',session_id=sid2,quiet_seconds=0,timeout_seconds=1)
                    cursor=tools.chat.row(sid2)['cursor'];tools.chat.suspend()
                    with access.connect() as db:db.execute('ALTER TABLE chat_inbox DROP COLUMN delivered')
                    tools.chat=MCPChat(access,tools.actions);tools.chat.resume()
                    eventually(lambda:tools.chat.receiver.status()['state']=='connected')
                    assert tools.chat.row(sid2)['cursor']==cursor
                    assert tools.chat.response_target(tools.chat.row(sid2),foreign)['onebot_message_id']=='9002'
                    TargetBot.intercept=during_lookup(lambda:access.revoke(g['id']))
                    try:result=send('revoke-during-check',session=sid2,reply_to_event_id=foreign,quote=True)
                    finally:TargetBot.intercept=None
                    rejected(result,before)
                    tools.chat.stop_all();tools.chat.suspend()
    finally:
        TargetBot.intercept=None;events.close();upstream.shutdown();upstream.server_close();invalidate_availability()
    print('PASS: native reply/@ contract on MCP HTTP; plain/CQ compatibility, independent targets/quote/mentions, no sticky markers, scope/member/mapping checks, delivered-only targets, signed IDs, recall/prune/restart, durable/concurrent idempotency and UNKNOWN, cancel/stop/revoke before dispatch, requested vs observed own echoes. Isolated fixtures only; no real QQ sends.')


if __name__=='__main__':main()
