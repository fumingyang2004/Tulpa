"""Redaction fixtures and a real local LAN support-server protocol check."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from desktop.diagnose import collect,failure_summary


def check_redaction():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='support-check-') as name:
        root=Path(name);private=root/'reports/private';private.mkdir(parents=True)
        secret='private-fixture-value-do-not-export'
        (root/'.env').write_text('API_KEY='+secret)
        (root/'data').mkdir()
        (root/'data/read-latest.json').write_text(json.dumps(dict(status='ok',finished_at='2026-09-24T23:20:00+08:00',
            platforms=[dict(platform='wechat',status='ok',added=3,duplicate=5,invalid_timestamps=6,detail=secret)],options=dict(scope='private-account'))))
        (private/'data-wechat-error.log').write_text('RuntimeError: 数据库无可用密钥: private-account\n'+secret,encoding='utf-8')
        (private/'live').mkdir()
        (private/'live/wechat.jsonl').write_text(json.dumps(dict(at=1800000000,event='failure',code='worker_timeout',stage='cached_keys',content=secret))+'\n')
        value=collect(root);raw=json.dumps(value)
        assert secret not in raw and 'private-account' not in raw and str(root) not in raw
        assert value['last_import']['wechat']['codes']==['wechat_no_database_keys']
        assert value['qq_runtime']['error']=='missing_helper'
        assert value['live']['wechat']['recent']==[dict(at=1800000000,event='failure',code='worker_timeout',stage='cached_keys')]
        assert value['latest_read']['platforms']==[dict(platform='wechat',status='ok',added=3,duplicate=5,invalid_timestamps=6)]
        # File sharing violations are not elevation failures. Paths/messages
        # remain private, while exact code location + OS code survives export.
        log=private/'data-wechat-error.log'
        log.write_text('Traceback (most recent call last):\n'
            '  File "C:\\Users\\private-person\\ChatWeave\\scripts\\snapshot_cache.py", line 89, in refresh\n'
            '    private-source-line\n'
            'PermissionError: [WinError 32] private-account private-file.db '+secret,encoding='utf-8')
        value=failure_summary(log)
        assert value['codes']==['file_in_use'] and value['winerror']==32
        assert value['frames']==[dict(file='scripts/snapshot_cache.py',line=89)]
        assert value['exception_type']=='PermissionError'
        assert all(word not in json.dumps(value) for word in (secret,'private-person','private-source-line','private-account','private-file'))
        log.write_text('RuntimeError: permissions may be insufficient; PermissionError is one possibility',encoding='utf-8')
        assert 'permission_denied' not in failure_summary(log)['codes']
    print('PASS report allowlist: raw logs, configuration, account names and paths not exported.')


def check_transport(package,bind):
    package=package.resolve();(package/'.tmp').mkdir(exist_ok=True)
    ready=package/'.tmp'/('support-'+uuid.uuid4().hex+'.json')
    process=subprocess.Popen([str(package/'runtime/python.exe'),str(package/'desktop/diagnose.py'),'--serve',bind,'--ready',str(ready)],
        cwd=package,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        deadline=time.monotonic()+25
        while not ready.exists() and time.monotonic()<deadline:
            assert process.poll() is None,process.stderr.read().decode('utf-8',errors='replace');time.sleep(.1)
        data=json.loads(ready.read_text());url=data['url'];headers={'Authorization':'Bearer '+data['token']}
        def status(path=url,head=headers,method='GET'):
            try:
                response=opener.open(urllib.request.Request(path,headers=head,method=method),timeout=3)
                return response.status,response.read()
            except urllib.error.HTTPError as exc:return exc.code,exc.read()
        code,body=status();report=json.loads(body)
        assert code==200 and report['qq_runtime']['ok'] and report['runtime']['packaged']
        assert report['qq_runtime']['sqlite_version']=='3.53.2'
        assert status(head={})[0]==403
        assert status(head={'Authorization':'Bearer wrong'})[0]==403
        assert status(head=dict(headers,Origin='https://example.test'))[0]==403
        assert status(path=url.replace('/diagnostics','/../.env'))[0]==403
        assert status(method='POST')[0]==501
    finally:
        process.kill();process.wait(timeout=5);ready.unlink(missing_ok=True)
    from urllib.parse import urlparse
    target=urlparse(url)
    with socket.socket() as sock:
        sock.settimeout(1);assert sock.connect_ex((target.hostname,target.port))!=0
    print('PASS actual packaged LAN transport: paired report only, bad token/origin/path/method rejected, listener removed on close. Not native GUI or second-PC acceptance.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--package',type=Path);parser.add_argument('--bind');args=parser.parse_args()
    check_redaction()
    if args.package and args.bind:check_transport(args.package,args.bind)
