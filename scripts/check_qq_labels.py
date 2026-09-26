"""QQ directory compatibility and label-only repair; isolated SQLite fixtures."""
import argparse
from contextlib import closing,redirect_stdout,nullcontext
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch


def check(root):
    sys.path[:0]=[str(root),str(root/'scripts')]
    from qq_labels import directory,conversation_labels,remember_peer
    from chatlocal.store import Store
    from chatlocal.qq_labels import apply_labels
    from chatlocal.live import LiveManager
    import live_reader,export_qq
    with tempfile.TemporaryDirectory(dir=root/'.tmp',prefix='qq-labels-') as tmp:
        folder=Path(tmp)
        for filename,ddl,rows in (
            ('profile_info.db','CREATE TABLE profile_info_v5 ("1002" INTEGER,"20002" TEXT,"20009" TEXT)',
             [(200,'好友昵称','好友备注'),(201,'仅昵称',''),(202,'','只有备注'),(203,'','')]),
            ('group_info.db','CREATE TABLE group_list ("60001" INTEGER,"60007" TEXT)',[(300,'\x11English\x10 Group')])):
            with closing(sqlite3.connect(folder/filename)) as db:
                db.execute(ddl);table='profile_info_v5' if filename.startswith('profile') else 'group_list'
                db.executemany(f'INSERT INTO {table} VALUES({",".join("?" for _ in rows[0])})',rows);db.commit()
        hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.glob('*.db')}
        opened=[]
        def open_db(name):
            db=sqlite3.connect(f'file:{folder/name}?mode=ro',uri=True);opened.append(db);return db
        try:
            buddy,groups,status=directory(open_db)
            assert buddy=={200:'备注：好友备注 · 昵称：好友昵称',201:'昵称：仅昵称',202:'备注：只有备注'}
            assert groups=={300:'English Group'} and status['buddy']['status']=='ok'
            remember_peer(buddy,204,999,'这是本人');assert 204 not in buddy
            remember_peer(buddy,204,204,'对方原始昵称');assert buddy[204]=='对方原始昵称'
            remember_peer(buddy,200,200,'较旧消息昵称');assert buddy[200].startswith('备注：')
        finally:
            for db in opened:db.close()
        store=Store(folder/'chats.sqlite3');batch=folder/'batch.json'
        def row(cid,label,source='1'):
            return dict(platform='qq',conversation_id=cid,conversation=label,sender='本人',sender_id='100',
                timestamp='2026-09-24 12:00:00',content='original content',source_id=source,is_self=True)
        rows=[row('100:direct:200','qq_friend_200'),row('100:group:300','qq_group_300'),
              row('100:direct:201','My custom label'),row('999:direct:200','qq_friend_200')]
        def put(messages,**kw):
            batch.write_text(json.dumps(dict(messages=messages,**kw),ensure_ascii=False),encoding='utf-8')
            return store.import_file(batch)
        assert put(rows)['imported']==4
        with store.connect() as db:
            before=[tuple(r) for r in db.execute('SELECT id,dedup_key,content,source_id,timestamp FROM messages ORDER BY id')]
            checkpoints=[tuple(r) for r in db.execute('SELECT platform,checkpoint FROM live_state')]
        # Duplicate native message enriches the entire old conversation.
        assert put([dict(rows[0],conversation=buddy[200])])['labels_updated']==1
        # Zero new messages still repairs group labels. No duplicate history.
        result=put([],label_account='100',conversation_labels=conversation_labels('100',buddy,groups))
        assert result['imported']==0 and result['labels_updated']==1
        with store.connect() as db:
            assert [tuple(r) for r in db.execute('SELECT id,dedup_key,content,source_id,timestamp FROM messages ORDER BY id')]==before
            labels=dict(db.execute('SELECT conversation_id,conversation FROM messages'))
            assert labels['999:direct:200']=='qq_friend_200' and labels['100:direct:201']=='My custom label'
            assert labels['100:group:300']=='English Group'
            high=db.execute('SELECT max(seq) FROM local_changes').fetchone()[0]
        assert put([],label_account='100',conversation_labels=conversation_labels('100',buddy,groups))['labels_updated']==0
        with store.connect() as db:
            assert db.execute('SELECT max(seq) FROM local_changes').fetchone()[0]==high
            assert [tuple(r) for r in db.execute('SELECT platform,checkpoint FROM live_state')]==checkpoints
            assert apply_labels(db,'100',[dict(conversation_id='999:direct:200',conversation='wrong account')])==0
        # Actual live loop must apply directory repairs even on a zero-message
        # pass, with the checkpoint in the same transaction.
        with store.connect() as db:db.execute("UPDATE messages SET conversation='qq_group_300' WHERE conversation_id='100:group:300'")
        class OneTick:
            def __init__(self):self.calls=0
            def wait(self,_):self.calls+=1;return self.calls>1
            def is_set(self):return False
        manager=LiveManager(store);manager.stop_event=OneTick()
        response=dict(ok=True,payload=dict(messages=[],label_account='100',conversation_labels=conversation_labels('100',buddy,groups)),
            checkpoint=dict(account='100',cursors={'tail':[1,2]}),observed_at=time.time(),metrics={})
        with patch('chatlocal.live.wake_signature',return_value=('100',('stamp',))),\
             patch('chatlocal.live.message_commit_lock',side_effect=nullcontext),\
             patch.object(manager,'client_running',return_value=True),\
             patch.object(manager,'worker',return_value=SimpleNamespace(read=lambda r:response)),patch.object(manager,'log'):
            manager.run('qq')
        assert manager.states['qq']['status']=='listening',manager.states
        with store.connect() as db:
            assert db.execute("SELECT conversation FROM messages WHERE conversation_id='100:group:300'").fetchone()[0]=='English Group'
            assert json.loads(db.execute("SELECT checkpoint FROM live_state WHERE platform='qq'").fetchone()[0])['cursors']=={'tail':[1,2]}
        # Live directory path must use current client metadata, not a possibly
        # deleted decrypted cache. No actual client is touched by this fixture.
        reader=live_reader.Reader('qq');opened=[]
        with patch('chatlocal.config.DB',folder/'chats.sqlite3'),patch.object(reader,'open',side_effect=lambda path,key:open_db(path.name)):
            try:reader.labels('100',folder,b'fixture')
            finally:
                for db in opened:db.close()
        assert reader.buddy[200]==buddy[200] and reader.names[300]=='English Group'
        assert '999:direct:200' not in reader.label_conversations
        # A missing expected column must not turn SQLite's double-quoted
        # string fallback into a fake name or identity.
        with closing(sqlite3.connect(':memory:')) as malformed:
            malformed.execute('CREATE TABLE profile_info_v5 (other TEXT)')
            b,g,s=directory(lambda name:malformed)
            assert not b and not g and s['buddy']['status']=='schema_unavailable'
        # Metadata-only cold refresh skips the large message decryption but
        # still returns name repairs even when its message fingerprint matches.
        q=export_qq.q;output=folder/'export.json';request=folder/'request.json'
        previous=dict(method='native-id-inventory-v1',account='100',cursors={'x':1},fingerprint={})
        request.write_text(json.dumps(dict(checkpoint=previous)))
        args=SimpleNamespace(incremental_request=str(request),account=None,refresh=True,output=str(output))
        meta=dict(account='100',source=str(folder),fingerprint={})
        stub=SimpleNamespace(is_available=lambda:True,key=b'fixture',db_path=folder/'nt_msg.db')
        def decrypt(q,path,key,dest):
            assert path.name!='nt_msg.db','unchanged main DB must not decrypt'
            return q._QQDecryptedDatabase(path,False)
        with patch('chatlocal.config.DB',folder/'chats.sqlite3'),patch.object(export_qq,'DATA',folder/'data'),patch.object(export_qq,'snapshot',return_value=meta),\
             patch.object(q,'QQDBReader',return_value=stub),patch('incremental_common.message_files_unchanged',return_value=True),\
             patch('qq_cached_reader.decrypt_database',side_effect=decrypt),redirect_stdout(io.StringIO()):
            export_qq.export(args)
        data=json.loads(output.read_text('utf-8'));assert data['unchanged'] and not data['messages']
        assert data['label_status']['buddy']['status']=='ok' and data['conversation_labels']
        assert all(hashlib.sha256((folder/name).read_bytes()).hexdigest()==digest for name,digest in hashes.items())
    print('PASS QQ labels: older profile schema, remarks/nicknames/groups, direct peer fallback, duplicate and zero-message recovery, live transaction, unchanged cold refresh, account isolation, real-name preservation, idempotence and read-only source. Isolated fixtures.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--package',type=Path,default=Path(__file__).resolve().parents[1]);a=p.parse_args()
    (a.package/'.tmp').mkdir(exist_ok=True)
    check(a.package.resolve())
