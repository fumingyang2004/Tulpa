"""Scoped imports/deletion on isolated local fixtures. Never deletes personal data."""
import hashlib
import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from chatlocal.store import Store
from chatlocal.data_delete import preview,purge
from chatlocal.import_scope import bounds,get_scope,save_scope,watch_refresh_plan
from chatlocal.normalize import decode_export
from chatlocal.config import local_path
from chatlocal.watches import Watches

def fails(fn):
    try:fn()
    except ValueError:return
    raise AssertionError('Expected rejection')


def deletion_test(folder):
    store=Store(folder/'chats.sqlite3')
    def msg(n,chat='A',date='2026-09-01 12:00',platform='qq'):
        return dict(source_id=str(n),platform=platform,conversation_id=chat,conversation=chat,
            sender='甲',is_self=False,timestamp=date,content='唯一删除目标' if n==1 else f'保留内容 {n}')
    original=folder/'fixture.json'
    original.write_text(json.dumps([msg(1),msg(2,'B'),msg(3,date='2026-09-02 00:00'),msg(4,platform='wechat')]),encoding='utf-8')
    store.import_file(original)
    media=folder/'media';media.mkdir()
    shared=media/'shared.png';shared.write_bytes(b'shared fixture')
    owned=media/'owned.png';owned.write_bytes(b'owned fixture')
    entries=lambda paths:json.dumps([dict(kind='image',status='available',local_path=str(p.relative_to(ROOT))) for p in paths])
    with store.connect() as db:
        db.execute('UPDATE messages SET media_json=? WHERE id=1',(entries([shared,owned]),))
        db.execute('UPDATE messages SET media_json=? WHERE id=2',(entries([shared]),))
        checkpoint_before=[tuple(r) for r in db.execute('SELECT * FROM sync_state')]
    card=Watches(store).create('测试','测试',['qq'],[json.dumps(['qq','A'])])
    scope=dict(platform='qq',conversations=['A'],start='2026-09-01',end='2026-09-01')
    expected=preview(store,scope);assert expected['messages']==1 and expected['media']==2
    fails(lambda:purge(store,scope,'stale'))
    result=purge(store,scope,expected['fingerprint']);assert result['deleted']==1
    assert store.message(1) is None and store.message(2) and store.message(3) and store.message(4)
    assert shared.exists() and not owned.exists()
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM message_fts WHERE message_fts MATCH '删除'").fetchone()[0]==0
        assert [tuple(r) for r in db.execute('SELECT * FROM sync_state')]==checkpoint_before
        sources=[dict(r) for r in db.execute('SELECT * FROM sources')]
        assert len(sources)==1
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert db.execute('SELECT count(*) FROM provenance').fetchone()[0]==3
    archived=local_path(sources[0]['archive']);raw=archived.read_bytes()
    assert hashlib.sha256(raw).hexdigest()==sources[0]['hash'] and b'_chatlocal_deleted' in raw
    assert '唯一删除目标' not in raw.decode('utf-8')
    assert archived.exists() and len(list((folder/'sources').iterdir()))==1
    # Surviving pointers keep their original indices; scrubbed source reimport
    # cannot recreate the removed message.
    result=store.import_file(archived);assert result['imported']==0 and result['duplicate']==3
    assert original.exists() and json.loads(original.read_text('utf-8'))[0]['content']=='唯一删除目标'
    for p,chat in [('qq','B'),('qq','A'),('wechat','A')]:
        body=dict(scope,platform=p,conversations=[chat],end='2026-09-03')
        purge(store,body,preview(store,body)['fingerprint'])
    assert not shared.exists() and not store.stats()
    new=folder/'new.jsonl';new.write_text(json.dumps(msg(5))+'\n'+json.dumps(msg(6,'B')),encoding='utf-8')
    store.import_file(new)
    with store.connect() as db:assert db.execute('SELECT min(id) FROM messages').fetchone()[0]==5
    purge(store,scope,preview(store,scope)['fingerprint'])
    with store.connect() as db:
        source=db.execute("SELECT * FROM sources WHERE filename='new.jsonl'").fetchone()
        pointer=db.execute('SELECT pointer FROM provenance').fetchone()[0]
    assert pointer=='line:2' and local_path(source['archive']).read_text('utf-8').startswith('\n')
    assert store.message(6)
    # Empty conversation selection spans the platform, but dates stay required.
    all_preview=preview(store,dict(scope,conversations=[]))
    assert all_preview['all_conversations'] and all_preview['messages']==1
    fails(lambda:preview(store,dict(scope,conversations=None)))
    fails(lambda:preview(store,dict(scope,start='')))
    fails(lambda:preview(store,dict(scope,start='2026-09-03')))
    assert Watches(store).get(card['id'])
    scope_config={'qq':dict(enabled=True,conversations=['A']),'wechat':dict(enabled=False,conversations=None)}
    save_scope(store,scope_config);assert get_scope(store)==scope_config
    assert watch_refresh_plan(store,['qq'],[json.dumps(['qq','A'])])[0]['coverage']=='full'
    assert watch_refresh_plan(store,['qq'],[])[0]['coverage']=='partial'
    assert watch_refresh_plan(store,['wechat'],[])[0]['coverage']=='none'
    assert not watch_refresh_plan(store,['qq'],[json.dumps(['qq','B'])])[0]['can_refresh']


def wechat_range_test(folder):
    import export_wechat  # Installs the existing upstream reader path.
    from scoped_wechat import export_selected
    user='test_friend';table='Msg_'+hashlib.md5(user.encode()).hexdigest()
    start,end=bounds('2026-06-01','2026-06-01');start=int(start/1000);end=int(end/1000)
    paths=[]
    for shard in range(2):
        path=folder/f'shard{shard}.sqlite3';paths.append(path)
        with closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE Name2Id(user_name TEXT)');db.execute("INSERT INTO Name2Id VALUES('test_sender')")
            db.execute(f'CREATE TABLE {table}(local_id INTEGER,local_type INT,server_id INT,real_sender_id INT,create_time INT,message_content TEXT,packed_info_data BLOB,sort_seq INT)')
            for n,t in enumerate([start-1,start+shard,start+10+shard,end,end+1]):
                db.execute(f'INSERT INTO {table} VALUES(?,1,?,1,?,?,NULL,?)',(n,shard*100+n,t,'文本',t))
            db.commit()
    class Reader:
        wxid='self'
        def _message_dbs(self):return [str(p) for p in paths]
        def _nickname_index(self):return {'test_sender':'甲'}
        def _export_row(self,row,names):return dict(content=row['message_content'],type_code=1,local_id=row['local_id'],create_time=row['create_time'])
    def open_db(reader,path):
        db=sqlite3.connect(path);db.row_factory=sqlite3.Row;return db
    media=SimpleNamespace(parse=lambda message:(message['content'],[],None))
    output=folder/'scoped.json'
    with patch('scoped_wechat.checked_open',side_effect=open_db),patch('reader_media.WeChatMedia',return_value=media):
        result=export_selected(Reader(),[dict(username=user,name='好友',message_count=10)],dict(snapshot_at='test'),output,2,{},start='2026-06-01',end='2026-06-01')
    payload=json.loads(output.read_text('utf-8'))
    assert [m['create_time'] for m in payload['messages']]==[start+10,start+11]
    assert result['messages']==2 and payload['chats'][0]['range_message_count']==4
    assert payload['coverage']['limited_chats']==1 and all(m['chat']==user for m in payload['messages'])


def api_test(folder):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from chatlocal.watch_routes import install_watch_routes
    import time
    store=Store(folder/'api.sqlite3');app=FastAPI();install_watch_routes(app,store)
    commands=[]
    def reader(platform,args,output,**kwargs):
        commands.append((platform,args))
        output.write_text(json.dumps([dict(platform=platform,conversation_id='only',conversation='only',sender='甲',timestamp='2026-06-01',content='范围导入',source_id='1')]),encoding='utf-8')
    scope={'qq':dict(enabled=False,conversations=None),'wechat':dict(enabled=True,conversations=['only'])}
    body=dict(scope=scope,limits=dict(qq_days=30,qq_per_chat=1,wechat_per_chat=1),start='2026-06-01',end='2026-06-01')
    with TestClient(app) as client,patch('chatlocal.data_routes.run_reader',side_effect=reader):
        assert client.post('/api/data/read',json=dict(body,start='bad')).status_code==400
        job=client.post('/api/data/read',json=body).json()
        def finish(job):
            for _ in range(100):
                result=client.get('/api/watch-jobs/'+job['job_id']).json()
                if result['status']!='running':assert result['status']=='completed',result;return result
                time.sleep(.02)
            raise AssertionError('Job timeout')
        assert finish(job)['result']['platforms'][0]['added']==1
        assert len(commands)==1 and commands[0][0]=='wechat' and '--chat' in commands[0][1] and '--start' in commands[0][1]
        assert client.get('/api/data/settings').json()['scope']==scope
        delete=dict(platform='wechat',conversations=['only'],start='2026-06-01',end='2026-06-01')
        before=client.post('/api/data/delete-preview',json=delete).json();assert before['messages']==1
        job=client.post('/api/data/delete',json={'token':before['token']}).json()
        assert finish(job)['result']['deleted']==1
        assert client.post('/api/data/delete',json={'token':before['token']}).status_code==409
        assert not store.stats()
        # Independent platform ranges must reach the right reader. The new UI
        # has no historical-day fallback, including when the QQ start is blank.
        commands.clear()
        separate=dict(scope={p:dict(enabled=True,conversations=['only']) for p in ('qq','wechat')},
            limits=dict(qq_per_chat=2,wechat_per_chat=3),ranges={
                'qq':dict(start='2026-07-01',end='2026-07-20'),
                'wechat':dict(start='2026-02-01',end='2026-02-23')})
        finish(client.post('/api/data/read',json=separate).json())
        assert len(commands)==2
        for platform,args in commands:
            assert args[args.index('--start')+1]==separate['ranges'][platform]['start']
            assert args[args.index('--end')+1]==separate['ranges'][platform]['end']
        assert '--date-range' in commands[0][1] and '--days' not in commands[0][1]
        invalid=dict(separate,ranges=dict(separate['ranges'],qq=dict(start='2026-09-01',end='2026-07-20')))
        assert client.post('/api/data/read',json=invalid).status_code==400
        assert len(commands)==2,'Invalid dates must not launch readers'
        separate['ranges']['qq']['start']='';commands.clear()
        finish(client.post('/api/data/read',json=separate).json())
        assert '--date-range' in commands[0][1] and '--start' not in commands[0][1] and '--days' not in commands[0][1]
        # Empty selection through the real route: both QQ conversations inside
        # the date window are removed; WeChat and next-day QQ records survive.
        extra=folder/'all-conversations.json'
        extra.write_text(json.dumps([dict(platform='qq',conversation_id=c,conversation=c,sender='甲',timestamp=t,content='保留或删除边界',source_id=c) for c,t in [('second','2026-06-01 23:59:59'),('outside','2026-06-02 00:00:00')]]),encoding='utf-8')
        store.import_file(extra)
        all_body=dict(platform='qq',conversations=[],start='2026-06-01',end='2026-06-01')
        all_preview=client.post('/api/data/delete-preview',json=all_body).json()
        assert all_preview['all_conversations'] and all_preview['messages']==2 and all_preview['conversations']==2
        assert finish(client.post('/api/data/delete',json={'token':all_preview['token']}).json())['result']['deleted']==2
        with store.connect() as db:
            remaining={(r['platform'],r['conversation_id']) for r in db.execute('SELECT platform,conversation_id FROM messages')}
        assert remaining=={('wechat','only'),('qq','outside')}


def qq_delta_scope_test(folder):
    from incremental_qq import delta_rows
    from array import array
    import zlib
    with closing(sqlite3.connect(':memory:')) as db:
        db.execute('CREATE TABLE qq("40001" INTEGER PRIMARY KEY,"40003" INT,"40050" INT,"40090" TEXT,"40033" INT,"40800" BLOB,"40021" TEXT,"40020" TEXT,"40011" INT,"40012" INT)')
        for uid,peer in [(100,'A'),(200,'B')]:db.execute("INSERT INTO qq VALUES(?,1,1000,'s',1,X'',?,'u',2,1)",(uid,peer))
        spec=SimpleNamespace(table='qq',conversation_column='40021',table_rank=1,conversation_type='group')
        request_path=folder/'request.json'
        rows,cursor=delta_rows(db,spec,dict(checkpoint={},bootstrap_since=0,conversations=['account:group:A']),request_path)
        assert [r[0] for r in rows]==[100] and cursor['count']==2
        # Scope filters bodies, not the complete source identity inventory.
        ids=array('q');ids.frombytes(zlib.decompress((folder/'qq.next.bin').read_bytes()));assert list(ids)==[100,200]
        request=dict(checkpoint=dict(method='native-id-inventory-v1',account='account',cursors={'qq':cursor}),inventory={'qq':str(folder/'qq.next.bin')},conversations=['account:group:A'])
        db.execute("INSERT INTO qq VALUES(50,2,900,'s',1,X'','A','u',2,1)")
        db.execute("INSERT INTO qq VALUES(60,2,900,'s',1,X'','B','u',2,1)")
        rows,next_cursor=delta_rows(db,spec,request,request_path)
        assert [r[0] for r in rows]==[50] and next_cursor['count']==4


if __name__=='__main__':
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='data-controls-') as tmp:
        folder=Path(tmp);deletion_test(folder);wechat_range_test(folder);api_test(folder);qq_delta_scope_test(folder)
    print('PASS: scoped dates before limits, source/media deletion, shared-file retention, monotonic IDs, scope guards, preview/confirm HTTP jobs')
