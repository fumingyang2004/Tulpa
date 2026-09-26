"""Export via the existing read-only WeChatDB module, consuming local snapshots."""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from chatlocal.config import DATA,local_path

os.environ['WECHATAUTO_KEYS_DIR']=str(DATA/'wechat-reader'/'keys')
sys.path.insert(0,str(ROOT/'tools'/'wechat-reader'))
from wechatauto.db import WeChatDB, auto_detect_db_dir, _find_account_dirs


from reader_metrics import timed, report
WeChatDB._decrypt_file = timed('decrypt')(WeChatDB._decrypt_file)
from wechat_cached_reader import install as install_page_cache
install_page_cache(WeChatDB)


@timed('snapshot')
def refresh_snapshot(account=None,reuse=None):
    source_root=auto_detect_db_dir()
    accounts=[Path(p) for p in _find_account_dirs(source_root)] if source_root else []
    if account:
        accounts=[p for p in accounts if p.name==account]
    if len(accounts)!=1:
        raise ValueError('需要一个微信账号；多个账号时使用 --account 明确指定。')
    source=accounts[0]/'db_storage'
    target=local_path(DATA/'wechat-reader-source'/accounts[0].name/'db_storage')
    sys.path.insert(0,str(ROOT/'tools'/'qq-reader'))
    from snapshot_cache import refresh_families
    from incremental_common import fingerprint
    source_before=fingerprint(source,list(source.rglob('*.db'))+list(source.rglob('*.db-wal')))
    if reuse and reuse.get('account')==accounts[0].name and reuse.get('fingerprint')==source_before:
        return dict(account=accounts[0].name,source_db=str(source),snapshot_at=datetime.now(timezone.utc).isoformat(),fingerprint=source_before)
    paths=list(source.rglob('*.db'))
    refresh_families(source,target,paths)
    count=len(paths)
    source_after=fingerprint(source,list(source.rglob('*.db'))+list(source.rglob('*.db-wal')))
    if source_before!=source_after:raise ValueError('数据库在复制期间更新；请重试，未推进进度。')
    metadata=dict(fingerprint=source_after,account=accounts[0].name,source_db=str(source),
                  snapshot_at=datetime.now(timezone.utc).isoformat(),databases=count)
    (DATA/'wechat-snapshot-info.json').write_text(json.dumps(metadata),encoding='utf-8')
    return metadata


SYSTEM_CHATS={'weixin','filehelper','newsapp','brandsessionholder','foldedchat'}


def select_chats(chats, requested=None):
    """Enumerate actual message tables, independent of recent/hidden sessions."""
    supported=[]
    excluded={'service':0,'unresolved':0,'empty':0}
    for chat in chats:
        user=chat['username']
        if not chat['message_count']:
            excluded['empty']+=1
        elif user==chat['md5']:
            excluded['unresolved']+=1
        elif user.startswith('gh_') or user in SYSTEM_CHATS:
            excluded['service']+=1
        else:
            supported.append(chat)
    if requested:
        by_id={key:c for c in supported for key in (c['username'],c['md5'])}
        missing=[user for user in requested if user not in by_id]
        if missing:
            raise ValueError('指定会话不在本机可读取的普通会话中；请先用 --list 查看会话 ID。')
        selected=list({by_id[user]['username']:by_id[user] for user in requested}.values())
    else:
        selected=supported
    if not selected:
        # Upstream interprets an empty users list as ALL users. Never pass it.
        raise ValueError('本机快照中没有可读取的普通会话；请检查账号或刷新快照。')
    return selected,dict(discovered_chats=len(chats),eligible_chats=len(supported),excluded=excluded)


from scoped_wechat import export_selected


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--list',action='store_true',help='列出所有实际含消息的会话，不受最近会话顺序限制')
    p.add_argument('--refresh',action='store_true')
    p.add_argument('--account')
    p.add_argument('--chat',action='append',help='仅导出指定会话 ID；默认全部可识别的普通会话')
    p.add_argument('--start',default='')
    p.add_argument('--end',default='')
    p.add_argument('--limit',type=int,default=500,help='每会话最近原始消息条数（含非文本），默认 500')
    p.add_argument('--output')
    p.add_argument('--no-stickers',action='store_true',help='表情包及动态图仅保留占位，不复制媒体')
    p.add_argument('--incremental-request')
    p.add_argument('--batch-dir')
    p.add_argument('--all-messages',action='store_true')
    args=p.parse_args()
    from chatlocal.import_scope import bounds
    bounds(args.start,args.end)
    if args.all_messages and not args.batch_dir:raise ValueError('读取全部消息需要分批导入模式')
    if args.batch_dir:os.environ['CHATWEAVE_BATCH_DIR']=str(local_path(args.batch_dir))
    from chatlocal.reader_batches import BatchWriter,stage
    stage('preparing')
    request=json.loads(local_path(args.incremental_request).read_text(encoding='utf-8')) if args.incremental_request else None
    if not 1<=args.limit<=10000:
        raise ValueError('limit 范围 1–10000')
    output=local_path(args.output or ('data/wechat-reader/catalog.json' if args.list else 'imports/wechat-real.json'))
    info=DATA/'wechat-snapshot-info.json'
    metadata=refresh_snapshot(args.account,request['checkpoint'] if request else None) if args.refresh or not info.exists() else json.loads(info.read_text(encoding='utf-8-sig'))
    account=metadata['account']
    if args.account and args.account!=account:
        raise ValueError('已有快照属于其他账号，请同时指定 --refresh。')
    if Path(account).name != account:
        raise ValueError('Invalid account directory')
    account_snapshot=DATA/'wechat-reader-source'/account/'db_storage'
    if not account_snapshot.exists():
        shutil.copytree(DATA/'wechat-snapshot',account_snapshot)
    from incremental_common import message_files_unchanged
    if request and message_files_unchanged(request['checkpoint'],metadata,'wechat'):
        current=dict(request['checkpoint'],fingerprint=metadata['fingerprint'])
        payload=dict(messages=[],sync_checkpoint=current,unchanged=True)
        if args.batch_dir:BatchWriter(args.batch_dir,{}).finish(payload)
        output.write_text(json.dumps(payload),encoding='utf-8')
        return
    reader=WeChatDB(db_dir=str(DATA/'wechat-reader-source'),account=account,
        workdir=str(DATA/'wechat-reader'/'work'),keys_file=str(DATA/'wechat-reader'/'keys.json'))
    if request:
        from incremental_wechat import export_delta
        payload=export_delta(reader,metadata,request,not args.no_stickers,batch_dir=local_path(args.batch_dir) if args.batch_dir else None)
        encoded=json.dumps(payload,ensure_ascii=False)
        if len(encoded.encode('utf-8'))>100*1024*1024:raise ValueError('新增导出超过单批限制；未推进进度，请先补读历史。')
        output.write_text(encoded,encoding='utf-8')
        return
    chats=reader.list_message_chats()
    if args.list:
        (DATA/'wechat-reader'/'sessions.json').write_text(json.dumps(chats,ensure_ascii=False),encoding='utf-8')
        selected,_=select_chats(chats)
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(dict(conversations=[dict(platform='wechat',conversation_id=c['username'],conversation=c['name'],count=c['message_count']) for c in selected]),ensure_ascii=False),encoding='utf-8')
        print(json.dumps({'sessions':len(chats),'fields':list(chats[0]) if chats else []},ensure_ascii=False))
        return
    selected,coverage=select_chats(chats,args.chat)
    coverage['selection']='explicit' if args.chat else 'all_supported_local_chats'
    # Publish only after sender resolution and coverage checks have succeeded.
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='wechat-export-') as folder:
        staging=Path(folder)/'export.json'
        result=export_selected(reader,selected,metadata,staging,None if args.all_messages else args.limit,coverage,load_stickers=not args.no_stickers,start=args.start,end=args.end,
            batch_dir=local_path(args.batch_dir) if args.batch_dir else None)
        if staging.stat().st_size>100*1024*1024:
            raise ValueError('导出超过单文件 100 MB，请使用 --chat 按会话导出；原文件保留。')
        output.parent.mkdir(parents=True,exist_ok=True)
        os.replace(staging,output)
    result['out']=str(output)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    try: main()
    finally: report()
