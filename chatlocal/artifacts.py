"""Local artifact inventory, immutable content cache and retained text index."""
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .config import ROOT, DATA
from .normalize import display_time

MAX_BYTES = 32 * 1024 * 1024
MAX_CONFIRMED_BYTES = 100 * 1024 * 1024
TEXT_TYPES = {'txt','md','csv','json','py','js','ts','c','h','cpp','java','rs','go','yaml','yml','toml','log'}
SUPPORTED = TEXT_TYPES | {'pdf','docx','pptx','xlsx'}
_lock = threading.RLock()


class ArtifactError(ValueError):
    pass


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS artifacts(
        sha256 TEXT PRIMARY KEY, size INTEGER NOT NULL, mime_type TEXT NOT NULL,
        local_path TEXT, cache_status TEXT NOT NULL DEFAULT 'CACHED',
        parse_status TEXT NOT NULL DEFAULT 'PENDING', parser TEXT, parse_note TEXT,
        created_at REAL NOT NULL, last_accessed_at REAL NOT NULL,
        user_pinned INTEGER NOT NULL DEFAULT 0, referenced_by_answer INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS artifact_sources(
        id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT NOT NULL UNIQUE,
        platform TEXT NOT NULL, source_type TEXT NOT NULL, conversation_id TEXT NOT NULL,
        conversation TEXT NOT NULL, sender TEXT NOT NULL, sender_id TEXT NOT NULL DEFAULT '',
        message_id INTEGER, original_message_id TEXT, file_id TEXT, busid TEXT, folder TEXT DEFAULT '/',
        filename TEXT NOT NULL, extension TEXT NOT NULL, size INTEGER, timestamp INTEGER,
        metadata_json TEXT NOT NULL DEFAULT '{}', availability TEXT NOT NULL DEFAULT 'METADATA_ONLY',
        remote_status TEXT NOT NULL DEFAULT 'unknown', sha256 TEXT REFERENCES artifacts(sha256),
        discovered_at REAL NOT NULL, last_seen_at REAL NOT NULL);
      CREATE INDEX IF NOT EXISTS artifact_source_scope ON artifact_sources(platform,conversation_id,timestamp);
      CREATE INDEX IF NOT EXISTS artifact_source_message ON artifact_sources(message_id);
      CREATE TABLE IF NOT EXISTS artifact_chunks(
        id INTEGER PRIMARY KEY AUTOINCREMENT, sha256 TEXT NOT NULL REFERENCES artifacts(sha256),
        ordinal INTEGER NOT NULL, text TEXT NOT NULL, locator TEXT NOT NULL,
        UNIQUE(sha256,ordinal));
      CREATE VIRTUAL TABLE IF NOT EXISTS artifact_fts USING fts5(terms, tokenize='unicode61');
      CREATE TABLE IF NOT EXISTS artifact_inventory(
        platform TEXT NOT NULL, conversation_id TEXT NOT NULL, conversation TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', last_success REAL, last_attempt REAL,
        detail TEXT NOT NULL DEFAULT '', PRIMARY KEY(platform,conversation_id));
    ''')


def register_message(db, message, mid, files):
    from .artifact_metadata import clean_descriptor
    existing=list(db.execute('SELECT source_key FROM artifact_sources WHERE message_id=? ORDER BY id',(mid,)))
    for index, value in enumerate(files):
        d = clean_descriptor(value)
        if not d: continue
        now=time.time()
        db.execute('''INSERT INTO artifact_sources(source_key,platform,source_type,conversation_id,conversation,
          sender,sender_id,message_id,original_message_id,filename,extension,size,timestamp,metadata_json,discovered_at,last_seen_at)
          VALUES(?,?,'message',?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET
          message_id=excluded.message_id,original_message_id=excluded.original_message_id,
          metadata_json=excluded.metadata_json,last_seen_at=excluded.last_seen_at''',
          (existing[index][0] if index<len(existing) else f"message-local:{mid}:{index}",message['platform'],
           message['conversation_id'],message['conversation'],message['sender'],message['sender_id'],mid,message['source_id'],
           d['filename'],d['extension'],d.get('size'),message['timestamp'],json.dumps(d,ensure_ascii=False),now,now))


def is_file_ack(db, prior, message, files):
    """Observed WX outgoing FileMessage rewrites a nonzero server ID after upload.

    Only an already-live row with the exact native locator (checked by caller),
    owner, timestamp, text, filename, size and MD5 can acquire the final ID.
    """
    if not prior or message['platform']!='wechat' or prior['is_self']!=1 or message['is_self']!=1:
        return False
    if (prior['sender_id']!=message['sender_id'] or prior['timestamp']!=message['timestamp'] or
        prior['content']!=message['content'] or not str(message['source_id']).isdigit() or int(message['source_id'])<=0):
        return False
    if not db.execute('SELECT 1 FROM live_events WHERE message_id=?',(prior['id'],)).fetchone():return False
    before=[json.loads(r[0]) for r in db.execute('SELECT metadata_json FROM artifact_sources WHERE message_id=? ORDER BY id',(prior['id'],))]
    if not files or len(before)!=len(files):return False
    return all(old.get('md5') and old.get('size') is not None and
        all(old.get(k)==new.get(k) for k in ('filename','extension','size','md5')) for old,new in zip(before,files))


def source_scope(plan=None, filters=None):
    from .retrieval import date_bound
    f=filters or {};where=['1=1'];args=[]
    platforms=plan.platforms if plan else ['qq','wechat']
    where.append('s.platform IN ('+','.join('?' for _ in platforms)+')');args+=platforms
    if plan and plan.snapshot_max_id is not None:
        where.append('(s.message_id IS NULL OR s.message_id<=?)');args.append(plan.snapshot_max_id)
    if plan and plan.conversations:
        parts=[]
        for selection in plan.conversations:
            platform,cid=json.loads(selection)
            parts.append('(s.platform=? AND s.conversation_id=?)');args.extend([platform,cid])
        where.append('('+' OR '.join(parts)+')')
    for key in ('platform','conversation_id','extension'):
        if f.get(key):where.append('s.'+key+'=?');args.append(f[key].lower() if key=='extension' else f[key])
    if f.get('source_id'):where.append('s.id=?');args.append(f['source_id'])
    if f.get('sender'):where.append('(instr(lower(s.sender),lower(?))>0 OR s.sender_id=?)');args.extend([f['sender']]*2)
    for value,op in [(getattr(plan,'start',None),'>='),(getattr(plan,'end',None),'<'),
                     (date_bound(f['start'],False) if f.get('start') else None,'>='),
                     (date_bound(f['end'],True) if f.get('end') else None,'<')]:
        if value is not None:where.append('s.timestamp'+op+'?');args.append(value)
    return ' AND '.join(where),args


class Artifacts:
    def __init__(self, store):
        self.store=store
        self.root=store.path.parent/'artifacts'
        self.root.mkdir(exist_ok=True)

    def get(self, source_id, plan=None):
        where,args=source_scope(plan)
        with self.store.connect() as db:
            row=db.execute('''SELECT s.*,a.cache_status,a.parse_status,a.parser,a.parse_note,a.local_path,
                a.user_pinned,a.referenced_by_answer FROM artifact_sources s LEFT JOIN artifacts a ON s.sha256=a.sha256
                WHERE s.id=? AND '''+where,[source_id,*args]).fetchone()
        if not row:raise ArtifactError('文件不存在或不在本次允许范围内。')
        return dict(row)

    def public(self,row):
        result={k:row.get(k) for k in ('id','platform','source_type','conversation_id','conversation','sender','sender_id',
            'message_id','filename','extension','size','timestamp','folder','availability','remote_status','sha256',
            'cache_status','parse_status','parser','parse_note','user_pinned','referenced_by_answer')}
        result['time']=display_time(row['timestamp']) if row.get('timestamp') else '时间未提供'
        result['citation_id']=f"F{row['id']}"
        result['url']=f"/api/artifacts/{row['id']}/content"
        return result

    def search(self,filters=None,plan=None,body=False):
        from .store import tokens
        f=filters or {};where,args=source_scope(plan,f)
        query=f.get('query','').strip()
        limit=max(1,min(int(f.get('limit',12)),40));offset=max(0,int(f.get('offset',0)))
        join=' LEFT JOIN artifacts a ON a.sha256=s.sha256 '
        fields='s.*,a.cache_status,a.parse_status,a.parser,a.parse_note,a.user_pinned,a.referenced_by_answer'
        if body:
            join+=' JOIN artifact_chunks c ON c.sha256=s.sha256 '
            fields+=',c.id chunk_id,c.ordinal,c.text,c.locator'
            if query:
                words=list(dict.fromkeys(tokens(query).split()))[:16]
                expression=' OR '.join('"'+w.replace('"','""')+'"' for w in words)
                if not expression:return dict(files=[],has_more=False,match_count=0)
                where+=' AND c.id IN (SELECT rowid FROM artifact_fts WHERE artifact_fts MATCH ?)';args.append(expression)
        elif query:
            words=query.split()[:12]
            where+=' AND ('+' OR '.join('instr(lower(s.filename),lower(?))>0' for _ in words)+')';args+=words
        with self.store.connect() as db:
            count=db.execute('SELECT count(*) FROM artifact_sources s'+join+' WHERE '+where,args).fetchone()[0]
            rows=db.execute('SELECT '+fields+' FROM artifact_sources s'+join+' WHERE '+where+
                ' ORDER BY s.timestamp DESC,s.id DESC'+(',c.ordinal' if body else '')+' LIMIT ? OFFSET ?',args+[limit,offset]).fetchall()
        items=[]
        for r in rows:
            row=dict(r);item=self.public(row)
            if body:item.update(citation_id=f"F{row['id']}:C{row['chunk_id']}",chunk_id=row['chunk_id'],
                ordinal=row['ordinal'],text=row['text'],locator=json.loads(row['locator']),evidence_kind='native_text')
            else:item['evidence_kind']='file_metadata'
            items.append(item)
        return dict(files=items,match_count=count,has_more=offset+len(items)<count,
            next_offset=offset+len(items) if offset+len(items)<count else None,
            note='正文搜索只覆盖已解析文件。元数据不证明正文内容；新文件需按需prepare_file后读取。' if body else
            '只返回文件元数据，未下载或解析正文。QQ消息附件不是完整群目录。')

    def _roots(self,platform):
        try:
            meta=json.loads((DATA/('qq-snapshot-info.json' if platform=='qq' else 'wechat-snapshot-info.json')).read_text('utf-8'))
            account_root=Path(meta['source']).parent if platform=='qq' else Path(meta['source_db']).parent
            return [account_root/'nt_data/File'] if platform=='qq' else [account_root/'msg/file']
        except (OSError,KeyError,ValueError):return []

    def local_candidate(self,row,*,binary=False):
        meta=json.loads(row['metadata_json']);roots=[p.resolve() for p in self._roots(row['platform'])]
        candidates=[]
        # Directory searches stay in client attachment roots. An exact native
        # QQ path may be elsewhere (e.g. Downloads), but must name this file and
        # pass its size + MD5 checks; never scan its containing directory.
        raw=meta.get('local_path','').replace('::NTOSFull::','')
        if raw and not raw.startswith(('\\\\','//')):
            path=Path(raw).resolve()
            native=(row['platform']=='qq' and (binary or row['extension'] in SUPPORTED) and bool(meta.get('md5')) and
                    row['size'] is not None and path.name==row['filename'] and not path.is_relative_to(ROOT))
            if (any(path.is_relative_to(root) for root in roots) or native) and path.is_file():candidates.append(path)
        for root in roots:
            if not root.is_dir():continue
            for path in root.rglob('*'):
                if path.name==row['filename'] and path.is_file() and path.resolve().is_relative_to(root):candidates.append(path.resolve())
        candidates=list(dict.fromkeys(candidates))
        if row['size'] is not None:candidates=[p for p in candidates if p.stat().st_size==row['size']]
        if meta.get('md5'):
            matched=[]
            for p in candidates:
                if p.stat().st_size<=MAX_CONFIRMED_BYTES:
                    with p.open('rb') as h:
                        if hashlib.file_digest(h,'md5').hexdigest()==meta['md5']:matched.append(p)
            candidates=matched
        # A filename collision must never silently attach another person's document.
        if len(candidates)>1:
            digests={}
            for p in candidates:
                if p.stat().st_size>MAX_CONFIRMED_BYTES:raise ArtifactError('同名文件无法在大小限制内核验。')
                with p.open('rb') as h:digests.setdefault(hashlib.file_digest(h,'sha256').hexdigest(),p)
            if len(digests)!=1:raise ArtifactError('存在多个不同内容的同名文件，无法可靠定位；请先核对来源。')
            candidates=list(digests.values())
        return candidates[0] if candidates else None

    def locate(self,sid):
        row=self.get(sid)
        if row['local_path'] and self.cache_path(row).is_file():return self.public(row)
        path=self.local_candidate(row)
        state='AVAILABLE_LOCAL' if path else 'METADATA_ONLY'
        with self.store.connect() as db:db.execute('UPDATE artifact_sources SET availability=? WHERE id=?',(state,sid))
        return self.public(self.get(sid))

    def cache_path(self,row):
        if not row.get('local_path'):return self.root/'absent'
        path=(ROOT/row['local_path']).resolve()
        if not path.is_relative_to(self.root.resolve()):raise ArtifactError('缓存路径无效。')
        return path

    def materialize(self,sid,*,confirmed=False,binary=False,remote=True):
        with _lock:
            row=self.get(sid)
            if not binary and row['extension'] not in SUPPORTED:raise ArtifactError('V1仅登记此类型，不下载、执行或解压。')
            cap=MAX_CONFIRMED_BYTES if confirmed else MAX_BYTES
            if row['size'] is not None and row['size']>cap:raise ArtifactError('文件超过32 MiB默认限制；可在文件页确认扩大至100 MiB。')
            path=self.cache_path(row)
            if row['local_path'] and path.is_file():
                with self.store.connect() as db:db.execute('UPDATE artifacts SET last_accessed_at=? WHERE sha256=?',(time.time(),row['sha256']))
                return row
            source=self.local_candidate(row,binary=binary)
            temp=self.root/(uuid.uuid4().hex+'.part')
            try:
                if source:
                    before=source.stat()
                    if before.st_size>cap:raise ArtifactError('实际文件超过允许的大小限制。')
                    with source.open('rb') as src,temp.open('wb') as dst:
                        size=0
                        while block:=src.read(1024*1024):
                            size+=len(block)
                            if size>cap:raise ArtifactError('实际文件超过允许的大小限制。')
                            dst.write(block)
                    after=source.stat()
                    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ArtifactError('客户端附件仍在写入，请稍后重试。')
                elif row['source_type']=='qq_group_file':
                    if not remote:raise ArtifactError('此连接未开启 OneBot 群资料读取权限，不能从 QQ 下载。')
                    from .artifact_onebot import OneBot
                    OneBot().download(row,temp,cap)
                else:
                    with self.store.connect() as db:db.execute("UPDATE artifact_sources SET availability='MISSING' WHERE id=?",(sid,))
                    raise ArtifactError('此项是聊天附件记录，本机未找到本体。QQ 群文件可先用 read_qq_group(view="files") 找到目录中的文件编号，再下载；历史附件与群文件目录不自动等同。其他来源请在客户端下载后重试。')
                if row['size'] is not None and temp.stat().st_size!=row['size']:raise ArtifactError('文件大小与来源不一致，未缓存。')
                meta=json.loads(row['metadata_json'])
                if meta.get('md5'):
                    with temp.open('rb') as h:
                        if hashlib.file_digest(h,'md5').hexdigest()!=meta['md5']:raise ArtifactError('文件MD5与消息不一致，未缓存。')
                with temp.open('rb') as h:sha=hashlib.file_digest(h,'sha256').hexdigest()
                target=self.root/(sha+'.blob')
                if not target.exists():temp.replace(target)
                now=time.time();size=target.stat().st_size
                with self.store.connect() as db:
                    db.execute('''INSERT INTO artifacts(sha256,size,mime_type,local_path,created_at,last_accessed_at)
                        VALUES(?,?,?,?,?,?) ON CONFLICT(sha256) DO UPDATE SET local_path=excluded.local_path,
                        cache_status=CASE WHEN user_pinned OR referenced_by_answer THEN 'ARCHIVED' ELSE 'CACHED' END,
                        last_accessed_at=excluded.last_accessed_at''',
                        (sha,size,mimetypes.guess_type(row['filename'])[0] or 'application/octet-stream',str(target.relative_to(ROOT)),now,now))
                    db.execute("UPDATE artifact_sources SET sha256=?,availability='CACHED' WHERE id=?",(sha,sid))
                    db.execute("UPDATE artifact_sources SET availability='CACHED' WHERE sha256=?",(sha,))
                return self.get(sid)
            finally:temp.unlink(missing_ok=True)

    def prepare(self,sid,*,confirmed=False,remote=True):
        from .store import tokens
        with _lock:
            row=self.get(sid)
            if row['parse_status'] in ('PARSED','PARTIAL','NO_TEXT'):return self.public(row)
            row=self.materialize(sid,confirmed=confirmed,remote=remote)
            # Another source may already have parsed the same SHA256.
            if row['parse_status'] in ('PARSED','PARTIAL','NO_TEXT'):return self.public(row)
            output=self.root/(uuid.uuid4().hex+'.parse.json')
            try:
                result=subprocess.run([sys.executable,str(ROOT/'scripts/parse_artifact.py'),str(self.cache_path(row)),row['extension'],str(output)],
                    cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=90,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if result.returncode or not output.is_file():raise ArtifactError('文档解析失败；原文件保留，可下载核对。')
                parsed=json.loads(output.read_text('utf-8'))
                if parsed.get('error'):raise ArtifactError(parsed['error'])
                with self.store.connect() as db:
                    for n,chunk in enumerate(parsed['chunks']):
                        cur=db.execute('INSERT INTO artifact_chunks(sha256,ordinal,text,locator) VALUES(?,?,?,?)',
                            (row['sha256'],n,chunk['text'],json.dumps(chunk['locator'],ensure_ascii=False)))
                        db.execute('INSERT INTO artifact_fts(rowid,terms) VALUES(?,?)',(cur.lastrowid,tokens(chunk['text'])))
                    db.execute('UPDATE artifacts SET parse_status=?,parser=?,parse_note=? WHERE sha256=?',
                        (parsed['status'],parsed['parser'],parsed['note'],row['sha256']))
                return self.public(self.get(sid))
            except subprocess.TimeoutExpired:raise ArtifactError('解析超过90秒已停止，原文件保留。') from None
            finally:output.unlink(missing_ok=True)

    def chunks(self,sid,offset=0,limit=4,plan=None):
        row=self.get(sid,plan)
        with self.store.connect() as db:
            total=db.execute('SELECT count(*) FROM artifact_chunks WHERE sha256=?',(row['sha256'],)).fetchone()[0]
            rows=db.execute('SELECT * FROM artifact_chunks WHERE sha256=? ORDER BY ordinal LIMIT ? OFFSET ?',
                (row['sha256'],min(8,max(1,limit)),max(0,offset))).fetchall()
            if row['sha256']:db.execute('UPDATE artifacts SET last_accessed_at=? WHERE sha256=?',(time.time(),row['sha256']))
        return dict(files=[dict(self.public(row),citation_id=f"F{sid}:C{r['id']}",chunk_id=r['id'],ordinal=r['ordinal'],
            text=r['text'],locator=json.loads(r['locator']),evidence_kind='native_text') for r in rows],
            match_count=total,has_more=offset+len(rows)<total,next_offset=offset+len(rows) if offset+len(rows)<total else None,
            note=row['parse_note'] or '尚未解析，请先prepare_file。')

    def pin(self,sid,*,confirmed=False):
        row=self.materialize(sid,confirmed=confirmed)
        with self.store.connect() as db:db.execute("UPDATE artifacts SET user_pinned=1,cache_status='ARCHIVED' WHERE sha256=?",(row['sha256'],))
        return self.public(self.get(sid))

    def evict(self,sid):
        with _lock:
            row=self.get(sid)
            if row['user_pinned'] or row['referenced_by_answer']:raise ArtifactError('此文件已长期保留或被正式回答引用，V1不清理其原文件。')
            if not row['local_path']:return dict(released_bytes=0)
            path=self.cache_path(row);size=path.stat().st_size if path.is_file() else 0
            path.unlink(missing_ok=True)
            with self.store.connect() as db:
                db.execute("UPDATE artifacts SET local_path=NULL,cache_status='EVICTED' WHERE sha256=?",(row['sha256'],))
                db.execute("UPDATE artifact_sources SET availability='METADATA_ONLY' WHERE sha256=?",(row['sha256'],))
            return dict(released_bytes=size,note='仅释放本项目原文件缓存，来源、解析文本和检索索引保留。')

    def mark_cited(self,evidence):
        hashes={v.get('sha256') for v in evidence if v.get('evidence_kind')=='native_text' and v.get('sha256')}
        with self.store.connect() as db:
            db.executemany("UPDATE artifacts SET referenced_by_answer=1,cache_status=CASE WHEN local_path IS NULL THEN cache_status ELSE 'ARCHIVED' END WHERE sha256=?",[(s,) for s in hashes])
