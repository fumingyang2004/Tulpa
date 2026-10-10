"""Offline managed setup contracts; synthetic packages, accounts and admin API.

Never starts SnowLuma, hooks QQ, downloads an upstream binary, or writes QQ.
"""
import argparse
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
from chatlocal.snowluma_package import PackageInstaller
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


class Installer:
    calls=0
    def __init__(self,manifest,directory):self.directory=directory
    def deploy(self,check,progress):
        check();Installer.calls+=1;progress('downloading',1,2);check();progress('download_verified',2,2)
        root=self.directory/'runtime';root.mkdir(parents=True,exist_ok=True);return root


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


def package_checks(root):
    original=load_manifest();manifest=copy.deepcopy(original)
    for doc in manifest['agreements']:doc['sha256']=hashlib.sha256(doc['id'].encode()).hexdigest()
    def archive(extra=None,version='1.14.22'):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            files={'package.json':json.dumps(dict(name='@snowluma/runtime',version=version)),
                   'index.mjs':'// nonexecutable fixture','node.exe':b'MZ-fixture','fixture.node':b'fixture',
                   'LICENSE':'fixture','EULA.md':'eula','PRIVACY.md':'privacy'}
            if extra:files.update(extra)
            for p,value in files.items():
                info=zipfile.ZipInfo(p);info.filename=p
                z.writestr(info,value)
        return stream.getvalue()
    raw=archive();manifest['package'].update(size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    requests=[]
    def server(request):
        requests.append(request)
        offset=int(request.headers['Range'].split('=')[1].split('-')[0])
        return httpx.Response(206,content=raw[offset:],headers={'Content-Range':f'bytes {offset}-{len(raw)-1}/{len(raw)}'})
    def factory(**kw):return httpx.Client(transport=httpx.MockTransport(server),**kw)
    package=PackageInstaller(manifest,root/'package',client_factory=factory)
    check=lambda:None;progress=lambda *a:None
    installed=package.deploy(check,progress)
    assert len(requests)==1 and (installed/'node.exe').read_bytes()==b'MZ-fixture'
    package.deploy(check,progress);assert len(requests)==1
    (installed/'index.mjs').write_text('changed')
    rejected('installed_hash_mismatch',lambda:package.deploy(check,progress))
    # Resume from bytes, cache always rehashed; corrupted cache never accepted.
    another=PackageInstaller(manifest,root/'resumed',client_factory=factory)
    partial=another.directory/'downloads'/(manifest['package']['sha256']+'.partial')
    partial.parent.mkdir(parents=True);partial.write_bytes(raw[:100])
    assert another.download(check,progress).read_bytes()==raw and requests[-1].headers['Range']=='bytes=100-'
    third=PackageInstaller(manifest,root/'complete-partial',client_factory=factory)
    part=third.directory/'downloads'/partial.name;part.parent.mkdir(parents=True);part.write_bytes(raw)
    before=len(requests);third.download(check,progress);assert len(requests)==before
    final=another.directory/'downloads'/(manifest['package']['sha256']+'.zip');final.write_bytes(b'bad')
    rejected('cache_hash_mismatch',lambda:another.download(check,progress))
    counter=[0]
    def cancel():
        counter[0]+=1
        if counter[0]>1:raise ManagedError('cancelled','fixture')
    cancelling=PackageInstaller(manifest,root/'cancelled',client_factory=factory)
    rejected('cancelled',lambda:cancelling.deploy(cancel,progress))
    assert not (cancelling.directory/'runtime').exists()
    for n,extra in enumerate([{'../escape':'x'},{'C:/outside':'x'},{'foo\\bar':'x'},{'NUL.txt':'x'},{'INDEX.MJS':'x'}]):
        bad=archive(extra);m=copy.deepcopy(manifest);m['package'].update(sha256=hashlib.sha256(bad).hexdigest(),size=len(bad))
        p=root/f'bad{n}.zip';p.write_bytes(bad)
        rejected('archive_path',lambda:PackageInstaller(m,root/f'bad{n}').extract(p,root/f'unpack{n}',check))
    wrong=archive(version='9.9.9');m=copy.deepcopy(manifest);m['package']['sha256']=hashlib.sha256(wrong).hexdigest();p=root/'wrong.zip';p.write_bytes(wrong)
    rejected('version_mismatch',lambda:PackageInstaller(m,root/'version').extract(p,root/'wrong-version',check))
    def bad_net(request):raise httpx.ConnectError('fixture secret omitted')
    net=PackageInstaller(manifest,root/'network',client_factory=lambda **kw:httpx.Client(transport=httpx.MockTransport(bad_net),**kw))
    rejected('download_network',lambda:net.download(check,progress))
    m=copy.deepcopy(manifest);m['package']['url']='https://untrusted.invalid/zip'
    rejected('source_rejected',lambda:PackageInstaller(m,root/'source').download(check,progress))
    print('PASS synthetic package: pinned hash, cache recheck, partial/complete resume, cancellation, corrupt cache, paths, wrong version, no network fallback')


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
    rejected('consent_required',lambda:locked.begin(dict(accepted=False,fingerprint=fingerprint)))
    rejected('upstream_permission_required',lambda:locked.begin(dict(accepted=True,fingerprint=fingerprint)))
    assert not locked.directory.exists()
    manifest=copy.deepcopy(load_manifest());manifest['automation_authorized']=True  # In-memory synthetic adapter only.
    runtime=Runtime();api=API(runtime);receivers=[];invalid=[]
    def probe(http,ws,account,check):
        check();r=Receiver(account);receivers.append(r);return r
    instances=[]
    def new(where):
        result=ManagedSnowLuma(where,manifest=manifest,runtime=runtime,api_factory=lambda _:api,
            installer_factory=Installer,vault_factory=Vault,probe=probe,poll_seconds=.04,on_invalidated=lambda:invalid.append(1))
        instances.append(result);return result
    service=new(root/'Tulpa');key=str(service.root.resolve()).casefold();_registry[key]=service
    body=dict(accepted=True,fingerprint=consent_key(manifest))
    try:
        service.begin(body);s=wait(service,'choosing');assert len(s['choices'])==1
        assert service.begin(body)['already_started']
        bad=dict(body,fingerprint='old');rejected('consent_required',lambda:service.begin(bad))
        service.select(s['choices'][0]['id']);s=wait(service,'ready');assert s['account']=='12345'
        assert runtime.spawns==1 and api.loaded==[101]
        config=service.connection('http');marker=config['_managed'];service.guard(marker)
        public=json.dumps(service.status());assert all(secret not in public for secret in service.vault.read().values())
        assert not (service.root/'.env').exists()
        peer=new(service.root);peer.begin(body);time.sleep(.1)
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
        runtime.qq.clear();api.loaded.clear();second=new(root/'OtherTulpa');second.begin(body);wait(second,'waiting_qq')
        runtime.qq[202]=dict(pid=202,created=200,executable='C:/Fixture/QQ.exe');api.accounts_by_pid[202]=''
        runtime.qq[303]=dict(pid=303,created=300,executable='C:/Fixture/QQ.exe');api.accounts_by_pid[303]='67890'
        s=wait(second,'choosing');assert len(s['choices'])==2 and s['choices'][0]['account_label']=='账号待验证'
        second.select(s['choices'][0]['id']);wait(second,'waiting_login');assert 303 not in api.loaded
        second.cancel();assert second.state()['phase']=='cancelled' and second.connection('http')['url']==''
        assert not list(second.directory.glob('*startup*'))
        second.close()
        # Existing external hooks are preserved; no download, attach, or stop.
        runtime.external_hooks={303};before=(Installer.calls,runtime.spawns,runtime.stops,list(api.calls))
        blocked=new(root/'ExternalConflict');blocked.begin(body)
        end=time.monotonic()+3
        while blocked.status()['phase']!='error' and time.monotonic()<end:time.sleep(.02)
        assert blocked.status()['code']=='external_hook_present'
        assert before==(Installer.calls,runtime.spawns,runtime.stops,api.calls)
        blocked.close();runtime.external_hooks.clear()
        # Browser routes cannot bypass the production license gate, leak keys,
        # or accept writes from a foreign Origin/missing UI header.
        app=FastAPI();install_desktop_routes(app,root/'routes')
        with TestClient(app) as client:
            s=client.get('/api/desktop/snowluma/managed').json();assert s['available'] is False
            route='/api/desktop/snowluma/managed/start'
            b=dict(accepted=True,fingerprint=s['consent_fingerprint'])
            assert client.post(route,json=b).status_code==403
            assert client.post(route,json=b,headers={'X-ChatWeave-UI':'1','Origin':'https://foreign.invalid'}).status_code==403
            r=client.post(route,json=b,headers={'X-ChatWeave-UI':'1'}).json();assert r['code']=='upstream_permission_required'
        print('PASS lifecycle fixtures: opt-in/license gate, atomic owner, duplicate start, unknown/no/multiple process identity, PID reuse, fixed account, WS loss, cancel/revoke, restart, no .env secrets, local-only routes')
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
        package_checks(root);adapter_checks();lifecycle_checks(root)
    print('PASS offline only; SnowLuma binary downloads=0, QQ injections=0, QQ writes=0, real-PC first-run=NOT RUN')


if __name__=='__main__':main()
