"""Reaction dry-run: real MCP HTTP + loopback OneBot/WS fixtures, no real QQ.

--package uses that installation's Python modules with entirely temporary data.
Optional --report writes only synthetic acceptance evidence, never credentials.
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
from check_mcp_chat_targets import TargetBot, text_segment
from check_mcp_chat import Events, eventually, ROOT
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from chatlocal.store import Store
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.mcp_reactions import TOOL, ChatReactions
from chatlocal.onebot import Client, OneBotError, OneBotUncertain, invalidate_availability


class ReactionBot(TargetBot):
    requests=[]
    writes=[]
    mode='ok'
    gate=threading.Event()
    entered=threading.Event()

    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        action=self.path[1:];self.requests.append((action,deepcopy(body)))
        if type(self).intercept:type(self).intercept(action,body)
        if action=='get_login_info':data={'user_id':self.login}
        elif action=='get_group_list':data=[{'group_id':g,'group_name':'Fixture '+str(g)} for g in (222,223)]
        elif action=='get_group_info':data={'group_id':body['group_id'],'group_name':'Fixture '+str(body['group_id'])}
        elif action=='get_msg':data=self.history.get(str(body['message_id']))
        elif action=='get_group_member_info':data={'group_id':body['group_id'],'user_id':body['user_id']}
        elif action=='send_group_msg':
            self.sent.append(deepcopy(body));data={'message_id':70000+len(self.sent)}
        elif action=='set_msg_emoji_like':
            self.writes.append(deepcopy(body));data=None
            if self.mode=='blocked':self.entered.set();self.gate.wait(5)
            if self.mode in ('503','401','429'):
                self.send_response(int(self.mode));self.end_headers();return
            if self.mode=='protocol':
                self.reply({'status':'async','retcode':1,'data':None});return
            if self.mode in ('missing','unsupported','permission','sequence','failed'):
                message={'missing':'message not found','unsupported':'emoji unsupported','permission':'permission denied',
                         'sequence':'message has no authoritative QQ sequence','failed':'failure'}[self.mode]
                self.reply({'status':'failed','retcode':1400,'message':message,'data':None});return
        else:raise AssertionError(action)
        self.reply(dict(status='ok',retcode=0,data=data))

    def reply(self,value):
        raw=json.dumps(value).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers()
        try:self.wfile.write(raw)
        except OSError:pass


async def wire(service,token,sid,eid):
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+token},timeout=20,trust_env=False) as http:
        async with streamable_http_client(service.status()['url'],http_client=http) as (read,write,_):
            async with ClientSession(read,write) as client:
                await client.initialize()
                tools={t.name:t for t in (await client.list_tools()).tools}
                assert TOOL in tools and not tools[TOOL].annotations.readOnlyHint and tools[TOOL].annotations.openWorldHint
                assert 'qq_like' in tools[TOOL].description
                evidence=[]
                for op in ('add','remove'):
                    args=dict(session_id=sid,event_id=eid,reaction_id='qq_like',operation=op,idempotency_key='wire-reaction-'+op)
                    result=await client.call_tool(TOOL,args)
                    out=result.structuredContent
                    assert out['state']=='SUCCEEDED',out
                    assert out['result']['retcode']==0 and not out['result']['client_display_confirmed']
                    assert out['result']['turn_complete']
                    evidence.append(dict(input={**args,'session_id':'<synthetic session>'},downstream=ReactionBot.writes[-1],
                                         state=out['state'],retcode=out['result']['retcode'],client_display_confirmed=False))
                return evidence


def main():
    for name in ('mcp_reactions','mcp_chat','mcp_tools','message_sender','onebot'):
        mod=__import__('chatlocal.'+name,fromlist=[name]);assert Path(mod.__file__).resolve()==ROOT/'chatlocal'/f'{name}.py'
    evidence=dict(environment='isolated loopback fixtures; no real QQ',paths=[],checks=[])
    events=Events();upstream=ThreadingHTTPServer(('127.0.0.1',0),ReactionBot)
    threading.Thread(target=upstream.serve_forever,daemon=True).start()
    allowed_ports={events.port,upstream.server_port};connections=[];real_connect=socket.create_connection
    def restricted_connect(address,*args,**kwargs):
        assert address[0] in ('127.0.0.1','localhost','::1') and int(address[1]) in allowed_ports,('non-fixture connection blocked',address)
        connections.append(address);return real_connect(address,*args,**kwargs)
    try:
        with tempfile.TemporaryDirectory(prefix='tulpa-reactions-') as tmp,patch.dict('os.environ',{},clear=True),patch('socket.create_connection',restricted_connect):
            root=Path(tmp);store=Store(root/'chats.sqlite3')
            (root/'.env').write_text(f'REPLY_ONEBOT_URL=http://127.0.0.1:{upstream.server_port}\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:{events.port}\nREPLY_ONEBOT_WS_TOKEN=event-fixture\n','utf-8')
            with patch('chatlocal.onebot.ROOT',root),patch('chatlocal.message_sender.ROOT',root):
                app=FastAPI();service=install_mcp_routes(app,store);access=service.access;tools=service.tools
                from chat_adapter_fixture import use_adapter_layer
                use_adapter_layer(tools)  # Adapter regressions; turn protocol has its own integration suite.
                with TestClient(app) as ui:
                    headers={'X-ChatWeave-UI':'1'}
                    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                    allowed_ports.add(port)
                    assert ui.put('/api/mcp',json=dict(enabled=True,port=port),headers=headers).json()['running']
                    config=dict(name='reaction-fixture',platforms=['qq'],conversations=[['qq',f'111:group:{g}'] for g in (222,223)],chat=True,send=True,chat_reactions=True)
                    def new_grant(**change):
                        result=ui.post('/api/mcp/connections',json=config|change,headers=headers)
                        assert result.status_code==200,result.text
                        return result.json()
                    g=new_grant();old=new_grant(chat_reactions=False);foreign_grant=new_grant()
                    def call(name,connection=g,**args):
                        return tools.call(connection['token'],name,args,threading.Event()).structuredContent
                    def start(group,key):
                        return call('start_chat_session',conversation_id=f'111:group:{group}',persona_preset='little_whale',idempotency_key=key)['session']['id']
                    sid=start(222,'reaction-main-session');sid2=start(223,'reaction-other-session')
                    def stored(mid,session=sid):
                        with access.connect() as db:
                            row=db.execute('SELECT * FROM chat_inbox WHERE session_id=? AND message_id=?',(session,str(mid))).fetchone()
                            return dict(row) if row else None
                    def add(mid,group=222,**extra):
                        item=dict(post_type='message',message_type='group',self_id=111,group_id=group,message_id=mid,message_seq=abs(mid)+100,
                                  time=int(time.time()),user_id=333,sender={'user_id':333,'nickname':'Fixture'},message=[text_segment('synthetic target')])|extra
                        ReactionBot.history[str(mid)]=deepcopy(item);events.send(item)
                        session=sid if group==222 else sid2
                        eventually(lambda:stored(mid,session))
                        return stored(mid,session)['id']
                    def read(session=sid):return call('get_chat_session',session_id=session)
                    def react(eid,key,operation='add',reaction='qq_like',session=sid,connection=g):
                        return call(TOOL,connection=connection,session_id=session,event_id=eid,reaction_id=reaction,operation=operation,idempotency_key=key)
                    def deny(result,count,code=None):
                        assert result.get('state')=='REJECTED' or result.get('error'),result
                        if code:assert result.get('error_code')==code,result
                        assert len(ReactionBot.writes)==count
                    def policy(**kwargs):
                        tools.chat.reactions.policy_path.write_text(json.dumps(dict(cooldown_seconds=0)|kwargs),'utf-8')
                    def clear_calls():
                        with access.connect() as db:db.execute('DELETE FROM calls')
                    assert TOOL not in {s['name'] for s in tools.schemas(access.by_id(old['id'],source=False))}
                    eid=add(-9001);eid2=add(-9002,223)
                    deny(react(eid,'unread-target'),0)
                    info=read();read(sid2)
                    catalog=info['chat_prompt']['reactions'];assert catalog['authorized'] and len(catalog['candidates'])==11
                    assert catalog['cooldown_seconds']==10 and '单独完成' in catalog['hint']
                    assert 'reactions' in info['chat_prompt'] and not info['chat_prompt']['examples']
                    deny(react(eid2,'wrong-group'),0)
                    deny(react(9001,'raw-message-id'),0)
                    deny(react(eid,'wrong-grant',connection=foreign_grant),0)
                    deny(react(eid,'legacy-not-authorized',connection=old),0,'tool_unavailable')
                    deny(react(eid,'unknown-emoji',reaction='999'),0,'emoji_unknown')
                    # A legal negative OneBot ID is passed through, never the local event id.
                    r=react(eid,'first-add')
                    assert r['state']=='SUCCEEDED' and r['result']['retcode']==0,r
                    assert ReactionBot.writes==[dict(message_id=-9001,emoji_id='76',set=True)]
                    deny(react(eid,'too-soon-remove','remove'),1,'reaction_cooldown')
                    assert react(eid,'same-state-other-key')['state']=='NOOP' and len(ReactionBot.writes)==1
                    assert react(eid,'first-add')['replayed'] and len(ReactionBot.writes)==1
                    deny(react(eid,'first-add','remove'),1,'idempotency_conflict')
                    policy()
                    assert react(eid,'remove-after-add','remove')['state']=='SUCCEEDED'
                    assert ReactionBot.writes[-1]==dict(message_id=-9001,emoji_id='76',set=False)
                    assert react(eid,'add-after-remove')['state']=='SUCCEEDED'
                    evidence['checks'].append('add/remove transition, negative id, null data, cooldown, idempotency')
                    # Contract path includes MCP discovery, target resolver and real loopback HTTP.
                    wireid=add(-9010);read()
                    before=len(ReactionBot.writes);sent=len(ReactionBot.sent)
                    evidence['paths'].append(dict(name='read -> add -> remove',actions=asyncio.run(wire(service,g['token'],sid,wireid)),
                                                 write_calls=len(ReactionBot.writes)-before,text_sends=len(ReactionBot.sent)-sent))
                    assert evidence['paths'][-1]['write_calls']==2 and evidence['paths'][-1]['text_sends']==0
                    assert read()['recent_operations'][0]['kind']=='reaction'
                    # Unicode uses one decimal scalar; compound strings/unverified candidates fail.
                    uni=react(eid,'unicode-bless',reaction='unicode_bless');assert uni['state']=='SUCCEEDED'
                    assert ReactionBot.writes[-1]['emoji_id']=='12951'
                    # Screenshot catalog: all seven are Unicode code points, not
                    # visually similar QQ face IDs, literal glyphs or emCode IDs.
                    expected=dict(unicode_thumbsup='128077',unicode_anxious='128560',unicode_muscle='128170',
                                  unicode_whale='128051',unicode_question='10068',unicode_fist='128074',unicode_loud_cry='128557')
                    by_id={c['id']:c for c in catalog['candidates']}
                    for name,code in expected.items():
                        assert by_id[name]['type']=='unicode' and by_id[name]['downstream_id']==code
                        assert by_id[name]['verification']=='live_tested'
                        for operation in ('add','remove'):
                            r=react(eid,'unicode-fixture-'+name+'-'+operation,operation,reaction=name)
                            assert r['state']=='SUCCEEDED',r
                            assert ReactionBot.writes[-1]==dict(message_id=-9001,emoji_id=code,set=operation=='add')
                    evidence['checks'].append('seven screenshot Unicode entries: exact decimal IDs and add/remove payloads')
                    candidates=deepcopy(catalog['candidates']);candidates=[{k:v for k,v in c.items() if k!='executable'} for c in candidates]
                    candidates[0]['enabled']=False;policy(candidates=candidates)
                    before=len(ReactionBot.writes);deny(react(eid,'disabled-candidate'),before,'emoji_disabled_or_unverified')
                    candidates[0]['enabled']=True;candidates[0]['verification']='pending';policy(candidates=candidates)
                    deny(react(eid,'pending-candidate'),before,'emoji_disabled_or_unverified')
                    candidates[0]['downstream_id']='👍';policy(candidates=candidates)
                    deny(react(eid,'bad-codepoint'),before,'tool_unavailable');policy()
                    # Verification labels cannot be borrowed for untested codes.
                    candidates=[{k:v for k,v in c.items() if k!='executable'} for c in deepcopy(catalog['candidates'])]
                    candidates[0]['verification']='live_tested';policy(candidates=candidates)
                    deny(react(eid,'forged-live-label'),before,'tool_unavailable');policy()
                    candidates[0]['verification']='source_checked'
                    candidates[4]['downstream_id']='128514';policy(candidates=candidates)
                    deny(react(eid,'forged-live-code'),before,'tool_unavailable');policy()
                    evidence['checks'].append('live verification label restricted to exact tested built-in IDs')
                    # Cross-group mappings and all untrusted target variants produce zero writes.
                    for mid,extra in ((9101,{'synthetic':True}),(9102,{'is_notify':True}),(9103,{'virtual':True})):
                        event_id=add(mid,**extra);read();deny(react(event_id,'virtual-'+str(mid)),before,'target_not_message')
                    original=deepcopy(ReactionBot.history['-9001'])
                    for i,mutation in enumerate((dict(group_id=223),dict(self_id=999),dict(sender={'user_id':999}),dict(time=original['time']-3),
                          dict(message_type='private'),dict(recalled=True),dict(message_seq=None),dict(message_seq=123),dict(sequence_authoritative=False))):
                        ReactionBot.history['-9001']=original|mutation
                        deny(react(eid,'bad-target-'+str(i),'remove'),before)
                    ReactionBot.history['-9001']=None;deny(react(eid,'missing-target','remove'),before)
                    ReactionBot.history['-9001']=original
                    events.send(dict(post_type='notice',notice_type='group_recall',self_id=111,group_id=222,message_id=-9010))
                    eventually(lambda:json.loads(stored(-9010)['payload']).get('recalled'))
                    deny(react(wireid,'recalled-target'),before)
                    # Same raw ID in a second group cannot hijack the first group's event.
                    collision=add(-9001,223);read(sid2)
                    deny(react(eid,'collision-reject','remove'),before)
                    assert react(collision,'collision-valid',session=sid2)['state']=='SUCCEEDED'
                    ReactionBot.history['-9001']=original
                    with access.connect() as db:
                        db.execute('DELETE FROM chat_inbox WHERE session_id=? AND id=?',(sid2,eid2))
                    deny(react(eid2,'evicted-target',session=sid2),len(ReactionBot.writes))
                    evidence['checks'].append('unread/raw/cross-group/virtual/private/recalled/missing/evicted/sequence/account boundaries')
                    # Freeze service and date scope; no mutation, no reference lifetime extension.
                    before=len(ReactionBot.writes);access.configure(False,port)
                    try:react(eid,'frozen-service')
                    except ValueError:pass
                    else:raise AssertionError('disabled service accepted')
                    assert len(ReactionBot.writes)==before;access.configure(True,port)
                    before_scope=access.by_id(g['id'],source=False)['scope']
                    with access.connect() as db:
                        future_scope=before_scope|dict(start='2099-01-01')
                        db.execute('UPDATE grants SET scope=? WHERE id=?',(json.dumps(future_scope),g['id']))
                    deny(react(eid,'future-grant'),before)
                    with access.connect() as db:db.execute('UPDATE grants SET scope=? WHERE id=?',(json.dumps(before_scope),g['id']))
                    with access.connect() as db:
                        bad=before_scope|dict(conversations=[json.dumps(['qq','111:group:223'])])
                        db.execute('UPDATE grants SET scope=? WHERE id=?',(json.dumps(bad),g['id']))
                    deny(react(eid,'group-no-longer-allowed'),before)
                    with access.connect() as db:db.execute('UPDATE grants SET scope=? WHERE id=?',(json.dumps(before_scope),g['id']))
                    # Freeze/cancellation can arrive during the native read; the
                    # final guard must stop dispatch, not merely check at entry.
                    for mode in ('cancel','freeze','permission'):
                        cancel=threading.Event();before=len(ReactionBot.writes)
                        def interrupt(action,body):
                            if action!='get_msg':return
                            if mode=='cancel':cancel.set()
                            elif mode=='freeze':tools.chat.available=False
                            else:
                                with access.connect() as db:
                                    db.execute('UPDATE grants SET scope=? WHERE id=?',(json.dumps(before_scope|dict(chat_reactions=False)),g['id']))
                        ReactionBot.intercept=interrupt
                        try:
                            result=tools.call(g['token'],TOOL,dict(session_id=sid,event_id=eid,reaction_id='qq_like',operation='remove',
                                                               idempotency_key='preflight-'+mode),cancel).structuredContent
                            deny(result,before)
                        finally:
                            ReactionBot.intercept=None;tools.chat.available=True
                            with access.connect() as db:db.execute('UPDATE grants SET scope=? WHERE id=?',(json.dumps(before_scope),g['id']))
                    evidence['checks'].append('cancel/freeze/permission change during preflight: zero writes')
                    # Concurrent desired state under different request keys issues one write.
                    concurrent=add(9200);read();clear_calls();before=len(ReactionBot.writes)
                    with ThreadPoolExecutor(max_workers=5) as pool:
                        results=list(pool.map(lambda i:react(concurrent,'parallel-'+str(i)),range(5)))
                    assert len(ReactionBot.writes)==before+1 and sum(r['state']=='SUCCEEDED' for r in results)==1,results
                    # Protocol/transport errors never poison success state or auto-retry.
                    for i,mode in enumerate(('missing','unsupported','permission','sequence','failed','401','429','503','protocol')):
                        testid=add(9300+i);read();ReactionBot.mode=mode
                        before=len(ReactionBot.writes);r=react(testid,'error-'+mode)
                        assert r['state']==('UNKNOWN' if mode in ('503','protocol') else 'FAILED'),r
                        if mode=='protocol':assert r['result']['retcode']==1
                        assert len(ReactionBot.writes)==before+1
                        ReactionBot.mode='ok'
                        if r['state']=='UNKNOWN':
                            again=react(testid,'new-key-'+mode)
                            assert again['state']=='UNKNOWN' and len(ReactionBot.writes)==before+1
                        else:
                            assert react(testid,'retry-known-failure-'+mode)['state']=='SUCCEEDED'
                    # No sequence in this event; get_msg still has the verified
                    # native sequence. After restart its optional presence must
                    # not allow a different request key to bypass UNKNOWN.
                    timed=add(9400,message_seq=None);ReactionBot.history['9400']['message_seq']=9500;read()
                    real_call=Client.call
                    def timeout(client,action,body,**kw):
                        if action=='set_msg_emoji_like':raise OneBotUncertain('fixture timeout','http_timeout')
                        return real_call(client,action,body,**kw)
                    with patch.object(Client,'call',timeout):assert react(timed,'timeout-once')['state']=='UNKNOWN'
                    assert react(timed,'timeout-no-blind-retry')['state']=='UNKNOWN'
                    def refused(request):raise httpx.ConnectError('fixture refused',request=request)
                    refused_client=Client(dict(url='http://127.0.0.1:1',token=''),transport=httpx.MockTransport(refused))
                    try:refused_client.call('set_msg_emoji_like',dict(message_id=-9001,emoji_id='76',set=True),approved=True)
                    except OneBotError as exc:assert type(exc) is OneBotError and exc.code=='http_unreachable'
                    else:raise AssertionError('connection refusal was not reported')
                    evidence['checks'].append('concurrency, explicit failures, timeout, unknown no-retry, failed state recovery')
                    # A slow reaction does not consume ordinary query/wait slots.
                    # A contradictory own echo racing with its receipt must not
                    # become a stale successful idempotency shortcut.
                    slow=add(9450);read();ReactionBot.mode='blocked';ReactionBot.gate.clear();ReactionBot.entered.clear()
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        future=pool.submit(react,slow,'slow-reaction')
                        assert ReactionBot.entered.wait(5)
                        began=time.monotonic();parallel=read();latency=time.monotonic()-began
                        assert parallel['session']['id']==sid and not future.done()
                        events.send(dict(post_type='notice',notice_type='group_msg_emoji_like',sub_type='remove',self_id=111,group_id=222,
                                         operator_id=111,message_id=9450,message_seq=9550,time=int(time.time()),likes=[dict(emoji_id='76',count=0)]))
                        eventually(lambda:any(n['target_event_id']==slow for n in tools.chat.reactions.context(tools.chat.row(sid))['items']))
                        ReactionBot.gate.set();assert future.result(5)['state']=='SUCCEEDED'
                    ReactionBot.mode='ok';before=len(ReactionBot.writes)
                    assert react(slow,'after-contradictory-echo')['state']=='SUCCEEDED' and len(ReactionBot.writes)==before+1
                    with access.connect() as db:db.execute('DELETE FROM chat_reaction_notices')
                    evidence['parallel_read_seconds']=round(latency,3)
                    evidence['checks'].append('ordinary query during blocked write; own echo/receipt race')
                    # Precise notifications, no chat insertion, no extra writes and no early wake.
                    clear_calls();notice_target=add(9500);read()
                    current=call('wait_chat_messages',session_id=sid,timeout_seconds=1,quiet_seconds=0,limit=50)
                    ack=current['read_through_id'];before=len(ReactionBot.writes);sent=len(ReactionBot.sent)
                    def notice(op,uid=444,count=1,**extra):
                        e=dict(post_type='notice',notice_type='group_msg_emoji_like',sub_type=op,self_id=111,group_id=222,
                               user_id=uid,operator_id=uid,message_id=9500,message_seq=9600,time=int(time.time()),likes=[dict(emoji_id='76',count=count)])|extra
                        events.send(e);return e
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        future=pool.submit(call,'wait_chat_messages',session_id=sid,acknowledge_through_id=ack,timeout_seconds=2,quiet_seconds=0)
                        eventually(lambda:sid in tools.chat.waiters)
                        event=notice('add');events.send(event)
                        eventually(lambda:len(tools.chat.reactions.context(tools.chat.row(sid))['items'])==1)
                        time.sleep(.15);assert not future.done(), 'reaction notification woke a normal wait'
                        notice('remove',count=0)
                        result=future.result(4)
                    observations=result['chat_guidance']['reaction_notices']['items']
                    assert [n['operation'] for n in observations]==['add','remove'],observations
                    assert all(n['target_event_id']==notice_target and n['operator_id']=='444' for n in observations)
                    assert [n['reported_count'] for n in observations]==[1,0]
                    assert result['event']=='idle' and result['messages']==[] and len(ReactionBot.writes)==before and len(ReactionBot.sent)==sent
                    evidence['paths'].append(dict(name='peer add/remove -> next normal context',notifications=observations,write_calls=0,text_sends=0,woke_wait=False))
                    notice('add',uid=111);event=notice('add',uid=111);events.send(event)
                    eventually(lambda:any(n['is_self'] for n in tools.chat.reactions.context(tools.chat.row(sid))['items']))
                    assert len(ReactionBot.writes)==before
                    n=len(tools.chat.reactions.context(tools.chat.row(sid))['items'])
                    notice('add',message_seq=77777);notice('add',group_id=223);notice('add',message_id=88888)
                    time.sleep(.15);assert len(tools.chat.reactions.context(tools.chat.row(sid))['items'])==n
                    # Unknown fields stay unknown, not invented members or zero counts.
                    notice('add',uid=0,count=None,time=None)
                    eventually(lambda:any(n['operator_id'] is None for n in tools.chat.reactions.context(tools.chat.row(sid))['items']))
                    unknown=tools.chat.reactions.context(tools.chat.row(sid))['items'][-1]
                    assert unknown['timestamp'] is None and unknown['reported_count'] is None
                    evidence['checks'].append('notice association, add/remove, dedup, own echo, unknown fields, no wake or sends')
                    # Reproduce a WS node with reportSelfMessage=false. Only the
                    # controlled successful send receipt exists for our message.
                    clear_calls();before=len(ReactionBot.writes)
                    sent=call('send_chat_message',session_id=sid,text='Fixture own-message marker',idempotency_key='receipt-notice-target')
                    assert sent['state']=='SUCCEEDED'
                    mid=sent['result']['message_id'];own=stored(mid);ownid=own['id']
                    assert json.loads(own['payload'])['source']=='send_receipt'
                    read();drained=call('wait_chat_messages',session_id=sid,timeout_seconds=1,quiet_seconds=0,limit=50)
                    ack=drained['read_through_id'];sends=len(ReactionBot.sent)
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        future=pool.submit(call,'wait_chat_messages',session_id=sid,acknowledge_through_id=ack,timeout_seconds=8,quiet_seconds=.2)
                        eventually(lambda:sid in tools.chat.waiters)
                        notice('add',message_id=mid,message_seq=77001)
                        notice('remove',count=0,message_id=mid,message_seq=77001)
                        result=future.result(3)
                    notices=result['chat_guidance']['reaction_notices']
                    assert result['event']=='reactions' and result['messages']==[],result
                    assert [n['operation'] for n in notices['new_items']]==['add','remove']
                    assert all(n['target_event_id']==ownid and n['target_source']=='send_receipt' for n in notices['new_items'])
                    assert any('按' in n or '决定' in n for n in result['chat_guidance']['reminders'])
                    assert not any('不发群消息，现在直接' in n for n in result['chat_guidance']['reminders'])
                    assert len(ReactionBot.writes)==before and len(ReactionBot.sent)==sends
                    # No repeated participation from cached notices or duplicate
                    # delivery. Our own echo never wakes the chat.
                    notice('remove',count=0,message_id=mid,message_seq=77001)
                    notice('add',uid=111,message_id=mid,message_seq=77001)
                    idle=call('wait_chat_messages',session_id=sid,timeout_seconds=1,quiet_seconds=0)
                    assert idle['event']=='idle' and not idle['chat_guidance']['reaction_notices']['new_items'],idle
                    # Receipt observation does not turn it into a write target.
                    deny(react(ownid,'receipt-is-not-write-target'),before,'target_expired')
                    # Missing/foreign/failed receipt, private source, bad seq,
                    # synthetic and expired records remain untrusted.
                    original=deepcopy(json.loads(own['payload']))
                    accepted=len(tools.chat.reactions.context(tools.chat.row(sid))['items'])
                    for i,mutation in enumerate(({'synthetic':True},{'reaction_epoch':'old'},
                            {'sender_id':'333'},{'send_request':{'operation_id':'unknown'}},{'recalled':True})):
                        with access.connect() as db:db.execute('UPDATE chat_inbox SET payload=? WHERE id=?',(json.dumps(original|mutation),ownid))
                        tools.chat.receive(dict(post_type='notice',notice_type='group_msg_emoji_like',sub_type='add',self_id=111,group_id=222,
                            user_id=555,operator_id=555,message_id=mid,message_seq=77001,time=int(time.time()),likes=[dict(emoji_id='76',count=i+2)]))
                    time.sleep(.2)
                    assert len(tools.chat.reactions.context(tools.chat.row(sid))['items'])==accepted
                    with access.connect() as db:db.execute('UPDATE chat_inbox SET payload=? WHERE id=?',(json.dumps(original),ownid))
                    notice('add',uid=555,message_id=mid,message_seq=0)
                    time.sleep(.1)
                    assert len(tools.chat.reactions.context(tools.chat.row(sid))['items'])==accepted
                    evidence['paths'].append(dict(name='suppressed own WS echo -> peer notice -> reactions batch',
                        setup_text_sends=1,reaction_generated_text_sends=0,notifications=notices['new_items'],write_calls=0))
                    evidence['checks'].append('verified own send receipt observation, peer-only coalesced wake, once-only delivery, no idle suppression, unchanged write boundary')
                    # Global rate budget is shared with normal calls; stop remains exempt.
                    with access.connect() as db:
                        db.executemany('INSERT INTO calls(grant_id,tool,at,status) VALUES(?,?,?,?)',[(g['id'],'fixture',time.time(),'ok')]*120)
                    deny(react(eid,'global-rate-limit'),len(ReactionBot.writes),'rate_limited');clear_calls()
                    # Endpoint/connection changes and restart cannot reuse a known remote state.
                    old_binding=tools.chat.receiver.http_binding;tools.chat.receiver.http_binding=('http://127.0.0.1:1','')
                    deny(react(eid,'wrong-instance'),len(ReactionBot.writes),'instance_changed');tools.chat.receiver.http_binding=old_binding
                    tools.chat.reactions.reset_connection()
                    deny(react(eid,'expired-connection'),len(ReactionBot.writes),'target_expired')
                    tools.chat.reactions=ChatReactions(tools.chat)
                    with access.connect() as db:assert db.execute('SELECT count(*) FROM chat_reaction_state').fetchone()[0]==0
                    # A fresh session/read of the same uncertain target still cannot retry it.
                    # Active sessions survive restart, but old references do not.
                    # Re-deliver the same target after its stale cache row expires.
                    with access.connect() as db:db.execute('DELETE FROM chat_inbox WHERE session_id=? AND message_id=?',(sid,'9400'))
                    info=ReactionBot.history['9400'];events.send(info)
                    eventually(lambda:stored(9400,sid));read(sid)
                    timed2=stored(9400,sid)['id']
                    blocked=react(timed2,'unknown-after-restart',session=sid)
                    assert blocked['state']=='UNKNOWN',blocked
                    # Revocation after dispatch cannot make a successful write
                    # look REJECTED. Its receipt survives; future calls are denied.
                    final=add(9900);read();ReactionBot.mode='blocked';ReactionBot.gate.clear();ReactionBot.entered.clear()
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        future=pool.submit(react,final,'revoke-after-dispatch')
                        assert ReactionBot.entered.wait(5)
                        access.revoke(g['id']);ReactionBot.gate.set()
                        assert future.result(5)['state']=='SUCCEEDED'
                    ReactionBot.mode='ok'
                    try:react(timed2,'revoked-grant',session=sid)
                    except ValueError:pass
                    else:raise AssertionError('revoked grant accepted')
                    evidence['checks'].append('global limiter, instance binding, disconnect expiry, restart unknown ledger, revocation')
                    evidence.update(network_connections=len(connections),external_connections=0,onebot_reaction_requests=len(ReactionBot.writes),
                                    text_sends=len(ReactionBot.sent),catalog_items=len(catalog['candidates']))
                    assert len(ReactionBot.sent)==1  # Explicit fixture target, never auto-generated text.
    finally:
        ReactionBot.gate.set();upstream.shutdown();events.close();invalidate_availability()
    if '--report' in sys.argv:
        path=Path(sys.argv[sys.argv.index('--report')+1]);path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(dict(status='passed',checks=evidence['checks'],paths=len(evidence['paths']),external_connections=0,
                         fixture_setup_text_sends=1,reaction_generated_text_sends=0),ensure_ascii=False))


if __name__=='__main__':main()
