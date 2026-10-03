"""Consume chatlog-keeper's authenticated SQLite output and message decoder.

The client is read only. No hooks, debugger, restart, or protocol implementation.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from chatlocal.config import DATA, local_path
from chatlocal.qq_storage import qq_workspace_lock, checked_path, CACHE, RECEIPT, cache_mode

os.environ['CHATLOG_KEEPER_DATA_DIR'] = str(DATA/'qq-reader')
sys.path.insert(0, str(ROOT/'tools'/'qq-reader'))
import chatlog_keeper.qq_db as q
from reader_metrics import timed, report, count
from qq_message_types import CONTENT_SUBTYPES, SUPPORTED_SQL, is_reply_candidate, history_query

# Timings include the original upstream work; no source data is logged.
q._skip_header = timed('strip_header')(q._skip_header)
q._decrypt_db_qq_result = timed('decrypt')(q._decrypt_db_qq_result)


@timed('snapshot')
def snapshot(account=None,reuse=None):
    source_root = q.find_qq_data_root()
    accounts = q.find_qq_account_databases(source_root) if source_root else {}
    from chatlocal.client_accounts import choose_account
    account=choose_account('qq',accounts,account)
    source=accounts[account]
    target = local_path(DATA/'qq-snapshot'/account/'nt_qq'/'nt_db')
    target.mkdir(parents=True, exist_ok=True)
    from snapshot_cache import refresh_families
    from incremental_common import fingerprint
    paths=lambda:[source.parent/(name+suffix) for name in ('nt_msg.db','profile_info.db','group_info.db') for suffix in ('','-wal')]
    source_before=fingerprint(source.parent,paths())
    if reuse and reuse.get('account')==account and reuse.get('fingerprint')==source_before:
        return dict(account=account,source=str(source.parent),snapshot_at=time.time(),fingerprint=source_before)
    refresh_families(source.parent, target,
        [source.parent/name for name in ('nt_msg.db','profile_info.db','group_info.db') if (source.parent/name).is_file()])
    source_after=fingerprint(source.parent,paths())
    if source_before!=source_after:raise ValueError('数据库在复制期间更新；请重试，未推进进度。')
    meta = dict(fingerprint=source_after,account=account, source=str(source.parent), snapshot_at=time.time())
    (DATA/'qq-snapshot-info.json').write_text(json.dumps(meta), encoding='utf-8')
    return meta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh', action='store_true', help='读取客户端文件，刷新项目内副本')
    parser.add_argument('--account')
    parser.add_argument('--list',action='store_true',help='列出本机有消息的会话，不导入正文')
    parser.add_argument('--chat',action='append',help='限定会话 ID，可重复指定')
    parser.add_argument('--start',default='')
    parser.add_argument('--end',default='')
    parser.add_argument('--date-range',action='store_true',help='仅按起止日期筛选；开始留空不设下限，不使用 days')
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--per-chat', type=int, default=500)
    parser.add_argument('--output')
    parser.add_argument('--no-stickers',action='store_true',help='表情包及动态图仅保留占位，不复制媒体')
    parser.add_argument('--incremental-request')
    parser.add_argument('--batch-dir', help='内部有界分批输出目录')
    parser.add_argument('--all-messages', action='store_true', help='读取日期范围内全部消息，仍受内部批次限制')
    args = parser.parse_args()
    args.output=args.output or ('data/qq-reader/catalog.json' if args.list else 'imports/qq-real.json')
    if not 1 <= args.days <= 3650 or not 1 <= args.per_chat <= 10000:
        raise ValueError('days 范围 1–3650，per-chat 范围 1–10000')
    from chatlocal.import_scope import bounds
    bounds(args.start,args.end)
    if args.all_messages and not args.batch_dir:raise ValueError('读取全部消息需要分批导入模式')
    if args.batch_dir:os.environ['CHATWEAVE_BATCH_DIR']=str(local_path(args.batch_dir))
    with qq_workspace_lock():
        # A failed/new export must not let an older import clear its working files.
        checked_path(CACHE / RECEIPT).unlink(missing_ok=True)
        export(args)


def export(args):
    from chatlocal.reader_batches import BatchWriter,stage
    stage('preparing')
    from incremental_common import message_files_unchanged
    policy=cache_mode()
    request=json.loads(local_path(args.incremental_request).read_text(encoding='utf-8')) if getattr(args,'incremental_request',None) else None
    previous=request['checkpoint'] if request else {}
    info = DATA/'qq-snapshot-info.json'
    meta = snapshot(args.account,previous if previous.get('method')=='native-id-inventory-v1' else None) if args.refresh or not info.exists() else json.loads(info.read_text(encoding='utf-8'))
    if args.account and args.account!=meta['account']:
        raise ValueError('已有快照属于其他账号，请同时指定 --refresh。')
    unchanged=previous.get('method')=='native-id-inventory-v1' and message_files_unchanged(previous,meta,'qq')
    reader = q.QQDBReader(data_root=DATA/'qq-snapshot', account_id=meta['account'], live_key_timeout_s=75)
    if not reader.is_available():
        raise ValueError('QQ 被动读取未获得有效密钥；请登录 QQ 后重试，或导入 QCE JSON。')
    if previous and previous['account']!=meta['account']:raise ValueError('账号变化；请确认账号，未推进同步进度。')
    work = DATA/'qq-reader'/'decrypted'
    work.mkdir(parents=True, exist_ok=True)
    from qq_cached_reader import decrypt_database
    # Read the wrapper in place: no second snapshot or no_header.db is needed.
    result = None if unchanged else decrypt_database(q, reader.db_path, reader.key, work/'messages.db')
    cached = {'shifted':result.shifted_pending_byte if result else False}
    for name in ('profile_info.db','group_info.db'):
        source = reader.db_path.parent/name
        try:
            dec = decrypt_database(q, source, reader.key, work/(source.stem+'_dec.db')) if source.is_file() else None
        except (ValueError,OSError):
            # Preserve the established auxiliary-reader fallback. Main message
            # authentication still must succeed; missing labels cannot erase it.
            count('auxiliary_full_rebuild_fallbacks')
            dec = q._decrypt_aux_db(source, reader.key, work)
        cached[name] = {'path':str(dec.path), 'shifted':dec.shifted_pending_byte} if dec else None
    def auxiliary(name):
        item = cached.get(name)
        return q._QQDecryptedDatabase(local_path(item['path']), item['shifted']) if item else work/'missing.db'
    group = q._build_group_member_map(auxiliary('group_info.db'))
    from qq_labels import directory,conversation_labels,remember_peer,imported_ids
    opened=[]
    def open_directory(name):
        conn=q._open_qq_sqlite_connection(auxiliary(name));opened.append(conn);return conn
    try:buddy,names,label_status=directory(open_directory)
    finally:
        for conn in opened:conn.close()
    if unchanged:
        # Contact/group metadata can change even when message pages do not.
        # Carry label repairs without re-decrypting or re-importing history.
        current=dict(previous,fingerprint=meta['fingerprint'])
        payload=dict(messages=[],sync_checkpoint=current,unchanged=True,
            label_account=meta['account'],conversation_labels=conversation_labels(meta['account'],buddy,names,imported_ids(meta['account'])),
            label_status=label_status)
        if getattr(args,'batch_dir',None):BatchWriter(args.batch_dir,{}).finish(payload)
        local_path(args.output).write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
        return
    db = q._open_qq_sqlite_connection(q._QQDecryptedDatabase(work/'messages.db',cached['shifted']))
    from reader_media import QQMedia
    media_reader=None
    cursors={}
    known=set()
    if request:
        from chatlocal.store import Store
        from incremental_qq import delta_rows
        with Store().connect() as existing:
            known={(r[0],r[1]) for r in existing.execute("SELECT conversation_id,source_id FROM messages WHERE platform='qq'")}
    since, until = int(time.time()-args.days*86400), int(time.time())
    if getattr(args,'date_range',False):since=None
    from chatlocal.import_scope import bounds
    lo,hi=bounds(getattr(args,'start',''),getattr(args,'end',''))
    if lo is not None:since=lo/1000
    until=hi/1000 if hi is not None else until+1
    requested=request.get('conversations') if request else getattr(args,'chat',None)
    selected={}
    for value in requested or []:
        parts=value.split(':',2)
        if len(parts)!=3 or parts[0]!=meta['account'] or parts[1] not in {s.conversation_type for s in q._QQ_MESSAGE_TABLE_SPECS}:
            raise ValueError('QQ 会话 ID 不属于当前账号')
        selected.setdefault(parts[1],[]).append(parts[2])
    messages, skipped, invalid_timestamps = [], 0, 0
    if getattr(args,'batch_dir',None):
        messages=BatchWriter(args.batch_dir,dict(reader='chatlog-keeper',snapshot=meta,load_stickers=not args.no_stickers))
    try:
        if getattr(args,'list',False):
            conversations=[]
            for spec in q._QQ_MESSAGE_TABLE_SPECS:
                for peer,total in db.execute(f'SELECT "{spec.conversation_column}",count(*) FROM "{spec.table}" GROUP BY "{spec.conversation_column}"'):
                    mapping=names if spec.conversation_type=='group' else buddy
                    label=mapping.get(int(peer),'') if str(peer).isdigit() else ''
                    conversations.append(dict(platform='qq',conversation_id=f"{meta['account']}:{spec.conversation_type}:{peer}",conversation=label or f'QQ {spec.conversation_type} {peer}',count=total))
            catalog=local_path(args.output);catalog.parent.mkdir(parents=True,exist_ok=True)
            catalog.write_text(json.dumps(dict(conversations=conversations),ensure_ascii=False),encoding='utf-8')
            return
        own = q._query_account_qq_number(db) or meta['account']
        for spec in q._QQ_MESSAGE_TABLE_SPECS:
            columns = q._qq_table_columns(db.cursor(), spec.table)
            required = {'40001','40003','40050','40090','40033','40800','40020','40011','40012',spec.conversation_column}
            if not columns or not required.issubset(columns):
                raise ValueError('QQ 当前消息表结构不兼容；未导入')
            peers=selected.get(spec.conversation_type,[])
            if requested and not peers and not request:continue
            for peer in peers:
                if not db.execute(f'SELECT 1 FROM "{spec.table}" WHERE "{spec.conversation_column}"=? LIMIT 1',(peer,)).fetchone():
                    raise ValueError('选中QQ会话在当前本机快照中不存在')
            # Preserve the previous text window, plus a separate recent window
            # for image/sticker/reply/file elements. Videos remain excluded.
            query,params=history_query(spec,since=since,until=until,peers=peers,limit=None if getattr(args,'all_messages',False) else args.per_chat)
            if request:
                rows,cursors[spec.table]=delta_rows(db,spec,request,args.incremental_request)
            else:
                bad_query,bad_params=history_query(spec,since=since,until=until,peers=peers,limit=args.per_chat,count_invalid=True)
                excluded=db.execute(bad_query,bad_params).fetchone()[0]
                invalid_timestamps+=excluded
                skipped+=excluded
                rows=db.execute(query,params)
            for row in rows:
                if isinstance(messages,BatchWriter):messages.observe()
                from chatlocal.artifact_metadata import qq_file
                file=qq_file(q,row[4]) if row[11] in (3,7) else None
                from chatlocal.voice_metadata import qq_voice,PLACEHOLDER
                voice=qq_voice(q,row[4]) if row[11]==6 else None
                if row[11]==6 and not voice:continue
                if row[11] in (3,7) and not file:continue
                parsed=None
                if is_reply_candidate(row[11],row[10]):
                    if media_reader is None:media_reader=QQMedia(q,meta['source'],load_stickers=not args.no_stickers)
                    parsed=media_reader.parse(row[4],row[10],row[1])
                    # Accept this native pair only when the payload has a quote.
                    if not parsed[2]:continue
                if spec.conversation_type=='direct':remember_peer(buddy,row[5],row[3],row[2])
                rec = q._qq_message_page_record(row[:10], account_id=meta['account'],conversation_id=str(row[5]),
                    buddy_map=buddy,group_map=group,group_name_map=names,recover_unreadable_rows=True)
                if rec.get('decode_status') == 'unreadable':
                    if request:raise ValueError('新增消息解码失败；未推进进度，请检查读取器。')
                    skipped += 1
                    continue
                source_id=str(rec['msg_id']) if rec['msg_id'] is not None else rec['source_offset']
                conversation_id=f"{meta['account']}:{spec.conversation_type}:{rec['conversation_id']}"
                if (conversation_id,source_id) in known:continue
                if not file and not voice and row[10]!=1 and media_reader is None:media_reader=QQMedia(q,meta['source'],load_stickers=not args.no_stickers)
                if not isinstance(messages,BatchWriter) and len(messages)>=50000:raise ValueError('单文件导出超过 50000 条，请使用产品中的分批导入或 --batch-dir。')
                sender_id = str(rec['sender_qq']) if rec['sender_qq'] else rec['sender_uid']
                if parsed is not None:text,media,reply=parsed
                elif voice:text,media,reply=PLACEHOLDER,[],None
                elif file:text,media,reply='[文件] '+file['filename'],[],None
                elif row[10]!=1:text,media,reply=media_reader.parse(row[4],row[10],row[1])
                else:text,media,reply=rec['content'],[],None
                if reply:
                    from reader_media import resolve_qq_quote
                    reply=resolve_qq_quote(db,spec,row[5],reply)
                if not text and not media and not reply:
                    skipped+=1;continue
                messages.append(dict(platform='qq',conversation=rec['chat_name'],
                    conversation_type=spec.conversation_type,
                    conversation_id=f"{meta['account']}:{spec.conversation_type}:{rec['conversation_id']}",
                    sender=rec['sender_name'],sender_id=sender_id,timestamp=rec['ts'],
                    content=text,media=media,reply_to=reply,files=[file] if file else [],voice=voice,is_self=(sender_id==str(own)) if rec['sender_qq'] else None,
                    source_id=str(rec['msg_id']) if rec['msg_id'] is not None else rec['source_offset'],
                    source_table=spec.table,source_rowid=row[7],source_offset=rec['source_offset']))
    finally:
        db.close()
    streaming=isinstance(messages,BatchWriter)
    if not streaming:messages.sort(key=lambda m:(m['timestamp'],m['conversation_id'],m['source_id']))
    output = local_path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    head=dict(reader='chatlog-keeper',snapshot=meta,days=args.days,
        read_scope=dict(conversations=requested,start=getattr(args,'start',''),end=getattr(args,'end',''),date_range=getattr(args,'date_range',False)),
        per_chat=args.per_chat,media_window=args.per_chat,all_messages=getattr(args,'all_messages',False),load_stickers=not args.no_stickers,
        label_account=meta['account'],conversation_labels=conversation_labels(meta['account'],buddy,names,
            imported_ids(meta['account'])|(messages.conversations if streaming else {m['conversation_id'] for m in messages})),label_status=label_status,
        supported_subtypes=sorted((*CONTENT_SUBTYPES,33)),skipped=skipped,invalid_timestamps=invalid_timestamps,
        sync_checkpoint=dict(method='native-id-inventory-v1',account=meta['account'],cursors=cursors,fingerprint=meta['fingerprint']) if request else None)
    if streaming:
        payload=messages.finish(head)
        from chatlocal.reader_batches import atomic_json
        atomic_json(output,payload)
        atomic_json(checked_path(work/RECEIPT),dict(source_hash=hashlib.sha256(output.read_bytes()).hexdigest(),cache_policy=policy))
        print(json.dumps(dict(messages=len(messages),skipped=skipped,invalid_timestamps=invalid_timestamps)))
        return
    payload=json.dumps(dict(head,messages=messages),ensure_ascii=False).encode('utf-8')
    if len(payload)>100*1024*1024:
        raise ValueError('QQ 导出超过 100 MB，请缩小范围；旧导出及读取缓存保留。')
    with tempfile.TemporaryDirectory(dir=checked_path(ROOT/'.tmp'),prefix='qq-export-') as folder:
        staging=Path(folder)/'export.json';staging.write_bytes(payload)
        os.replace(staging,output)
        receipt=Path(folder)/RECEIPT
        receipt.write_text(json.dumps(dict(source_hash=hashlib.sha256(payload).hexdigest(),cache_policy=policy)),encoding='utf-8')
        os.replace(receipt,checked_path(work/RECEIPT))
    print(json.dumps(dict(file=str(output.relative_to(ROOT)),messages=len(messages),
        conversations=len({m['conversation_id'] for m in messages}),skipped=skipped,invalid_timestamps=invalid_timestamps,
        media=sum(len(m['media']) for m in messages),available=sum(i['status']=='available' for m in messages for i in m['media']),
        unknown_self=sum(m['is_self'] is None for m in messages)),ensure_ascii=False))


if __name__ == '__main__':
    try: main()
    finally: report()
