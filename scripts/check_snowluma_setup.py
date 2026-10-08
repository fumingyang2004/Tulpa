"""Real localhost HTTP/WS against synthetic SnowLuma files, never personal QQ."""
import json
import sys
import socket
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from websockets.sync.server import serve
from websockets.exceptions import ConnectionClosed
from chatlocal.desktop_routes import install_desktop_routes,save_product_settings
from chatlocal.snowluma_setup import inspect_folder,SetupError,check_account_scope
from chatlocal.onebot_events import configuration as event_configuration,probe_connection
from chatlocal.store import Store
from chatlocal.mcp_access import MCPAccess


class HTTP(BaseHTTPRequestHandler):
    account='12345'
    token='fixture-http-secret'
    error=0
    actions=[]
    def log_message(self,*args):pass
    def do_POST(self):
        HTTP.actions.append(self.path)
        self.rfile.read(int(self.headers.get('Content-Length','0')))
        status=self.error or (401 if self.headers.get('Authorization','') != ('Bearer '+self.token if self.token else '') else 200)
        if self.path!='/custom/api/get_login_info':status=404
        raw=json.dumps(dict(status='ok',retcode=0,data=dict(user_id=self.account))).encode()
        self.send_response(status);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)


class Events:
    def __init__(self):
        self.account='12345';self.token='fixture-ws-secret';self.mode='ok';self.calls=[]
        self.server=serve(self.handle,'127.0.0.1',0,process_request=self.check)
        self.port=self.server.socket.getsockname()[1]
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def check(self,connection,request):
        self.calls.append(request.path)
        if request.path!='/custom/events':return connection.respond(404,'not found')
        expected='Bearer '+self.token if self.token else ''
        if request.headers.get('Authorization','')!=expected:return connection.respond(401,'unauthorized')
    def handle(self,ws):
        # A message before lifecycle cannot prove setup success or reach storage.
        ws.send(json.dumps(dict(post_type='message',self_id=self.account,raw_message='fixture message ignored')))
        if self.mode!='silent':
            ws.send(json.dumps(dict(post_type='meta_event',meta_event_type='heartbeat',self_id=self.account,status=dict(online=self.mode!='offline'))))
        try:
            for _ in ws:raise AssertionError('Probe must not send any websocket action')
        except ConnectionClosed:pass
    def close(self):self.server.shutdown();self.thread.join(3)


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')


def main():
    http=ThreadingHTTPServer(('127.0.0.1',0),HTTP)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start();events=Events()
    try:
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='snow-setup-') as name,patch.dict('os.environ',{},clear=True):
            root=Path(name);snow=root/'SnowLuma';product=root/'Tulpa';product.mkdir();snow.mkdir()
            write(snow/'package.json',dict(name='@snowluma/runtime',version='1.14.20'));(snow/'index.mjs').write_text('// fixture')
            h=dict(name='http-fixture',host='127.0.0.1',port=http.server_port,path='/custom/api',accessToken=HTTP.token)
            w=dict(name='event-fixture',host='127.0.0.1',port=events.port,path='/custom/events',accessToken=events.token,role='Event')
            config=dict(mode='snapshot',networks=dict(httpServers=[h],wsServers=[w]))
            account_path=snow/'config/onebot_12345.json'
            app=FastAPI();install_desktop_routes(app,product);c=TestClient(app);headers={'X-ChatWeave-UI':'1'}
            def inspect():return c.post('/api/desktop/snowluma/inspect',headers=headers,json=dict(folder=str(snow))).json()
            def select(preview,account='12345',ws=True):
                row=next(a for a in preview['accounts'] if a['account']==account)
                return dict(selection_id=preview['selection_id'],account=account,http_id=row['nodes']['http'][0]['id'],ws_id=row['nodes']['ws'][0]['id'] if ws else '')
            def apply(body):return c.post('/api/desktop/snowluma/import',headers=headers,json=body).json()
            def snapshot():return (product/'.env').read_bytes() if (product/'.env').exists() else b''
            assert inspect()['code']=='account_config_missing'
            assert not snapshot()
            write(account_path,config)
            # Explicit original values must survive preview and failures.
            old=dict(sender_url='http://127.0.0.1:19999/previous',sender_token='old-http',events_url='ws://127.0.0.1:19998/previous',events_token='old-ws')
            save_product_settings(old,product);original=snapshot()
            source=account_path.read_bytes()
            p=inspect();assert p['ok'] and snapshot()==original
            assert not any(secret in json.dumps(p) for secret in (HTTP.token,events.token,'old-http','old-ws'))
            body=select(p)
            assert c.post('/api/desktop/snowluma/inspect',json=dict(folder=str(snow))).status_code==403
            assert c.post('/api/desktop/snowluma/import',headers=dict(headers,Origin='https://elsewhere.test'),json=body).status_code==403
            assert c.post('/api/desktop/snowluma/inspect',headers=dict(headers,**{'Sec-Fetch-Site':'cross-site'}),json=dict(folder=str(snow))).status_code==403
            # No arbitrary config values accepted from the renderer.
            assert apply(dict(body,http_id='not-a-node'))['code']=='selection_required'
            assert apply(dict(body,account='67890'))['code']=='selection_required'
            assert apply(dict(body,token='untrusted'))['code']=='selection_required'
            HTTP.error=401;assert apply(body)['code']=='http_auth_error';HTTP.error=404
            assert apply(body)['code']=='http_path_error';HTTP.error=0
            HTTP.account='67890';assert apply(body)['code']=='account_mismatch';HTTP.account='12345'
            with socket.socket() as unused:
                unused.bind(('127.0.0.1',0));h['port']=unused.getsockname()[1]
                write(account_path,config);assert apply(select(inspect()))['code']=='http_unreachable'
            h['port']=http.server_port;write(account_path,config);body=select(inspect())
            assert snapshot()==original
            events.token='other';r=apply(body);assert r['code']=='ws_auth_error' and r['http_ok'] and not r['saved'];events.token=w['accessToken']
            events.account='67890';assert apply(body)['code']=='ws_account_mismatch';events.account='12345'
            w['path']='/wrong-events';write(account_path,config);assert apply(select(inspect()))['code']=='ws_handshake_error'
            w['path']='/custom/events';write(account_path,config);body=select(inspect())
            assert snapshot()==original
            events.mode='silent'
            with patch('chatlocal.onebot_events.probe_connection',side_effect=lambda *args:probe_connection(*args,timeout=.4)):
                assert apply(body)['code']=='ws_unverified'
            events.mode='offline';assert apply(body)['code']=='ws_upstream_offline';events.mode='ok'
            assert snapshot()==original
            # Independent empty tokens replace old values, never inherit stale secrets.
            r=apply(body);assert r['ok'] and r['saved'] and not r['settings']['configured'],r
            cfg=event_configuration(product);assert cfg['token']==w['accessToken'] and cfg['url'].endswith('/custom/events')
            assert account_path.read_bytes()==source
            assert all(secret not in json.dumps(r) for secret in (h['accessToken'],w['accessToken']))
            assert apply(body)['saved']  # Repeat import is stable, not a node mutation.
            # A parallel manual save must not be overwritten after a slow probe.
            def concurrent_save(*args):
                result=probe_connection(*args)
                save_product_settings(dict(sender_url='http://127.0.0.1:19876/concurrent'),product)
                return result
            with patch('chatlocal.onebot_events.probe_connection',side_effect=concurrent_save):
                assert apply(body)['code']=='save_conflict'
            assert '19876/concurrent' in snapshot().decode()
            HTTP.token='';events.token='';h['accessToken']='';w['accessToken']='';write(account_path,config)
            assert apply(body)['code']=='config_changed'
            r=apply(select(inspect()));assert r['saved'] and event_configuration(product)['token']==''
            assert "REPLY_ONEBOT_TOKEN=''" in snapshot().decode()
            assert apply(select(inspect(),ws=False))['code']=='http_only'
            assert event_configuration(product)==dict(url='',token='')
            # First-time configuration doesn't require any model credentials.
            empty=root/'Empty';empty.mkdir();app2=FastAPI();install_desktop_routes(app2,empty);c2=TestClient(app2)
            p2=c2.post('/api/desktop/snowluma/inspect',headers=headers,json=dict(folder=str(snow))).json()
            r2=c2.post('/api/desktop/snowluma/import',headers=headers,json=select(p2)).json()
            assert r2['saved'] and 'API_KEY' not in (empty/'.env').read_text()
            # Overlay uses named replacement; snapshot does not accidentally import another global node.
            extra=dict(h,name='other-http',port=18888)
            write(snow/'config/onebot.json',dict(networks=dict(httpServers=[extra],wsServers=[])))
            assert len(inspect()['accounts'][0]['nodes']['http'])==1
            config['mode']='overlay';write(account_path,config)
            assert len(inspect()['accounts'][0]['nodes']['http'])==2
            write(account_path,dict(mode='overlay'))
            assert inspect()['accounts'][0]['nodes']['http'][0]['url']=='http://127.0.0.1:18888/custom/api'
            write(account_path,config)
            write(snow/'config/onebot.json',dict(networks=dict(httpServers=[dict(h,port=18888)],wsServers=[])))
            assert inspect()['accounts'][0]['nodes']['http'][0]['url'].startswith(f'http://127.0.0.1:{http.server_port}/')
            config['mode']='snapshot'
            config['networks']['httpServers'].append(dict(h,name='disabled',enabled=False))
            config['networks']['wsServers'].append(dict(w,name='api-only',role='Api'))
            write(account_path,config);write(snow/'config/onebot_67890.json',config)
            p=inspect();assert len(p['accounts'])==2
            row=p['accounts'][0];assert not row['nodes']['http'][1]['usable'] and not row['nodes']['ws'][1]['usable']
            assert apply(dict(select(p),http_id=row['nodes']['http'][1]['id']))['code']=='selection_required'
            # Damaged account doesn't disappear behind another account's valid config.
            account_path.write_text('{broken');p=inspect();assert p['accounts'][0]['code']=='invalid_json'
            account_path.unlink();assert len(inspect()['accounts'])==1
            write(account_path,dict(mode='future',networks={}));assert inspect()['accounts'][0]['code']=='unsupported_format'
            # Missing port, remote bind, and path query never become guessed local defaults.
            for changed in ({'port':None},{'host':'remote.test'},{'path':'/?token=bad'}):
                write(account_path,dict(mode='snapshot',networks=dict(httpServers=[dict(h,**changed)])))
                assert not inspect()['accounts'][0]['nodes']['http'][0]['usable']
            # HTTP upgrade follows the explicitly enabled node, including its path.
            write(account_path,dict(mode='snapshot',networks=dict(httpServers=[dict(h,enableWebSocket=True)])))
            p=inspect()['accounts'][0];assert p['nodes']['ws'][0]['url']==f'ws://127.0.0.1:{http.server_port}/custom/api'
            # Same token content hidden even when it appears in a node name.
            write(account_path,dict(mode='snapshot',networks=dict(httpServers=[dict(h,name='SECRET',accessToken='SECRET')])))
            assert 'SECRET' not in json.dumps(inspect())
            # Existing grants are checked without expanding or rewriting them.
            store=Store(root/'scope/messages.db');access=MCPAccess(store)
            with access.connect() as db:
                db.execute('INSERT INTO grants VALUES(?,?,?,?,?,?,?,?)',('grant','fixture','hash','{}','epoch',json.dumps({'qq':'67890'}),0,0))
            service=SimpleNamespace(access=access)
            try:check_account_scope(service,'12345')
            except SetupError as exc:assert exc.code=='scope_mismatch'
            else:raise AssertionError('Account scope mismatch accepted')
            check_account_scope(service,'67890')
            assert set(HTTP.actions)=={'/custom/api/get_login_info'}
            assert set(events.calls)=={'/custom/events','/wrong-events'}
        print('PASS: isolated real HTTP/WS, exact account/path/separate tokens, no-token import, old values retained on failure, scopes, JSON/snapshot/overlay, disabled/multiple nodes, fresh/repeated saves, origin guards; no personal QQ or message content accessed.')
    finally:events.close();http.shutdown();http.server_close();thread.join(3)


if __name__=='__main__':main()
