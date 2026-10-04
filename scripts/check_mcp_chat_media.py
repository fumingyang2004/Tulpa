"""Live stickers: isolated real HTTP/WS MCP, no real QQ or internal model."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
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

sys.path.insert(0,str(Path(__file__).resolve().parent))
from check_mcp_chat import Events, OneBot, eventually, ROOT
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from PIL import Image
from chatlocal.store import Store
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.mcp_chat_media import MEDIA_TOOLS, pixels, download, image_ref, public_addresses, MAX_BYTES
from chatlocal.onebot import invalidate_availability


def fixture_image(animated=False):
    out=io.BytesIO()
    frames=[Image.new('RGB',(80,60),c) for c in ('red','green','blue')]
    if animated:frames[0].save(out,'GIF',save_all=True,append_images=frames[1:],duration=150,loop=0)
    else:frames[0].save(out,'PNG')
    return out.getvalue()


class StickerBot(OneBot):
    collected=[]
    def do_POST(self):
        if self.path not in ('/fetch_custom_face_detail','/get_image','/add_custom_face'):
            return super().do_POST()
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path=='/fetch_custom_face_detail':data=[{'emoji_id':'gif1','url':'https://gchat.qpic.cn/fixture.gif','desc':'测试动图'}]
        elif self.path=='/get_image':data={'url':'https://gchat.qpic.cn/fixture.gif'}
        else:
            self.collected.append(body)
            if self.uncertain:self.send_response(503);self.end_headers();return
            data={'emoji_id':'gif1'}
        raw=json.dumps({'status':'ok','retcode':0,'data':data}).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)


async def protocol(svc, token, sid, eid):
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+token},timeout=15,trust_env=False) as http:
        async with streamable_http_client(svc.status()['url'],http_client=http) as (r,w,_):
            async with ClientSession(r,w) as c:
                await c.initialize()
                tools={x.name:x for x in (await c.list_tools()).tools}
                assert MEDIA_TOOLS.keys()<=tools.keys()
                assert tools['send_chat_sticker'].annotations.openWorldHint
                result=await c.call_tool('read_chat_image',dict(session_id=sid,event_id=eid))
                assert not result.isError,result
                assert len([x for x in result.content if x.type=='image'])==3
                assert result.structuredContent['total_frames']==3
                for img in result.content[1:]:Image.open(io.BytesIO(base64.b64decode(img.data))).verify()
                assert 'https://' not in result.model_dump_json()


def main():
    animated=fixture_image(True)
    assert pixels(animated)[0]['frame_indices']==[0,1,2]
    assert 'file' not in image_ref({'file':'C:/private/secret.png'})
    for url in ('file:///C:/secret','http://127.0.0.1/a','https://qq.com.evil.invalid/a','https://qpic.cn@evil.invalid/a'):
        try:download(url,threading.Event());raise AssertionError(url)
        except ValueError:pass
    with patch('socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]):
        try:download('https://qq.com/a',threading.Event());raise AssertionError('private DNS accepted')
        except ValueError:pass
    for address in ('175.27.35.211','127.0.0.1'):
        response=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'Answer':[{'type':1,'data':address}]})),trust_env=False)
        with patch('socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('198.18.0.97',443))]),patch('chatlocal.mcp_chat_media.httpx.Client',return_value=response):
            if address.startswith('175.'):
                assert public_addresses('multimedia.nt.qq.com.cn',443)[0][4][0]==address
            else:
                try:public_addresses('multimedia.nt.qq.com.cn',443);raise AssertionError('private fallback accepted')
                except ValueError:pass
    for raw in (b'not an image',b'x'*(MAX_BYTES+1)):
        try:pixels(raw);raise AssertionError('invalid image accepted')
        except ValueError:pass
    events=Events();upstream=ThreadingHTTPServer(('127.0.0.1',0),StickerBot)
    threading.Thread(target=upstream.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='chat-media-') as tmp,patch.dict('os.environ',{},clear=True):
            folder=Path(tmp);store=Store(folder/'chats.sqlite3')
            (folder/'.env').write_text(f'REPLY_ONEBOT_URL=http://127.0.0.1:{upstream.server_port}\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:{events.port}\nREPLY_ONEBOT_WS_TOKEN=event-fixture\n','utf-8')
            app=FastAPI();svc=install_mcp_routes(app,store);access=svc.access;tools=svc.tools
            with patch('chatlocal.onebot.ROOT',folder),patch('chatlocal.message_sender.ROOT',folder),TestClient(app) as ui:
                invalidate_availability()
                with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                assert ui.put('/api/mcp',json={'enabled':True,'port':port},headers={'X-ChatWeave-UI':'1'}).json()['running']
                config=dict(name='sticker-test',platforms=['qq'],conversations=[['qq','111:group:222']],send=True,chat=True)
                old=access.create(config)
                assert not MEDIA_TOOLS.keys()&{s['name'] for s in tools.schemas(access.authorize(old['token']))}
                g=access.create(config|dict(chat_images=True,chat_sticker_send=True,chat_sticker_collect=True))
                ro=access.create(config|dict(chat_images=True))
                for bad in (config|dict(chat=False,chat_images=True),config|dict(chat_sticker_send=True)):
                    try:access.create(bad);raise AssertionError('permission dependency bypassed')
                    except ValueError:pass
                def call(name,conn=g,**args):return tools.call(conn['token'],name,args,threading.Event())
                def data(name,**args):return call(name,**args).structuredContent
                def start(key):return data('start_chat_session',conversation_id='111:group:222',persona='自然聊天',idempotency_key=key)['session']['id']
                sid=start('media-session')
                eventually(lambda:tools.chat.receiver.status()['state']=='connected')
                def add(mid=890):
                    events.send(dict(post_type='message',message_type='group',self_id=111,group_id=222,message_id=mid,user_id=333,time=time.time(),message=[{'type':'image','data':{'url':'https://gchat.qpic.cn/fixture.gif','file':'gif1'}}]))
                    def row():
                        with access.connect() as db:return db.execute('SELECT id FROM chat_inbox WHERE session_id=? AND message_id=?',(sid,str(mid))).fetchone()
                    eventually(row);return row()[0]
                eid=add()
                with patch('chatlocal.mcp_chat_media.download',return_value=animated),patch.object(store,'connect',side_effect=AssertionError('Native history DB used')):
                    asyncio.run(protocol(svc,g['token'],sid,eid))
                    context=data('get_chat_session',session_id=sid)
                    assert context['context']['messages'][0]['segments'][0]['image_index']==0
                    assert 'qpic' not in json.dumps(context)
                    assert call('read_chat_image',conn=ro,session_id=sid,event_id=eid).isError
                    assert call('read_chat_image',session_id=sid,event_id=eid+900).isError
                    assert call('send_chat_sticker',conn=ro,session_id=sid,sticker_id='a'*32,idempotency_key='no-write').isError
                    asset=data('read_chat_image',session_id=sid,event_id=eid)['sticker_id']
                    listed=data('list_chat_stickers',session_id=sid)['items'][0]['sticker_id']
                    assert call('send_chat_sticker',session_id=sid,sticker_id=listed,idempotency_key='unseen-gif').isError
                    assert not call('read_chat_sticker',session_id=sid,sticker_id=listed).isError
                    assert data('note_chat_sticker',session_id=sid,sticker_id=listed,description='兴奋时使用',tags=['开心'])['saved']
                    assert data('list_chat_stickers',session_id=sid,query='开心')['matched']==1
                    send=dict(session_id=sid,sticker_id=asset,idempotency_key='gif-send-once')
                    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:data('send_chat_sticker',**send),range(2)))
                    assert len(StickerBot.sent)==1 and all(r['state'] in ('SUCCEEDED','EXECUTING') for r in results),results
                    assert data('send_chat_sticker',**send)['state']=='SUCCEEDED'
                    assert base64.b64decode(StickerBot.sent[0]['message'][0]['data']['file'][9:])==animated
                    collect=dict(send,idempotency_key='collect-once')
                    assert data('collect_chat_sticker',**collect)['state']=='SUCCEEDED'
                    assert data('collect_chat_sticker',**collect)['state']=='SUCCEEDED' and len(StickerBot.collected)==1
                    StickerBot.uncertain=True
                    unknown=dict(send,idempotency_key='unknown-gif')
                    assert data('send_chat_sticker',**unknown)['state']=='UNKNOWN'
                    assert data('send_chat_sticker',**unknown)['state']=='UNKNOWN' and len(StickerBot.sent)==2
                    unknown=dict(send,idempotency_key='unknown-collect')
                    assert data('collect_chat_sticker',**unknown)['state']=='UNKNOWN'
                    assert data('collect_chat_sticker',**unknown)['state']=='UNKNOWN' and len(StickerBot.collected)==2
                    StickerBot.uncertain=False
                # Stalled image requests cannot occupy ordinary tools, receive, or stop.
                entered=threading.Barrier(3);release=threading.Event();eid=add(891)
                def blocked(*args):entered.wait(3);release.wait(8);return animated
                with patch('chatlocal.mcp_chat_media.download',side_effect=blocked),ThreadPoolExecutor(2) as pool:
                    tasks=[pool.submit(call,'read_chat_image',session_id=sid,event_id=eid) for _ in range(2)]
                    entered.wait(3)
                    assert call('read_chat_image',session_id=sid,event_id=eid).structuredContent['error_code']=='media_busy'
                    began=time.monotonic();assert not call('get_data_status').isError;assert time.monotonic()-began<2
                    assert not call('send_chat_message',session_id=sid,text='ordinary still works',idempotency_key='ordinary-send').isError
                    add(892)
                    assert data('wait_chat_messages',session_id=sid,quiet_seconds=0,timeout_seconds=1)['messages']
                    began=time.monotonic();assert data('stop_chat_session',session_id=sid)['event']=='stopped';assert time.monotonic()-began<1
                    release.set();assert all(t.result(5).isError for t in tasks)
                # New session retains model notes but never inherits proof of viewing.
                sid=start('media-new-session')
                eventually(lambda:tools.chat.receiver.status()['state']=='connected')
                with patch('chatlocal.mcp_chat_media.download',return_value=animated):
                    favorites=data('list_chat_stickers',session_id=sid,query='开心')['items'];assert favorites
                    aid=favorites[0]['sticker_id']
                    assert call('send_chat_sticker',session_id=sid,sticker_id=aid,idempotency_key='new-unseen').isError
                    call('read_chat_sticker',session_id=sid,sticker_id=aid)
                    # Stop during preflight, before the external mutation.
                    StickerBot.block=True;StickerBot.entered.clear();StickerBot.release.clear()
                    count=len(StickerBot.sent)
                    with ThreadPoolExecutor(1) as pool:
                        future=pool.submit(call,'send_chat_sticker',session_id=sid,sticker_id=aid,idempotency_key='stop-before-send')
                        assert StickerBot.entered.wait(3)
                        data('stop_chat_session',session_id=sid);StickerBot.release.set();assert future.result(5).isError
                    StickerBot.block=False;assert len(StickerBot.sent)==count
                with access.connect() as db:
                    db.executemany('INSERT INTO calls(grant_id,tool,at,status) VALUES(?,?,?,?)',[(g['id'],'list_chat_stickers',time.time(),'ok')]*120)
                assert not call('get_data_status').isError,'Media budget consumed ordinary budget'
                assert call('list_chat_stickers',session_id=sid).structuredContent['error_code']=='rate_limited'
                assert data('stop_chat_session',session_id=sid)['event']=='stopped'
                from chatlocal.agent_tools import SCHEMAS
                assert not MEDIA_TOOLS.keys()&{s['name'] for s in SCHEMAS}
                import tomllib
                from chatlocal.mcp_connect import codex_config
                entries=tomllib.loads(codex_config('http://127.0.0.1:18777/mcp','fixture',access.authorize(g['token'])['scope']))['mcp_servers']['tulpa']['tools']
                assert MEDIA_TOOLS.keys()<=entries.keys()
                fresh=access.create(config|dict(chat_images=True))
                sid=data('start_chat_session',conn=fresh,conversation_id='111:group:222',persona='撤销验收',idempotency_key='revoke-media-session')['session']['id']
                eid=add(899);entered=threading.Event();release=threading.Event()
                def slow(*args):entered.set();release.wait(5);return animated
                with patch('chatlocal.mcp_chat_media.download',side_effect=slow),ThreadPoolExecutor(1) as pool:
                    pending=pool.submit(call,'read_chat_image',conn=fresh,session_id=sid,event_id=eid)
                    assert entered.wait(3);access.revoke(fresh['id']);release.set();assert pending.result(5).isError
    finally:
        StickerBot.release.set();events.close();upstream.shutdown();upstream.server_close();invalidate_availability()
    print('PASS: MCP actual image blocks, animated original send, QQ favorites collection, notes/search, scoped handles, independent permissions/quotas/concurrency, native DB forbidden, SSRF/local-path/invalid-image rejection, idempotency/UNKNOWN, stop during IO/preflight. No real QQ writes.')


if __name__=='__main__':main()
