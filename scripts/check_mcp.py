"""Real MCP HTTP client + isolated source fixtures; never sends QQ messages."""
import asyncio
import io
import json
import socket
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(sys.argv[sys.argv.index('--package')+1]).resolve() if '--package' in sys.argv else Path(__file__).resolve().parents[1]
(ROOT/'.tmp').mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from chatlocal.store import Store
from chatlocal.mcp_access import MCPAccess
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.agent_tools import ChatTools
from chatlocal.artifacts import Artifacts


@asynccontextmanager
async def client(url, token):
    async with httpx.AsyncClient(headers={'Authorization': 'Bearer '+token}, timeout=30, trust_env=False) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                result = await session.initialize()
                assert result.serverInfo.name == 'tulpa'
                yield session


async def protocol(url, first, second, ui, service, store, folder):
    async with httpx.AsyncClient(trust_env=False) as http:
        assert (await http.post(url, json={})).status_code == 401
        assert (await http.post(url, headers={'Authorization':'Bearer '+first, 'Origin':'https://evil.invalid'}, json={})).status_code == 403
        assert (await http.post(url, headers={'Authorization':'Bearer '+first, 'Host':'evil.invalid'}, json={})).status_code == 403
        assert (await http.post(url, headers={'Authorization':'Bearer '+first}, content='x'*33000)).status_code == 413
    async with client(url, first) as a, client(url, second) as b:
        listed = await a.list_tools()
        names = {t.name for t in listed.tools}
        assert {'search_messages','read_conversation','read_file_chunks','query_communication_db','read_message'} <= names
        assert not {'workspace_draft','evidence_collection','qq_group_admin','inspect_image','read_qq_group','read_image'} & names
        async def call(session, name, args):
            result = await session.call_tool(name, args)
            assert result.structuredContent is not None, result
            return result.structuredContent
        count = await call(a, 'get_data_status', {})
        assert count['platforms'][0]['count'] == 520, count
        found = await call(a, 'search_messages', {'query':'课程设计'})
        assert found['messages'] and all(m['conversation_id']=='course' for m in found['messages'])
        assert found['sources'][0]['citation_id'].startswith('M')
        assert not (await call(a,'search_messages', {'query':'FORBIDDEN'}))['messages']
        people = await call(a, 'find_people', {'query':'FORBIDDEN'})
        assert not people['people']
        ids = await call(a, 'get_my_identity', {})
        assert 'OLD_NAME' not in json.dumps(ids)
        assert (await call(a, 'get_context', {'message_id':521})).get('error')
        other = await call(b, 'read_conversation', {'platform':'wechat','conversation_id':'private','limit':5})
        assert len(other['messages'])==1 and other['messages'][0]['platform']=='wechat'
        assert not (await call(b,'search_messages', {'query':'课程设计'}))['messages']
        sql = await call(a, 'query_communication_db', {'sql':'SELECT count(*) FROM agent_messages'})
        assert sql['rows']==[[520]], sql
        assert (await call(a, 'query_communication_db', {'sql':"ATTACH DATABASE '.env' AS bad"})).get('error')
        assert (await call(a, 'workspace_draft', {})).get('error')
        assert (await call(a, 'read_conversation', {'platform':'qq','conversation_id':'course','limit':1000})).get('error')
        seen=[];offset=0;snapshot=None
        for _ in range(20):
            args=dict(platform='qq',conversation_id='course',limit=50,offset=offset)
            if snapshot is not None:args['snapshot_max_id']=snapshot
            page=await call(a,'read_conversation',args)
            seen += [m['id'] for m in page['messages']]
            snapshot=page['snapshot_max_id']
            if not page['has_more']:break
            offset=page['next_offset']
        assert len(seen)==len(set(seen))==520
        # Newly imported messages are excluded from an earlier page sequence.
        path=folder/'new.json';path.write_text(json.dumps([dict(platform='qq',conversation_id='course',conversation='课程群',sender='老师',sender_id='teacher',source_id='new',timestamp='2026-10-02',content='新增课程设计通知')]),'utf-8')
        store.import_file(path)
        old=await call(a,'read_conversation',dict(platform='qq',conversation_id='course',offset=520,snapshot_max_id=snapshot))
        assert not old['messages']
        fresh=await call(a,'read_conversation',dict(platform='qq',conversation_id='course',order='newest'))
        assert fresh['messages'][0]['content']=='新增课程设计通知'
        long=await call(a,'read_message',dict(message_id=1,offset=2000,limit=3000))
        assert len(long['content'])==3000 and long['has_more']
        files=await call(a,'search_files',{})
        assert len(files['files'])==1
        assert (await call(a,'read_file_chunks',{'file_id':2})).get('error')
        chunks=await call(a,'read_file_chunks',{'file_id':1})
        assert '交付源码和报告' in chunks['files'][0]['text']
        # Genuine HTTP cancellation + same-grant request identity.
        entered=threading.Event()
        original=ChatTools.execute
        def slow(self,name,args):
            if name=='get_context':
                entered.set()
                assert self.cancel.wait(8), 'Cancellation not propagated to worker'
                return dict(messages=[],error='cancelled')
            return original(self,name,args)
        headers={'Authorization':'Bearer '+first,'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-11-25'}
        async with httpx.AsyncClient(headers=headers,timeout=15,trust_env=False) as http:
            with patch.object(ChatTools,'execute',slow):
                task=asyncio.create_task(http.post(url,json=dict(jsonrpc='2.0',id=991,method='tools/call',params=dict(name='get_context',arguments=dict(message_id=1)))))
                for _ in range(100):
                    if entered.is_set():break
                    await asyncio.sleep(.03)
                assert entered.is_set()
                await http.post(url,json=dict(jsonrpc='2.0',method='notifications/cancelled',params=dict(requestId=991)))
                response=await task
                assert response.json()['result']['isError']
        # Revoke while the server is generating a response: no old data disclosed.
        entered.clear();resume=threading.Event()
        def paused(self,name,args):
            result=original(self,name,args)
            if name=='get_context':entered.set();resume.wait(8)
            return result
        async with httpx.AsyncClient(headers=headers,timeout=15,trust_env=False) as http:
            with patch.object(ChatTools,'execute',paused):
                task=asyncio.create_task(http.post(url,json=dict(jsonrpc='2.0',id=992,method='tools/call',params=dict(name='get_context',arguments=dict(message_id=1)))))
                for _ in range(100):
                    if entered.is_set():break
                    await asyncio.sleep(.03)
                assert entered.is_set()
                service.access.revoke(service.access.authorize(first)['id']);service.cancel()
                resume.set()
                response=await task
                assert response.json()['result']['isError'] and '课程设计' not in response.text
            assert (await http.post(url,json=dict(jsonrpc='2.0',id=993,method='tools/list'))).status_code==401


async def optional_tools(url, service, store, folder):
    from PIL import Image
    from chatlocal import media
    cid='111:group:222'
    path=folder/'optional.json'
    # Dense messages deliberately exceed a page's character budget.
    rows=[dict(platform='qq',conversation_id=cid,conversation='Fixture group',conversation_type='group',
               source_id=f'optional-{i}',timestamp='2026-10-01',sender='Fixture',content='课程'*1000) for i in range(65)]
    path.write_text(json.dumps(rows),'utf-8');store.import_file(path)
    with store.connect() as db:
        mids=[r[0] for r in db.execute('SELECT id FROM messages WHERE conversation_id=? ORDER BY id',(cid,))]
    base=dict(name='Optional fixture',platforms=['qq'],conversations=[['qq',cid]],start='2026-10-01',end='2026-10-01',
              media=True,prepare=True,voice=True,onebot=True)
    token=service.access.create(base)['token']
    class QQ:
        url='http://127.0.0.1:9'
        def login(self):return '111'
        def call(self,action,payload):
            assert action in {'get_login_info','get_group_info','get_group_member_list','_get_group_notice','get_essence_msg_list'}, action
            if action=='get_login_info':return dict(user_id=111)
            assert str(payload['group_id'])=='222'
            if action=='get_group_info':return dict(group_id=222,group_name='Fixture group')
            if action=='get_group_member_list':return [dict(group_id=222,user_id=333,nickname='Fixture member',role='member')]
            if action=='get_essence_msg_list':return []
            return [dict(notice_id='n1',sender_id=333,publish_time=1790784000,message=dict(text='请提交课程设计报告')),
                    dict(notice_id='old',sender_id=333,publish_time=1700000000,message=dict(text='FORBIDDEN old notice'))]
    pixels=io.BytesIO()
    frames=[Image.new('RGB',(16,16),color) for color in ('red','green','blue','yellow','purple')]
    frames[0].save(pixels,format='GIF',save_all=True,append_images=frames[1:],duration=100)
    with patch.object(media,'DATA',folder):
        cached=media.cache_image(pixels.getvalue())
        with store.connect() as db:db.execute('UPDATE messages SET media_json=? WHERE id=?',(json.dumps([cached]),mids[0]))
        with patch.object(service.tools,'onebot',return_value=QQ()):
            async with client(url,token) as c:
                names={s.name for s in (await c.list_tools()).tools}
                assert {'read_image','prepare_file','transcribe_voice','get_group_knowledge','read_qq_group'}<=names
                assert 'qq_group_admin' not in names
                result=await c.call_tool('read_image',dict(message_id=mids[0]))
                assert len([b for b in result.content if b.type=='image'])==3, result
                assert result.structuredContent['total_frames']==5
                assert (await c.call_tool('read_image',dict(message_id=521))).isError
                knowledge=(await c.call_tool('get_group_knowledge',dict(group_id=cid))).structuredContent
                assert 'entries' in knowledge, knowledge
                assert len(knowledge['entries'])==1 and '课程设计报告' in knowledge['entries'][0]['content'],knowledge
                assert knowledge['sources'][0]['citation_id'].startswith('K')
                members=await c.call_tool('read_qq_group',dict(conversation_id=cid,view='members'))
                assert members.structuredContent['items'][0]['name']=='Fixture member'
                assert (await c.call_tool('read_qq_group',dict(conversation_id=cid,view='requests'))).isError
                assert (await c.call_tool('read_qq_group',dict(conversation_id='111:group:444',view='members'))).isError
                seen=[];offset=0
                while True:
                    page=(await c.call_tool('read_conversation',dict(platform='qq',conversation_id=cid,limit=50,offset=offset))).structuredContent
                    seen += [m['id'] for m in page['messages']]
                    if not page['has_more']:break
                    assert page['next_offset']>offset
                    offset=page['next_offset']
                assert seen==mids, 'Dense pages skipped messages'
        with patch.object(service.tools,'onebot',return_value=None):
            async with client(url,token) as c:
                assert 'get_group_knowledge' not in {s.name for s in (await c.list_tools()).tools}
    gid=service.access.authorize(token)['id']
    for _ in range(12):service.access.begin_call(gid,'prepare_file')
    async with client(url,token) as c:
        result=await c.call_tool('prepare_file',dict(file_id=1))
        assert result.structuredContent['error_code']=='rate_limited'
    service.access.revoke(gid)


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='mcp-check-') as tmp:
        folder=Path(tmp);store=Store(folder/'chats.sqlite3')
        raw=folder/'任务书.txt';raw.write_text('课程设计：交付源码和报告。截止10月15日。','utf-8')
        rows=[dict(platform='qq',conversation_id='course',conversation='课程群',conversation_type='group',sender='老师',sender_id='teacher',source_id=str(i),timestamp='2026-10-01 10:00:00',content=f'课程设计讨论 {i}',is_self=False) for i in range(520)]
        rows[0]['content']='课程设计'+('说明'*3000)
        rows[0]['files']=[dict(filename=raw.name,size=raw.stat().st_size)]
        rows += [dict(rows[1],platform='wechat',conversation_id='private',source_id='wx',content='FORBIDDEN 微信',files=[dict(filename=raw.name,size=raw.stat().st_size)]),
                 dict(rows[1],conversation_id='other',source_id='other',sender='FORBIDDEN_OTHER',content='FORBIDDEN 群'),
                 dict(rows[1],source_id='old',timestamp='2026-09-01',sender='FORBIDDEN_OLD_NAME',is_self=True,content='FORBIDDEN 日期')]
        path=folder/'messages.json';path.write_text(json.dumps(rows,ensure_ascii=False),'utf-8');store.import_file(path)
        layer=Artifacts(store);layer._roots=lambda p:[folder];layer.prepare(1);layer.prepare(2)
        app=FastAPI();service=install_mcp_routes(app,store)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        ui_headers={'X-ChatWeave-UI':'1','Origin':'http://testserver'}
        with TestClient(app) as ui:
            assert not ui.get('/api/mcp').json()['enabled']
            assert ui.put('/api/mcp',json=dict(enabled=True,port=port)).status_code==403
            assert ui.get('/api/mcp',headers={'Host':'evil.invalid'}).status_code==403
            base=dict(name='Fixture A',platforms=['qq'],conversations=[['qq','course']],start='2026-10-01',end='',media=False,prepare=False,voice=False,onebot=False)
            assert ui.post('/api/mcp/connections',json=dict(base,conversations=[]),headers=ui_headers).status_code==400
            first=ui.post('/api/mcp/connections',json=base,headers=ui_headers).json()['token']
            second=ui.post('/api/mcp/connections',json=dict(base,name='Fixture B',platforms=['wechat'],conversations=[['wechat','private']]),headers=ui_headers).json()['token']
            settings=ui.put('/api/mcp',json=dict(enabled=True,port=port),headers=ui_headers).json()
            assert settings['running'], settings
            # A second process cannot silently take over this server's port.
            from chatlocal.mcp_server import MCPService
            rival=MCPService(service.access);rival.start()
            assert not rival.status()['running'] and rival.status()['error']
            rival.stop()
            assert ui.post('/api/mcp/test',json={'token':first}).status_code==403
            checked=ui.post('/api/mcp/test',json={'token':first},headers=ui_headers)
            assert checked.status_code==200 and checked.json()['ok'],checked.text
            assert checked.json()['tools']>=18 and not checked.json()['onebot']
            assert ui.post('/api/mcp/test',json={'token':'invalid'},headers=ui_headers).status_code==400
            import tomllib
            home=folder/'codex';home.mkdir();config=home/'config.toml'
            config.write_text('# keep comment\nmodel="user-model"\n[mcp_servers.example]\nurl="https://example.test/mcp"\n','utf-8')
            before=config.read_bytes()
            from chatlocal.mcp_connect import install_codex
            with patch('chatlocal.mcp_connect.codex_home',return_value=home):
                gid=service.access.authorize(first)['id']
                route='/api/mcp/connections/'+gid+'/codex'
                assert ui.post(route,json={'token':first}).status_code==403
                assert ui.post(route,json={'token':second},headers=ui_headers).status_code==403
                assert ui.post(route,json={'token':first},headers=ui_headers).json()['installed']
                assert not ui.post(route,json={'token':first},headers=ui_headers).json()['changed']
                assert next((home/'backups').glob('*.toml')).read_bytes()==before
                parsed=tomllib.loads(config.read_text('utf-8'))
                assert parsed['model']=='user-model' and 'example' in parsed['mcp_servers']
                assert parsed['mcp_servers']['tulpa']['http_headers']['Authorization']=='Bearer '+first
                saved=config.read_bytes()
                try:install_codex(settings['url'],second,home=home);raise AssertionError('Existing entry overwritten')
                except ValueError:pass
                assert config.read_bytes()==saved
            asyncio.run(protocol(settings['url'],first,second,ui,service,store,folder))
            value=ui.get('/api/mcp').json();assert first not in json.dumps(value) and second not in json.dumps(value)
            assert value['recent'] and not any('content' in r for r in value['recent'])
            reopened=MCPAccess(store);assert len(reopened.list()['connections'])==2
            with patch('chatlocal.mcp_access.bound_account',return_value='different'):
                try:reopened.authorize(second);raise AssertionError('Source account changed without rejection')
                except ValueError:pass
            # Rate limit and durable expensive-call quota survive reconnects.
            gid=reopened.authorize(second)['id']
            for _ in range(12):reopened.begin_call(gid,'prepare_file')
            try:reopened.begin_call(gid,'prepare_file');raise AssertionError('Quota not enforced')
            except ValueError:pass
            asyncio.run(optional_tools(settings['url'],service,store,folder))
            # Restart reuses durable settings and grants without widening scope.
            service.stop();service.start()
            assert service.status()['running'] and service.access.authorize(second)['id']==gid
            assert ui.put('/api/mcp',json=dict(enabled=False,port=port),headers=ui_headers).json()['running'] is False
        assert not service.thread
    print('PASS: MCP HTTP lifecycle, scopes/SQL/files, 520-message and dense pagination, images, OneBot fixtures, cancel/revoke, accounts, quotas and settings')


if __name__=='__main__':main()
