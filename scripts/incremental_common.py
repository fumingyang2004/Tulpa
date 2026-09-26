"""Cursor helpers used with the existing authenticated readers, not client protocols."""
import hashlib
import json
import re
from pathlib import Path


def message_files_unchanged(previous, current, platform):
    """Contact/session counters alone cannot add native message rows."""
    if not previous.get('cursors') or previous.get('account') != current.get('account'):
        return False
    pattern = r'nt_msg\.db(?:-wal)?' if platform == 'qq' else r'message/message_\d+\.db(?:-wal)?'
    def selected(value):
        return {k:v for k,v in value.get('fingerprint',{}).items() if re.fullmatch(pattern,k)}
    old, new = selected(previous), selected(current)
    return bool(old) and old == new


def fingerprint(root, paths):
    result={}
    for p in sorted(paths):
        if not p.is_file():continue
        stat=p.stat()
        with p.open('rb') as handle:
            digest=hashlib.sha256(handle.read(4096))
            if stat.st_size>4096:
                handle.seek(max(4096,stat.st_size-4096));digest.update(handle.read(4096))
        result[str(p.relative_to(root)).replace('\\','/')]=[stat.st_size,stat.st_mtime_ns,digest.hexdigest()]
    return result


def boundary(conn, table, id_column, identity_columns, previous, *, allow_server_ack=False):
    # Validate the last native row as well as its number: resets/replaced DBs
    # must not silently jump over history. All identifiers come from readers.
    cols=','.join('"'+c+'"' for c in identity_columns)
    def signature(row):
        return hashlib.sha256(json.dumps(list(row),default=str).encode()).hexdigest()
    replay=None
    if previous and previous['last']:
        row=conn.execute(f'SELECT {cols} FROM "{table}" WHERE "{id_column}"=?',(previous['last'],)).fetchone()
        if row is not None and allow_server_ack and identity_columns==['server_id','create_time'] and row[0] and any(signature([empty,row[1]])==previous['signature'] for empty in (0,None)):
            replay=previous['last']-1
        elif row is None or signature(row)!=previous['signature']:
            raise ValueError('源消息游标失效：数据库可能被替换或清理。请补读历史后重建同步进度。')
    row=conn.execute(f'SELECT "{id_column}",{cols} FROM "{table}" ORDER BY "{id_column}" DESC LIMIT 1').fetchone()
    result=dict(last=row[0],signature=signature(row[1:])) if row else dict(last=0,signature='')
    if replay is not None:result['replay_from']=replay
    return result


def bootstrap_time(store,platform,full_only=False):
    # Initial upgrade starts from the last successfully imported snapshot;
    # overlap also covers equal timestamps. Later syncs use native IDs only.
    from datetime import datetime
    from chatlocal.config import local_path
    with store.connect() as db:
        rows=db.execute('SELECT archive FROM sources ORDER BY imported_at DESC').fetchall()
        latest=db.execute('SELECT max(timestamp) FROM messages WHERE platform=?',(platform,)).fetchone()[0]
    for row in rows:
        try:
            head=json.loads(local_path(row[0]).read_text(encoding='utf-8-sig'))
            if head.get('_partial_export'):continue
            if full_only and head.get('sync_checkpoint'):continue
            if head.get('reader') != {'qq':'chatlog-keeper','wechat':'wechatauto-replica'}[platform]:continue
            when=head.get('snapshot',{}).get('snapshot_at') if platform=='qq' else head.get('snapshot_at')
            if when:return int(float(when) if isinstance(when,(int,float)) else datetime.fromisoformat(when).timestamp())-300
        except (ValueError,OSError,AttributeError):continue
    if latest:return latest//1000-300
    raise ValueError('尚无本平台基线，请先在数据管理中按范围读取一次历史。')
