"""Explicit, scoped repair of existing media. No message/history synchronization."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from .config import ROOT,local_path
from .import_scope import bounds
from .media import omitted_sticker
from .normalize import decode_export,normalize,stable_key,TZ
from .sync import ingestion_lock,message_commit_lock


def selection(body):
    p=body.get('platform');ids=body.get('conversations')
    if p not in ('qq','wechat') or not isinstance(ids,list) or len(ids)>500 or any(not isinstance(x,str) or not x.strip() or len(x)>300 for x in ids):
        raise ValueError('请选择有效平台和会话；空列表表示该平台全部已导入会话。')
    lo,hi=bounds(body.get('start',''),body.get('end',''))
    if lo is None or hi is None:raise ValueError('媒体刷新须填写开始和结束日期。')
    stickers=body.get('load_stickers',False)
    if type(stickers)is not bool:raise ValueError('表情包开关无效')
    return p,list(dict.fromkeys(ids)),lo,hi,stickers


def prepare(store,body):
    p,ids,lo,hi,stickers=selection(body)
    condition=' AND conversation_id IN ('+','.join('?' for _ in ids)+')' if ids else ''
    with store.connect() as db:
        rows=db.execute('''SELECT * FROM messages WHERE platform=? AND timestamp>=? AND timestamp<?
            AND (media_json!='[]' OR original_content IS NOT NULL)'''+condition+' ORDER BY id',(p,lo,hi,*ids)).fetchall()
    @lru_cache(maxsize=1)
    def archive(path,digest):
        path=local_path(path)
        if path.stat().st_size>100*1024*1024:raise ValueError('archive_too_large')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('archive_changed')
        head,records,kind=decode_export(path)
        return head,dict(records),kind
    result=[]
    for row in rows:
        entries=json.loads(row['media_json'])
        # Keep stable attachment indices when prior imports omitted stickers.
        slots=[omitted_sticker() for _ in range(max((i.get('source_index',n) for n,i in enumerate(entries)),default=-1)+1)]
        for n,item in enumerate(entries):slots[item.get('source_index',n)]=item
        raw={k:row[k] for k in ('platform','conversation_id','conversation','conversation_type','sender_id','sender','timestamp','source_id','is_self')}
        raw.update(content=row['original_content'] if row['original_content'] is not None else row['content'],
            media=slots,reply_to=json.loads(row['reply_to']) if row['reply_to'] else None)
        # Archive descriptors may already say "available" even though a later
        # opt-out removed them from this stored message. Compare against the
        # actual DB state, not against the descriptor recovered from provenance.
        raw['_media_before']=entries
        if (stickers and row['original_content'] is not None) or any(not i.get('metadata',{}).get('md5') for i in entries if i.get('status')=='unavailable'):
            with store.connect() as db:
                sources=db.execute('''SELECT s.archive,s.hash,p.pointer FROM provenance p JOIN sources s ON s.hash=p.source_hash
                    WHERE p.message_id=? ORDER BY s.imported_at DESC''',(row['id'],)).fetchall()
            for source in sources:
                try:
                    head,records,kind=archive(source['archive'],source['hash']);original=records[source['pointer']]
                    message=normalize(original,head,kind)
                    if not message or stable_key(message)!=row['dedup_key'] or message['timestamp']!=row['timestamp'] or message['content']!=raw['content']:continue
                    # A later repair archive can contain empty placeholders for
                    # omitted slots. Look back for resource metadata without
                    # discarding media that has already been recovered.
                    for n,item in enumerate(message['media']):
                        while len(raw['media'])<=n:raw['media'].append(omitted_sticker())
                        current=raw['media'][n]
                        if (not current.get('metadata',{}).get('md5') and item.get('metadata',{}).get('md5')):
                            raw['media'][n]=item
                    if kind=='wechatauto':raw['_wechat_raw']=original
                    if '_wechat_raw' in raw or not any(i.get('status') in ('omitted','unavailable') and not i.get('metadata',{}).get('md5') for i in raw['media']):break
                except (OSError,ValueError,TypeError,KeyError,IndexError):continue
        raw['_message_id']=row['id'];raw['_omitted_before']=row['original_content'] is not None
        result.append(raw)
    return result


def run_local(request,output):
    completed=subprocess.run([sys.executable,str(ROOT/'scripts/refresh_media.py'),'--request',str(request),'--output',str(output)],
        cwd=ROOT,env=dict(os.environ,PYTHONUTF8='1'),capture_output=True,text=True,encoding='utf-8',errors='replace',
        timeout=600,creationflags=subprocess.CREATE_NO_WINDOW)
    if completed.returncode:
        log=ROOT/'reports/private/media-refresh-error.log';log.parent.mkdir(exist_ok=True,parents=True)
        log.write_text(completed.stderr,'utf-8')
        raise ValueError('媒体读取失败，请确认当前账号及本地附件可用；未移动同步进度，诊断已保存在本机。')


def refresh(store,body,progress=lambda text:None,runner=run_local):
    with ingestion_lock(commit=False):
        return _refresh_locked(store,body,progress,runner)


def refresh_for_import(store,platform,conversations,start,end,progress=lambda text:None,runner=run_local):
    """Called under the import ingestion lock; repair existing rows, independent
    of the upstream per-chat history cap. Never import extra history or expand scope.
    """
    lo,hi=bounds(start,end)
    clauses=["platform=?","(media_json!='[]' OR original_content IS NOT NULL)"];args=[platform]
    if conversations:
        clauses.append('conversation_id IN ('+','.join('?' for _ in conversations)+')');args.extend(conversations)
    if lo is not None:clauses.append('timestamp>=?');args.append(lo)
    if hi is not None:clauses.append('timestamp<?');args.append(hi)
    with store.connect() as db:
        first,last=db.execute('SELECT min(timestamp),max(timestamp) FROM messages WHERE '+' AND '.join(clauses),args).fetchone()
    if first is None:return dict(summary='选定范围没有已导入的媒体消息。',checked=0,updated=0,recovered=0,unavailable=0)
    date=lambda ts:datetime.fromtimestamp(ts/1000,TZ).strftime('%Y-%m-%d')
    body=dict(platform=platform,conversations=conversations or [],start=start or date(first),end=end or date(last),load_stickers=True)
    return _refresh_locked(store,body,progress,runner)


def _refresh_locked(store,body,progress,runner):
    p,ids,lo,hi,stickers=selection(body)
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='media-refresh-') as tmp:
        rows=prepare(store,body)
        progress(f'选定范围内有 {len(rows)} 条媒体消息，正在检查本机图片缓存…')
        if not rows:return dict(summary='选定范围没有已导入的媒体消息。',checked=0,updated=0,recovered=0,unavailable=0)
        folder=Path(tmp);request=folder/'request.json';output=folder/'output.json'
        request.write_text(json.dumps(dict(platform=p,load_stickers=stickers,messages=rows),ensure_ascii=False),'utf-8')
        runner(request,output)
        payload=json.loads(output.read_text('utf-8'));updated=0
        # Reuse the idempotent importer with deletion protection. Each batch
        # takes the media/commit lock briefly; chat and live reading can continue.
        for n in range(0,len(payload['messages']),50):
            batch=folder/'batch.json'
            batch.write_text(json.dumps(dict(reader='local-media-refresh',messages=payload['messages'][n:n+50]),ensure_ascii=False),'utf-8')
            with message_commit_lock():
                result=store.import_file(batch,load_stickers=stickers,discovery=True)
                updated+=result['duplicate']
        stats=payload['stats']
        summary=f"检查 {len(rows)} 条媒体消息，更新 {updated} 条；恢复/升级 {stats['recovered']} 个媒体，仍不可用 {stats['unavailable']} 个。"
        if stats['skipped_sticker_messages']:summary+=f" {stats['skipped_sticker_messages']} 条含已省略表情，本次未开启恢复。"
        if stats['metadata_missing_messages']:summary+=f" {stats['metadata_missing_messages']} 条缺少媒体定位信息，需从客户端按范围补读历史。"
        progress(summary)
        return dict(summary=summary,checked=len(rows),updated=updated,**stats)
