"""Critical live consistency checks in an isolated store; no client/model calls."""
import json
import sys
import tempfile
import time
import hashlib
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from chatlocal.store import Store
from chatlocal.data_delete import preview,purge
from chatlocal.live import LiveManager,LIVE_SCOPE_KEY,resume_checkpoint
from chatlocal.import_scope import save_scope,get_scope
from chatlocal.normalize import normalize


def independent_import_scope(root):
    store=Store(root/'scope.sqlite3')
    only_qq=dict(qq=dict(enabled=True,conversations=['one-qq-chat']),
                 wechat=dict(enabled=False,conversations=None))
    only_wechat=dict(qq=dict(enabled=False,conversations=None),
                     wechat=dict(enabled=True,conversations=['one-wechat-chat']))
    # Preserve actually read WeChat cursors; legacy excluded-table markers do
    # not prove those conversations were read. QQ keeps its per-peer progress.
    key=lambda cid:'message/message_0.db/Msg_'+hashlib.md5(cid.encode()).hexdigest()
    old=dict(account='test',scope=json.dumps(dict(enabled=True,conversations=['A'])),
             cursors={key('A'):dict(last=10,signature='a'),key('B'):dict(last=20,signature='b')})
    resumed=resume_checkpoint('wechat',old,{},'test')
    assert set(resumed['cursors'])=={key('A')} and len(old['cursors'])==2
    assert resume_checkpoint('qq',old,{},'test')['cursors']==old['cursors']
    assert resume_checkpoint('wechat',{},dict(account='other'), 'test')=={}
    assert resume_checkpoint('qq',old,{},'other')['account']=='test' # mismatch remains visible
    class OneTick:
        def __init__(self):self.calls=0
        def wait(self,_):self.calls+=1;return self.calls>1
        def is_set(self):return False
    for platform in ('wechat','qq'):
        # Each platform is disabled in the import form yet must keep listening.
        initial,changed=(only_qq,only_wechat) if platform=='wechat' else (only_wechat,only_qq)
        save_scope(store,initial)
        manager=LiveManager(store);manager.stop_event=OneTick();seen=[]
        def read(request):
            seen.append(request);save_scope(store,changed)
            return dict(ok=True,payload=dict(messages=[]),checkpoint=dict(account='test',cursors={'tail':[10,20]}),
                        observed_at=time.time(),metrics={})
        with patch('chatlocal.live.wake_signature',return_value=('test',('unchanged',))), \
             patch('chatlocal.live.message_commit_lock',side_effect=nullcontext), \
             patch.object(manager,'client_running',return_value=True), \
             patch.object(manager,'worker',return_value=SimpleNamespace(read=read)),patch.object(manager,'log'):
            manager.run(platform)
        assert len(seen)==1 and seen[0]['conversations'] is None
        assert manager.states[platform]['status']=='listening' and get_scope(store)==changed
        with store.connect() as db:
            checkpoint=json.loads(db.execute('SELECT checkpoint FROM live_state WHERE platform=?',(platform,)).fetchone()[0])
        assert checkpoint['cursors']=={'tail':[10,20]} and checkpoint['scope']==LIVE_SCOPE_KEY
        # Explicitly disabling LIVE while a batch runs must still cancel commit.
        manager.stop_event=OneTick()
        def disable(request):
            manager.configure({platform:'off'})
            return dict(ok=True,payload=dict(messages=[]),checkpoint=dict(account='test',cursors={'tail':[99,99]}),
                        observed_at=time.time(),metrics={})
        with patch('chatlocal.live.wake_signature',return_value=('test',('changed',))), \
             patch('chatlocal.live.message_commit_lock',side_effect=nullcontext), \
             patch.object(manager,'client_running',return_value=True), \
             patch.object(manager,'worker',return_value=SimpleNamespace(read=disable)),patch.object(manager,'log'):
            manager.run(platform)
        with store.connect() as db:
            assert json.loads(db.execute('SELECT checkpoint FROM live_state WHERE platform=?',(platform,)).fetchone()[0])==checkpoint
        manager.stop_event=OneTick()
        with patch.object(manager,'worker') as worker,patch.object(manager,'reset_worker'):
            manager.run(platform);worker.assert_not_called()
        assert manager.states[platform]['status']=='off'


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='live-check-') as temp:
        root=Path(temp);independent_import_scope(root)
        store=Store(root/'messages.sqlite3');file=root/'batch.json'
        row=dict(platform='qq',conversation_id='test:direct:1',conversation='测试',sender_id='self',sender='本人',
            timestamp='2026-09-22 17:00:00',content='测试确认',is_self=True,source_id='100')
        def put(rows,**kw):file.write_text(json.dumps(dict(messages=rows)),encoding='utf-8');return store.import_file(file,**kw)
        cp=dict(account='test',cursors={'test':[1,100]})
        transition=dict(platform='qq',previous={},next=cp,observed_at=time.time())
        assert put([row],live=transition)['imported']==1
        with store.connect() as db:
            mid=db.execute('SELECT id FROM messages').fetchone()[0]
            assert db.execute('SELECT verification_state FROM live_events').fetchone()[0]=='database_verified'
        # Same row through both hot and cold paths has one canonical message.
        assert put([row],live=dict(transition,previous=cp))['duplicate']==1
        assert put([row],sync=dict(platform='qq',previous={},next={'account':'test'},detail='test'))['duplicate']==1
        # Real QQ outgoing rows can acquire a server timestamp after first read.
        ack=dict(row,timestamp='2026-09-22 17:00:01')
        assert put([ack],live=dict(transition,previous=cp))['duplicate']==1
        with store.connect() as db:assert db.execute('SELECT id FROM messages').fetchone()[0]==mid
        for invalid,trans in [(dict(row,source_id='new'),transition),(dict(ack,content='伪造修改'),dict(transition,previous=cp)),(dict(row,timestamp='2026-09-23 17:00'),dict(transition,previous=cp))]:
            try:put([invalid],live=trans)
            except ValueError:pass
            else:raise AssertionError('Invalid batch accepted')
        with store.connect() as db:
            assert db.execute('SELECT count(*) FROM messages').fetchone()[0]==1
            assert json.loads(db.execute("SELECT checkpoint FROM live_state WHERE platform='qq'").fetchone()[0])==cp
        # User deletion survives automatic overlap; explicit import can restore.
        body=dict(platform='qq',conversations=[],start='2026-09-22',end='2026-09-22')
        purge(store,body,preview(store,body)['fingerprint'])
        assert put([ack],live=dict(transition,previous=cp))['imported']==0
        assert put([ack])['imported']==1
        with store.connect() as db:assert db.execute('SELECT id FROM messages').fetchone()[0]>mid
        manager=LiveManager(store)
        manager.configure(dict(qq='off',wechat='database'))
        assert LiveManager(store).settings['qq']=='off'
        assert not hasattr(manager,'reconciliation_due') and 'reconcile_minutes' not in manager.settings
        assert manager.settings['load_stickers'] is False
        # Identity metadata must preserve the cold reader's group-prefix rule.
        head=dict(reader='wechatauto-replica',wxid='me',identity_method='per-shard',chats=[dict(username='g@chatroom',name='群')])
        raw=dict(chat='g@chatroom',sender_username='me',sender_name='本人',create_time=1790067000,server_id=20,type_code=1,content='me:\n正文')
        result=normalize(raw,head,'wechatauto','','','')
        assert result['sender_id']=='me' and result['is_self']==1 and result['content']=='正文'
        # A WeChat native local row acquires a server ID without duplicating its
        # canonical ID. Both raw exports remain independently traceable.
        head.update(messages=[dict(raw,server_id=0,local_id=7,source_db='message/message_0.db',source_table='Msg_'+'a'*32)])
        file.write_text(json.dumps(head),encoding='utf-8');store.import_file(file)
        with store.connect() as db:wxmid=db.execute("SELECT id FROM messages WHERE platform='wechat'").fetchone()[0]
        head['messages'][0]['server_id']=20
        file.write_text(json.dumps(head),encoding='utf-8');assert store.import_file(file)['duplicate']==1
        with store.connect() as db:
            assert list(map(tuple,db.execute("SELECT id,source_id FROM messages WHERE platform='wechat'")))==[(wxmid,'20')]
            assert db.execute('SELECT count(*) FROM provenance WHERE message_id=?',(wxmid,)).fetchone()[0]==2
        body.update(platform='wechat');purge(store,body,preview(store,body)['fingerprint'])
        wxtransition=dict(platform='wechat',previous={},next={'account':'test'},observed_at=time.time())
        assert store.import_file(file,live=wxtransition)['imported']==0
        # A zero->server ID acknowledgement is not a database reset. All other
        # identity changes still fail closed; replay the boundary row once.
        import sqlite3,hashlib
        from incremental_common import boundary
        db=sqlite3.connect(':memory:');db.execute('CREATE TABLE native(local_id INTEGER PRIMARY KEY,server_id INT,create_time INT)')
        db.execute('INSERT INTO native VALUES(7,0,123)');before=boundary(db,'native','local_id',['server_id','create_time'],None)
        db.execute('UPDATE native SET server_id=20')
        after=boundary(db,'native','local_id',['server_id','create_time'],before,allow_server_ack=True)
        assert after.pop('replay_from')==6
        db.execute('UPDATE native SET server_id=30')
        try:boundary(db,'native','local_id',['server_id','create_time'],after,allow_server_ack=True)
        except ValueError:pass
        else:raise AssertionError('Reused server identity accepted')
        db.close()
    print('PASS: import/live scope independence, legacy cursor migration, live-disable cancellation, atomic cursor rollback, hot/cold dedup, QQ/WeChat acknowledgements, native aliases, ID/content conflict rejection, deletion tombstones, settings persistence, WeChat identity. Synthetic only.')


if __name__=='__main__':main()
