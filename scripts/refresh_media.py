"""Local-only media refresh worker; consumes selected existing message metadata."""
import argparse
import json
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tools/qq-reader'),str(ROOT/'tools/wechat-reader')]
from chatlocal.config import DATA
os.environ['CHATLOG_KEEPER_DATA_DIR']=str(DATA/'qq-reader')
os.environ['WECHATAUTO_KEYS_DIR']=str(DATA/'wechat-reader/keys')
from chatlocal.media import media_path,media_at,is_sticker
from reader_media import QQMedia,WeChatMedia


def parser_for(platform,stickers):
    if platform=='qq':
        import chatlog_keeper.qq_db as q
        meta=json.loads((DATA/'qq-snapshot-info.json').read_text('utf-8'))
        return QQMedia(q,meta['source'],load_stickers=stickers),meta['account']
    from wechatauto.db import WeChatDB
    meta=json.loads((DATA/'wechat-snapshot-info.json').read_text('utf-8'))
    reader=WeChatDB(db_dir=str(Path(meta['source_db']).parent.parent),account=meta['account'],
        workdir=str(DATA/'wechat-reader/work'),keys_file=str(DATA/'wechat-reader/keys.json'))
    return WeChatMedia(reader,meta,load_stickers=stickers),meta['account']


def recover(payload,parser,account):
    platform=payload['platform'];stickers=payload['load_stickers'];messages=[]
    stats=dict(recovered=0,unavailable=0,omitted=0,skipped_sticker_messages=0,metadata_missing_messages=0)
    for row in payload['messages']:
        if platform=='qq' and not row['conversation_id'].startswith(str(account)+':'):
            raise ValueError('QQ 会话不属于当前本机账号')
        raw=dict(row);items=[];changed=False;missing_locator=False
        if row.get('_omitted_before') and not stickers:stats['skipped_sticker_messages']+=1
        if stickers and row.get('_omitted_before') and not row['media']:missing_locator=True
        for index,old in enumerate(row['media']):
            kind=old.get('kind','image');metadata=old.get('metadata') or {};item=old
            prior=media_at(row.get('_media_before',row['media']),index)
            prior_available=prior is not None and prior.get('status')=='available' and media_path(prior) is not None
            old_available=old.get('status')=='available' and media_path(old) is not None
            eligible=not old_available or metadata.get('thumbnail')
            if eligible and (stickers or kind=='image'):
                if platform=='qq':
                    item=parser.resolve(kind,metadata.get('md5'),metadata.get('filename',''),row['timestamp'])
                else:
                    native=row.get('_wechat_raw') or dict(chat=row['conversation_id'],create_time=row['timestamp']//1000,
                        type_code=47 if kind in ('sticker','gif') else 3,md5=metadata.get('md5'),content='')
                    recovered=parser.parse(native)[1]
                    if recovered:item=recovered[0]
                if old_available and item.get('status')!='available':item=old
                if item.get('status')=='omitted' and old.get('status')!='omitted':changed=True
            elif not stickers and kind in ('sticker','gif'):
                # Opting out here leaves already cached/omitted stickers alone.
                item=old
            if item.get('status')=='available' and (stickers or not is_sticker(item)) and (not prior_available or prior.get('sha256')!=item.get('sha256')):
                stats['recovered']+=1;changed=True
            if item.get('status')=='unavailable':stats['unavailable']+=1
            if item.get('status')=='omitted':stats['omitted']+=1
            if eligible and (stickers or kind=='image') and item.get('status') in ('unavailable','omitted') and not metadata.get('md5') and not row.get('_wechat_raw'):
                missing_locator=True
            items.append(item)
        if missing_locator:stats['metadata_missing_messages']+=1
        if changed:
            for key in ('_wechat_raw','_message_id','_omitted_before','_media_before'):raw.pop(key,None)
            raw['media']=items
            messages.append(raw)
    return dict(messages=messages,stats=stats)


if __name__=='__main__':
    args=argparse.ArgumentParser();args.add_argument('--request',type=Path,required=True);args.add_argument('--output',type=Path,required=True)
    args=args.parse_args();payload=json.loads(args.request.read_text('utf-8'))
    parser,account=parser_for(payload['platform'],payload['load_stickers'])
    args.output.write_text(json.dumps(recover(payload,parser,account),ensure_ascii=False),'utf-8')
