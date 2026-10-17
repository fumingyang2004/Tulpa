"""SL extensions: real MCP/HTTP/WS against an isolated QQ protocol fixture."""
import asyncio
import base64
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from test_environment import system_environment
from unittest.mock import patch
import wave

sys.path.insert(0,str(Path(__file__).resolve().parent))
from check_mcp_chat import Events, eventually, ROOT
from check_mcp_chat_media import fixture_image
from chatlocal.mcp_qq_permissions import QQ_FLAGS
from chatlocal.mcp_qq_catalog import WRITE_APIS, CATALOG
from chatlocal.mcp_qq_media import attachment_ref, MAX_BYTES, decode
from chatlocal.mcp_chat_media import remote_target
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.store import Store
from chatlocal.onebot import invalidate_availability
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def audio():
    out=io.BytesIO()
    with wave.open(out,'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\0\0'*1600)
    return out.getvalue()


class Bot(BaseHTTPRequestHandler):
    calls=[];writes=[];history={};account=111;role='owner';uncertain=False;intercept=None
    def log_message(self,*args):pass
    def do_POST(self):
        args=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        action=self.path[1:];type(self).calls.append((action,deepcopy(args)))
        if type(self).intercept:type(self).intercept(action,args)
        if action=='get_login_info':data={'user_id':self.account}
        elif action=='get_version_info':data={'app_name':'SnowLuma','app_version':'1.14.22-fixture'}
        elif action=='get_group_list':data=[dict(group_id=g,group_name='Fixture '+str(g)) for g in (222,223)]
        elif action=='get_friend_list':data=[dict(user_id=u,nickname='Friend '+str(u),remark='') for u in (333,334)]
        elif action=='get_group_info':data=dict(group_id=args['group_id'],group_name='Fixture '+str(args['group_id']))
        elif action=='get_group_member_info':data=dict(group_id=args['group_id'],user_id=args['user_id'],role=self.role if args['user_id']==111 else 'member',nickname='Fixture')
        elif action=='get_msg':data=deepcopy(self.history.get(args['message_id']))
        elif action in ('get_group_msg_history','get_friend_msg_history'):data={'messages':list(deepcopy(self.history).values())}
        elif action=='get_record':data={'base64':base64.b64encode(audio()).decode(),'out_format':'wav'}
        elif action=='fetch_ptt_text':data={'text':'这是隔离测试语音'}
        elif action in ('get_group_file_url','get_private_file_url'):data={'url':'https://gchat.qpic.cn/fixture.bin'}
        elif action=='get_ai_characters':data=[{'type':'test','characters':[{'character_id':'voice-fixture','character_name':'Fixture'}]}]
        elif action=='get_forward_msg':data={'messages':[{'content':'Forward fixture'}]}
        elif action=='get_stranger_info':data={'user_id':111,'nickname':'Fixture'}
        elif action=='fetch_custom_face_detail':data=[]
        elif action in ('_get_group_notice','get_essence_msg_list','get_friends_with_category'):data=[]
        elif action=='get_group_admin_settings':data={'member_invite_policy':'require_approval'}
        elif action=='get_qun_album_list':data={'album_list':[{'album_id':'album1','name':'Fixture'}],'attach_info':'','has_more':False}
        elif action=='get_group_album_media_list':data={'mediaList':[{'lloc':'photo1'}],'nextAttachInfo':''}
        elif action=='get_qzone_msg_list':data={'msglist':[{'tid':'tid1','content':'Fixture'}],'total':1}
        elif action=='get_qzone_feeds':data={'feeds':[],'has_more':False}
        elif action in ('get_group_root_files','get_group_files_by_folder'):data={'files':[{'file_id':'file1','file_name':'test.txt'}],'folders':[]}
        elif action in WRITE_APIS:
            type(self).writes.append((action,deepcopy(args)))
            if self.uncertain:self.send_response(503);self.end_headers();return
            data={'message_id':70000+len(self.writes)} if action in ('send_group_msg','send_private_msg','send_group_ai_record','forward_group_single_msg','forward_friend_single_msg','send_group_forward_msg','send_private_forward_msg') else {'file_id':'uploaded'} if action in ('upload_group_file','upload_private_file') else {'tid':'tid-new'} if action=='send_qzone_msg' else None
        else:
            self.send_error(404);return
        raw=json.dumps(dict(status='ok',retcode=0,data=data)).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)


async def wire(service, grant, denied, raw):
    url=service.status()['url']
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+grant['token']},timeout=30,trust_env=False) as http:
        async with streamable_http_client(url,http_client=http) as (read,write,_):
            async with ClientSession(read,write) as c:
                await c.initialize()
                listed={t.name:t for t in (await c.list_tools()).tools}
                assert set(CATALOG)<=listed.keys()
                assert listed['qq_qzone_action'].annotations.openWorldHint
                assert listed['qq_group_action'].annotations.destructiveHint
                assert not listed['import_qq_media'].annotations.openWorldHint
                payload=dict(kind='file',filename='wire.txt',data_base64=base64.b64encode(raw).decode())
                r=await c.call_tool('import_qq_media',payload)
                assert not r.isError and r.structuredContent['bytes']==len(raw),r.structuredContent
                duplicate=await c.call_tool('import_qq_media',payload)
                assert duplicate.structuredContent['asset_id']==r.structuredContent['asset_id']
                aid=r.structuredContent['asset_id']
                invalid=await c.call_tool('qq_qzone_action',dict(action='send_qzone_msg',params={'content':'DO-NOT-ECHO-PRIVATE-ARG'},idempotency_key='missing-visibility'))
                assert invalid.isError and 'DO-NOT-ECHO-PRIVATE-ARG' not in invalid.model_dump_json()
                invalid=await c.call_tool('qq_qzone_action',dict(action='send_qzone_msg',params={'content':'x','ugc_right':4,'images':['file:///private']},idempotency_key='path-injection'))
                assert invalid.isError
        large=dict(jsonrpc='2.0',id=11,method='tools/call',params=dict(name='import_qq_media',arguments=payload))
        assert (await http.post(url,headers={'Authorization':'Bearer '+denied['token']},json=large)).status_code==413
        large['params']['name']='get_qq_capabilities'
        assert (await http.post(url,json=large)).status_code==413
    return aid


def main():
    events=Events();upstream=ThreadingHTTPServer(('127.0.0.1',0),Bot)
    threading.Thread(target=upstream.serve_forever,daemon=True).start();checks=[]
    try:
        with tempfile.TemporaryDirectory(prefix='tulpa-sl-extensions-') as tmp,patch.dict('os.environ',system_environment(),clear=True):
            root=Path(tmp)
            (root/'.env').write_text(f'REPLY_ONEBOT_URL=http://127.0.0.1:{upstream.server_port}\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:{events.port}\nREPLY_ONEBOT_WS_TOKEN=event-fixture\n')
            with patch('chatlocal.onebot.ROOT',root),patch('chatlocal.message_sender.ROOT',root):
                app=FastAPI();service=install_mcp_routes(app,Store(root/'chats.sqlite3'))
                access=service.access;tools=service.tools
                with TestClient(app) as ui:
                    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
                    access.configure(True,port);invalidate_availability();service.start()
                    (root/'mcp-chat-policy.json').write_text('{"quiet_seconds":0.1,"reply_cooldown_seconds":0,"bubble_min_seconds":0.1}')
                    base=dict(name='extensions',platforms=['qq'],conversations=[['qq','111:group:222'],['qq','111:group:223'],['qq','111:direct:333']],chat=True,send=True)
                    g=access.create(base|dict(manage=True,chat_images=True,chat_sticker_send=True,**{f:True for f in QQ_FLAGS}))
                    denied=access.create(base)
                    other=access.create(base|dict(**{f:True for f in QQ_FLAGS}))
                    def call(name,*,conn=g,cancel=None,**args):return tools.call(conn['token'],name,args,cancel or threading.Event())
                    def data(name,**args):
                        r=call(name,**args);assert not r.isError,r.structuredContent;return r.structuredContent
                    def start(cid):return data('start_chat_session',conversation_id=cid,persona='测试人格',idempotency_key='start-'+cid)['session']['id']
                    sid=start('111:group:222');private=start('111:direct:333')
                    eventually(lambda:tools.chat.receiver.status()['state']=='connected')
                    tools.chat.learning.configure(sid,{'enabled':False})
                    def phase():
                        with access.connect() as db:db.execute('DELETE FROM calls')
                    def send(s,bubble):
                        time.sleep(.12)
                        batch=data('get_chat_session',session_id=s)['input_batch']['id']
                        p=data('plan_chat_reply',session_id=s,batch_id=batch,action='reply',expressions=[bubble['kind']],intent='执行明确授权的隔离验收操作')
                        assert p['state']=='READY',p
                        args=dict(session_id=s,plan_id=p['plan_id'],bubbles=[bubble])
                        return data('send_chat_reply',**args),args
                    def upload(kind,raw,name):return data('import_qq_media',kind=kind,filename=name,data_base64=base64.b64encode(raw).decode())['asset_id']
                    assert [r['conversation_id'] for r in data('list_chat_friends')['items']]==['111:direct:333']
                    before=len(Bot.writes);blob=b'fixture text\n'*12000
                    file=asyncio.run(wire(service,g,denied,blob))
                    assert len(Bot.writes)==before
                    voice=upload('voice',audio(),'voice.wav')
                    video=upload('video',b'\x00\x00\x00\x18ftypmp42'+b'\0'*64,'video.mp4')
                    img=upload('image',fixture_image(),'avatar.png')
                    for session,kind,aid in ((sid,'voice',voice),(private,'voice',voice),(sid,'video',video),(private,'video',video),(sid,'file',file),(private,'file',file)):
                        outcome,args=send(session,dict(kind=kind,asset_id=aid));assert outcome['state']=='SUCCEEDED',outcome
                        api,payload=Bot.writes[-1]
                        assert ('group_id' in payload)==(session==sid)
                        encoded=payload['file'] if kind=='file' else payload['message'][0]['data']['file']
                        assert base64.b64decode(encoded[9:])==({'voice':audio(),'video':b'\x00\x00\x00\x18ftypmp42'+b'\0'*64,'file':blob}[kind])
                        count=len(Bot.writes);assert data('send_chat_reply',**args)['state']=='SUCCEEDED' and len(Bot.writes)==count
                    outcome,_=send(sid,dict(kind='ai_voice',character_id='voice-fixture',text='语音测试'))
                    assert outcome['state']=='SUCCEEDED' and Bot.writes[-1][0]=='send_group_ai_record'
                    image=data('import_chat_image',session_id=private,data_base64=base64.b64encode(fixture_image()).decode())['sticker_id']
                    outcome,_=send(private,dict(kind='sticker',sticker_id=image))
                    assert outcome['state']=='SUCCEEDED' and Bot.writes[-1][0]=='send_private_msg'
                    outcome,_=send(private,dict(kind='text',text='私聊文本'))
                    assert outcome['state']=='SUCCEEDED' and Bot.writes[-1][0]=='send_private_msg'
                    checks.append('real MCP discovery/upload >32 KiB, private text/image, voice/video/file bytes to both scopes, AI voice, replay dedup')
                    phase()
                    def receive(mid,kind='group',uid=333,peer=222,segments=None,subtype=None):
                        event=dict(post_type='message',message_type=kind,self_id=111,message_id=mid,user_id=uid,sender=dict(user_id=uid,nickname='Fixture'),time=int(time.time()),message=segments or [dict(type='text',data=dict(text='新消息'))])
                        event.update({'group_id':peer} if kind=='group' else {'target_id':peer if uid==111 else 111,'sub_type':subtype or 'friend'})
                        Bot.history[mid]=deepcopy(event);events.send(event)
                        s=sid if kind=='group' else private
                        def cached():
                            with access.connect() as db:return db.execute('SELECT id FROM chat_inbox WHERE session_id=? AND message_id=?',(s,str(mid))).fetchone()
                        eventually(cached)
                        return cached()[0]
                    eid=receive(9001,segments=[dict(type='record',data=dict(file='voice1')),dict(type='file',data=dict(file_id='file1',name='real.txt')),dict(type='video',data=dict(url='https://gchat.qpic.cn/video.mp4'))])
                    assert call('read_chat_attachment',session_id=sid,event_id=eid,index=0).isError
                    current=data('get_chat_session',session_id=sid)
                    assert [s['attachment_index'] for s in current['context']['messages'][-1]['segments']]==[0,1,2]
                    r=call('read_chat_attachment',session_id=sid,event_id=eid,index=0)
                    assert not r.isError and r.structuredContent['transcript']=='这是隔离测试语音'
                    assert any(c.type=='audio' for c in r.content)
                    for index,raw in ((1,b'actual file'),(2,b'\x00\x00\x00\x18ftypmp42'+b'\0'*64)):
                        with patch('chatlocal.mcp_qq_media.download',return_value=raw):
                            r=data('read_chat_attachment',session_id=sid,event_id=eid,index=index)
                        assert Path(r['local_path']).read_bytes()==raw
                        if os.name == 'posix':
                            assert Path(r['local_path']).stat().st_mode&0o077==0
                    assert call('read_chat_attachment',session_id=private,event_id=eid,index=0).isError
                    incoming=receive(9002,'private',peer=333)
                    private_file=receive(9004,'private',peer=333,segments=[dict(type='file',data=dict(file_id='private-file',file_hash='trusted-hash',name='friend.txt'))])
                    data('get_chat_session',session_id=private)
                    with patch('chatlocal.mcp_qq_media.download',return_value=b'private file'):
                        result=data('read_chat_attachment',session_id=private,event_id=private_file,index=0)
                        assert result['filename']=='friend.txt'
                    assert ('get_private_file_url',dict(user_id=333,file_id='private-file',file_hash='trusted-hash')) in Bot.calls
                    own=receive(9003,'private',uid=111,peer=333)
                    context=data('get_chat_session',session_id=private)['context']['messages']
                    assert any(m['id']==own and m['is_self'] for m in context)
                    # Private native quote verifies recipient/sender/time, with no @.
                    time.sleep(.12);batch=data('get_chat_session',session_id=private)['input_batch']['id']
                    p=data('plan_chat_reply',session_id=private,batch_id=batch,action='reply',target_event_id=incoming,expressions=['text'],intent='回复私聊原消息')
                    r=data('send_chat_reply',session_id=private,plan_id=p['plan_id'],bubbles=[dict(kind='text',text='引用回复',quote=True)])
                    assert r['state']=='SUCCEEDED' and Bot.writes[-1][1]['message'][0]['type']=='reply',r
                    events.send(dict(post_type='notice',notice_type='friend_recall',self_id=111,user_id=333,message_id=9002))
                    def recalled():
                        with access.connect() as db:
                            row=db.execute('SELECT payload FROM chat_inbox WHERE id=?',(incoming,)).fetchone()
                        return row and json.loads(row[0]).get('recalled')
                    eventually(recalled)
                    checks.append('live attachment read/audio/transcript, private inbound/self-echo/quote/recall, unread and cross-session denial')
                    phase()
                    def op(tool,action,params=None,cid='111:group:223',**extra):
                        args=dict(action=action,params=params or {},idempotency_key='test-'+action,**extra)
                        if CATALOG[tool]['scope']!='account':args['conversation_id']=cid
                        return data(tool,**args),args
                    r,args=op('qq_interact','send_poke',{'user_id':'333'})
                    assert r['state']=='SUCCEEDED' and Bot.writes[-1]==('send_poke',dict(group_id=223,user_id=333))
                    assert call('qq_interact',action='send_poke',conversation_id='111:group:222',params={'user_id':'333'},idempotency_key='bypass-plan').isError
                    r,_=send(sid,dict(kind='qq_action',tool='qq_interact',action='send_poke',params={'user_id':'333'}))
                    assert r['state']=='SUCCEEDED' and Bot.writes[-1][1]['group_id']==222
                    Bot.history[9010]=dict(Bot.history[9001],group_id=223,message_id=9010)
                    Bot.history[9011]=dict(Bot.history[9001],group_id=999,message_id=9011)
                    # Destination 223 is idle; source 222 is authorized and current.
                    r,_=op('qq_message_action','forward_many',dict(source_conversation_id='111:group:222',message_ids=[9001]))
                    assert r['state']=='SUCCEEDED' and Bot.writes[-1][1]['messages']==[{'type':'node','data':{'id':'9001'}}]
                    assert call('qq_message_action',action='delete_msg',conversation_id='111:group:223',params={'message_id':9011},idempotency_key='foreign-msg').isError
                    history,_=op('qq_message_action','history',{'count':10})
                    assert [m['message_id'] for m in history['data']['messages']]==[9010]
                    more=dict(Bot.history[9010],message_id=-9012,time=Bot.history[9010]['time']+1)
                    Bot.history[-9012]=more
                    history,_=op('qq_message_action','history',{'count':10},limit=1)
                    assert history['data']['next_message_id']==9010 and history['pagination']['messages']['next_offset']==1
                    following,_=op('qq_message_action','history',{'count':10},limit=1,offset=1)
                    assert following['data']['messages'][0]['message_id']==-9012
                    assert not following['pagination']['messages']['has_more']
                    for tool,action,params in (
                        ('qq_group_action','set_group_whole_ban',{'enable':False}),
                        ('qq_group_action','set_group_admin',{'user_id':'333','enable':True}),
                        ('qq_group_action','set_group_card',{'user_id':'333','card':'测试名片'}),
                        ('qq_group_action','set_group_portrait',{'asset_id':img}),
                        ('qq_group_action','_send_group_notice',{'content':'测试公告','asset_id':img}),
                        ('qq_file_action','rename_group_file',{'file_id':'file1','new_name':'changed.txt'}),
                        ('qq_file_action','create_group_file_folder',{'name':'测试目录'}),
                        ('qq_album_action','upload_image_to_qun_album',{'album_id':'album1','album_name':'Fixture','asset_id':img}),
                        ('qq_album_action','upload_video_to_qun_album',{'album_id':'album1','album_name':'Fixture','asset_id':video}),
                        ('qq_album_action','do_group_album_comment',{'album_id':'album1','lloc':'photo1','content':'测试评论'}),
                        ('qq_profile_action','set_qq_avatar',{'asset_id':img}),
                        ('qq_profile_action','set_self_longnick',{'longNick':'测试签名'}),
                        ('qq_friend_action','set_friend_remark',{'user_id':'333','remark':'测试备注'}),
                        ('qq_friend_action','set_friends_category',{'user_id':'333','category_id':2}),
                        ('qq_qzone_action','send_qzone_msg',{'content':'测试说说','ugc_right':64,'asset_ids':[img]}),
                        ('qq_qzone_action','comment_qzone',{'tid':'tid1','target_uin':'333','content':'测试评论','asset_ids':[img]}),
                        ('qq_qzone_action','delete_qzone_msg',{'tid':'tid1'}),
                    ):
                        r,args=op(tool,action,params);assert r['state']=='SUCCEEDED',(tool,action,r)
                        before=len(Bot.writes);assert data(tool,**args)['state']=='SUCCEEDED' and len(Bot.writes)==before
                        assert call(tool,conn=denied,**args).isError
                    Bot.role='member'
                    assert call('qq_group_action',action='set_group_whole_ban',conversation_id='111:group:223',params={'enable':True},idempotency_key='not-admin').isError
                    Bot.role='owner'
                    events.send(dict(post_type='request',request_type='friend',self_id=111,user_id=555,flag='verified-request-flag',comment='测试申请'))
                    def request_ready():
                        with access.connect() as db:return db.execute('SELECT id FROM qq_friend_requests').fetchone()
                    eventually(request_ready)
                    requests,_=op('qq_friend_action','requests');rid=requests['data'][0]['request_id']
                    assert 'verified-request-flag' not in json.dumps(requests)
                    r,_=op('qq_friend_action','approve_request',{'request_id':rid,'approve':False})
                    assert r['state']=='SUCCEEDED' and Bot.writes[-1]==('set_friend_add_request',{'flag':'verified-request-flag','approve':False})
                    checks.append('scoped forwarding/history/interaction, native admin/file/album/profile/friend/qzone payloads, request handle, permissions and idempotency')
                    phase()
                    before=len(Bot.writes)
                    assert call('qq_qzone_action',action='send_qzone_msg',params={'content':'x','ugc_right':16},idempotency_key='missing-targets').isError
                    assert call('qq_profile_action',conn=other,action='set_qq_avatar',params={'asset_id':img},idempotency_key='foreign-asset').isError
                    for invalid in ('https://example.invalid/file','file:///private','data:audio/wav;base64,AA==','秘密'):
                        assert call('import_qq_media',kind='voice',filename='x.wav',data_base64=invalid).isError
                    for badname in ('../secret','/tmp/private','x\\y','x\x00y'):
                        assert call('import_qq_media',kind='file',filename=badname,data_base64='eA==').isError
                    assert not attachment_ref('record',{'file':'/etc/passwd'}).get('file')
                    assert remote_target('https://203.205.137.30:8080/file',trusted_file=True).port==8080
                    for url in ('http://127.0.0.1:8080/file','http://169.254.169.254/file','http://10.0.0.1/file','https://qq.com.evil.invalid/file'):
                        try:remote_target(url,trusted_file=True);raise AssertionError('private or foreign download address accepted')
                        except ValueError:pass
                    assert len(Bot.writes)==before
                    Bot.account=777
                    assert call('qq_profile_action',action='set_self_longnick',params={'longNick':'changed'},idempotency_key='wrong-account').isError
                    Bot.account=111
                    Bot.uncertain=True
                    r,args=op('qq_qzone_action','like_qzone',{'tid':'unknown-tid','target_uin':'333'})
                    assert r['state']=='UNKNOWN';before=len(Bot.writes)
                    Bot.uncertain=False
                    assert data('qq_qzone_action',**args)['state']=='UNKNOWN'
                    assert data('qq_qzone_action',**(args|dict(idempotency_key='different-key')))['state']=='UNKNOWN'
                    assert len(Bot.writes)==before
                    r,_=send(sid,dict(kind='file',asset_id=file));assert r['state']=='SUCCEEDED'
                    data('stop_chat_session',session_id=sid)
                    assert call('read_chat_attachment',session_id=sid,event_id=eid,index=0).isError
                    # Stop during preparation must prevent the native write.
                    entered=threading.Event();release=threading.Event()
                    def intercept(action,args):
                        if action=='get_friend_list':entered.set();release.wait(8)
                    def race():return send(private,dict(kind='file',asset_id=file))[0]
                    Bot.intercept=intercept
                    with ThreadPoolExecutor(1) as pool:
                        future=pool.submit(race);assert entered.wait(4)
                        data('stop_chat_session',session_id=private);before=len(Bot.writes);release.set()
                        result=future.result(10)
                    Bot.intercept=None
                    assert result['state']!='SUCCEEDED' and len(Bot.writes)==before,result
                    with access.connect() as db:
                        ledger=[dict(r) for r in db.execute('SELECT * FROM operations')]
                    assert base64.b64encode(blob).decode() not in json.dumps(ledger)
                    assert g['token'] not in json.dumps(ledger)
                    checks.append('bad data/path denial, cross-grant assets, account switch, UNKNOWN re-key refusal, stop during send, no binary/tokens in ledger')
                    access.revoke(g['id'])
                    try:call('get_qq_capabilities');raise AssertionError('revoked token accepted')
                    except ValueError:pass
                    access.revoke(other['id'])
                    # Expired/future grants must not keep a request subscription alive.
                    for dates in (dict(end='2001-01-01'),dict(start='2099-01-01')):
                        access.create(base|dict(qq_friends=True)|dates)
                    request=dict(post_type='request',request_type='friend',self_id=111,user_id=556,
                                 flag='outside-grant-window',comment='date boundary fixture')
                    tools.chat.qq.receive(request)
                    with access.connect() as db:
                        assert not db.execute('SELECT 1 FROM qq_friend_requests WHERE user_id=?',('556',)).fetchone()
                    access.create(base|dict(qq_friends=True))
                    tools.chat.qq.receive(request)
                    with access.connect() as db:
                        assert db.execute('SELECT 1 FROM qq_friend_requests WHERE user_id=?',('556',)).fetchone()
                    access.configure(False,port)
                    tools.chat.qq.receive(request|dict(user_id=557,flag='mcp-disabled-request'))
                    with access.connect() as db:
                        assert not db.execute('SELECT 1 FROM qq_friend_requests WHERE user_id=?',('557',)).fetchone()
                    checks.append('friend requests ignored after revocation, outside grant dates and while MCP is disabled')
    finally:upstream.shutdown();upstream.server_close();events.close()
    print(json.dumps(dict(status='passed',checks=checks,fixture_writes=len(Bot.writes),real_qq_writes=0),ensure_ascii=False))


if __name__=='__main__':main()
