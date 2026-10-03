"""Multi-account directory/API fixtures; no real client, key extraction or model calls."""
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from chatlocal.store import Store
from chatlocal.client_accounts import account_id,choose_account,resolve_account,accounts_view,bound_account
from chatlocal.import_scope import get_scope,save_scope


def fails(fn,contains=''):
    try:fn()
    except ValueError as exc:
        assert contains in str(exc),(contains,str(exc))
        return
    raise AssertionError('Expected rejection')


def directories(folder):
    import export_wechat as wx
    import export_qq as qq
    from list_client_accounts import discover
    for platform in ('qq','wechat'):
        source=folder/platform;source.mkdir()
        ids=['10001','10002'] if platform=='qq' else ['wxid_fixture_a','custom_fixture_b']
        for name in ids:
            leaf=source/name/('nt_qq/nt_db' if platform=='qq' else 'db_storage')
            leaf.mkdir(parents=True)
            (leaf/('nt_msg.db' if platform=='qq' else 'contact.db')).write_bytes(b'fixture')
        data=folder/(platform+'-out');data.mkdir()
        if platform=='qq':
            root_patch=patch.object(qq.q,'find_qq_data_root',return_value=source)
            export_module=qq;snapshot=qq.snapshot
        else:
            root_patch=patch('wechatauto.db.auto_detect_db_dir',return_value=str(source))
            export_module=wx;snapshot=wx.refresh_snapshot
        with root_patch:
            assert set(discover(platform))==set(ids)
        with patch.object(export_module,'DATA',data),patch('snapshot_cache.refresh_families') as copy,patch('incremental_common.fingerprint',return_value={}):
            context=patch.object(qq.q,'find_qq_data_root',return_value=source) if platform=='qq' else patch.object(wx,'auto_detect_db_dir',return_value=str(source))
            with context:
                fails(snapshot,'多个')
                fails(lambda:snapshot('missing'),'未找到')
                assert not copy.called,'Ambiguous/missing account must fail before creating snapshots'
                result=snapshot(ids[1])
                assert result['account']==ids[1]
                assert ids[1] in str(copy.call_args.args[0])
                copy.reset_mock()
                fails(lambda:snapshot('../outside'),'路径')
                assert not copy.called
        assert all((source/name/('nt_qq/nt_db/nt_msg.db' if platform=='qq' else 'db_storage/contact.db')).read_bytes()==b'fixture' for name in ids)


def api(folder):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from chatlocal.watch_routes import install_watch_routes
    from chatlocal.sync import sync_messages
    store=Store(folder/'api/chats.sqlite3');app=FastAPI();install_watch_routes(app,store)
    candidates={'qq':['10001','10002'],'wechat':['wxid_fixture_a','wxid_fixture_b']}
    calls=[]
    def reader(platform,args,output,**kwargs):
        account=args[args.index('--account')+1];calls.append((platform,account,list(args)))
        (store.path.parent/f'{platform}-snapshot-info.json').write_text(json.dumps(dict(account=account)),encoding='utf-8')
        payload={'conversations':[dict(platform=platform,conversation_id='fixture_chat',conversation='Fixture',count=1)]} if '--list' in args else [dict(platform=platform,conversation_id='fixture_chat',conversation='Fixture',sender='Fixture',timestamp='2026-10-01',content='synthetic account fixture',source_id='1')]
        output.write_text(json.dumps(payload),encoding='utf-8')
        return {}
    def wait(client,job):
        assert 'job_id' in job,job
        for _ in range(150):
            result=client.get('/api/watch-jobs/'+job['job_id']).json()
            if result['status']!='running':return result
            time.sleep(.02)
        raise AssertionError('Job timeout')
    with patch('chatlocal.client_accounts.discover_accounts',side_effect=lambda p:candidates[p]),patch('chatlocal.data_routes.run_reader',side_effect=reader),TestClient(app) as client:
        response=client.get('/api/data/accounts').json()
        assert response['wechat']['selected'] is None and len(response['wechat']['accounts'])==2
        assert client.get('/api/data/accounts',headers={'origin':'https://example.invalid'}).status_code==403
        missing=wait(client,client.post('/api/data/catalog',json=dict(platforms=['wechat'])).json())
        assert '多个' in missing['result']['summary'] and not calls
        assert client.post('/api/data/catalog',json=dict(platforms=['wechat'],accounts={'wechat':'../bad'})).status_code==400
        result=wait(client,client.post('/api/data/catalog',json=dict(platforms=['wechat'],accounts={'wechat':candidates['wechat'][1]})).json())
        assert result['status']=='completed' and calls[-1][1]=='wxid_fixture_b'
        body=dict(scope={p:dict(enabled=True,conversations=None,account=candidates[p][1]) for p in candidates},ranges={p:dict(start='',end='') for p in candidates},limits=dict(qq_per_chat=2,wechat_per_chat=2))
        result=wait(client,client.post('/api/data/read',json=body).json())
        assert result['status']=='completed',result
        assert [(p,a) for p,a,args in calls if '--list' not in args]==[('qq','10002'),('wechat','wxid_fixture_b')]
        assert get_scope(Store(store.path))==body['scope'],'Selection survives restart'
        with store.connect() as db:before=[tuple(r) for r in db.execute('SELECT * FROM sync_state')];message_count=db.execute('SELECT count(*) FROM messages').fetchone()[0]
        body['scope']['wechat']['account']='wxid_fixture_a';body['scope']['qq']['enabled']=False
        count=len(calls)
        body['load_stickers']=True
        with patch('chatlocal.media_refresh.refresh_for_import') as media:
            rejected=wait(client,client.post('/api/data/read',json=body).json())
            assert not media.called,'Rejected account must not refresh another account\'s media'
        assert rejected['status']=='error' and '独立' in rejected['error'] and len(calls)==count
        with store.connect() as db:
            assert before==[tuple(r) for r in db.execute('SELECT * FROM sync_state')]
            assert db.execute('SELECT count(*) FROM messages').fetchone()[0]==message_count
        assert get_scope(store)['wechat']['account']=='wxid_fixture_b'
        # Both legacy runner and production batch path carry the saved account.
        captured=[]
        def runner(command,**kwargs):
            captured.append(command)
            raise ValueError('fixture stop before reading')
        sync_messages(store,['wechat'],runner=runner)
        assert captured[0][captured[0].index('--account')+1]=='wxid_fixture_b'
        def batches(store,platform,args,output,**kwargs):
            assert args[args.index('--account')+1]=='wxid_fixture_b'
            assert kwargs['key']['account']=='wxid_fixture_b'
            captured.append(args)
            raise ValueError('fixture stop before reading')
        with patch('chatlocal.import_pipeline.run_batches',side_effect=batches):sync_messages(store,['wechat'])
        assert len(captured)==2
        candidates['wechat']=['wxid_fixture_a']
        fails(lambda:resolve_account(store,'wechat'),'不会自动')
        assert accounts_view(store)['wechat']['selected']=='wxid_fixture_b'
        # The source metadata protects live bootstrap; a missing metadata file
        # cannot make an existing sync cursor silently bind to a different owner.
        with store.connect() as db:db.execute("UPDATE sync_state SET checkpoint=? WHERE platform='wechat'",(json.dumps(dict(account='wxid_fixture_b')),))
        (store.path.parent/'wechat-snapshot-info.json').unlink()
        assert bound_account(store,'wechat')=='wxid_fixture_b'
        fails(lambda:resolve_account(store,'wechat','wxid_fixture_a'),'独立')
        # Single-account machines keep the automatic/default behavior.
        fresh=Store(folder/'single/chats.sqlite3')
        assert resolve_account(fresh,'wechat')=='wxid_fixture_a'
        candidates['wechat']=[]
        fails(lambda:resolve_account(fresh,'wechat'),'未找到')


if __name__=='__main__':
    for bad in ('','../bad','a/b','a\\b','C:bad','a\n',{},'..'):
        fails(lambda:account_id(bad))
    assert choose_account('wechat',['custom_account'])=='custom_account'
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='account-check-') as tmp:
        folder=Path(tmp);directories(folder);api(folder)
    print('PASS: actual directory discovery + exporter selection, multi/single/missing accounts, API persistence and parameter forwarding, sync paths, account/cursor isolation. Synthetic directories only; real multi-account computers untested.')
