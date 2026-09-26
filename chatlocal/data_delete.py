"""Scoped deletion of imported rows and owned, unshared artifacts."""
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from .config import ROOT,DATA,DB,local_path
from .import_scope import bounds


def selection(body):
    platform=body.get('platform');ids=body.get('conversations')
    if platform not in ('qq','wechat') or not isinstance(ids,list) or len(ids)>500 or any(not isinstance(x,str) or not x.strip() for x in ids):
        raise ValueError('必须指定有效平台和会话列表；空列表表示该平台全部会话。')
    start,end=bounds(body.get('start',''),body.get('end',''))
    if start is None or end is None:raise ValueError('删除必须填写开始和结束时间。')
    return platform,list(dict.fromkeys(ids)),start,end


def matched(db,body):
    p,ids,start,end=selection(body)
    conversations=f' AND conversation_id IN ({",".join("?" for _ in ids)})' if ids else ''
    return db.execute(f'SELECT id,media_json,conversation_id FROM messages WHERE platform=?{conversations} AND timestamp>=? AND timestamp<? ORDER BY id',[p,*ids,start,end]).fetchall()


def preview(store,body):
    with store.connect() as db:rows=matched(db,body)
    identity=hashlib.sha256(json.dumps([list(r) for r in rows]).encode()).hexdigest()
    return dict(messages=len(rows),media=sum(len(json.loads(r['media_json'])) for r in rows),fingerprint=identity,
        platform=body['platform'],conversations=len({r['conversation_id'] for r in rows}),
        all_conversations=not body['conversations'],start=body['start'],end=body['end'])


def redact_archive(path,pointers):
    raw=path.read_bytes()
    if path.suffix.lower() in ('.jsonl','.ndjson'):
        lines=raw.decode('utf-8-sig').splitlines()
        for pointer in pointers:
            if not pointer.startswith('line:'):raise ValueError('来源定位格式无效，未删除。')
            lines[int(pointer[5:])-1]=''
        return ('\n'.join(lines)+'\n').encode('utf-8')
    value=json.loads(raw.decode('utf-8-sig'))
    root=value.get('data') if isinstance(value,dict) and isinstance(value.get('data'),dict) and 'messages' not in value else value
    for pointer in pointers:
        parts=pointer.strip('/').split('/')
        if len(parts)==1 and isinstance(root,list):root[int(parts[0])]={'_chatlocal_deleted':True}
        elif len(parts)==2 and parts[0]=='messages':root['messages'][int(parts[1])]={'_chatlocal_deleted':True}
        else:raise ValueError('来源定位格式无效，未删除。')
    return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8')


def purge(store,body,expected,progress=None):
    from .activity import evidence_access
    with evidence_access(store,delete=True):return _purge(store,body,expected,progress)


def _purge(store,body,expected,progress=None):
    """Caller holds ingestion lock. Preview binds exact rows, not only a count."""
    before=sum(p.stat().st_size for p in [store.path,Path(str(store.path)+'-wal')] if p.exists())
    archive_root=(store.path.parent/'sources').resolve()
    media_root=(DATA/'media' if store.path.resolve()==DB.resolve() else store.path.parent/'media').resolve()
    removable=set();created=set();media_candidates=set();warnings=[]
    try:
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows=matched(db,body)
            identity=hashlib.sha256(json.dumps([list(r) for r in rows]).encode()).hexdigest()
            if identity!=expected:raise ValueError('匹配记录已变化，请重新预览后确认删除。')
            if not rows:return dict(summary='范围内没有已导入消息。',deleted=0,freed_bytes=0)
            db.execute('CREATE TEMP TABLE purge_ids(id INTEGER PRIMARY KEY)')
            db.executemany('INSERT INTO purge_ids VALUES(?)',[(r['id'],) for r in rows])
            for row in rows:
                for item in json.loads(row['media_json']):
                    if item.get('local_path'):media_candidates.add(item['local_path'])
            # Keep IDs monotonic even after deleting the newest messages. Old
            # answer links/watch cursors must never attach to a new message.
            db.execute('UPDATE message_counter SET value=max(value,(SELECT coalesce(max(id),0) FROM messages)) WHERE id=1')
            sources=db.execute('SELECT DISTINCT s.* FROM sources s JOIN provenance p ON p.source_hash=s.hash JOIN purge_ids d ON d.id=p.message_id').fetchall()
            for source in sources:
                old=local_path(source['archive'])
                if not old.is_relative_to(archive_root):raise ValueError('来源归档路径异常，未删除。')
                removed={r[0] for r in db.execute('SELECT pointer FROM provenance WHERE source_hash=? AND message_id IN (SELECT id FROM purge_ids)',(source['hash'],))}
                kept={r[0] for r in db.execute('SELECT pointer FROM provenance WHERE source_hash=? AND message_id NOT IN (SELECT id FROM purge_ids)',(source['hash'],))}
                db.execute('DELETE FROM provenance WHERE source_hash=? AND message_id IN (SELECT id FROM purge_ids)',(source['hash'],))
                if kept and removed-kept:
                    if not old.exists() or hashlib.sha256(old.read_bytes()).hexdigest()!=source['hash']:
                        raise ValueError('来源归档缺失或校验失败，未删除；请先修复归档。')
                    raw=redact_archive(old,removed-kept);digest=hashlib.sha256(raw).hexdigest()
                    new=archive_root/(digest+old.suffix)
                    if not new.exists():new.write_bytes(raw);created.add(new)
                    elif hashlib.sha256(new.read_bytes()).hexdigest()!=digest:raise ValueError('清理后的归档校验失败。')
                    db.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?,?,?)',(digest,source['filename'],new.relative_to(ROOT).as_posix(),source['format'],source['imported_at'],source['total'],source['skipped']))
                    db.execute('INSERT OR IGNORE INTO provenance SELECT message_id,?,pointer FROM provenance WHERE source_hash=?',(digest,source['hash']))
                    db.execute('DELETE FROM provenance WHERE source_hash=?',(source['hash'],))
                if not db.execute('SELECT 1 FROM provenance WHERE source_hash=? LIMIT 1',(source['hash'],)).fetchone():
                    db.execute('DELETE FROM sources WHERE hash=?',(source['hash'],));removable.add(old)
            db.execute('DELETE FROM message_fts WHERE rowid IN (SELECT id FROM purge_ids)')
            db.execute('INSERT OR IGNORE INTO deleted_message_keys SELECT dedup_key FROM messages WHERE id IN (SELECT id FROM purge_ids)')
            db.execute('INSERT OR IGNORE INTO deleted_native_ids SELECT platform,conversation_id,locator FROM message_native_ids WHERE message_id IN (SELECT id FROM purge_ids)')
            db.execute('DELETE FROM provenance WHERE message_id IN (SELECT id FROM purge_ids)')
            # File-message sources follow their chat records. Shared or explicitly
            # archived binaries remain; only unreferenced disposable caches go.
            hashes=[r[0] for r in db.execute('SELECT DISTINCT sha256 FROM artifact_sources WHERE message_id IN (SELECT id FROM purge_ids) AND sha256 IS NOT NULL')]
            db.execute('DELETE FROM artifact_sources WHERE message_id IN (SELECT id FROM purge_ids)')
            for sha in hashes:
                if db.execute('SELECT 1 FROM artifact_sources WHERE sha256=? LIMIT 1',(sha,)).fetchone():continue
                artifact=db.execute('SELECT * FROM artifacts WHERE sha256=?',(sha,)).fetchone()
                if not artifact or artifact['user_pinned'] or artifact['referenced_by_answer']:continue
                if artifact['local_path']:
                    from .artifacts import Artifacts
                    removable.add(Artifacts(store).cache_path(dict(artifact)))
                db.execute('DELETE FROM artifact_fts WHERE rowid IN (SELECT id FROM artifact_chunks WHERE sha256=?)',(sha,))
                db.execute('DELETE FROM artifact_chunks WHERE sha256=?',(sha,))
                db.execute('DELETE FROM artifacts WHERE sha256=?',(sha,))
            db.execute('DELETE FROM messages WHERE id IN (SELECT id FROM purge_ids)')
            referenced=set()
            for (raw,) in db.execute("SELECT media_json FROM messages WHERE media_json!='[]'"):
                referenced.update(i.get('local_path') for i in json.loads(raw) if i.get('local_path'))
            for value in media_candidates-referenced:
                path=local_path(value)
                if path.is_relative_to(media_root):removable.add(path)
    except BaseException:
        # Only newly created replacement files are eligible; original archives
        # remain untouched until the SQL transaction commits.
        for path in created:path.unlink(missing_ok=True)
        raise
    freed_files=0
    from .voice import cleanup as cleanup_voice
    freed_files+=cleanup_voice(store)
    for path in removable:
        try:
            if path.exists():size=path.stat().st_size;path.unlink();freed_files+=size
        except OSError:warnings.append('部分文件被占用，消息已删除，但文件空间暂未回收。')
    if progress:progress(f'已删除 {len(rows)} 条消息，正在压缩数据库…')
    try:
        with closing(sqlite3.connect(store.path,timeout=10,isolation_level=None)) as db:
            db.execute("INSERT INTO message_fts(message_fts) VALUES('optimize')")
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            db.execute('VACUUM')
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    except sqlite3.Error:warnings.append('数据库正被使用，空闲页已可复用，文件压缩未完成。')
    after=sum(p.stat().st_size for p in [store.path,Path(str(store.path)+'-wal')] if p.exists())
    created_bytes=sum(p.stat().st_size for p in created if p.exists())
    freed=max(0,before-after+freed_files-created_bytes)
    return dict(deleted=len(rows),freed_bytes=freed,warnings=list(dict.fromkeys(warnings)),
        summary=f'已删除 {len(rows)} 条消息，回收约 {freed/1024**2:.2f} MiB。'+''.join(dict.fromkeys(warnings)))
