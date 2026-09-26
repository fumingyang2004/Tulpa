"""Read authenticated QQ directory metadata, independent of message history.

Older clients can use an earlier profile_info table. Only the known numeric
identity/name columns are accepted; never infer a person's identity by name.
"""
import re


def clean(value):
    # QQ can store invisible formatting bytes (e.g. DLE/DC1) inside a real
    # group name. Remove those characters, not the entire useful label.
    return ''.join(c for c in value if ord(c)>=32 and ord(c)!=127).strip()[:512] if isinstance(value,str) else ''


def remember_peer(buddy,peer,sender,name):
    # A direct correspondent's own native sender label is a valid fallback.
    # Never use our outgoing sender label or a group member as a group name.
    peer=str(peer);name=clean(name)
    if not re.fullmatch('[1-9][0-9]{0,19}',peer) or peer!=str(sender):return
    if not name or name.isdigit() or name.startswith(('qq_friend_','qq_group_','QQ direct ','QQ group ')):return
    buddy.setdefault(int(peer),name)


def directory(open_database):
    buddy={};groups={};status={}
    for filename,kind in (('profile_info.db','buddy'),('group_info.db','group')):
        try:
            db=open_database(filename)
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            candidates=([t for t in ('profile_info',*(f'profile_info_v{i}' for i in range(1,7))) if t in tables]
                        if kind=='buddy' else [t for t in ('group_info','group_list','group_detail_info_ver1') if t in tables])
            compatible=0;limited=False
            for table in candidates:
                columns={r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}
                identity='1002' if kind=='buddy' else '60001'
                name='20002' if kind=='buddy' else '60007'
                if identity not in columns or not ({name,'20009'} if kind=='buddy' else {name}) & columns:continue
                compatible+=1
                fields=[f'"{identity}"',f'"{name}"' if name in columns else 'NULL']
                if kind=='buddy':fields.append('"20009"' if '20009' in columns else 'NULL')
                rows=db.execute(f'SELECT {",".join(fields)} FROM "{table}" LIMIT 100001').fetchall()
                limited=limited or len(rows)>100000
                for row in rows[:100000]:
                    if not re.fullmatch('[1-9][0-9]{0,19}',str(row[0] or '')):continue
                    peer=int(row[0]);nickname=clean(row[1])
                    if kind=='group':
                        if nickname:groups[peer]=nickname
                        continue
                    remark=clean(row[2])
                    if remark and nickname and remark!=nickname:label=f'备注：{remark} · 昵称：{nickname}'
                    elif remark:label=f'备注：{remark}'
                    elif nickname:label=f'昵称：{nickname}'
                    else:continue
                    buddy[peer]=label
            status[kind]=dict(status='ok' if compatible else 'schema_unavailable',tables=compatible,
                              labels=len(buddy if kind=='buddy' else groups),limited=limited)
        except Exception as exc:
            status[kind]=dict(status='unavailable',error_type=type(exc).__name__)
    return buddy,groups,status


def conversation_labels(account,buddy,groups,ids=None):
    return [dict(conversation_id=f'{account}:{kind}:{peer}',conversation=name)
            for kind,mapping in (('direct',buddy),('group',groups)) for peer,name in mapping.items()
            if name and (ids is None or f'{account}:{kind}:{peer}' in ids)]


def imported_ids(account):
    # Export repair metadata only for already imported conversations, not the
    # client's entire contact directory. No store migration in the reader.
    import sqlite3
    from contextlib import closing
    from chatlocal.config import DB
    if not DB.is_file():return set()
    with closing(sqlite3.connect(f'file:{DB}?mode=ro',uri=True)) as db:
        return {r[0] for r in db.execute("SELECT DISTINCT conversation_id FROM messages WHERE platform='qq'")
                if r[0].startswith(str(account)+':')}
