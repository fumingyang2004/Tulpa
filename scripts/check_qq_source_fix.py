"""Custom-source repair regression. Source/decoded messages are synthetic.

Uses a copy of source modules by default, in a synthetic 0.5.1 installation
envelope. --package can check an actual archived 0.5.1 module set. Account
discovery and batch importer/SQLite are real; QQ messages/decryption are fixtures.
Never modifies the running release or reads user chats.
"""
import argparse
import ast
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from fix_qq_source import find_sources, select_source, existing_selection, atomic_json, install_patch, validate_install
from reader_patches import patch_qq_configured_root

SEED = '''
import json, sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path[:0]=[str(root),str(root/'scripts')]
from chatlocal.store import Store
from chatlocal.import_scope import save_scope
s=Store()
p=root/'imports/seed.json'
p.write_text(json.dumps([dict(platform='wechat',conversation_id='fixture-wx',conversation='fixture',sender='fixture',sender_id='fixture',timestamp=1780000000,content='retained fixture',source_id='fixture-wx-1')]),'utf-8')
s.import_file(p)
save_scope(s,{'qq':dict(enabled=False,conversations=None),'wechat':dict(enabled=True,conversations=['fixture-wx'],account='fixture-wx-account')})
'''

EXPORT = '''
import argparse,json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts'),str(ROOT/'tools/qq-reader')]
from chatlog_keeper.qq_db import find_qq_data_root,find_qq_account_databases
from chatlocal.reader_batches import BatchWriter,atomic_json
p=argparse.ArgumentParser()
p.add_argument('--account');p.add_argument('--output',type=Path);p.add_argument('--batch-dir',type=Path)
p.add_argument('--refresh',action='store_true');p.add_argument('--all-messages',action='store_true')
p.add_argument('--date-range',action='store_true');p.add_argument('--no-stickers',action='store_true');p.add_argument('--per-chat')
a=p.parse_args();assert a.all_messages and a.date_range and a.refresh
source=find_qq_account_databases(find_qq_data_root())[a.account]
meta=dict(account=a.account,source=str(source.parent),fingerprint={},snapshot_at=time.time())
atomic_json(ROOT/'data/qq-snapshot-info.json',meta)
os.environ['CHATWEAVE_BATCH_DIR']=str(a.batch_dir)
w=BatchWriter(a.batch_dir,dict(reader='chatlog-keeper',snapshot=meta))
for n in range(1201):
    w.observe()
    w.append(dict(platform='qq',conversation_id=a.account+':group:90002002',conversation='fixture',sender='fixture',sender_id='90003003',timestamp=1780000000+n,content='fixture row '+str(n),source_id=str(n),is_self=False))
    if (ROOT/'simulate-interruption').exists() and n==800:
        w.flush()
        raise ValueError('synthetic read interruption')
atomic_json(a.output,w.finish(dict(reader='chatlog-keeper',snapshot=meta,all_messages=True)))
'''


def invoke(python, code, *args, success=True):
    command = [str(python), '-X', 'utf8', '-B', *code, *map(str, args)]
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if success and result.returncode:
        raise AssertionError(result.stdout+'\n'+result.stderr)
    return result


def scalar(root, query):
    with closing(sqlite3.connect(root/'data/chats.sqlite3')) as db:
        return db.execute(query).fetchone()[0]


def expect_error(action):
    try:
        action()
    except (ValueError, OSError):
        return
    raise AssertionError('Expected a bounded refusal')


def check(package, python):
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp', prefix='qq-path-修复-test-') as tmp:
        base=Path(tmp); target=base/'Tulpa'; target.mkdir()
        for name in ('chatlocal','scripts','tools/qq-reader'):
            shutil.copytree(package/name,target/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        shutil.copy2(package/'tools/reader-manifest.json',target/'tools/reader-manifest.json')
        if (package/'build-manifest.json').is_file():
            shutil.copy2(package/'build-manifest.json',target/'build-manifest.json')
        else:
            # CI has source + pinned readers, not a private installed release.
            # The legacy repair still only accepts 0.5.1; production validation
            # is not relaxed just to make this fixture run.
            (target/'build-manifest.json').write_text(json.dumps(dict(
                product='Tulpa',version='0.5.1',edition='mcp',
                files=['tools/qq-reader/chatlog_keeper/qq_db.py'],file_sha256={})), 'utf-8')
        (target/'runtime').mkdir();(target/'runtime/python.exe').write_bytes(b'fixture marker; never executed')
        (target/'Tulpa.exe').write_bytes(b'fixture marker; never executed')
        (target/'private-sentinel').write_text('untouched fixture')
        (target/'.env').write_text('MODEL=fixture-model\nAPI_KEY=fixture-secret\n')
        (target/'data').mkdir();(target/'data/mcp-access.sqlite3').write_bytes(b'untouched grant sentinel')
        (target/'data/desktop-preferences.json').write_text('{"background":true}')
        (target/'qq_path_fix').mkdir()
        shutil.copy2(ROOT/'scripts/fix_qq_source.py',target/'qq_path_fix/fix.py')
        shutil.copy2(ROOT/'scripts/reader_patches.py',target/'qq_path_fix/reader_patches.py')
        shutil.copy2(ROOT/'scripts/qq_passive_keys.py',target/'qq_path_fix/qq_passive_keys.py')
        source=base/'聊天缓存'/ 'custom level';db=source/'90001001/nt_qq/nt_db/nt_msg.db'
        db.parent.mkdir(parents=True);db.write_bytes(b'synthetic encrypted source marker')
        source_hash=hashlib.sha256(db.read_bytes()).hexdigest()
        roots=find_sources(base/'聊天缓存')
        assert len(roots)==1 and roots[0][0]==source
        assert select_source(roots)[1]=='90001001'
        assert find_sources(source/'90001001')[0][0]==source
        other=base/'other/90001004/nt_db/nt_msg.db'
        other.parent.mkdir(parents=True);other.write_bytes(b'another fixture')
        both=roots+find_sources(base/'other')
        assert select_source(both,bound='90001001')[1]=='90001001'
        assert select_source(both,ask=lambda _: '1')[1]=='90001004'
        expect_error(lambda:select_source(both,bound='90001001',requested='90001004'))
        expect_error(lambda:select_source(both,bound='999'))
        expect_error(lambda:find_sources(base/'not-present'))
        (base/'empty').mkdir();expect_error(lambda:find_sources(base/'empty'))
        original=(target/'tools/qq-reader/chatlog_keeper/qq_db.py').read_bytes()
        patched=patch_qq_configured_root(original)
        assert patch_qq_configured_root(patched)==patched
        assert b'Tulpa explicit QQ source v1' in patched
        compile(patched,'qq_db.py','exec')
        before=ast.parse(original);after=ast.parse(patched)
        def unaffected(tree):
            return [ast.dump(n) for n in tree.body if not isinstance(n,ast.FunctionDef) or n.name!='find_qq_data_root']
        assert unaffected(before)==unaffected(after),'Only directory lookup may change'
        compile(patch_qq_configured_root(original.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')),'qq_db.py','exec')
        expect_error(lambda:patch_qq_configured_root(b'changed upstream'))
        seed=base/'seed.py';seed.write_text(SEED,'utf-8')
        invoke(python,[str(seed)],target)
        assert scalar(target,"SELECT count(*) FROM messages WHERE platform='wechat'")==1
        wx_scope=json.loads(scalar(target,"SELECT value FROM client_settings WHERE name='import_scope'"))['wechat']
        untouched={name:hashlib.sha256((target/name).read_bytes()).hexdigest() for name in (
            '.env','data/mcp-access.sqlite3','data/desktop-preferences.json','private-sentinel')}
        # Deliberately make the installed detector fail, exercising atomic patch rollback.
        catalog=target/'scripts/list_client_accounts.py';real_catalog=catalog.read_bytes()
        catalog.write_text('raise RuntimeError("fixture")','utf-8')
        folder=base/'rollback';folder.mkdir()
        expect_error(lambda:install_patch(target,source,'90001001',folder))
        assert (target/'tools/qq-reader/chatlog_keeper/qq_db.py').read_bytes()==original
        assert not (target/'data/qq-source-root.json').exists()
        catalog.write_bytes(real_catalog)
        # Replace only QQ decryption/producer with synthetic rows. Real scopes,
        # account verification, subprocesses, batch import and SQLite remain.
        (target/'scripts/export_qq.py').write_text(EXPORT,'utf-8')
        cmd=[str(target/'qq_path_fix/fix.py'),'--root',str(target),'--source',str(base/'聊天缓存'),'--no-stickers','--no-launch']
        first=invoke(python,cmd)
        assert '[5/5]' in first.stdout, first.stdout
        assert scalar(target,"SELECT count(*) FROM messages WHERE platform='qq'")==1201
        assert scalar(target,"SELECT count(*) FROM messages WHERE platform='wechat'")==1
        assert json.loads(scalar(target,"SELECT value FROM client_settings WHERE name='import_scope'"))['wechat']==wx_scope
        assert existing_selection(target)[0]=='90001001'
        assert not (target/'.tmp/upgrade.lock').exists()
        # Persisted setting, no shell variable or special launcher required.
        catalog_result=invoke(python,[str(catalog),'qq'])
        assert json.loads(catalog_result.stdout)==['90001001'],catalog_result.stdout
        invoke(python,cmd)
        assert scalar(target,"SELECT count(*) FROM messages WHERE platform='qq'")==1201
        mismatch=invoke(python,cmd+['--account','90001004'],success=False)
        assert mismatch.returncode==1
        # Partial producer failure must retain already committed rows and be retryable.
        (target/'simulate-interruption').touch()
        failed=invoke(python,cmd,success=False)
        assert failed.returncode==2,failed.stdout+'\n'+failed.stderr
        assert not (target/'.tmp/upgrade.lock').exists()
        assert scalar(target,"SELECT count(*) FROM messages WHERE platform='qq'")==1201
        (target/'simulate-interruption').unlink()
        invoke(python,cmd)
        assert scalar(target,"SELECT count(*) FROM messages WHERE platform='qq'")==1201
        assert scalar(target,"SELECT count(*) FROM import_jobs WHERE status!='completed'")==0
        # Explicit invalid setting must not fall back to this machine's real account.
        setting=target/'data/qq-source-root.json';saved=setting.read_bytes()
        atomic_json(setting,dict(format='tulpa-qq-source-v1',path=str(base/'missing')))
        assert invoke(python,[str(catalog),'qq'],success=False).returncode!=0
        setting.write_bytes(saved)
        # Existing app/upgrade marker rejects work before touching source settings.
        lock=target/'.tmp/upgrade.lock';lock.touch()
        locked=invoke(python,cmd,success=False)
        assert locked.returncode==1 and setting.read_bytes()==saved
        lock.unlink()
        # An actual child process at this install's live-reader entry point
        # must block patching, without killing that process or changing data.
        sleeper=target/'scripts/live_reader.py'
        sleeper.write_text('from pathlib import Path\nimport time\nPath(__file__).with_suffix(".ready").touch()\ntime.sleep(90)\n','utf-8')
        running=subprocess.Popen([str(python),str(sleeper)],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            for _ in range(50):
                if sleeper.with_suffix('.ready').exists():break
                time.sleep(.1)
            assert sleeper.with_suffix('.ready').exists()
            blocked=invoke(python,cmd,success=False)
            assert blocked.returncode==1 and '仍在运行' in blocked.stdout,blocked.stdout
            assert running.poll() is None and setting.read_bytes()==saved
        finally:
            running.terminate();running.wait(timeout=10)
        for name, value in untouched.items():
            assert hashlib.sha256((target/name).read_bytes()).hexdigest()==value,name
        assert hashlib.sha256(db.read_bytes()).hexdigest()==source_hash
        backups=list((target/'reports/private/qq-source-fix').glob('*/userdata/chats.sqlite3'))
        assert backups
        with closing(sqlite3.connect(backups[0])) as db_copy:
            assert db_copy.execute('PRAGMA quick_check').fetchone()[0]=='ok'
            assert db_copy.execute("SELECT count(*) FROM messages WHERE platform='wechat'").fetchone()[0]==1
        print(json.dumps(dict(passed=True,qq_rows=1201,real_import_pipeline=True,client_decryption='synthetic fixture',
            checks=['custom Unicode path','account ambiguity and binding','persistent process restart','AST scope',
                    'patch rollback','WeChat/settings/grants preservation','SQLite backup','idempotent import',
                    'partial failure and retry','explicit invalid path fails closed','upgrade lock','source unchanged']),ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--package',type=Path,default=ROOT)
    p.add_argument('--python',type=Path,default=Path(sys.executable));args=p.parse_args()
    check(args.package.resolve(),args.python.resolve())
