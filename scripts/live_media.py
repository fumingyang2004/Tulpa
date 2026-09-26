"""Bounded local-only retries for recently ingested QQ/WeChat attachments.

Replay the original archived record through the existing import transaction:
message IDs, evidence, deduplication and deletion tombstones stay authoritative.
No history sync, source DB scan, download, OCR or model request is performed.
"""
import hashlib
import json
import re
import sqlite3
import time
from contextlib import closing

from chatlocal.config import DB,local_path


class WeChatMediaRetries:
    platform='wechat'
    def __init__(self,path=DB):
        self.path=path
        self.next_scan=0
        self.after=0
        self.backoff={}
        self.scope=None

    def collect(self,media,wxid,wanted,*,exclude=(),now=None):
        now=time.time() if now is None else now
        scope=(wxid,tuple(sorted(wanted)) if wanted is not None else None,media.load_stickers)
        if scope!=self.scope:
            self.scope=scope;self.after=0;self.next_scan=0;self.backoff={}
        if now<self.next_scan or wanted==[]:return [],{}
        self.next_scan=now+15
        result=[];chats={};attempted=0
        with closing(sqlite3.connect(f'file:{self.path}?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            clause=' AND m.conversation_id IN ('+','.join('?' for _ in wanted)+')' if wanted is not None else ''
            # Work only on incomplete media seen by realtime in the last day.
            # Paginate to prevent old unavailable files starving later messages.
            rows=db.execute('''SELECT m.id,m.conversation_id,m.conversation,m.source_id,m.media_json
                FROM messages m JOIN live_events e ON e.message_id=m.id
                WHERE m.platform=? AND e.ingested_at>=? AND m.id>?
                  AND instr(m.media_json,'"unavailable"')>0'''+clause+' ORDER BY m.id LIMIT 128',
                (self.platform,now-86400,self.after,*(wanted or []))).fetchall()
            for row in rows:
                self.after=row['id']
                if (row['conversation_id'],row['source_id']) in exclude:continue
                deadline,tries=self.backoff.get(row['id'],(0,0))
                if now<deadline:continue
                if not any(i.get('status')=='unavailable' and (media.load_stickers or i.get('kind')=='image')
                           for i in json.loads(row['media_json'])):continue
                attempted+=1
                self.backoff[row['id']]=(now+min(300,15*2**min(tries,5)),tries+1)
                if len(self.backoff)>512:self.backoff.pop(next(iter(self.backoff)))
                raw=self.original(db,row,wxid)
                if raw and (recovered:=self.recover(media,raw)):
                    result.append(recovered)
                    chats[row['conversation_id']]=dict(username=row['conversation_id'],name=row['conversation'])
                if attempted>=4:break
            else:
                if len(rows)<128:self.after=0
        return result,chats

    @staticmethod
    def recover(media,raw):
        text,items,reply=media.parse(raw)
        if any(i.get('status') in ('available','omitted') for i in items):
            if text is not None:raw['text']=text
            raw.update(media=items,reply_to=reply)
            return raw

    @staticmethod
    def original(db,row,wxid):
        sources=db.execute('''SELECT s.archive,p.pointer FROM provenance p JOIN sources s ON s.hash=p.source_hash
            WHERE p.message_id=? ORDER BY s.imported_at DESC LIMIT 4''',(row['id'],)).fetchall()
        for source in sources:
            match=re.fullmatch('/messages/([0-9]+)',source['pointer'])
            if not match:continue
            try:
                path=local_path(source['archive'])
                # Live batches are small. Do not load a large historical export
                # repeatedly on the realtime hot path.
                if path.stat().st_size>2*1024*1024:continue
                payload=json.loads(path.read_text('utf-8'))
                if payload.get('reader')!='wechatauto-replica' or payload.get('wxid')!=wxid:continue
                raw=payload['messages'][int(match[1])]
                if (raw.get('chat')!=row['conversation_id'] or str(raw.get('server_id'))!=row['source_id']
                    or (raw.get('type_code',0)&0xffffffff) not in (3,47)):continue
                return dict(raw)
            except (OSError,ValueError,TypeError,KeyError,IndexError):continue
        return None


class QQMediaRetries(WeChatMediaRetries):
    platform='qq'

    @staticmethod
    def original(db,row,account):
        if not row['conversation_id'].startswith(str(account)+':'):return None
        sources=db.execute('''SELECT s.hash,s.archive,p.pointer FROM provenance p JOIN sources s ON s.hash=p.source_hash
            WHERE p.message_id=? ORDER BY s.imported_at DESC LIMIT 4''',(row['id'],)).fetchall()
        for source in sources:
            match=re.fullmatch('/messages/([0-9]+)',source['pointer'])
            if not match:continue
            try:
                path=local_path(source['archive'])
                if path.stat().st_size>2*1024*1024:continue
                data=path.read_bytes()
                if hashlib.sha256(data).hexdigest()!=source['hash']:continue
                payload=json.loads(data)
                if payload.get('reader')!='qq-live-database':continue
                raw=payload['messages'][int(match[1])]
                if (raw.get('platform')!='qq' or raw.get('conversation_id')!=row['conversation_id']
                    or str(raw.get('source_id'))!=row['source_id']):continue
                if not isinstance(raw.get('media'),list) or len(raw['media'])>16:continue
                return dict(raw)
            except (OSError,ValueError,TypeError,KeyError,IndexError):continue
        return None

    @staticmethod
    def recover(media,raw):
        items=[];changed=False
        for item in raw['media']:
            if not isinstance(item,dict):return None
            new=item
            if item.get('status')=='unavailable' and item.get('kind') in ('image','sticker','gif'):
                metadata=item.get('metadata') or {}
                md5=metadata.get('md5');filename=metadata.get('filename','')
                if isinstance(md5,str) and re.fullmatch('[a-fA-F0-9]{32}',md5) and isinstance(filename,str):
                    new=media.resolve(item['kind'],md5,filename,raw.get('timestamp'))
                    changed=changed or new['status'] in ('available','omitted')
            items.append(new)
        if changed:return dict(raw,media=items)
