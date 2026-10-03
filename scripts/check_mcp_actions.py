"""Direct MCP write grants: isolated HTTP OneBot; never touches a real QQ account."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
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
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.onebot import invalidate_availability
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import httpx


class Fixture(BaseHTTPRequestHandler):
    writes=[]
    uncertain=False
    actor='owner'
    def log_message(self,*args):pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        action=self.path.removeprefix('/')
        if action=='get_login_info':data=dict(user_id=111)
        elif action=='get_group_info':data=dict(group_id=222,group_name='Isolated test')
        elif action=='get_group_member_info':data=dict(group_id=222,user_id=body['user_id'],nickname='Fixture',role=self.actor if body['user_id']==111 else 'member')
        elif action=='get_group_system_msg':data=[dict(group_id=222,checked=False,flag='fixture-join',requester_uin=333,sub_type='add')]
        elif action.startswith(('send_','set_')):
            self.writes.append((action,body))
            if self.uncertain:
                self.send_response(503);self.end_headers();return
            data=dict(message_id=700+len(self.writes)) if action.startswith('send_') else None
        else:raise AssertionError(action)
        raw=json.dumps(dict(status='ok',retcode=0,data=data)).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)


async def protocol(url,token):
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+token},trust_env=False) as http:
        async with streamable_http_client(url,http_client=http) as (read,write,_):
            async with ClientSession(read,write) as client:
                await client.initialize()
                tools={t.name:t for t in (await client.list_tools()).tools}
                assert not tools['send_qq_message'].annotations.readOnlyHint
                assert tools['manage_qq_group'].annotations.destructiveHint
                result=await client.call_tool('send_qq_message',dict(conversation_id='111:group:222',text='HTTP fixture',idempotency_key='http-exactly-once'))
                assert not result.isError,result
                assert result.structuredContent['state']=='SUCCEEDED',result


def main():
    server=ThreadingHTTPServer(('127.0.0.1',0),Fixture);threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='mcp-actions-') as tmp,patch.dict('os.environ',{},clear=True):
            folder=Path(tmp);store=Store(folder/'chats.sqlite3')
            file=folder/'fixture.json';file.write_text(json.dumps([dict(platform='qq',conversation_id=cid,conversation='Isolated test',conversation_type='group',sender_id='333',sender='Fixture',source_id=cid,content='Fixture data',timestamp='2026-10-01') for cid in ['111:group:222','111:group:444']]),'utf-8');store.import_file(file)
            # Token is optional on the server; no illegal empty Bearer header.
            (folder/'.env').write_text('REPLY_ONEBOT_URL=http://127.0.0.1:'+str(server.server_port)+'\n','utf-8')
            app=FastAPI();service=install_mcp_routes(app,store);access=service.access;adapter=service.tools
            headers={'X-ChatWeave-UI':'1'}
            with patch('chatlocal.onebot.ROOT',folder),patch('chatlocal.message_sender.ROOT',folder),TestClient(app) as ui:
                invalidate_availability()
                with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                assert ui.put('/api/mcp',json=dict(enabled=True,port=port),headers=headers).json()['running']
                def grant(**flags):
                    r=ui.post('/api/mcp/connections',headers=headers,json=dict(name='Fixture',platforms=['qq'],conversations=[['qq','111:group:222']],**flags))
                    assert r.status_code==200,r.text
                    return r.json()
                ro=grant();send=grant(send=True);manage=grant(manage=True);both=grant(send=True,manage=True)
                import tomllib
                from chatlocal.mcp_connect import codex_config
                readonly=tomllib.loads(codex_config('http://127.0.0.1:18777/mcp','fixture'))
                assert 'tools' not in readonly['mcp_servers']['tulpa']
                with patch('chatlocal.mcp_connect.codex_home',return_value=folder/'codex'):
                    r=ui.post('/api/mcp/connections/'+send['id']+'/codex',headers=headers,json=dict(token=send['token']))
                    assert r.status_code==200,r.text
                parsed=tomllib.loads((folder/'codex/config.toml').read_text('utf-8'))['mcp_servers']['tulpa']
                assert parsed['tools']=={'send_qq_message':{'approval_mode':'approve'}}
                def names(g):return {s['name'] for s in adapter.schemas(access.authorize(g['token']))}
                assert 'send_qq_message' not in names(ro) and 'manage_qq_group' not in names(ro)
                assert 'send_qq_message' in names(send) and 'manage_qq_group' not in names(send)
                assert 'manage_qq_group' in names(manage) and 'send_qq_message' not in names(manage)
                def call(g,name,**args):return adapter.call(g['token'],name,args,threading.Event()).structuredContent
                payload=dict(conversation_id='111:group:222',text='literal [CQ:at,qq=all]',idempotency_key='same-operation-1')
                assert call(ro,'send_qq_message',**payload)['error_code']=='tool_unavailable'
                assert call(send,'send_qq_message',**dict(payload,conversation_id='111:group:444'))['error_code']=='operation_rejected'
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results=list(pool.map(lambda _:call(send,'send_qq_message',**payload),range(2)))
                assert all(r['state']=='SUCCEEDED' for r in results),results
                assert results[0]['id']==results[1]['id'] and len(Fixture.writes)==1
                assert Fixture.writes[0][1]['message']==[dict(type='text',data=dict(text=payload['text']))]
                assert 'approval_token' not in str(results) and 'PENDING' not in str(results)
                assert call(send,'send_qq_message',**dict(payload,text='different'))['error_code']=='operation_rejected'
                assert call(manage,'get_qq_operation',operation_id=results[0]['id'])['error_code']=='operation_rejected'
                for action,extra in [('mute',dict(user_id='333',duration_seconds=30)),('unmute',dict(user_id='333')),('rename',dict(group_name='Fixture changed')),('kick',dict(user_id='333'))]:
                    r=call(manage,'manage_qq_group',conversation_id='111:group:222',action=action,idempotency_key='fixture-'+action,**extra)
                    assert r['state']=='SUCCEEDED',r
                # Test join approval using the real hashed request identity, not an invented protocol flag.
                from chatlocal.group_admin import requests,group_identity
                from chatlocal.onebot import Client
                target=group_identity(Client(),'111:group:222');rid=requests(Client(),target)[0]['request_id']
                assert call(manage,'manage_qq_group',conversation_id='111:group:222',action='request',request_id=rid,approve=True,idempotency_key='fixture-request')['state']=='SUCCEEDED'
                Fixture.actor='member';before=len(Fixture.writes)
                assert call(manage,'manage_qq_group',conversation_id='111:group:222',action='rename',group_name='Denied',idempotency_key='fixture-no-role')['error_code']=='operation_rejected'
                assert len(Fixture.writes)==before;Fixture.actor='owner'
                Fixture.uncertain=True
                uncertain=dict(payload,idempotency_key='fixture-unknown')
                r=call(send,'send_qq_message',**uncertain);assert r['state']=='UNKNOWN',r
                before=len(Fixture.writes);adapter.actions.recover()
                assert call(send,'send_qq_message',**uncertain)['state']=='UNKNOWN' and len(Fixture.writes)==before
                Fixture.uncertain=False
                asyncio.run(protocol(service.status()['url'],both['token']))
                expired=grant(send=True,end='2026-09-01')
                assert call(expired,'send_qq_message',**payload)['error_code']=='operation_rejected'
                access.revoke(send['id'])
                try:call(send,'send_qq_message',**dict(payload,idempotency_key='after-revoke'))
                except ValueError:pass
                else:raise AssertionError('Revoked grant accepted')
                records=ui.get('/api/mcp/operations').json()['operations'];assert records and all(o['state']!='PENDING' for o in records)
                assert ui.post('/api/mcp/operations/'+results[0]['id']+'/decision',headers=headers,json={'allow':True}).status_code==404
    finally:server.shutdown();server.server_close();invalidate_availability()
    print('PASS direct UI grants, real MCP HTTP writes to isolated OneBot, permission/scope/role/revoke, concurrent idempotency, UNKNOWN no retry, literal text, receipts; no real QQ sends/kicks.')


if __name__=='__main__':main()
