"""Planner/Replyer public MCP integration. Isolated fixtures, no real QQ/LLM.

Unlike adapter regression suites this exercises the mandatory plan gate, native
adapters, durable queue, stop/revoke and service restart without a protocol seam.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
from http.server import ThreadingHTTPServer
import io
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
from chatlocal.mcp_chat_turns import ChatTurns
from chatlocal.onebot import invalidate_availability
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import httpx


async def wire(service,token,sid):
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+token},trust_env=False,timeout=20) as http:
        async with streamable_http_client(service.status()['url'],http_client=http) as (r,w,_):
            async with ClientSession(r,w) as client:
                await client.initialize()
                names={t.name:t for t in (await client.list_tools()).tools}
                assert names['send_chat_reply'].annotations.openWorldHint
                async def call(name,**args):
                    return (await client.call_tool(name,args)).structuredContent
                read=await call('get_chat_session',session_id=sid)
                plan=await call('plan_chat_reply',session_id=sid,batch_id=read['input_batch']['id'],
                    idempotency_key='wire-plan-001',action='reply',expressions=['text'],intent='short fixture reply')
                assert plan['state']=='READY',plan
                long_text='完整解释可以保持一个气泡，不按句号机械切分。'*35
                sent=await call('send_chat_reply',session_id=sid,plan_id=plan['plan_id'],
                    idempotency_key='wire-reply-001',bubbles=[dict(kind='text',text=long_text)])
                assert sent['state']=='SUCCEEDED',sent
                assert len(sent['bubbles'])==1 and ReactionBot.sent[-1]['message']==[dict(type='text',data={'text':long_text})]
                return dict(discovered=True,state=sent['state'],bubble_count=len(sent['bubbles']))


def main():
    assert Path(__import__('chatlocal.mcp_chat_turns',fromlist=['ChatTurns']).__file__).resolve()==ROOT/'chatlocal/mcp_chat_turns.py'
    whale=ROOT/'chatlocal/prompts/mcp_chat/little_whale.md';before=hashlib.sha256(whale.read_bytes()).hexdigest()
    events=Events();ReactionBot.sent=[];ReactionBot.writes=[];ReactionBot.history={};ReactionBot.intercept=None
    upstream=ThreadingHTTPServer(('127.0.0.1',0),ReactionBot)
    threading.Thread(target=upstream.serve_forever,daemon=True).start()
    ports={events.port,upstream.server_port};connect=socket.create_connection;network=[]
    def restrict(address,*args,**kwargs):
        assert address[0] in ('localhost','127.0.0.1','::1') and address[1] in ports,address
        network.append(address);return connect(address,*args,**kwargs)
    results=[];counter=0
    try:
        with tempfile.TemporaryDirectory(prefix='tulpa-turns-') as tmp,patch.dict('os.environ',{},clear=True),patch('socket.create_connection',restrict):
            root=Path(tmp);store=Store(root/'chats.sqlite3')
            (root/'.env').write_text(f'REPLY_ONEBOT_URL=http://127.0.0.1:{upstream.server_port}\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:{events.port}\nREPLY_ONEBOT_WS_TOKEN=event-fixture\n','utf-8')
            with patch('chatlocal.onebot.ROOT',root),patch('chatlocal.message_sender.ROOT',root):
                app=FastAPI();service=install_mcp_routes(app,store);access=service.access;tools=service.tools
                with TestClient(app) as ui:
                    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                    ports.add(port);access.configure(True,port);service.start();assert service.status()['running']
                    cfg=dict(name='turn-fixture',platforms=['qq'],conversations=[['qq','111:group:222'],['qq','111:group:223']],
                        send=True,chat=True,chat_reactions=True,chat_images=True,chat_sticker_send=True)
                    g=access.create(cfg);grant=access.authorize(g['token'],source=False)
                    policy=access.path.parent/'mcp-chat-policy.json'
                    policy.write_text(json.dumps(dict(quiet_seconds=.15,reply_cooldown_seconds=0,bubble_min_seconds=.25,bubble_max_seconds=.35)),'utf-8')
                    tools.chat.reactions.policy_path.write_text('{"cooldown_seconds":0}','utf-8')
                    def call(name,**args):return tools.call(g['token'],name,args,threading.Event()).structuredContent
                    def key():
                        nonlocal counter
                        counter+=1;return f'fixture-key-{counter:04}'
                    sid=call('start_chat_session',conversation_id='111:group:222',persona_preset='little_whale_v2',idempotency_key=key())['session']['id']
                    def stored(mid):
                        with access.connect() as db:
                            r=db.execute('SELECT id FROM chat_inbox WHERE session_id=? AND message_id=?',(sid,str(mid))).fetchone()
                            return r[0] if r else None
                    def add(text='fixture',uid=333,segments=None,group=222,**extra):
                        mid=-int(key().split('-')[-1])-80000
                        event=dict(post_type='message',message_type='group',self_id=111,group_id=group,message_id=mid,message_seq=abs(mid),
                            user_id=uid,sender={'user_id':uid},time=int(time.time()),message=segments or [dict(type='text',data={'text':text})])|extra
                        ReactionBot.history[str(mid)]=deepcopy(event);events.send(event)
                        if group==222:eventually(lambda:stored(mid));return stored(mid)
                    def read():return call('get_chat_session',session_id=sid)
                    def plan(target=None,expression=None,action='reply',batch=None,**extra):
                        args=dict(session_id=sid,batch_id=(batch or read())['input_batch']['id'],action=action,intent='fixture communication',
                            expressions=expression or (['text'] if action=='reply' else []),idempotency_key=key(),**extra)
                        if target:args['target_event_id']=target
                        return call('plan_chat_reply',**args)
                    def send(p,bubbles=None,**extra):
                        return call('send_chat_reply',session_id=sid,plan_id=p['plan_id'],idempotency_key=key(),
                            bubbles=bubbles or [dict(kind='text',text='fixture reply')],**extra)
                    def clear_budget():
                        with access.connect() as db:db.execute('DELETE FROM calls')

                    bad=call('wait_chat_messages',session_id=1234)
                    assert bad['error_code']=='invalid_arguments' and bad['argument_errors'][0]==dict(path=['session_id'],rule='type',expected='string'),bad
                    bad=call('plan_chat_reply',session_id=sid,batch_id='a'*32,action='private-sentinel',intent='private-sentinel')
                    assert 'private-sentinel' not in json.dumps(bad)
                    assert bad['recovery']['executed'] is False and not ReactionBot.sent
                    from chatlocal.mcp_tools import argument_problems,argument_validator
                    import jsonschema
                    schema=next(s['parameters'] for s in tools.schemas(grant) if s['name']=='wait_chat_messages')
                    params=dict(session_id=sid,timeout_seconds=1)
                    argument_problems(params,schema);cache_before=argument_validator.cache_info()
                    began=time.perf_counter()
                    for _ in range(100):assert not argument_problems(params,schema)
                    cached_ms=(time.perf_counter()-began)*1000
                    assert argument_validator.cache_info().hits-cache_before.hits==100
                    began=time.perf_counter()
                    for _ in range(100):jsonschema.validate(params,schema)
                    uncached_ms=(time.perf_counter()-began)*1000
                    validation_cost=dict(sample_calls=100,cached_ms=round(cached_ms,2),previous_ms=round(uncached_ms,2))
                    results.append('field-specific argument errors, no private value echo, schema cache retains validation')

                    # MCP ordinary get/legacy sends are not a quiet-window bypass.
                    fast_policy=policy.read_text('utf-8');policy.write_text('{"quiet_seconds":2,"reply_cooldown_seconds":0}','utf-8')
                    eid=add('第一段')
                    denied=call('send_chat_message',session_id=sid,text='too early',idempotency_key=key())
                    assert denied['error_code']=='plan_required' and not ReactionBot.sent,denied
                    early=plan(eid);assert early.get('error_code')=='needs_wait',early
                    policy.write_text(fast_policy,'utf-8')
                    drained=call('wait_chat_messages',session_id=sid,timeout_seconds=1,quiet_seconds=0)
                    with ThreadPoolExecutor() as pool:
                        waiting=pool.submit(call,'wait_chat_messages',session_id=sid,timeout_seconds=2,acknowledge_through_id=drained['read_through_id'])
                        time.sleep(.04);add('第一段')
                        time.sleep(.06);add('第二段');time.sleep(.06);last=add('第三段')
                        batch=waiting.result(3)
                    assert [m['content'] for m in batch['messages']]==['第一段','第二段','第三段']
                    assert batch['input_batch']['collection']['reason']=='quiet'
                    assert len(batch['input_batch']['bursts'][0]['event_ids'])==3
                    p=plan(last,batch=batch);assert p['state']=='READY',p
                    # New same-speaker correction while model was composing.
                    add('更正，改成明天');out=send(p)
                    assert out['error_code']=='stale' and not ReactionBot.sent,out
                    assert out['recovery']['next_call']==dict(tool='get_chat_session',arguments=dict(session_id=sid))
                    assert send(p)['error_code']=='plan_not_ready'
                    results.append('three-part collection, ordinary read cannot bypass, stale old answer blocked')

                    # Unrelated busy traffic does not reset selected topic.
                    time.sleep(.18);p=plan(last);assert p['state']=='READY',p
                    halt=threading.Event()
                    def hot():
                        while not halt.is_set():add('无关游戏话题',uid=444);halt.wait(.04)
                    worker=threading.Thread(target=hot);worker.start()
                    try:
                        started=time.monotonic();out=send(p);elapsed=time.monotonic()-started
                        assert out['state']=='SUCCEEDED' and elapsed<3,out
                    finally:halt.set();worker.join()
                    results.append('unrelated traffic does not starve a selected response')

                    # Real spacing, one immutable queue and no duplicate dispatch.
                    clear_budget();last=add('引用和点名的目标');time.sleep(.18);p=plan(last)
                    assert p.get('state')=='READY',p
                    bubbles=[dict(kind='text',text='第一句',quote=True,mention_user_ids=['333']),dict(kind='text',text='补充原因')]
                    stamps=[];ReactionBot.intercept=lambda action,body:stamps.append(time.monotonic()) if action=='send_group_msg' else None
                    prior=len(ReactionBot.sent)
                    with ThreadPoolExecutor() as pool:
                        first=pool.submit(send,p,bubbles);time.sleep(.03);duplicate=send(p,bubbles);out=first.result(15)
                    assert out['state']=='SUCCEEDED',out
                    assert len(ReactionBot.sent)==prior+2 and stamps[-1]-stamps[-2]>=.25
                    assert send(p,bubbles)['state']=='SUCCEEDED' and len(ReactionBot.sent)==prior+2
                    assert duplicate.get('error_code')=='queue_busy' or duplicate['state']=='SUCCEEDED'
                    assert [s['type'] for s in ReactionBot.sent[-2]['message']][:2]==['reply','at']
                    assert all(s['type']=='text' for s in ReactionBot.sent[-1]['message'])
                    ReactionBot.intercept=None
                    results.append('semantic bubbles spaced, first-only native quote/at, concurrent/rekey replay no duplicate')

                    # A new speaker quoting the first bubble must interrupt tail.
                    last=add('新的可见目标');time.sleep(.18)
                    p=plan(last);assert p.get('state')=='READY',p
                    prior=len(ReactionBot.sent)
                    def interrupt(action,body):
                        if action=='send_group_msg':
                            mid=70000+len(ReactionBot.sent)+1
                            def later():
                                time.sleep(.08);add(uid=555,segments=[dict(type='reply',data={'id':str(mid)}),dict(type='text',data={'text':'等下，有个更正'})])
                            threading.Thread(target=later).start()
                    ReactionBot.intercept=interrupt
                    out=send(p,[dict(kind='text',text='先说一点'),dict(kind='text',text='旧尾部不应该发')])
                    ReactionBot.intercept=None
                    assert out['state']=='INTERRUPTED' and out['reason']=='stale',out
                    assert [b['state'] for b in out['bubbles']]==['SUCCEEDED','CANCELLED'] and len(ReactionBot.sent)==prior+1
                    assert send(p,[dict(kind='text',text='先说一点'),dict(kind='text',text='旧尾部不应该发')])['state']=='INTERRUPTED'
                    results.append('related reply to first bubble cancels tail; old queue cannot resume')

                    clear_budget();time.sleep(.18);prior=len(ReactionBot.sent)
                    assert plan(action='silence')['state']=='SILENT'
                    assert plan(action='wait',wait_seconds=1)['state']=='WAITING'
                    assert plan()['error_code']=='needs_wait'
                    call('wait_chat_messages',session_id=sid,timeout_seconds=2)
                    time.sleep(1)
                    assert len(ReactionBot.sent)==prior
                    p=plan(last,expression=['reaction']);out=send(p,[dict(kind='reaction',event_id=last,reaction_id='unicode_whale',operation='add')])
                    assert out['state']=='SUCCEEDED' and len(ReactionBot.sent)==prior,out
                    assert ReactionBot.writes[-1]['emoji_id']=='128051'
                    p=plan(last,expression=['reaction']);assert send(p,[dict(kind='reaction',event_id=last,reaction_id='unicode_whale',operation='remove')])['state']=='SUCCEEDED'
                    assert ReactionBot.writes[-1]['set'] is False
                    results.append('silence/wait and reaction-only complete without added text')
                    p=plan(last,expression=['text','reaction'])
                    out=send(p,[dict(kind='text',text='一句话配一个小回应'),dict(kind='reaction',event_id=last,reaction_id='unicode_whale',operation='add')])
                    assert out['state']=='SUCCEEDED' and len(out['bubbles'])==2,out
                    results.append('mixed text and reaction share a single checked queue')

                    # Seen-pixel and existing scope rules remain in force in queue.
                    media=tools.chat.media;aid=media.upsert(sid,'fixture:blue',dict(url='https://gchat.qpic.cn/fixture.png'))
                    buf=io.BytesIO();Image.new('RGB',(20,20),'blue').save(buf,'PNG')
                    p=plan(last,expression=['sticker']);out=send(p,[dict(kind='sticker',sticker_id=aid)])
                    assert out['state']!='SUCCEEDED'
                    with patch('chatlocal.mcp_chat_media.download',return_value=buf.getvalue()):
                        assert not tools.call(g['token'],'read_chat_sticker',dict(session_id=sid,sticker_id=aid),threading.Event()).isError
                        p=plan(last,expression=['sticker']);out=send(p,[dict(kind='sticker',sticker_id=aid)])
                    assert out['state']=='SUCCEEDED' and ReactionBot.sent[-1]['message'][0]['type']=='image',out
                    results.append('unknown image denied, seen image-only queue uses existing native adapter')

                    # UNKNOWN is durable, not restarted with a new caller key.
                    p=plan(last,expression=['reaction']);ReactionBot.mode='503';prior=len(ReactionBot.writes)
                    b=[dict(kind='reaction',event_id=last,reaction_id='unicode_fist',operation='add')]
                    out=send(p,b);ReactionBot.mode='ok';assert out['state']=='UNKNOWN',out
                    assert send(p,b)['state']=='UNKNOWN' and len(ReactionBot.writes)==prior+1
                    old=tools.chat.turns;tools.chat.turns=ChatTurns(tools.chat)
                    assert send(p,b)['state']=='UNKNOWN' and len(ReactionBot.writes)==prior+1
                    assert plan(last,batch=batch)['error_code']=='batch_expired'
                    results.append('unknown no automatic retry, process epoch invalidates old input')

                    clear_budget();time.sleep(.18)
                    report=asyncio.run(wire(service,g['token'],sid));results.append('public MCP discovery -> plan -> queue -> OneBot fixture passed')
                    # Bounded collection forces reassessment, never permission to
                    # answer a still-active speaker. Ordinary get is still gated.
                    policy.write_text('{"quiet_seconds":2,"reply_cooldown_seconds":0}','utf-8')
                    current=add('还有一段')
                    with access.connect() as db:pending=db.execute('SELECT * FROM chat_inbox WHERE session_id=? AND id=?',(sid,current)).fetchall()
                    capped=tools.chat.turns.collection(tools.chat.row(sid),pending,2,time.monotonic()-9)
                    assert capped['reason']=='collection_limit' and not capped['ready_to_send']
                    assert plan(current).get('error_code')=='needs_wait'
                    policy.write_text(fast_policy,'utf-8');time.sleep(.18)
                    p=plan(current)
                    sid2=call('start_chat_session',conversation_id='111:group:223',persona_preset='little_whale',idempotency_key=key())['session']['id']
                    foreign=call('send_chat_reply',session_id=sid2,plan_id=p['plan_id'],idempotency_key=key(),bubbles=[dict(kind='text',text='wrong group')])
                    assert foreign['error_code']=='plan_missing'
                    prior=len(ReactionBot.sent)
                    bypass=call('send_qq_message',conversation_id='111:group:222',text='bypass forbidden',idempotency_key=key())
                    assert bypass.get('error_code') and len(ReactionBot.sent)==prior
                    with patch.object(tools.chat.receiver,'status',return_value={'state':'disconnected','account':'111'}):
                        assert send(p)['error_code']=='source_unavailable'
                    assert len(ReactionBot.sent)==prior
                    with access.connect() as db:db.execute('UPDATE chat_turn_plans SET created=created-100 WHERE id=?',(p['plan_id'],))
                    assert send(p)['error_code']=='plan_expired'
                    synthetic=add('not real',synthetic=True);time.sleep(.18)
                    assert plan(synthetic)['error_code']=='target_expired'
                    call('stop_chat_session',session_id=sid2)
                    results.append('collection cap only reassesses; cross-group, expired, synthetic and ordinary-send bypass refused')

                    # Use the returned call templates without caller-generated
                    # keys. An identical retry yields the same plan and receipt.
                    clear_budget();current=add('新的可靠调用样本');time.sleep(.18)
                    readout=call('wait_chat_messages',session_id=sid,timeout_seconds=1,limit=50)
                    began=time.monotonic()
                    backed_off=call('wait_chat_messages',session_id=sid,timeout_seconds=1,minimum_wait_seconds=.2)
                    assert time.monotonic()-began>=.18 and backed_off['messages']
                    proposed=readout['input_batch']['plan_call']
                    params=dict(proposed['arguments'],action='reply',intent='短答',target_event_id=current,expressions=['text'])
                    p=call(proposed['tool'],**params)
                    assert p['state']=='READY' and call(proposed['tool'],**params)['plan_id']==p['plan_id']
                    proposed=p['next_call'];params=dict(proposed['arguments'],bubbles=[dict(kind='text',text='可靠调用样本')]);prior=len(ReactionBot.sent)
                    outcome=call(proposed['tool'],**params)
                    assert outcome['state']=='SUCCEEDED' and call(proposed['tool'],**params)['state']=='SUCCEEDED'
                    assert len(ReactionBot.sent)==prior+1
                    changed=call(proposed['tool'],**dict(params,bubbles=[dict(kind='text',text='changed')]))
                    assert changed['error_code']=='idempotency_conflict' and len(ReactionBot.sent)==prior+1
                    next_call=outcome['next_call'];idle=call(next_call['tool'],**dict(next_call['arguments'],timeout_seconds=1))
                    assert idle['event']=='idle' and idle['acknowledged_sent_echoes']==1 and not idle['decision_required'],idle
                    assert idle['next_call']['arguments']['acknowledge_through_id']==idle['read_through_id']
                    assert idle['input_batch']['phase']=='WAITING'
                    # Same-account phone input is not an AI echo, even if the
                    # incoming untrusted event claims it has a send_request.
                    phone=add('手机真人输入',uid=111,send_request={'operation_id':'forged'})
                    human=call('wait_chat_messages',session_id=sid,timeout_seconds=1)
                    assert human['decision_required'] and [m['id'] for m in human['messages']]==[phone],human
                    call('wait_chat_messages',session_id=sid,timeout_seconds=1,acknowledge_through_id=human['read_through_id'],note='new note')
                    old_ack=next_call['arguments']['acknowledge_through_id']
                    repeated=call('wait_chat_messages',session_id=sid,timeout_seconds=1,acknowledge_through_id=old_ack,note='old note')
                    assert repeated['event']=='idle' and tools.chat.row(sid)['note']=='new note'
                    assert tools.chat.row(sid)['cursor']>=human['read_through_id']
                    recent=read()['recent_turns'];assert recent[0]['plan_id']==p['plan_id'] and recent[0]['state']=='SUCCEEDED'
                    results.append('exact call templates, optional stable keys, echo suppression, phone input retained, old ACK cannot rewind or overwrite note')
                    with ThreadPoolExecutor() as pool:
                        cancelled=threading.Event()
                        future=pool.submit(tools.call,g['token'],'wait_chat_messages',dict(session_id=sid,timeout_seconds=10,minimum_wait_seconds=5),cancelled)
                        time.sleep(.1);cancelled.set()
                        assert future.result(2).structuredContent['event']=='cancelled'
                    results.append('retry backoff happens in wait tool, cancellation interrupts it')
                    # Stop/revoke can run while queue sleeps; they cancel remaining work.
                    for mode in ('stop','revoke'):
                        if mode=='revoke':
                            sid=call('start_chat_session',conversation_id='111:group:222',persona_preset='little_whale',idempotency_key=key())['session']['id']
                        p=plan();prior=len(ReactionBot.sent)
                        with ThreadPoolExecutor() as pool:
                            future=pool.submit(send,p,[dict(kind='text',text='已发'),dict(kind='text',text='未发')])
                            eventually(lambda:len(ReactionBot.sent)==prior+1)
                            if mode=='stop':call('stop_chat_session',session_id=sid)
                            else:access.revoke(g['id'])
                            out=future.result(15)
                        assert len(ReactionBot.sent)==prior+1 and out['bubbles'][1]['state']=='CANCELLED',out
                    results.append('stop and revoke interrupt sleeping tail; old and v2 persona snapshots selectable')
                    assert hashlib.sha256(whale.read_bytes()).hexdigest()==before
    finally:
        ReactionBot.intercept=None;ReactionBot.gate.set();upstream.shutdown();events.close();invalidate_availability()
    result=dict(status='passed',checks=results,original_persona_sha256=before,wire=report,validation_cost=validation_cost,
                mock_text_or_image_sends=len(ReactionBot.sent),mock_reactions=len(ReactionBot.writes),external_connections=0)
    if '--report' in sys.argv:Path(sys.argv[sys.argv.index('--report')+1]).write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
