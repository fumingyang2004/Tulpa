"""Offline managed setup contracts; synthetic packages, accounts and admin API.

Never starts SnowLuma, hooks QQ, downloads an upstream binary, or writes QQ.
"""
import argparse
from contextlib import contextmanager
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import zipfile
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]
args=argparse.ArgumentParser();args.add_argument('--package',type=Path);options=args.parse_args()
ROOT=(options.package or SOURCE).resolve();sys.path.insert(0,str(ROOT))
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from chatlocal.snowluma_managed import ManagedSnowLuma,consent_key,load_manifest,_registry
from chatlocal.snowluma_secure import ManagedError,SecretVault,atomic_json,sha256,file_lock
from chatlocal.snowluma_local import LocalInstallation
from chatlocal.snowluma_runtime import SnowLumaAPI,onebot_config,process_identity
from chatlocal.desktop_routes import install_desktop_routes


def rejected(code,fn):
    try:fn()
    except ManagedError as exc:assert exc.code==code,(code,exc.code);return
    raise AssertionError('Expected '+code)


def wait(manager,phase,timeout=5):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        state=manager.status()
        if state['phase']==phase:return state
        if state['phase']=='error':raise AssertionError((phase,state))
        time.sleep(.015)
    raise AssertionError((phase,manager.status()))


class Vault:
    values={}
    def __init__(self,path):self.path=str(path)
    def read(self):return dict(self.values.get(self.path,{}))
    def write(self,value):self.values[self.path]=dict(value)


class Runtime:
    def __init__(self):
        self.qq={101:dict(pid=101,created=100,executable='C:/Fixture/QQ.exe')}
        self.owner=None;self.spawns=0;self.stops=0;self.external_hooks=set()
    def hooks(self):return set(self.external_hooks)
    def identity(self,pid):return copy.deepcopy(self.qq.get(pid))
    def spawn(self,directory,port,credentials,instance):
        self.spawns+=1;self.owner=dict(pid=901,created=self.spawns,executable=str(directory/'node.exe'),instance=instance)
        return dict(self.owner)
    def owned(self,owner,directory,instance):return self.owner==owner and owner.get('instance')==instance
    def stop(self,owner,directory,instance):
        assert self.owned(owner,directory,instance);self.stops+=1;self.owner=None;return True


def fixture_folder(folder, manifest):
    folder.mkdir(parents=True,exist_ok=True)
    files={'package.json':json.dumps(dict(name='@snowluma/runtime',version=manifest['version'])),
           'index.mjs':'// nonexecutable fixture','node.exe':b'MZ-fixture','fixture.node':b'fixture','LICENSE':'fixture'}
    for doc in manifest['agreements']:files[doc['file']]=doc['id']
    for name,value in files.items():(folder/name).write_bytes(value.encode() if isinstance(value,str) else value)
    return folder


def fixture_manifest():
    value=copy.deepcopy(load_manifest())
    for doc in value['agreements']:doc['sha256']=hashlib.sha256(doc['id'].encode()).hexdigest()
    return value


def accept(service, folder=None):
    folder=folder or service.root/'fixture-snow'
    if not folder.exists():fixture_folder(folder,service.manifest)
    preview=service.inspect(str(folder))
    return dict(accepted=True,fingerprint=preview['consent_fingerprint'],selection_id=preview['selection_id'])


class Installer(LocalInstallation):
    calls=0
    @contextmanager
    def acquire(self,receipt,instance,check):
        check();Installer.calls+=1
        with super().acquire(receipt,instance,check) as root:yield root


class API:
    def __init__(self,runtime):
        self.runtime=runtime;self.accounts_by_pid={101:'12345'};self.loaded=[];self.calls=[];self.changed_terms=False;self.auth_fail=False;self.fail_config=False
    def initialize(self,password,manifest,check):
        check();self.calls.append('initialize')
        if self.changed_terms:raise ManagedError('terms_changed','Fixture terms changed')
        if self.auth_fail:raise ManagedError('admin_auth_failed','Fixture authentication failure')
    def processes(self):return [dict(pid=pid,path='') for pid in self.runtime.qq]
    def probe(self,pid):return self.accounts_by_pid.get(pid,'')
    def accounts(self):return [self.accounts_by_pid[p] for p in self.loaded if self.accounts_by_pid.get(p)]
    def load(self,pid):
        self.calls.append(('load',pid))
        if pid not in self.loaded:self.loaded.append(pid)
    def unload(self,pid):
        self.calls.append(('unload',pid))
        if pid in self.loaded:self.loaded.remove(pid)
    def configure(self,account,ports,credentials):
        self.calls.append(('configure',account));self.config=onebot_config(ports,credentials)
        if self.fail_config:raise ManagedError('config_not_applied','Fixture port conflict',retryable=True)
    def request(self,*args):return dict(mustChangePassword=False)


class Receiver:
    def __init__(self,account):self.account=account;self.state='connected';self.stopped=False
    def status(self):return dict(state=self.state,account=self.account)
    def stop(self):self.stopped=True


def local_folder_checks(root):
    manifest=fixture_manifest()
    installer=LocalInstallation(manifest,root/'app/data/snowluma-managed')
    rejected('folder_required',lambda:installer.inspect('relative/path'))
    rejected('folder_missing',lambda:installer.inspect(str(root/'absent')))
    folder=fixture_folder(root/'user-supplied',manifest)
    before={p.name:p.read_bytes() for p in folder.iterdir()}
    receipt=installer.inspect(str(folder))
    assert receipt['mode']=='local' and not (folder/'config').exists()
    assert before=={p.name:p.read_bytes() for p in folder.iterdir()}
    (folder/'config').mkdir();(folder/'config/runtime.json').write_text('{"webuiPort": 5099}')
    with installer.acquire(receipt,'instance',lambda:None) as selected:
        assert selected==folder and (folder/'config/tulpa-owner.json').is_file()
        other=LocalInstallation(manifest,root/'other/data/snowluma-managed')
        rejected('owned_elsewhere',lambda:other.inspect(str(folder)))
        rejected('busy',lambda:installer.acquire(receipt,'instance',lambda:None).__enter__())
        backup=installer.directory/'local-config-backup/instance/runtime.json'
        assert backup.read_bytes()==(folder/'config/runtime.json').read_bytes()
    assert installer.verify(receipt)==folder
    (folder/'index.mjs').write_text('// changed')
    rejected('installation_changed',lambda:installer.verify(receipt))
    (folder/'EULA.md').write_text('changed terms')
    rejected('terms_changed',lambda:installer.inspect(str(folder)))
    external=fixture_folder(root/'external',manifest);(external/'config').mkdir()
    auth=external/'config/webui.json';auth.write_text('{"fixture":"existing admin auth, do not touch"}')
    value=auth.read_bytes();assert installer.inspect(str(external))['mode']=='existing' and auth.read_bytes()==value
    newer=fixture_folder(root/'newer',manifest)
    (newer/'package.json').write_text('{"name":"@snowluma/runtime","version":"9.9.9"}')
    rejected('unsupported_version',lambda:installer.inspect(str(newer)))
    lite=fixture_folder(root/'lite',manifest);(lite/'node.exe').unlink()
    rejected('package_incomplete',lambda:installer.inspect(str(lite)))
    if os.name=='nt':
        import subprocess
        link=root/'linked'
        result=subprocess.run(['powershell.exe','-NoProfile','-Command',
            'New-Item -ItemType Junction -Path '+repr(str(link))+' -Target '+repr(str(external))],capture_output=True)
        assert result.returncode==0
        try:rejected('unsafe_path',lambda:installer.inspect(str(link)))
        finally:link.rmdir()
    assert not any(p.name=='downloads' for p in root.rglob('*'))
    print('PASS local folder: read-only inspection, consent resource hash, in-place ownership/lock/backup, independent password preservation, changed files/terms, missing runtime, wrong version, junction rejection; downloads=0')


def adapter_checks():
    manifest=copy.deepcopy(load_manifest())
    for doc in manifest['agreements']:doc['sha256']=hashlib.sha256(doc['id'].encode()).hexdigest()
    calls=[];failure=[None]
    def server(request):
        calls.append((request.method,request.url.path,json.loads(request.content) if request.content else None))
        if failure[0]=='auth':return httpx.Response(401,json={'token':'must-not-appear'})
        path=request.url.path
        if path=='/api/login':return httpx.Response(200,json=dict(success=True,token='fixture-management-token',mustChangePassword=False))
        assert request.headers['Authorization']=='Bearer fixture-management-token'
        if path=='/api/agreements':return httpx.Response(200,json=dict(consentRequired=True,version='fixture-hash',documents=[dict(id=d['id'],text=d['id']+('changed' if failure[0]=='terms' else '')) for d in manifest['agreements']]))
        if path=='/api/agreements/record-consent':return httpx.Response(200,json=dict(success=True))
        if path=='/api/auth/state':return httpx.Response(200,json=dict(mustChangePassword=False))
        if path=='/api/status':return httpx.Response(200,json=dict(status='running'))
        if path=='/api/config/12345':return httpx.Response(200,json=dict(success=True,saved=True,applied=failure[0]!='apply',online=True))
        raise AssertionError(path)
    api=SnowLumaAPI(19876,client_factory=lambda **kw:httpx.Client(transport=httpx.MockTransport(server),**kw))
    api.initialize('fixture-password',manifest,lambda:None)
    assert [p for _,p,_ in calls]==['/api/login','/api/agreements','/api/agreements/record-consent','/api/auth/state','/api/status']
    assert calls[2][2]=={'version':'fixture-hash'}
    credentials=dict(http_token='http-secret',ws_token='ws-secret')
    api.configure('12345',dict(http=19001,ws=19002),credentials)
    config=calls[-1][2]
    assert config['networks']['httpServers'][0]['accessToken']=='http-secret'
    assert config['networks']['wsServers'][0]['accessToken']=='ws-secret'
    assert config['networks']['wsServers'][0]['role']=='Event'
    assert config['networks']['httpServers'][0]['host']=='127.0.0.1'
    failure[0]='apply';rejected('config_not_applied',lambda:api.configure('12345',dict(http=1,ws=2),credentials))
    failure[0]='terms';before=len(calls);rejected('terms_changed',lambda:api.initialize('fixture',manifest,lambda:None))
    assert '/api/agreements/record-consent' not in [p for _,p,_ in calls[before:]]
    failure[0]='auth';rejected('admin_auth_failed',lambda:api.initialize('fixture',manifest,lambda:None))
    rejected('admin_action_rejected',lambda:api.request('POST','/api/debug/invoke',{}))
    print('PASS official v1.14.22 request shapes (mock): login, exact agreements hash, official consent, separate tokens, applied!=saved, auth failure, narrow action list')


def lifecycle_checks(root):
    locked=ManagedSnowLuma(root/'locked');fingerprint=consent_key(locked.manifest)
    rejected('consent_required',lambda:locked.begin(dict(accepted=False,fingerprint=fingerprint,selection_id='absent')))
    rejected('selection_expired',lambda:locked.begin(dict(accepted=True,fingerprint=fingerprint,selection_id='absent')))
    assert not locked.directory.exists()
    manifest=fixture_manifest()
    runtime=Runtime();api=API(runtime);receivers=[];invalid=[]
    def probe(http,ws,account,check):
        check();r=Receiver(account);receivers.append(r);return r
    instances=[]
    def new(where):
        result=ManagedSnowLuma(where,manifest=manifest,runtime=runtime,api_factory=lambda _:api,
            installer_factory=Installer,vault_factory=Vault,probe=probe,poll_seconds=.04,on_invalidated=lambda:invalid.append(1),auto_select_single=False)
        instances.append(result);return result
    service=new(root/'Tulpa');key=str(service.root.resolve()).casefold();_registry[key]=service
    body=accept(service)
    try:
        service.begin(body);s=wait(service,'choosing');assert len(s['choices'])==1
        assert service.begin(body)['already_started']
        bad=dict(body,fingerprint='old');rejected('consent_required',lambda:service.begin(bad))
        service.select(s['choices'][0]['id']);s=wait(service,'ready');assert s['account']=='12345'
        assert runtime.spawns==1 and api.loaded==[101]
        config=service.connection('http');marker=config['_managed'];service.guard(marker)
        public=json.dumps(service.status());assert all(secret not in public for secret in service.vault.read().values())
        assert not (service.root/'.env').exists()
        peer=new(service.root);peer.begin(accept(peer));time.sleep(.1)
        assert runtime.spawns==1,(runtime.spawns,service.state(),api.calls)
        # Broken WS invalidates dependent sessions and restores only same identity.
        receivers[-1].state='disconnected';wait(service,'reconnecting');assert service.connection('http')['url']=='' and invalid
        receivers[-1].state='connected';wait(service,'ready')
        runtime.qq[101]['created']=101
        end=time.monotonic()+3
        while service.status()['phase']!='error' and time.monotonic()<end:time.sleep(.02)
        assert service.status()['code']=='process_changed' and not service.connection('http')['url']
        service.close();assert runtime.stops==1
        # Restarts retain account but require newly verified process identity.
        service.retry();s=wait(service,'choosing');service.select(s['choices'][0]['id']);wait(service,'ready')
        rejected('cancelled',lambda:service.guard(marker))
        marker=service.connection('http')['_managed']
        service.close();assert service.state()['phase']=='stopped'
        restored=new(service.root);_registry[key]=restored;restored.resume();wait(restored,'ready')
        assert restored.state()['account']=='12345'
        # Account change: fixed binding fails, no token roulette/new-account config.
        api.accounts_by_pid[101]='67890'
        end=time.monotonic()+3
        while restored.status()['phase']!='error' and time.monotonic()<end:time.sleep(.02)
        assert restored.status()['code']=='account_mismatch'
        assert ('configure','67890') not in api.calls
        restored.cancel(revoke=True);assert restored.status()['phase']=='revoked'
        rejected('consent_required',restored.retry)
        assert not restored.connection('http')['url']
        restored.use_external();assert restored.connection('http') is None
        # Two roots, multiple QQ, unknown account, no QQ, cancel/retry, stale choices.
        runtime.qq.clear();api.loaded.clear();second=new(root/'OtherTulpa');second.begin(accept(second));wait(second,'waiting_qq')
        runtime.qq[202]=dict(pid=202,created=200,executable='C:/Fixture/QQ.exe');api.accounts_by_pid[202]=''
        runtime.qq[303]=dict(pid=303,created=300,executable='C:/Fixture/QQ.exe');api.accounts_by_pid[303]='67890'
        s=wait(second,'choosing');assert len(s['choices'])==2 and s['choices'][0]['account_label']=='账号待验证'
        second.select(s['choices'][0]['id']);wait(second,'waiting_login');assert 303 not in api.loaded
        second.cancel();assert second.state()['phase']=='cancelled' and second.connection('http')['url']==''
        assert not list(second.directory.glob('*startup*'))
        second.close()
        # Existing external hooks are preserved; no download, attach, or stop.
        runtime.external_hooks={303};before=(Installer.calls,runtime.spawns,runtime.stops,list(api.calls))
        blocked=new(root/'ExternalConflict');blocked.begin(accept(blocked))
        end=time.monotonic()+3
        while blocked.status()['phase']!='error' and time.monotonic()<end:time.sleep(.02)
        assert blocked.status()['code']=='external_hook_present'
        assert before==(Installer.calls,runtime.spawns,runtime.stops,api.calls)
        blocked.close();runtime.external_hooks.clear()
        # Browser routes cannot bypass explicit folder/consent selection, leak keys,
        # or accept writes from a foreign Origin/missing UI header.
        app=FastAPI();install_desktop_routes(app,root/'routes')
        with TestClient(app) as client:
            s=client.get('/api/desktop/snowluma/managed').json();assert s['available'] is True
            route='/api/desktop/snowluma/managed/start'
            b=dict(accepted=True,fingerprint=s['consent_fingerprint'],selection_id='forged')
            assert client.post(route,json=b).status_code==403
            assert client.post(route,json=b,headers={'X-ChatWeave-UI':'1','Origin':'https://foreign.invalid'}).status_code==403
            r=client.post(route,json=b,headers={'X-ChatWeave-UI':'1'}).json();assert r['code']=='selection_expired'
        # The default one-QQ flow proceeds without an extra process-picker step.
        runtime.qq={101:dict(pid=101,created=400,executable='C:/Fixture/QQ.exe')};api.loaded.clear();api.accounts_by_pid[101]='12345'
        automatic=new(root/'Automatic');automatic.auto_select_single=True
        automatic.begin(accept(automatic));wait(automatic,'ready');automatic.cancel(revoke=True)
        constrained=new(root/'GrantMismatch');constrained.auto_select_single=True
        def mismatch(account):raise ManagedError('scope_mismatch','Fixture account is outside current grants')
        constrained.check_account=mismatch;before=list(api.calls)
        constrained.begin(accept(constrained))
        end=time.monotonic()+3
        while constrained.status()['phase']!='error' and time.monotonic()<end:time.sleep(.02)
        assert constrained.status()['code']=='scope_mismatch'
        assert not any(isinstance(c,tuple) and c[0] in ('load','configure') for c in api.calls[len(before):])
        constrained.close()
        print('PASS lifecycle fixtures: explicit local folder/consent, atomic owner, one-QQ automatic setup, duplicate start, unknown/no/multiple process identity, PID reuse, fixed account, WS loss, cancel/revoke, restart, no .env secrets, local-only routes')
    finally:
        for value in list(_registry.values()):
            if str(value.root).startswith(str(root)):value.close()
        for value in instances:value.close()


def main():
    (SOURCE/'.tmp').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='managed-snow-',dir=SOURCE/'.tmp') as temp:
        root=Path(temp)
        if os.name=='nt':
            vault=SecretVault(root/'vault');secret=dict(password='synthetic-not-a-service-password')
            vault.write(secret);assert vault.read()==secret and b'synthetic' not in vault.path.read_bytes()
            identity=process_identity(os.getpid());assert identity and identity['pid']==os.getpid()
            print('PASS Windows DPAPI roundtrip/current-process identity using synthetic local material')
        local_folder_checks(root);adapter_checks();lifecycle_checks(root)
    print('PASS offline only; SnowLuma binary downloads=0, QQ injections=0, QQ writes=0, real-PC first-run=NOT RUN')


if __name__=='__main__':main()
