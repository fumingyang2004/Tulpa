"""One-time/scoped local file-message backfill. Reuses authenticated readers.

Future file messages use the normal live/cold ingestion; no separate hot poller.
No binary copy, parser or LLM runs here.
"""
import contextlib
import hashlib
import io
import json
import sys
import time
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(ROOT),str(ROOT/'tools/qq-reader'),str(ROOT/'tools/wechat-reader')]
from chatlocal.config import DATA
from chatlocal.store import Store
from chatlocal.import_scope import get_scope
from chatlocal.artifact_metadata import qq_file,wechat_file
from chatlocal.normalize import normalize,stable_key
from live_reader import Reader
from live_vfs import SourceBusy


def collect(platform,scope,snapshot=False):
    reader=Reader(platform);reader.views=[];messages=[]
    try:
        if platform=='qq':
            import chatlog_keeper.qq_db as q
            meta=json.loads((DATA/'qq-snapshot-info.json').read_text('utf-8'))
            account=meta['account'];reader.labels(account)
            source=DATA/'qq-snapshot'/account/'nt_qq/nt_db/nt_msg.db' if snapshot else Path(meta['source'])/'nt_msg.db'
            db=reader.open(source,q.load_cached_key_for_account(account) or q.load_cached_key())
            own=q._query_account_qq_number(db) or account
            for spec in q._QQ_MESSAGE_TABLE_SPECS:
                rows=db.execute(f'''SELECT "40001","40050","40090","40033","40800","{spec.conversation_column}",
                  {spec.table_rank},rowid,'{spec.conversation_type}',"40020" FROM "{spec.table}" WHERE "40011" IN (3,7)''')
                for row in rows:
                    cid=f'{account}:{spec.conversation_type}:{row[5]}'
                    if scope['conversations'] is not None and cid not in scope['conversations']:continue
                    file=qq_file(q,row[4])
                    if not file:continue
                    rec=q._qq_message_page_record(tuple(row),account_id=account,conversation_id=str(row[5]),buddy_map=reader.buddy,
                        group_map=reader.group,group_name_map=reader.names,recover_unreadable_rows=True)
                    sid=str(rec['sender_qq'] or rec['sender_uid'] or '')
                    messages.append(dict(platform='qq',conversation_id=cid,conversation=rec['chat_name'],conversation_type=spec.conversation_type,
                        sender=rec['sender_name'],sender_id=sid,timestamp=rec['ts'],is_self=sid==str(own) if sid else None,
                        source_id=str(rec['msg_id']) if rec['msg_id'] is not None else rec['source_offset'],
                        content='[文件] '+file['filename'],files=[file]))
            payload=dict(reader='qq-artifact-discovery',messages=messages,snapshot_at=meta['snapshot_at'] if snapshot else time.time())
        else:
            from wechatauto.db import WeChatDB,MSG_TYPE_NAMES
            meta=json.loads((DATA/'wechat-snapshot-info.json').read_text('utf-8'))
            wx=WeChatDB(db_dir=str(Path(meta['source_db']).parent.parent),account=meta['account'],
                workdir=str(DATA/'live/wechat'),keys_file=str(DATA/'wechat-reader/keys.json'))
            opened={}
            def op(rel):
                if rel not in opened:
                    if rel not in wx._keys:raise ValueError('微信分片缺少密钥')
                    opened[rel]=reader.open(wx._db_path(rel),wx._keys[rel])
                return opened[rel]
            wx._open=op;idx=wx._build_md5_index();nicks=wx._nickname_index();chats={}
            for rel in wx._message_dbs():
                db=op(rel);identities=dict(db.execute('SELECT rowid,user_name FROM Name2Id'))
                for user in identities.values():
                    if user:idx.setdefault(hashlib.md5(user.encode()).hexdigest(),user)
                tables=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'").fetchall()
                for table, in tables:
                    user=idx.get(table[4:])
                    if not user or user.startswith('gh_'):continue
                    if scope['conversations'] is not None and user not in scope['conversations']:continue
                    rows=db.execute(f'''SELECT local_id,local_type,server_id,real_sender_id,create_time,message_content,packed_info_data,sort_seq
                      FROM "{table}" WHERE (local_type & 4294967295)=49''')
                    for row in rows:
                        m=wx._export_row(row,MSG_TYPE_NAMES)
                        if not wechat_file(m.get('content','')):continue
                        sender=identities.get(row['real_sender_id'],'')
                        m.update(chat=user,sender_username=sender,sender_name=nicks.get(sender,sender) or '未知发送者',
                            source_db=rel.replace('\\','/'),source_table=table)
                        messages.append(m);chats[user]=dict(username=user,name=nicks.get(user,user))
            payload=dict(reader='wechatauto-replica',wxid=wx.wxid,identity_method='per-shard Name2Id',chats=list(chats.values()),messages=messages)
        for view in reader.views:view.view.assert_fresh()
        return payload
    finally:
        for view in reader.views:view.close()


def discover(platform=None):
    store=Store();scope=get_scope(store);result={}
    for p in ([platform] if platform else ['qq','wechat']):
        if p not in scope or not scope[p]['enabled']:continue
        started=time.perf_counter()
        try:
            for attempt in range(3):
                try:
                    with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):payload=collect(p,scope[p])
                    break
                except SourceBusy:
                    if attempt==2:
                        if p!='qq':raise
                        from chatlocal.qq_storage import qq_workspace_lock
                        from export_qq import snapshot
                        with qq_workspace_lock():
                            with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                                snapshot()
                                payload=collect(p,scope[p],snapshot=True)
                        break
                    time.sleep(0.8)
            kind='wechatauto' if p=='wechat' else 'canonical'
            with store.connect() as db:deleted={r[0] for r in db.execute('SELECT dedup_key FROM deleted_message_keys')}
            payload['messages']=[m for m in payload['messages'] if stable_key(normalize(m,payload,kind)) not in deleted]
            output=ROOT/'.tmp'/('artifact-discovery-'+uuid.uuid4().hex+'.json')
            output.write_text(json.dumps(payload,ensure_ascii=False),'utf-8')
            try:imported=store.import_file(output)
            finally:output.unlink(missing_ok=True)
            result[p]=dict(status='ok',found=len(payload['messages']),imported=imported['imported'],duplicate=imported['duplicate'],seconds=round(time.perf_counter()-started,3))
        except Exception as exc:result[p]=dict(status='error',error_type=type(exc).__name__,seconds=round(time.perf_counter()-started,3))
    return result


if __name__=='__main__':
    print(json.dumps(discover(sys.argv[1] if len(sys.argv)>1 else None),ensure_ascii=False))
