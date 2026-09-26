"""Real subprocess/batch/SQLite regression; QQ source/decryption are fixtures.

No client writes, no cloud. Exercises >50k native QQ rows, interruption/resume,
receipt atomicity, duplicate refresh and the bounded producer protocol.
"""
import argparse
from contextlib import ExitStack, closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]


def worker(args):
    from chatlocal.reader_batches import BatchWriter,atomic_json
    flags=args.reader
    folder=Path(flags[flags.index('--batch-dir')+1]);os.environ['CHATWEAVE_BATCH_DIR']=str(folder)
    output=Path(flags[flags.index('--output')+1])
    if args.mode.startswith('wechat'):
        import export_wechat
        from scoped_wechat import export_selected
        from incremental_wechat import export_delta
        from wechatauto.db import WeChatDB
        from chatlocal.store import Store
        class Reader(WeChatDB):
            wxid='fixture_owner'
            def __init__(self):pass
            def _message_dbs(self):return ['message/message_0.db','message/message_1.db']
            def _nickname_index(self):return {'test_sender':'Fixture sender','test_friend':'Fixture friend'}
            def _build_md5_index(self):return {hashlib.md5(b'test_friend').hexdigest():'test_friend'}
        def open_db(reader,rel):
            db=sqlite3.connect(args.fixture/'wx'/rel);db.row_factory=sqlite3.Row;return db
        meta=dict(account='fixture_owner',source_db=str(args.fixture/'wx'),snapshot_at='2026-09-25T00:00:00+00:00',fingerprint={})
        with patch('scoped_wechat.checked_open',side_effect=open_db),patch('incremental_wechat.checked_open',side_effect=open_db),\
             patch('reader_media.WeChatMedia',return_value=SimpleNamespace(parse=lambda m:(m['content'],[],None))),\
             patch('incremental_wechat.Store',return_value=Store(args.fixture/'messages.sqlite3')):
            if args.mode=='wechat_delta':
                payload=export_delta(Reader(),meta,dict(checkpoint={},bootstrap_since=0,conversations=['test_friend']),False,batch_dir=folder)
                atomic_json(output,payload)
            else:export_selected(Reader(),[dict(username='test_friend',name='Fixture friend',message_count=2402)],meta,output,None,{},False,batch_dir=folder)
        return
    if args.mode=='qq':
        import export_qq as ex
        data=args.fixture/'data';work=data/'qq-reader/decrypted'
        reader=SimpleNamespace(is_available=lambda:True,key=b'fixture',db_path=args.fixture/'client/nt_db/nt_msg.db')
        meta=dict(account='100',source=str(args.fixture/'client/nt_db'),snapshot_at=1780000100,fingerprint={})
        with ExitStack() as stack:
            for target,value in [('qq_cached_reader.decrypt_database',ex.q._QQDecryptedDatabase(work/'messages.db',False)),
                                 ('qq_labels.imported_ids',set())]:stack.enter_context(patch(target,return_value=value))
            stack.enter_context(patch.object(ex,'DATA',data));stack.enter_context(patch.object(ex,'snapshot',return_value=meta))
            stack.enter_context(patch.object(ex.q,'QQDBReader',return_value=reader))
            stack.enter_context(patch.object(ex.q,'_query_account_qq_number',return_value='100'))
            ex.export(SimpleNamespace(account=None,refresh=True,days=30,per_chat=500,date_range=True,start='',end='',
                chat=['100:group:200'],list=False,no_stickers=True,incremental_request=None,output=str(output),
                all_messages=True,batch_dir=str(folder)))
        return
    writer=BatchWriter(folder,{})
    for n in range(1201):
        writer.observe()
        writer.append(dict(platform='qq',conversation_id='100:direct:201',conversation='Fixture',sender='Fixture',sender_id='x',
            timestamp=1780000000+n,content='bounded fixture',source_id=str(n),is_self=False))
    if args.mode=='fail':
        writer.flush()
        raise ValueError('fixture reader stopped after committed batches')
    atomic_json(output,writer.finish({}))


def check(rows=50101):
    from chatlocal.store import Store
    from chatlocal.import_pipeline import run_batches,mark_complete,ImportInterrupted,totals,verify_complete
    from chatlocal.reader_batches import BatchWriter,MAX_FILE_BYTES,MAX_PENDING
    from check_qq_history import protobuf
    import export_qq
    popen=subprocess.Popen
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='batch-check-') as tmp:
        folder=Path(tmp);work=folder/'data/qq-reader/decrypted';work.mkdir(parents=True)
        import chatlocal.import_pipeline as pipeline
        pipeline.REPORTS=folder/'reports'
        source=work/'messages.db';N=rows
        with closing(sqlite3.connect(source)) as db:
            for spec in export_qq.q._QQ_MESSAGE_TABLE_SPECS:
                db.execute(f'CREATE TABLE "{spec.table}" ("40001" INTEGER,"40003" INTEGER,"40050" INTEGER,"40090" TEXT,"40033" INTEGER,"40800" BLOB,"{spec.conversation_column}" TEXT,"40020" TEXT,"40011" INTEGER,"40012" INTEGER)')
            db.executemany('INSERT INTO group_msg_table VALUES(?,?,?,?,?,?,?,?,?,?)',
                ((n,n,1780000000+n,'fixture',101,protobuf(45101,'native '+str(n)),'200','uid_fixture',2,1) for n in range(1,N+1)))
            db.commit()
        digest=hashlib.sha256(source.read_bytes()).hexdigest()
        store=Store(folder/'messages.sqlite3');out=folder/'final.json';events=[];mode=['qq'];peak=[0,0]
        def spawn(command,**kwargs):
            return popen([sys.executable,'-u',str(Path(__file__).resolve()),'--worker','--mode',mode[0],
                '--fixture',str(folder),'--',*command[3:]],**kwargs)
        def progress(event):
            if isinstance(event,dict):
                events.append(event)
                chunks=list((folder/'import-jobs').glob('*/attempt-*/batch-*.json'))
                peak[0]=max(peak[0],len(chunks))
                peak[1]=max([peak[1],*(p.stat().st_size for p in chunks if p.exists())])
                # A second SQLite connection can query while import is running.
                with store.connect() as db:db.execute('SELECT count(*) FROM messages').fetchone()
        started=time.monotonic()
        with patch('chatlocal.import_pipeline.subprocess.Popen',side_effect=spawn):
            result=run_batches(store,'qq',['--all-messages','--no-stickers'],out,progress=progress)
        assert result['imported']==N and result['payload']['exported_messages']==N,result
        assert result['batches']>N//500 and peak[0]<=MAX_PENDING and peak[1]<=MAX_FILE_BYTES,peak
        assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
        assert any(e['imported']>0 and e['stage']=='committing' for e in events)
        # An exported-but-not-finalized job resumes without invoking a reader.
        with patch('chatlocal.import_pipeline.subprocess.Popen',side_effect=AssertionError('must resume sealed export')):
            resumed=run_batches(Store(store.path),'qq',['--all-messages','--no-stickers'],out)
        assert resumed['resumed'] and resumed['imported']==N
        store.import_file(out,complete_job=result['job_id'])
        with store.connect() as db:
            assert db.execute('SELECT status FROM import_jobs WHERE id=?',(result['job_id'],)).fetchone()[0]=='completed'
        # The full native export is still idempotent when explicitly repeated.
        with patch('chatlocal.import_pipeline.subprocess.Popen',side_effect=spawn):
            duplicate=run_batches(store,'qq',['--all-messages','--no-stickers'],out)
        assert duplicate['imported']==0 and duplicate['duplicate']==N
        mark_complete(store,duplicate['job_id'])
        # Failed producer: keep committed messages, retry source scan, deduplicate.
        mode[0]='fail'
        with patch('chatlocal.import_pipeline.subprocess.Popen',side_effect=spawn):
            try:run_batches(store,'qq',['--no-stickers'],out,key='interruption')
            except ImportInterrupted as exc:failed=exc.result
            else:raise AssertionError('must report failure')
        assert failed['imported']==1201
        mode[0]='small'
        with patch('chatlocal.import_pipeline.subprocess.Popen',side_effect=spawn):
            retry=run_batches(Store(store.path),'qq',['--no-stickers'],out,key='interruption')
        assert retry['job_id']==failed['job_id'] and retry['imported']==1201 and retry['duplicate']==1201
        mark_complete(store,retry['job_id'])
        # Message + receipt transaction roll back together on a native-ID conflict.
        conflict=folder/'conflict.json'
        conflict.write_text(json.dumps([dict(platform='qq',conversation_id='100:direct:201',sender='Fixture',sender_id='x',
            timestamp=1780000000,content='conflicting content',source_id='0')]),encoding='utf-8')
        before=totals(store,'conflict')
        with store.connect() as db:db.execute("INSERT INTO import_jobs VALUES('conflict','fixture','qq','running',0,0)")
        try:store.import_file(conflict,batch={'token':'conflict:1','job':'conflict'},complete_job='conflict')
        except ValueError:pass
        else:raise AssertionError('conflict should fail')
        assert totals(store,'conflict')==before
        with store.connect() as db:
            assert db.execute("SELECT status FROM import_jobs WHERE id='conflict'").fetchone()[0]=='running'
        # Native WeChat row decoding across two shards, same global top-N/all
        # semantics; old raw types are counted separately from supported rows.
        user='test_friend';table='Msg_'+hashlib.md5(user.encode()).hexdigest()
        (folder/'wx/message').mkdir(parents=True)
        for shard in range(2):
            with closing(sqlite3.connect(folder/f'wx/message/message_{shard}.db')) as db:
                db.execute('CREATE TABLE Name2Id(user_name TEXT)');db.execute("INSERT INTO Name2Id VALUES('test_sender')")
                db.execute(f'CREATE TABLE {table}(local_id INTEGER,local_type INT,server_id INT,real_sender_id INT,create_time INT,message_content TEXT,packed_info_data BLOB,sort_seq INT)')
                db.executemany(f'INSERT INTO {table} VALUES(?,1,?,1,?,?,NULL,?)',
                    ((n,shard*10000+n,1780000000+n,'wechat fixture',n*2+shard) for n in range(1,1202)))
                db.commit()
        mode[0]='wechat_delta'
        with patch('chatlocal.import_pipeline.subprocess.Popen',side_effect=spawn):
            try:delta=run_batches(store,'wechat',['--no-stickers'],out,key='wechat_delta',automatic=True)
            except ImportInterrupted:
                print((pipeline.REPORTS/'data-wechat-error.log').read_text('utf-8'))
                raise
        assert delta['imported']==2402 and delta['payload']['sync_checkpoint']['cursors']
        mark_complete(store,delta['job_id'])
        mode[0]='wechat'
        with patch('chatlocal.import_pipeline.subprocess.Popen',side_effect=spawn):
            wx=run_batches(store,'wechat',['--all-messages','--no-stickers'],out)
        assert wx['imported']==0 and wx['duplicate']==2402 and wx['payload']['coverage']['limited_chats']==0
        mark_complete(store,wx['job_id'])
        # A missing committed receipt must prevent a sealed export from claiming
        # completeness (and thus from advancing a platform cursor).
        attempt=next((folder/'import-jobs'/wx['job_id']).glob('attempt-*'))
        with store.connect() as db:db.execute('DELETE FROM import_batches WHERE token=(SELECT token FROM import_batches WHERE job=? LIMIT 1)',(wx['job_id'],))
        try:verify_complete(store,attempt)
        except ValueError:pass
        else:raise AssertionError('missing batch must be detected')
        stop=threading.Event();stop.set()
        with patch('chatlocal.import_pipeline.subprocess.Popen',side_effect=AssertionError('cancelled must not launch')):
            try:run_batches(store,'wechat',[],out,key='cancel',cancelled=stop)
            except ImportInterrupted:pass
            else:raise AssertionError('cancel should fail')
        print(json.dumps(dict(passed=True,native_qq_rows=N,peak_pending_files=peak[0],peak_batch_bytes=peak[1],
            seconds=round(time.monotonic()-started,2),coverage=[f'native export {N} rows','bounded backlog','concurrent reads',
                'sealed restart','idempotent repeat','partial failure restart','atomic receipt rollback','cancel',
                'WeChat native incremental and history across shards','batch gap guard']),ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--worker',action='store_true');p.add_argument('--mode',default='qq');p.add_argument('--fixture',type=Path);p.add_argument('--rows',type=int,default=50101)
    args,rest=p.parse_known_args();args.reader=rest[1:] if rest and rest[0]=='--' else rest
    worker(args) if args.worker else check(args.rows)
