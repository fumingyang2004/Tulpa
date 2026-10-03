"""Save EXE OneBot settings without an LLM, then use real HTTP MCP and OneBot fixtures."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT=Path(sys.argv[sys.argv.index('--package')+1]).resolve() if '--package' in sys.argv else Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from chatlocal.store import Store
from chatlocal.desktop_routes import install_desktop_routes
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.mcp_connect import test_connection
from chatlocal.onebot import invalidate_availability
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import httpx


class Fixture(BaseHTTPRequestHandler):
    actions=[]
    def log_message(self,*args):pass
    def do_POST(self):
        if self.headers.get('Authorization')!='Bearer fixture-onebot':
            self.send_response(401);self.end_headers();return
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        action=self.path.removeprefix('/');self.actions.append(action)
        data=None
        if action=='get_login_info':data=dict(user_id=111)
        elif action=='_get_group_notice':
            assert str(body['group_id'])=='222'
            data=[dict(notice_id='n1',sender_id=333,publish_time=1790784000,message=dict(text='共享配置公告验收'))]
        elif action=='get_essence_msg_list':data=[]
        else:raise AssertionError('Unexpected OneBot action: '+action)
        raw=json.dumps(dict(status='ok',retcode=0,data=data)).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)


async def read_notice(url,token):
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+token},trust_env=False) as http:
        async with streamable_http_client(url,http_client=http) as (read,write,_):
            async with ClientSession(read,write) as client:
                await client.initialize()
                names={t.name for t in (await client.list_tools()).tools}
                assert 'get_group_knowledge' in names and 'qq_group_admin' not in names
                result=await client.call_tool('get_group_knowledge',dict(group_id='111:group:222',types=['notice']))
                assert not result.isError,result
                assert '共享配置公告验收' in str(result.structuredContent),result


def main():
    server=ThreadingHTTPServer(('127.0.0.1',0),Fixture);threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='mcp-onebot-') as tmp,patch.dict('os.environ',{},clear=True):
            folder=Path(tmp);(folder/'.tmp').mkdir();store=Store(folder/'chats.sqlite3')
            imported=folder/'fixture.json';imported.write_text(json.dumps([dict(platform='qq',conversation_id='111:group:222',conversation='Fixture group',conversation_type='group',sender='Fixture',source_id='1',content='测试资料',timestamp='2026-10-01')]),'utf-8');store.import_file(imported)
            app=FastAPI();install_desktop_routes(app,folder);service=install_mcp_routes(app,store)
            headers={'X-ChatWeave-UI':'1'}
            with patch('chatlocal.onebot.ROOT',folder),TestClient(app) as ui:
                invalidate_availability()
                with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                assert ui.put('/api/mcp',json=dict(enabled=True,port=port),headers=headers).json()['running']
                created=ui.post('/api/mcp/connections',headers=headers,json=dict(name='Fixture',platforms=['qq'],all_conversations=True,onebot=True)).json()
                url=service.status()['url'];token=created['token']
                assert not asyncio.run(test_connection(url,token))['onebot']
                result=ui.put('/api/desktop/settings',headers=headers,json=dict(sender_url='http://127.0.0.1:'+str(server.server_port),sender_token='fixture-onebot'))
                assert result.status_code==200 and not result.json()['configured'],result.text
                assert ui.post('/api/desktop/onebot/test',headers=headers,json={}).json()['ok']
                assert asyncio.run(test_connection(url,token))['onebot']
                asyncio.run(read_notice(url,token))
                assert 'API_KEY' not in (folder/'.env').read_text('utf-8')
                # Saving a bad token must remove availability without a restart.
                ui.put('/api/desktop/settings',headers=headers,json=dict(sender_token='incorrect'))
                assert not asyncio.run(test_connection(url,token))['onebot']
                assert ui.post('/api/desktop/onebot/test',headers=headers,json={}).json()['ok'] is False
    finally:server.shutdown();server.server_close();invalidate_availability()
    assert '_get_group_notice' in Fixture.actions
    print('PASS shared EXE OneBot settings -> live MCP tool visibility -> HTTP notice read; no LLM key, hot token replacement, no sending. Isolated OneBot fixture.')


if __name__=='__main__':main()
