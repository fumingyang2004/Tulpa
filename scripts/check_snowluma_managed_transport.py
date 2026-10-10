"""Local TCP/WS + Windows primitives with synthetic accounts, never SnowLuma/QQ."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

# Windows embeddable Python intentionally omits the caller's directory from
# sys.path; load fixture helpers explicitly, just as the package tests do.
sys.path.insert(0,str(Path(__file__).resolve().parent))
from check_snowluma_managed import (ROOT, SOURCE, ManagedSnowLuma, ManagedError,
    Runtime, API, Installer, Vault, Receiver, load_manifest, consent_key, wait, rejected)
from chatlocal.snowluma_secure import file_lock, read_json, atomic_json
from chatlocal.snowluma_runtime import WindowsRuntime, process_identity
from chatlocal.onebot import Client, OneBotError
from chatlocal.message_sender import QQSender, SendError
from chatlocal.snowluma_managed import _registry
from websockets.sync.server import serve
from websockets.exceptions import ConnectionClosed


def error_state(service, code):
    end=time.monotonic()+4
    while time.monotonic()<end:
        state=service.status()
        if state['phase']=='error':
            assert state['code']==code,(code,state);return
        time.sleep(.01)
    raise AssertionError((code,service.status()))


def faults(root):
    manifest=copy.deepcopy(load_manifest());manifest['automation_authorized']=True
    body=dict(accepted=True,fingerprint=consent_key(manifest))
    for case in ('terms','auth','config','login','event','cancel-download','restart-init'):
        runtime=Runtime();api=API(runtime);entered=threading.Event();release=threading.Event()
        class DelayedInstaller(Installer):
            def deploy(self, check, progress):
                entered.set();release.wait(3);check();return super().deploy(check,progress)
        receiver=Receiver('12345')
        if case=='terms':api.changed_terms=True
        if case=='auth':api.auth_fail=True
        if case=='config':api.fail_config=True
        if case=='login':api.accounts_by_pid[101]=''
        if case=='event':receiver.state='verifying'
        service=ManagedSnowLuma(root/case,manifest=manifest,runtime=runtime,api_factory=lambda _:api,
            installer_factory=DelayedInstaller if case=='cancel-download' else Installer,
            vault_factory=Vault,probe=lambda *a:receiver,poll_seconds=.02,login_timeout=.15,event_timeout=.15)
        try:
            service.begin(body)
            if case=='cancel-download':
                assert entered.wait(2)
                cancel=threading.Thread(target=service.cancel);cancel.start()
                while service.state()['enabled']:time.sleep(.01)
                release.set();cancel.join(5);assert not cancel.is_alive()
                assert runtime.spawns==0 and service.state()['phase']=='cancelled';continue
            if case in ('terms','auth'):
                error_state(service,'terms_changed' if case=='terms' else 'admin_auth_failed')
                assert not api.loaded and runtime.stops==1;continue
            s=wait(service,'choosing')
            if case=='restart-init':
                original=service.vault.read();service.close();service.resume();s=wait(service,'choosing')
                assert service.vault.read()==original and runtime.spawns==2
            service.select(s['choices'][0]['id'])
            if case in ('config','login','event'):
                error_state(service,dict(config='config_not_applied',login='login_timeout',event='event_unverified')[case])
                assert runtime.stops==1 and not service.connection('http')['url']
            else:wait(service,'ready')
        finally:release.set();service.close()
    print('PASS lifecycle failures: changed terms/auth, config not applied, login/event timeouts, cancellation after download, initialization restart')


def transport(root):
    manifest=copy.deepcopy(load_manifest());manifest['automation_authorized']=True
    runtime=Runtime();api=API(runtime);resources=[];received=[];credentials={};wire=[]
    class HTTP(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length','0')))
            wire.append(self.path)
            assert self.path=='/get_login_info','Unexpected write action'
            assert self.headers.get('Authorization')=='Bearer '+credentials['http_token']
            raw=json.dumps(dict(status='ok',retcode=0,data=dict(user_id='12345'))).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def configure(account,ports,secrets):
        credentials.update(secrets)
        http=ThreadingHTTPServer(('127.0.0.1',ports['http']),HTTP)
        ht=threading.Thread(target=http.serve_forever,daemon=True);ht.start();resources.append((http,ht))
        def auth(ws,request):
            if request.headers.get('Authorization')!='Bearer '+credentials['ws_token']:return ws.respond(401,'fixture')
        def events(ws):
            # The setup probe must ignore chat content, including wrong-account text.
            ws.send(json.dumps(dict(post_type='message',self_id='99999',raw_message='synthetic ignored')))
            ws.send(json.dumps(dict(post_type='meta_event',meta_event_type='heartbeat',self_id='12345',status=dict(online=True))))
            try:
                for item in ws:received.append(item)
            except ConnectionClosed:pass
        ws=serve(events,'127.0.0.1',ports['ws'],process_request=auth)
        wt=threading.Thread(target=ws.serve_forever,daemon=True);wt.start();resources.append((ws,wt))
    api.configure=configure
    service=ManagedSnowLuma(root,manifest=manifest,runtime=runtime,api_factory=lambda _:api,
        installer_factory=Installer,vault_factory=Vault,poll_seconds=.03)
    _registry[str(root.resolve()).casefold()]=service
    try:
        service.begin(dict(accepted=True,fingerprint=consent_key(manifest)))
        s=wait(service,'choosing');service.select(s['choices'][0]['id']);wait(service,'ready')
        config=service.connection('http');client=Client(config)
        assert client.login()=='12345' and service.heartbeat.status()['last_message_at']==0
        sender=QQSender(dict(REPLY_ONEBOT_URL=config['url'],REPLY_ONEBOT_TOKEN=config['token'],_managed=config['_managed']))
        assert sender.call('get_login_info',{})['user_id']=='12345'
        service.cancel(revoke=True);before=len(wire)
        try:client.call('send_group_msg',{},approved=True)
        except OneBotError as exc:assert exc.code=='managed_not_ready'
        else:raise AssertionError('Revoked cached client accepted write')
        try:sender.call('send_group_msg',{},sending=True)
        except SendError:pass
        else:raise AssertionError('Revoked cached sender accepted write')
        assert len(wire)==before and not received and set(wire)=={'/get_login_info'}
    finally:
        service.close();_registry.pop(str(root.resolve()).casefold(),None)
        for server,thread in resources:
            server.shutdown();thread.join(3)
            if hasattr(server,'server_close'):server.server_close()
    print('PASS real loopback sockets / synthetic QQ: distinct HTTP+WS tokens, metadata identity, cached write clients reject revocation; QQ write requests=0')


def primitives(root):
    # Stress actual Windows replacement/read handles rather than merely mocks.
    path=root/'atomic.json';atomic_json(path,{'n':0});failures=[]
    def writes():
        try:
            for n in range(150):atomic_json(path,{'n':n})
        except Exception as exc:failures.append((type(exc).__name__,getattr(exc,'winerror',None),str(exc)))
    thread=threading.Thread(target=writes);thread.start()
    while thread.is_alive():assert isinstance(read_json(path)['n'],int)
    thread.join();assert not failures,failures
    lock=root/'owner.lock'
    with file_lock(lock):
        rejected('busy',lambda:file_lock(lock,blocking=False).__enter__())
        child_code="from pathlib import Path\nimport sys\nsys.path.insert(0,sys.argv[1])\nfrom chatlocal.snowluma_secure import file_lock,ManagedError\ntry:\n with file_lock(Path(sys.argv[2]),blocking=False): pass\nexcept ManagedError as e:\n sys.exit(0 if e.code=='busy' else 3)\nsys.exit(4)"
        result=subprocess.run([sys.executable,'-c',child_code,str(ROOT),str(lock)],capture_output=True,timeout=12,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        assert result.returncode==0,result.stderr.decode(errors='replace')
    with file_lock(lock,blocking=False):pass  # Stale on-disk lock is harmless.
    if os.name=='nt':
        runtime=WindowsRuntime()
        child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)'],creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            identity=process_identity(child.pid);assert identity
            runtime._job(child);runtime.release_job(child.pid);child.wait(timeout=5)
            assert child.poll() is not None
        finally:
            runtime.release_job(child.pid)
            if child.poll() is None:child.terminate();child.wait(timeout=5)
        with patch('chatlocal.snowluma_runtime.os.listdir',return_value=['mojo.101.control','OtherPrivatePipe','mojo.202.recv']):
            assert runtime.hooks()=={101}
        broken=root/'broken-auth';(broken/'config').mkdir(parents=True)
        authfile=broken/'config/webui.json';authfile.write_text('{"mustChangePassword":true}')
        original=authfile.read_bytes()
        with patch.object(runtime,'hooks',return_value=set()),patch('chatlocal.snowluma_runtime.port_available',return_value=True),patch('chatlocal.snowluma_runtime.subprocess.Popen') as launch:
            rejected('credential_state_invalid',lambda:runtime.spawn(broken,19800,{},'fixture'))
            assert not launch.called and authfile.read_bytes()==original
    print('PASS real OS primitives: concurrent atomic status I/O, cross-process lock, stale lock recovery, Windows owned job termination (Python fixture only)')


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='managed-wire-',dir=SOURCE/'.tmp') as temp:
        root=Path(temp);primitives(root);faults(root);transport(root/'wire')
    print('PASS offline integration; no real SnowLuma, QQ process, credentials, or chat data accessed')
