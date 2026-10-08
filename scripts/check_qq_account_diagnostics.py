"""Directory fixtures, failure classification and privacy for the support probe."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import diagnose_qq_accounts as d


def main():
    with tempfile.TemporaryDirectory(prefix='qq-account-diagnostic-') as tmp:
        folder=Path(tmp);install=folder/'private-install';install.mkdir()
        data=folder/'private-dataset';data.mkdir()
        known=data/'12345678912'/'nt_qq/nt_db/nt_msg.db'
        changed=data/'nt_qq_private-owner'/'nt_db/nt_msg.db'
        legacy=data/'12345678913'/'Msg3.0.db'
        for path in (known,changed,legacy):
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b'DO-NOT-READ-CHAT-CONTENT')
        (data/'0000'/'nt_qq/nt_db').mkdir(parents=True)
        (data/'0000'/'nt_qq/nt_db/nt_msg.db').write_bytes(b'')
        before={str(p):p.read_bytes() for p in data.rglob('*.db')}
        with patch.object(Path,'read_bytes',side_effect=AssertionError('Metadata probe read a file body')):
            scan=d.inspect_directory(data)
            assert scan['state']=='directory' and len(scan['databases'])==3
            assert any(c.get('legacy_db') for c in scan['children'])
            account_root=d.inspect_directory(known.parents[2])
            assert any(item['depth']==0 for item in account_root['databases'])
        assert d.inspect_directory(data/'absent')['state']=='missing'
        assert d.inspect_directory(known)['state']=='not_directory'
        assert d.inspect_directory(data,limit=1)['truncated']
        with patch('diagnose_qq_accounts.os.scandir',side_effect=PermissionError(13,'private permission text')):
            denied=d.inspect_directory(data)
            assert denied['state']=='error' and denied['error']['errno']==13
            assert 'private permission text' not in json.dumps(denied)
        ini=folder/'UserDataInfo.ini'
        ini.write_text('[data]\nUserDataSavePath='+str(data)+'\nToken=secret-token-never-export\n','utf-8')
        config=d.config_hints([str(ini)])
        assert config[0]['hints']==[str(data)]
        assert 'secret-token' not in json.dumps(config)
        def worker(stage,root,payload=None,timeout=12):
            if stage=='paths':return dict(roots=[dict(source='fixture',path=str(data),stock=True)],drives=[],override_set=False)
            if stage=='processes':return dict(state='ok',processes=[dict(name='QQ.exe',version='9.9.36.53644',exe=str(folder/'private-user/QQ.exe'),user_data=None)])
            if stage=='reader':return dict(state='ok',selected_root=str(data),accounts=[])
            if stage=='configs':return dict(files=d.config_hints([str(ini)]))
            if stage=='directory':return d.inspect_directory(Path(payload['path']))
            raise AssertionError(stage)
        with patch.object(d,'run_worker',side_effect=worker):report=d.collect(install)
        raw=json.dumps(report,ensure_ascii=False)+d.text_report(report)
        for secret in ('12345678912','12345678913','nt_qq_private-owner','private-dataset','private-install','private-user','secret-token','DO-NOT-READ-CHAT-CONTENT',str(folder)):
            assert secret not in raw,'Report leaked '+secret
        assert '<账号' in raw and '<目录' in raw
        codes={item['code'] for item in report['conclusions']}
        assert {'account_detection_empty','non_numeric_account_directory'}<=codes
        report['installed_detection'].update(state='timeout',phase='find_data_root')
        assert 'account_detection_timeout' in {item['code'] for item in d.conclusions(report)}
        report['installed_detection'].update(state='error',error={'type':'ModuleNotFoundError','module':'Crypto'})
        assert 'account_detection_error' in {item['code'] for item in d.conclusions(report)}
        report['installed_detection'].update(state='ok',account_count=2)
        assert 'accounts_found_now' in {item['code'] for item in d.conclusions(report)}
        timeout=subprocess.TimeoutExpired(['python'],25,output=b'{"progress":"find_data_root"}\nprivate-secret-path')
        with patch.object(d.subprocess,'run',side_effect=timeout):
            value=d.run_worker('reader',install)
            assert value['state']=='timeout' and value['phase']=='find_data_root'
            assert 'private-secret' not in json.dumps(value)
        assert before=={str(p):p.read_bytes() for p in data.rglob('*.db')}
    print('PASS: bounded metadata-only directory scan, numeric/non-numeric/legacy/empty layouts, directory errors/timeouts, config path allowlist, redacted report, source database bytes unchanged.')


if __name__=='__main__':main()
