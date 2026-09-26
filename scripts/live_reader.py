"""Persistent local worker. JSONL is private IPC, never a realtime log.

Reuse upstream decoders/media, native IDs and authenticated read-only pages.
QQ sequence queries are a bounded hot-path hint; the cold ID inventory remains
authoritative for late/backfilled/nonmonotonic messages.
"""
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from chatlocal.config import DATA
os.environ['CHATLOG_KEEPER_DATA_DIR'] = str(DATA / 'qq-reader')
os.environ['WECHATAUTO_KEYS_DIR'] = str(DATA / 'wechat-reader/keys')
from live_vfs import ReadView, SourceBusy, family_stamp, configure_qq_worker
from incremental_common import boundary


class Row(tuple):
    def __new__(cls, values, names):
        obj = super().__new__(cls, values)
        obj.names = names
        return obj
    def __getitem__(self, key):
        return super().__getitem__(self.names.index(key) if isinstance(key, str) else key)


class Cursor:
    def __init__(self, cursor):
        self.cursor = cursor
        try: self.names = [x[0] for x in cursor.get_description()]
        except Exception: self.names = []
    def __iter__(self):
        for values in self.cursor: yield Row(values, self.names)
    def fetchone(self): return next(iter(self), None)
    def fetchall(self): return list(self)
    def fetchmany(self, size):
        import itertools
        return list(itertools.islice(iter(self),size))


class Connection:
    def __init__(self, view): self.view = view
    def execute(self, sql, params=()):
        self.result=Cursor(self.view.db.execute(sql, params))
        return self.result
    def close(self): pass
    def cursor(self): return self
    def fetchone(self): return self.result.fetchone()
    def fetchall(self): return self.result.fetchall()
    def fetchmany(self,size): return self.result.fetchmany(size)


class Reader:
    def __init__(self, platform, progress=None):
        self.platform = platform
        self.progress = progress or (lambda stage: None)
        self.reader = None
        self.media = None
        self.media_at = 0
        self.media_stickers = None
        self.maps_at = 0
        self.nicks = {}
        self.idx = {}
        self.media_retries = None

    def labels(self, account, source, key):
        import sqlite3
        from contextlib import closing
        from chatlocal.config import DB
        from chatlocal.qq_labels import placeholder
        from qq_labels import directory,remember_peer
        self.buddy, self.group, self.names = {}, {}, {}
        self.label_conversations=set()
        with closing(sqlite3.connect(f'file:{DB}?mode=ro',uri=True)) as db:
            rows = db.execute("SELECT conversation_id,conversation,sender_id,sender FROM messages WHERE platform='qq' ORDER BY id")
            for cid, label, sender, name in rows:
                parts = cid.split(':', 2)
                if len(parts) != 3 or parts[0] != account or not parts[2].isdigit(): continue
                peer = int(parts[2])
                self.label_conversations.add(cid)
                if not placeholder(label,peer,parts[1]):
                    (self.names if parts[1] == 'group' else self.buddy)[peer] = label
                if parts[1]=='direct':remember_peer(self.buddy,peer,sender,name)
                if parts[1] == 'group' and sender.isdigit(): self.group[peer, int(sender)] = name
        # Read authenticated current directories, not an optional decrypted
        # cache which space-saving historical imports may have removed.
        buddy,names,self.label_status=directory(lambda name:self.open(source/name,key))
        self.buddy.update(buddy);self.names.update(names)
        self.maps_at = time.monotonic()

    def read(self, request):
        started = time.perf_counter()
        self.views = []
        self.request = request
        try:
            self.progress('runtime')
            if self.platform == 'qq': configure_qq_worker()
            payload, checkpoint = self.qq() if self.platform == 'qq' else self.wechat()
            # Reject the entire batch, including cursor, if a main DB changed.
            self.progress('source_fence')
            for view in self.views: view.view.assert_fresh()
            metrics = dict(seconds=round(time.perf_counter()-started, 4),
                bytes_read=sum(v.view.bytes_read for v in self.views),
                pages_decrypted=sum(v.view.decrypted for v in self.views),
                databases=len(self.views), candidates=len(payload['messages']))
            return dict(ok=True, payload=payload, checkpoint=checkpoint, metrics=metrics,
                observed_at=time.time())
        except Exception:
            self.maps_at=0;self.idx={}
            # A changing B-tree can make SQLite raise before the final fence.
            # Classify that as a retryable source change, not a broken reader.
            for view in self.views: view.view.assert_fresh()
            raise
        finally:
            for view in self.views: view.close()

    def open(self, path, key):
        view = ReadView(path, key, self.platform)
        self.views.append(view)
        return Connection(view)

    def qq(self):
        self.progress('cached_keys')
        import chatlog_keeper.qq_db as q
        from reader_media import QQMedia
        from qq_message_types import supported, is_reply_candidate
        meta = json.loads((DATA/'qq-snapshot-info.json').read_text(encoding='utf-8'))
        previous = self.request['checkpoint']
        if previous and previous['account'] != meta['account']: raise ValueError('account_changed')
        account = meta['account']
        key = q.load_cached_key_for_account(account) or q.load_cached_key()
        if not key: raise ValueError('key_unavailable')
        self.progress('open_pages')
        db = self.open(Path(meta['source'])/'nt_msg.db', key)
        self.progress('directory')
        refresh_labels=time.monotonic()-self.maps_at > 60
        if refresh_labels:self.labels(account,Path(meta['source']),key)
        if not hasattr(self,'own'):self.own=q._query_account_qq_number(db) or account
        own=self.own
        cursors = dict(previous.get('cursors', {}))
        messages = [];more=False
        wanted = self.request.get('conversations')
        seen = self.request.get('known', {})
        self.progress('messages')
        for spec in q._QQ_MESSAGE_TABLE_SPECS:
            columns = {r[1] for r in db.execute(f'PRAGMA table_info("{spec.table}")')}
            required = {'40001','40003','40027','40050','40090','40033','40800','40020','40011','40012',spec.conversation_column}
            if not required.issubset(columns): raise ValueError('schema_changed')
            # Skip-scan distinct peer keys: O(peers * log rows), not GROUP BY's
            # full index scan. Both queries use the client's existing index.
            peers = db.execute(f'''WITH RECURSIVE peers(x) AS (
              SELECT min("40027") FROM "{spec.table}" UNION ALL
              SELECT (SELECT min("40027") FROM "{spec.table}" WHERE "40027">x)
              FROM peers WHERE x IS NOT NULL) SELECT x FROM peers WHERE x IS NOT NULL''').fetchall()
            for peer, in peers:
                if len(messages)>=4000:more=True;continue
                top = db.execute(f'SELECT "40003",rowid,"{spec.conversation_column}","40050" FROM "{spec.table}" WHERE "40027"=? ORDER BY "40003" DESC,rowid DESC LIMIT 1', (peer,)).fetchone()
                if not top: continue
                cid = f'{account}:{spec.conversation_type}:{top[2]}'
                if wanted is not None and cid not in wanted: continue
                ck = spec.table + ':' + str(peer)
                prior = cursors.get(ck)
                current = [top[0], top[1]]
                if prior == current: continue
                if prior and top[0] < prior[0]: raise ValueError('sequence_reset')
                select = f'''SELECT "40001","40050","40090","40033","40800","{spec.conversation_column}",
                    {spec.table_rank},rowid,'{spec.conversation_type}',"40020","40012","40003","40011" FROM "{spec.table}"'''
                # At most 2,000 per peer/batch. A persisted partial cursor drains
                # bursts over subsequent passes instead of silently truncating.
                if prior:
                    rows = db.execute(select+' WHERE "40027"=? AND ("40003">? OR ("40003"=? AND rowid>?)) ORDER BY "40003",rowid LIMIT 2000', (peer, prior[0], prior[0], prior[1])).fetchall()
                else:
                    rows = db.execute(select+' WHERE "40027"=? ORDER BY "40003" DESC,rowid DESC LIMIT 256', (peer,)).fetchall() if top[3]>=self.request['since'] else []
                    rows = [r for r in reversed(rows) if r[1] >= self.request['since']]
                last = prior
                for row in rows:
                    last = [row[11],row[7]]
                    from chatlocal.artifact_metadata import qq_file
                    file=qq_file(q,row[4]) if row[12] in (3,7) else None
                    from chatlocal.voice_metadata import qq_voice,PLACEHOLDER
                    voice=qq_voice(q,row[4]) if row[12]==6 else None
                    if row[12]==6 and not voice:continue
                    if not supported(row[12],row[10]) or (row[12] in (3,7) and not file): continue
                    parsed=None
                    if row[10]!=1 and not file and not voice:
                        if self.media is None or self.media_stickers != self.request['load_stickers'] or time.monotonic()-self.media_at>30:
                            self.media = QQMedia(q, meta['source'], load_stickers=self.request['load_stickers'])
                            self.media_at=time.monotonic(); self.media_stickers=self.request['load_stickers']
                        parsed=self.media.parse(row[4],row[10],row[1])
                        if is_reply_candidate(row[12],row[10]) and not parsed[2]:continue
                    if spec.conversation_type=='direct':
                        from qq_labels import remember_peer
                        remember_peer(self.buddy,row[5],row[3],row[2])
                    rec = q._qq_message_page_record(tuple(row[:10]), account_id=account, conversation_id=str(row[5]),
                        buddy_map=self.buddy, group_map=self.group, group_name_map=self.names, recover_unreadable_rows=True)
                    if rec.get('decode_status') == 'unreadable': raise ValueError('decode_failed')
                    source_id = str(rec['msg_id']) if rec['msg_id'] is not None else rec['source_offset']
                    if source_id in seen.get(cid, []): continue
                    if file:text,media,reply='[文件] '+file['filename'],[],None
                    elif voice:text,media,reply=PLACEHOLDER,[],None
                    elif parsed is not None:text,media,reply=parsed
                    else: text,media,reply=rec['content'],[],None
                    if reply:
                        from reader_media import resolve_qq_quote
                        reply=resolve_qq_quote(db,spec,row[5],reply)
                    if not text and not media and not reply: continue
                    sender_id = str(rec['sender_qq']) if rec['sender_qq'] else rec['sender_uid']
                    messages.append(dict(platform='qq',conversation=rec['chat_name'],conversation_type=spec.conversation_type,
                        conversation_id=cid,sender=rec['sender_name'],sender_id=sender_id,timestamp=rec['ts'],
                        content=text,media=media,reply_to=reply,files=[file] if file else [],voice=voice,is_self=(sender_id==str(own)) if rec['sender_qq'] else None,
                        source_id=source_id,source_table=spec.table,source_rowid=row[7],source_offset=rec['source_offset']))
                cursors[ck] = last if prior and len(rows)==2000 else current
                if cursors[ck]!=current:more=True
        # Retry incomplete local attachments independently of native row cursors.
        if self.media is None or self.media_stickers!=self.request['load_stickers']:
            self.media=QQMedia(q,meta['source'],load_stickers=self.request['load_stickers'])
            self.media_at=time.monotonic();self.media_stickers=self.request['load_stickers']
        if self.media_retries is None:
            from live_media import QQMediaRetries
            self.media_retries=QQMediaRetries()
        recovered,_=self.media_retries.collect(self.media,account,wanted,
            exclude={(m['conversation_id'],m['source_id']) for m in messages})
        messages.extend(recovered)
        from qq_labels import conversation_labels
        label_ids=self.label_conversations|{m['conversation_id'] for m in messages} if refresh_labels else set()
        return dict(reader='qq-live-database',messages=messages,more=more,media_replays=len(recovered),
            label_account=account,conversation_labels=conversation_labels(account,self.buddy,self.names,label_ids),
            label_status=self.label_status), dict(account=account,cursors=cursors)

    def wechat(self):
        self.progress('cached_keys')
        from wechatauto.db import MSG_TYPE_NAMES
        from live_wechat import LiveWeChatDB
        from reader_media import WeChatMedia
        meta=json.loads((DATA/'wechat-snapshot-info.json').read_text(encoding='utf-8'))
        previous=self.request['checkpoint']
        if previous and previous['account']!=meta['account']: raise ValueError('account_changed')
        if self.reader is None or self.reader.account!=meta['account']:
            self.reader=LiveWeChatDB(db_dir=str(Path(meta['source_db']).parent.parent),account=meta['account'],
                workdir=str(DATA/'live/wechat'),keys_file=str(DATA/'wechat-reader/keys.json'))
        reader=self.reader
        self.progress('open_pages')
        rels=reader._message_dbs()
        opened={}
        def open_rel(rel):
            if rel not in opened:
                key=reader._keys.get(rel)
                if not key: raise ValueError('new_shard_key_unavailable')
                opened[rel]=self.open(reader._db_path(rel),key)
            return opened[rel]
        reader._open=open_rel
        # Cached directory labels, refreshed periodically and immediately on a
        # previously unknown table. Message sender IDs remain per-shard.
        self.progress('directory')
        if not self.idx or time.monotonic()-self.maps_at>60:
            self.idx=reader._build_md5_index(); self.nicks=reader._nickname_index(); self.maps_at=time.monotonic()
        cursors={};messages=[];chats={};more=False;pending_ack=False
        wanted=self.request.get('conversations')
        allowed={hashlib.md5(v.encode()).hexdigest() for v in wanted} if wanted is not None else None
        self.progress('messages')
        for rel in rels:
            db=open_rel(rel)
            identities=dict(db.execute('SELECT rowid,user_name FROM Name2Id'))
            # Name2Id also resolves recently created chats not yet in contacts.
            for user in identities.values():
                if user: self.idx.setdefault(hashlib.md5(user.encode()).hexdigest(),user)
            tables=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'").fetchall()
            for table, in tables:
                if not re.fullmatch('Msg_[a-fA-F0-9]{32}',table): raise ValueError('schema_changed')
                ck=rel.replace('\\','/')+'/'+table
                prior=previous.get('cursors',{}).get(ck)
                if len(messages)>=4000:
                    if prior:cursors[ck]=prior
                    more=True;continue
                try:top=boundary(db,table,'local_id',['server_id','create_time'],prior,allow_server_ack=True)
                except ValueError:
                    # Upload completion can replace an already nonzero WX file
                    # server ID. Replay only a locally verified acknowledgement;
                    # Store revalidates it in the same transaction as the cursor.
                    if not self.file_ack(reader,db,rel,table,prior,identities):raise
                    top=boundary(db,table,'local_id',['server_id','create_time'],None)
                    top['replay_from']=prior['last']-1
                replay=top.pop('replay_from',prior['last'] if prior else 0)
                cursors[ck]=top
                if allowed is not None and table[4:] not in allowed: continue
                user=self.idx.get(table[4:])
                if not user and top['last']:
                    self.idx.update(reader._build_md5_index());user=self.idx.get(table[4:])
                if not user:
                    if top['last'] and (not prior or top['last']>prior['last']): raise ValueError('conversation_unresolved')
                    continue
                if user.startswith('gh_') or user in {'weixin','filehelper','newsapp','brandsessionholder','foldedchat'}: continue
                condition='local_id>?' if prior else 'create_time>=?'
                value=replay if prior else self.request['since']
                sql=f'''SELECT local_id,local_type,server_id,real_sender_id,create_time,message_content,packed_info_data,sort_seq
                  FROM "{table}" WHERE {condition} AND local_id<=? ORDER BY local_id LIMIT 2000'''
                rows=db.execute(sql,(value,top['last'])).fetchall()
                pending=False
                processed=[]
                for row in rows:
                    base=row['local_type'] & 0xffffffff
                    if base in (1,3,34,47,49) and not row['server_id']:
                        # An outgoing row can appear before server acknowledgement.
                        # Do not invent a temporary live identity that becomes a
                        # duplicate when the server ID is assigned a moment later.
                        pending=True;pending_ack=True;more=True;break
                    processed.append(row)
                    if base not in (1,3,34,47,49): continue
                    message=reader._export_row(row,MSG_TYPE_NAMES)
                    sender=identities.get(row['real_sender_id'],'')
                    if user.endswith('@chatroom') and (not sender or sender==user):
                        prefix=message['content'].partition(':\n')[0]
                        if prefix in self.nicks: sender=prefix
                    message.update(chat=user,sender_username=sender,sender_name=self.nicks.get(sender,sender) or '未知发送者',
                        is_self=sender==reader.wxid if sender else None,source_db=rel.replace('\\','/'),source_table=table)
                    if base not in (1,34):
                        if self.media is None or self.media_stickers!=self.request['load_stickers']:
                            self.media=WeChatMedia(reader,meta,load_stickers=self.request['load_stickers'],live=True);self.media_stickers=self.request['load_stickers']
                        text,media,reply=self.media.parse(message)
                        if text is not None: message['text']=text
                        message.update(media=media,reply_to=reply)
                    messages.append(message);chats[user]=dict(username=user,name=self.nicks.get(user,user))
                if pending and not processed:
                    cursors[ck]=prior or dict(last=0,signature='')
                elif processed and (pending or len(rows)==2000 and rows[-1]['local_id']<top['last']):
                    end=processed[-1]
                    signature=hashlib.sha256(json.dumps([end['server_id'],end['create_time']],default=str).encode()).hexdigest()
                    cursors[ck]=dict(last=end['local_id'],signature=signature);more=True
        if set(previous.get('cursors',{}))-set(cursors): raise ValueError('shard_removed')
        # Messages may commit before their attachment files arrive. Retry a few
        # recent incomplete items even on a pass with no new native rows.
        self.progress('media_retry')
        if self.media is None or self.media_stickers!=self.request['load_stickers']:
            self.media=WeChatMedia(reader,meta,load_stickers=self.request['load_stickers'],live=True);self.media_stickers=self.request['load_stickers']
        if self.media_retries is None:
            from live_media import WeChatMediaRetries
            self.media_retries=WeChatMediaRetries()
        self.media.advance_index()
        recovered,recovered_chats=self.media_retries.collect(self.media,reader.wxid,wanted,
            exclude={(m['chat'],str(m.get('server_id'))) for m in messages})
        messages.extend(recovered)
        for user,chat in recovered_chats.items():chats.setdefault(user,chat)
        return dict(reader='wechatauto-replica',wxid=reader.wxid,chats=list(chats.values()),messages=messages,
            identity_method='per-shard Name2Id + verified group prefix',
            snapshot_at=datetime.now(timezone.utc).isoformat(),live=True,more=more,pending_ack=pending_ack,media_replays=len(recovered)),dict(account=meta['account'],cursors=cursors)

    def file_ack(self,reader,db,rel,table,prior,identities):
        if not prior or not prior.get('last'):return False
        from wechatauto.db import MSG_TYPE_NAMES
        from chatlocal.normalize import normalize
        from chatlocal.artifacts import is_file_ack
        from chatlocal.config import DB
        from contextlib import closing
        import sqlite3
        row=db.execute(f'''SELECT local_id,local_type,server_id,real_sender_id,create_time,message_content,packed_info_data,sort_seq
            FROM "{table}" WHERE local_id=?''',(prior['last'],)).fetchone()
        user=self.idx.get(table[4:])
        if not row or (row['local_type'] & 0xffffffff)!=49 or not user:return False
        if identities.get(row['real_sender_id'])!=reader.wxid:return False
        raw=reader._export_row(row,MSG_TYPE_NAMES)
        raw.update(chat=user,sender_username=reader.wxid,sender_name=self.nicks.get(reader.wxid,reader.wxid))
        message=normalize(raw,dict(wxid=reader.wxid,identity_method='per-shard',chats=[]),'wechatauto')
        if not message or not message.get('files'):return False
        locator=json.dumps([reader.wxid,rel.replace('\\','/'),table,prior['last']])
        with closing(sqlite3.connect(f'file:{DB}?mode=ro',uri=True)) as local:
            local.row_factory=sqlite3.Row
            old=local.execute('''SELECT m.* FROM messages m JOIN message_native_ids n ON n.message_id=m.id
                WHERE n.platform='wechat' AND n.conversation_id=? AND n.locator=?''',(user,locator)).fetchone()
            if not is_file_ack(local,old,message,message['files']):return False
            signature=hashlib.sha256(json.dumps([int(old['source_id']),old['timestamp']//1000]).encode()).hexdigest()
            return signature==prior['signature']


def main():
    output=sys.stdout
    def progress(stage):
        output.write(json.dumps(dict(progress=stage))+'\n');output.flush()
    reader=Reader(sys.argv[1],progress)
    for line in sys.stdin:
        try:
            request=json.loads(line)
            # Upstream diagnostics may contain paths: discard, never log/IPC.
            with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                result=reader.read(request)
        except Exception as exc:
            from chatlog_keeper.core._wal import WalValidationError
            code='source_busy' if isinstance(exc,(SourceBusy,WalValidationError)) else str(exc) if isinstance(exc,ValueError) and re.fullmatch('[a-z_]+',str(exc)) else 'reader_failed'
            result=dict(ok=False,error=code,error_type=type(exc).__name__)
        print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__': main()
