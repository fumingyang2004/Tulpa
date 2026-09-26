import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import jieba

from .config import DB, DATA, ROOT, local_path
from .normalize import TZ, decode_export, display_time, normalize, stable_key
from .media import hydrate,import_media,is_sticker,media_at,STICKER_PLACEHOLDER

jieba.dt.tmp_dir = str(ROOT / '.cache')
jieba.setLogLevel(30)


def tokens(text):
    return ' '.join(t.lower() for t in jieba.cut_for_search(text) if t.strip())


class Store:
    def __init__(self, path=DB):
        self.path = local_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS sources(
                hash TEXT PRIMARY KEY, filename TEXT NOT NULL, archive TEXT NOT NULL,
                format TEXT NOT NULL, imported_at TEXT NOT NULL, total INTEGER, skipped INTEGER);
              CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY, dedup_key TEXT NOT NULL UNIQUE,
                platform TEXT NOT NULL CHECK(platform IN ('qq','wechat')),
                conversation_id TEXT NOT NULL, conversation TEXT NOT NULL,
                sender_id TEXT NOT NULL, sender TEXT NOT NULL, timestamp INTEGER NOT NULL,
                content TEXT NOT NULL, is_self INTEGER CHECK(is_self IN (0,1)),
                source_id TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS msg_timeline ON messages(platform,conversation_id,timestamp,id);
              CREATE INDEX IF NOT EXISTS msg_time ON messages(timestamp);
              CREATE INDEX IF NOT EXISTS msg_sender ON messages(platform,sender_id);
              CREATE TABLE IF NOT EXISTS provenance(
                message_id INTEGER REFERENCES messages(id), source_hash TEXT REFERENCES sources(hash),
                pointer TEXT NOT NULL, PRIMARY KEY(message_id,source_hash,pointer));
              CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(terms, tokenize='unicode61');
            ''')
            if 'conversation_type' not in {r[1] for r in db.execute('PRAGMA table_info(messages)')}:
                db.execute("ALTER TABLE messages ADD COLUMN conversation_type TEXT NOT NULL DEFAULT 'unknown'")
            columns={r[1] for r in db.execute('PRAGMA table_info(messages)')}
            if 'media_json' not in columns:db.execute("ALTER TABLE messages ADD COLUMN media_json TEXT NOT NULL DEFAULT '[]'")
            if 'reply_to' not in columns:db.execute('ALTER TABLE messages ADD COLUMN reply_to TEXT')
            from .reply_history import initialize as initialize_reply_history
            initialize_reply_history(db)
            if 'original_content' not in columns:db.execute('ALTER TABLE messages ADD COLUMN original_content TEXT')
            from .sync_state import initialize
            initialize(db)
            from .import_scope import initialize as initialize_scope
            initialize_scope(db)
            from .live_state import initialize as initialize_live
            initialize_live(db)
            from .artifacts import initialize as initialize_artifacts
            initialize_artifacts(db)
            from .voice import initialize as initialize_voice
            initialize_voice(db)
            from .group_knowledge import initialize as initialize_knowledge
            initialize_knowledge(db)
            from .collections import initialize as initialize_collections
            initialize_collections(db)
            from .changes import initialize as initialize_changes
            initialize_changes(db)
            from .workspaces import initialize as initialize_workspaces
            initialize_workspaces(db)
            from .replies import initialize as initialize_replies
            initialize_replies(db)
            from .group_admin import initialize as initialize_group_admin
            initialize_group_admin(db)
            from .import_pipeline import initialize as initialize_imports
            initialize_imports(db)
            from .tulpa import initialize as initialize_tulpa
            initialize_tulpa(db)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def import_file(self, path, platform='', conversation='', self_ids='',load_stickers=True, *, sync=None, live=None, discovery=False, batch=None, complete_job=None):
        if not isinstance(load_stickers,bool):raise ValueError('加载表情包必须为开或关')
        path = local_path(path)
        if path.suffix.lower() not in ('.json', '.jsonl', '.ndjson'):
            raise ValueError('请选择 .json 或统一格式 .jsonl 文件')
        if path.stat().st_size > 100*1024*1024:
            raise ValueError('MVP 单文件上限 100 MB，请按会话/时间拆分。')
        original = path.read_bytes()
        if len(original) > 100*1024*1024:
            raise ValueError('MVP 单文件上限 100 MB，请按会话/时间拆分。')
        digest = hashlib.sha256(original).hexdigest()
        if batch is not None:
            with self.connect() as db:
                saved=db.execute('SELECT result FROM import_batches WHERE token=?',(batch['token'],)).fetchone()
            if saved:
                result=json.loads(saved[0])
                if result['source_hash']!=digest:raise ValueError('已提交批次内容变化，拒绝重复使用进度。')
                return result
        archive_dir = self.path.parent / 'sources'
        archive_dir.mkdir(exist_ok=True)
        archive = archive_dir / (digest + path.suffix.lower())
        if archive.exists():
            if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
                raise ValueError('原始归档校验失败，请核对本地 sources 文件；未导入。')
        else:
            archive.write_bytes(original)
        # Parse precisely the bytes we archived; a concurrently refreshed export
        # must never detach normalized messages from their provenance hash.
        head, records, kind = decode_export(archive)
        parsed, skipped, seen = [], 0, Counter()
        native_locators={}
        for pointer, raw in records:
            try:
                message = normalize(raw, head, kind, platform, conversation, self_ids)
            except (ValueError, TypeError, OverflowError) as error:
                raise ValueError(f'{path.name} {pointer}: {error}；整份文件未导入') from None
            if message is None:
                skipped += 1
                continue
            if batch is not None and batch.get('platform') and message['platform']!=batch['platform']:
                raise ValueError('分批文件平台与读取任务不匹配；本批未导入。')
            if sync is not None and message['platform']!=sync['platform']:
                raise ValueError('增量文件的平台与同步进度不匹配；未导入。')
            if live is not None and message['platform']!=live['platform']:
                raise ValueError('实时消息平台不匹配；未导入。')
            fingerprint = stable_key(message)
            key = stable_key(message, seen[fingerprint])
            seen[fingerprint] += 1
            parsed.append((key, pointer, message))
            if kind=='wechatauto' and head.get('identity_method') and raw.get('source_db') and raw.get('source_table') and raw.get('local_id'):
                native_locators[pointer]=json.dumps([head.get('wxid'),str(raw['source_db']).replace('\\','/'),raw['source_table'],int(raw['local_id'])])
        added, duplicate, enriched, placeholders = 0, 0, 0, 0
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?,?,?)',
                       (digest, path.name, str(archive.relative_to(ROOT)), kind,
                       datetime.now(TZ).isoformat(), len(records), skipped))
            next_id=max(db.execute('SELECT value FROM message_counter WHERE id=1').fetchone()[0],
                        db.execute('SELECT coalesce(max(id),0) FROM messages').fetchone()[0])
            for key, pointer, message in parsed:
                locator=native_locators.get(pointer)
                native=(message['platform'],message['conversation_id'],locator)
                # Automatic overlap/reconciliation must not resurrect records
                # the user removed. Explicit historical import may restore them.
                if sync is not None or live is not None or discovery:
                    if db.execute('SELECT 1 FROM deleted_message_keys WHERE dedup_key=?',(key,)).fetchone():
                        skipped+=1
                        continue
                    if locator and db.execute('SELECT 1 FROM deleted_native_ids WHERE platform=? AND conversation_id=? AND locator=?',native).fetchone():
                        skipped+=1
                        continue
                else:
                    db.execute('DELETE FROM deleted_message_keys WHERE dedup_key=?',(key,))
                    if locator:db.execute('DELETE FROM deleted_native_ids WHERE platform=? AND conversation_id=? AND locator=?',native)
                message=dict(message)
                files=message.pop('files',[])
                voice=message.pop('voice',None)
                source_content=message['content']
                old = db.execute('SELECT * FROM messages WHERE dedup_key=?', (key,)).fetchone()
                if locator:
                    prior=db.execute('SELECT m.* FROM messages m JOIN message_native_ids n ON n.message_id=m.id WHERE n.platform=? AND n.conversation_id=? AND n.locator=?',native).fetchone()
                    # Legacy exports may predate native aliases. Match their
                    # exact local-ID/time fallback, never names or text alone.
                    legacy_key=stable_key(dict(message,source_id=f"local:{json.loads(locator)[3]}:{message['timestamp']}"))
                    prior=prior or db.execute('SELECT * FROM messages WHERE dedup_key=?',(legacy_key,)).fetchone()
                    if (sync is not None or live is not None or discovery) and db.execute('SELECT 1 FROM deleted_message_keys WHERE dedup_key=?',(legacy_key,)).fetchone():
                        skipped+=1
                        continue
                    if prior and old and prior['id']!=old['id']:raise ValueError('同一微信本地消息已有两个身份，请核对旧导入；本批未写入。')
                    if prior and not old:
                        from .artifacts import is_file_ack
                        file_ack=is_file_ack(db,prior,message,files)
                        if not file_ack and (not prior['source_id'].startswith('local:') or not message['source_id'].isdigit() or int(message['source_id'])<=0):
                            raise ValueError('微信本地消息身份发生冲突；本批未写入。')
                        # Content/time validation below is in this transaction;
                        # a failed acknowledgement rolls the key update back.
                        db.execute('UPDATE messages SET dedup_key=?,source_id=? WHERE id=?',(key,message['source_id'],prior['id']))
                        old=db.execute('SELECT * FROM messages WHERE id=?',(prior['id'],)).fetchone()
                entries=message.pop('media',[])
                # A newer export can lose the local file and label an old GIF
                # as an unavailable generic image. Retain known classification.
                if old and not load_stickers and isinstance(entries,list):
                    previous=json.loads(old['media_json'])
                    entries=[dict(item) if isinstance(item,dict) else item for item in entries]
                    for slot,item in enumerate(entries):
                        if not isinstance(item,dict) or item.get('status')=='available':continue
                        metadata=item.get('metadata') if isinstance(item.get('metadata'),dict) else {}
                        md5=metadata.get('md5')
                        prior=next((p for p in previous if md5 and p.get('metadata',{}).get('md5')==md5),None) if md5 else media_at(previous,slot)
                        if prior and is_sticker(prior):item.update(kind=prior['kind'],animated=prior.get('animated',False))
                imported_media=import_media(entries,load_stickers=load_stickers)
                omitted=any(item['status']=='omitted' for item in imported_media)
                media=[dict(item,source_index=i) for i,item in enumerate(imported_media) if item['status']!='omitted']
                message['original_content']=source_content if omitted else None
                if omitted:
                    message['content']=(source_content+'\n' if source_content else '')+STICKER_PLACEHOLDER
                    placeholders+=1
                message['media_json']=json.dumps(media,ensure_ascii=False)
                message['reply_to']=json.dumps(message.get('reply_to'),ensure_ascii=False) if message.get('reply_to') else None
                if old:
                    old_source=old['original_content'] if old['original_content'] is not None else old['content']
                    # NTQQ exposes an outgoing row before server acknowledgement;
                    # the same native UID can receive its server timestamp later.
                    # Permit only the observed narrow case: known live outgoing
                    # QQ row, identical text and <=120s clock adjustment. Retain
                    # both source hashes; arbitrary ID/content conflicts still fail.
                    acknowledged=(live is not None or sync is not None or discovery) and message['platform']=='qq' and old['is_self']==1 and message['is_self']==1 and old_source==source_content and abs(old['timestamp']-message['timestamp'])<=120000 and db.execute('SELECT 1 FROM live_events WHERE message_id=?',(old['id'],)).fetchone()
                    if old['timestamp']!=message['timestamp'] and acknowledged:
                        db.execute('UPDATE messages SET timestamp=? WHERE id=?',(message['timestamp'],old['id']))
                    elif old['timestamp']!=message['timestamp'] or old_source!=source_content:
                        raise ValueError(f'{pointer}: 原始 ID 与已导入消息冲突；整份文件已回滚，请核对会话 ID。')
                    duplicate += 1
                    mid = old['id']
                    # Recovered files can enrich a previously missing attachment;
                    # an older/text-only export must never erase recovered media.
                    if imported_media:
                        previous=json.loads(old['media_json'])
                        for i,item in enumerate(media):
                            # Omitted elements change array positions. Match resource
                            # identity first; positional fallback only for full imports.
                            resource=item.get('metadata',{}).get('md5')
                            match=next((p for p in previous if resource and p.get('metadata',{}).get('md5')==resource),None)
                            if match is None and not resource:match=media_at(previous,item['source_index'])
                            if match and item['status']!='available' and match.get('status')=='available':media[i]=dict(match,source_index=item['source_index'])
                        db.execute('UPDATE messages SET content=?,original_content=?,media_json=? WHERE id=?',
                            (message['content'],message['original_content'],json.dumps(media,ensure_ascii=False),mid))
                        if old['content']!=message['content']:
                            db.execute('UPDATE message_fts SET terms=? WHERE rowid=?',(tokens(message['content']),mid))
                    if message['reply_to']:
                        quoted=json.loads(message['reply_to'])
                        prior_quote=json.loads(old['reply_to'] or '{}')
                        if prior_quote.get('source_id'):
                            if quoted.get('source_id') and quoted['source_id']!=prior_quote['source_id']:
                                raise ValueError(f'{pointer}: 引用消息身份冲突；本批未写入。')
                            # An older export/media retry may contain only a preview.
                            # Preserve the verified reference instead of downgrading it.
                            if not quoted.get('source_id'):quoted=prior_quote
                        db.execute('UPDATE messages SET reply_to=? WHERE id=?',
                            (json.dumps(quoted,ensure_ascii=False),mid))
                    if old['is_self'] is None and message['is_self'] is not None:
                        db.execute('UPDATE messages SET is_self=? WHERE id=?', (message['is_self'], mid))
                        enriched += 1
                    if old['conversation_type']=='unknown' and message['conversation_type']!='unknown':
                        db.execute('UPDATE messages SET conversation_type=? WHERE id=?',(message['conversation_type'],mid))
                else:
                    next_id+=1
                    message['id']=next_id
                    columns = list(message)
                    cur = db.execute(f"INSERT INTO messages(dedup_key,{','.join(columns)}) VALUES({','.join('?' for _ in range(len(columns)+1))})",
                                     [key] + [message[c] for c in columns])
                    mid = cur.lastrowid
                    db.execute('INSERT INTO message_fts(rowid,terms) VALUES(?,?)',
                               (mid, tokens(message['content'])))
                    added += 1
                db.execute('INSERT OR IGNORE INTO provenance VALUES(?,?,?)', (mid, digest, pointer))
                if files:
                    from .artifacts import register_message
                    register_message(db,message,mid,files)
                if voice:
                    from .voice import register,reindex
                    register(db,mid,voice,is_new=old is None,automatic=sync is not None or live is not None or discovery)
                    reindex(db,mid)
                if locator:db.execute('INSERT OR IGNORE INTO message_native_ids VALUES(?,?,?,?)',(*native,mid))
                if live is not None and not old:
                    from .live_state import record
                    record(db,mid,live)
                if not old and (live is not None or sync is not None or discovery):
                    from .tulpa import collect
                    collect(db,mid)
            db.execute('UPDATE message_counter SET value=? WHERE id=1',(next_id,))
            from .qq_labels import payload_labels,apply_labels
            labels_updated=payload_labels(db,head)
            # Newer imports may resolve a previously unnamed conversation even
            # when every native message ID already exists (including QCE JSON).
            by_account={}
            for _,_,message in parsed:
                if message['platform']=='qq' and ':' in message['conversation_id']:
                    by_account.setdefault(message['conversation_id'].split(':',1)[0],[]).append(message)
            for account,items in by_account.items():labels_updated+=apply_labels(db,account,items)
            if sync is not None:
                from .sync_state import commit_checkpoint
                commit_checkpoint(db,sync,sync.get('added',added))
            if live is not None:
                from .live_state import commit
                commit(db,live,added,next_id)
            if complete_job:
                from .import_pipeline import complete_job as finish_job
                finish_job(db,complete_job)
            if batch is not None:
                receipt=dict(imported=added,duplicate=duplicate,enriched=enriched,skipped=skipped,total=len(records),
                    unknown_self=sum(m['is_self'] is None for _,_,m in parsed),source_hash=digest,labels_updated=labels_updated)
                db.execute('INSERT INTO import_batches(token,job,result) VALUES(?,?,?)',
                    (batch['token'],batch['job'],json.dumps(receipt)))
        unknown = sum(m['is_self'] is None for _, _, m in parsed)
        if live is not None:
            # Observability uses the return from the successful SQLite commit,
            # not the earlier INSERT execution. Diagnostic timestamps must not
            # turn a committed batch into an apparent ingestion failure.
            import time
            committed_at=time.time()
            try:
                with self.connect() as db:
                    db.execute('UPDATE live_events SET ingested_at=? WHERE source=? AND observed_at=?',
                        (committed_at,live['platform']+'_database',live['observed_at']))
                    db.execute('UPDATE live_state SET last_commit=? WHERE platform=?',(committed_at,live['platform']))
            except sqlite3.Error:pass
        result = dict(file=path.name, format=kind, total=len(records), imported=added,
                    duplicate=duplicate, enriched=enriched, skipped=skipped, unknown_self=unknown,
                    sticker_placeholders=placeholders,load_stickers=load_stickers,
                    source_hash=digest,labels_updated=labels_updated,
                    warning='本人身份未知的消息不会被当成本人承诺。' if unknown else '')
        if head.get('reader') == 'chatlog-keeper':
            from .qq_storage import release_qq_cache
            cleanup = release_qq_cache(self, digest)
            if cleanup is not None:
                result['cache_cleanup'] = cleanup
        return result

    def stats(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('''SELECT platform,count(*) AS messages,
              count(DISTINCT conversation_id) AS conversations,min(timestamp) AS first,
              max(timestamp) AS last,sum(is_self IS NULL) AS unknown_self,
              sum(media_json!='[]') AS media_messages,
              sum(original_content IS NOT NULL) AS sticker_placeholders FROM messages GROUP BY platform''')]

    def conversations(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('''SELECT platform,conversation_id,
              max(conversation) AS conversation,count(*) AS count FROM messages
              GROUP BY platform,conversation_id ORDER BY platform,conversation''')]

    def latest_wechat_coverage(self):
        """Coverage of the latest successfully imported export, not a pending file."""
        with self.connect() as db:
            rows=db.execute("SELECT archive FROM sources WHERE format='wechatauto' ORDER BY imported_at DESC").fetchall()
        for row in rows:
            try:
                payload=json.loads(local_path(row['archive']).read_text(encoding='utf-8-sig'))
                coverage=payload.get('coverage')
                if isinstance(coverage,dict):return coverage
            except (OSError,ValueError):continue
        return None

    def latest_qq_coverage(self):
        """Settings of the last imported automatic QQ export, not a pending file."""
        with self.connect() as db:
            row=db.execute("SELECT archive FROM sources WHERE filename='qq-real.json' AND format='canonical' ORDER BY imported_at DESC LIMIT 1").fetchone()
        if not row:return None
        try:
            payload=json.loads(local_path(row['archive']).read_text(encoding='utf-8-sig'))
            if payload.get('reader')!='chatlog-keeper':return None
            from .read_options import read_options
            checked=read_options(payload.get('days'),payload.get('per_chat'))
            result=dict(days=checked['qq_days'],per_chat=checked['qq_per_chat'],
                media_window=payload.get('media_window',checked['qq_per_chat']))
            scope=payload.get('read_scope')
            if isinstance(scope,dict):result['read_scope']=scope
            if payload.get('all_messages'):result['all_messages']=True
            return result
        except (OSError,ValueError,TypeError,AttributeError):
            return None

    def provenance(self, mid):
        with self.connect() as db:
            return [dict(r) for r in db.execute('''SELECT s.filename,s.archive,s.hash,p.pointer
              FROM provenance p JOIN sources s ON s.hash=p.source_hash WHERE p.message_id=?''', (mid,))]

    def context(self, mid, radius=3, start=None, end=None, max_id=None):
        with self.connect() as db:
            row = db.execute('SELECT * FROM messages WHERE id=?', (mid,)).fetchone()
            if not row:
                return []
            clauses, args = ['platform=?', 'conversation_id=?'], [row['platform'], row['conversation_id']]
            if max_id is not None:
                clauses.append('id<=?');args.append(max_id)
            if start is not None:
                clauses.append('timestamp>=?'); args.append(start)
            if end is not None:
                clauses.append('timestamp<?'); args.append(end)
            where = ' AND '.join(clauses)
            before = db.execute(f'''SELECT * FROM messages WHERE {where} AND
                (timestamp,id)<(?,?) ORDER BY timestamp DESC,id DESC LIMIT ?''',
                                args + [row['timestamp'], mid, radius]).fetchall()
            after = db.execute(f'''SELECT * FROM messages WHERE {where} AND
                (timestamp,id)>(?,?) ORDER BY timestamp,id LIMIT ?''',
                               args + [row['timestamp'], mid, radius]).fetchall()
            return [hydrate(r) for r in reversed(before)] + [hydrate(row)] + [hydrate(r) for r in after]

    def message(self,mid):
        with self.connect() as db:
            row=db.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        return hydrate(row) if row else None
