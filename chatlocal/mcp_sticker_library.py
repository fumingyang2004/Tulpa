"""Scoped sticker knowledge, bounded local originals and receipt-based usage.

No QQ/network access here. A note is not proof of seeing pixels: every reusable
row originates in a successful image read and every reuse verifies the bytes.
"""
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid

from .mcp_actions import dump


def scope_key(grant):
    return hashlib.sha256(dump(grant['scope']).encode()).hexdigest()


def terms(text, limit=128):
    """A small inverted index, not a vector model or a full-library scan."""
    out = []
    for word in re.findall(r'[a-z0-9_]+|[\u3400-\u9fff]+', str(text).casefold()[:2000]):
        parts = [word[:32]] if word.isascii() or len(word) < 2 else [word[i:i+2] for i in range(len(word)-1)]
        for part in parts:
            if part not in out:out.append(part)
            if len(out) >= limit:return out
    return out


def tidy(value, limit):
    return ' '.join(str(value).split())[:limit]


def note_fields(args):
    return dict(description=tidy(args.get('description', ''), 200),
                tags=list(dict.fromkeys(tidy(t, 24) for t in args.get('tags', []) if tidy(t, 24)))[:12],
                emotion=tidy(args.get('emotion', ''), 80), usage=tidy(args.get('usage', ''), 160),
                avoid=tidy(args.get('avoid', ''), 160), uncertainty=tidy(args.get('uncertainty', ''), 160))


class StickerLibrary:
    def __init__(self, media):
        self.media, self.access = media, media.access
        self.root = self.access.path.parent / 'mcp-chat-library'
        self.lock = threading.RLock()
        self.legacy_checked = set()
        self.policy = dict(candidate_limit=4, rotation_seconds=1800, cooldown_seconds=120,
                           scope_mib=128, total_mib=256)
        bounds = dict(candidate_limit=(1,6), rotation_seconds=(60,86400), cooldown_seconds=(10,3600),
                      scope_mib=(6,512), total_mib=(6,2048))
        try:
            config = json.loads((self.access.path.parent/'mcp-stickers-policy.json').read_text('utf-8'))
            for key, (low, high) in bounds.items():
                if type(config.get(key)) is int:self.policy[key] = max(low, min(high, config[key]))
        except (OSError, ValueError, AttributeError):pass
        with self.access.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sticker_library(
                    grant_id TEXT NOT NULL, account TEXT NOT NULL, digest TEXT NOT NULL,
                    scope_key TEXT NOT NULL, source_cid TEXT NOT NULL, source_ref TEXT NOT NULL,
                    origin TEXT NOT NULL, seen_at REAL NOT NULL, info TEXT NOT NULL,
                    semantic TEXT NOT NULL DEFAULT '{}', ready INTEGER NOT NULL DEFAULT 0, local_bytes INTEGER NOT NULL DEFAULT 0,
                    qq_id TEXT NOT NULL DEFAULT '', use_count INTEGER NOT NULL DEFAULT 0,
                    last_used REAL NOT NULL DEFAULT 0, last_state TEXT NOT NULL DEFAULT '',
                    last_attempt REAL NOT NULL DEFAULT 0, availability TEXT NOT NULL DEFAULT 'cache',
                    updated REAL NOT NULL, PRIMARY KEY(grant_id,account,digest));
                CREATE INDEX IF NOT EXISTS sticker_frequent ON sticker_library(grant_id,account,scope_key,use_count DESC,digest);
                CREATE INDEX IF NOT EXISTS sticker_rotation ON sticker_library(grant_id,account,scope_key,digest);
                CREATE TABLE IF NOT EXISTS sticker_terms(
                    grant_id TEXT NOT NULL, account TEXT NOT NULL, term TEXT NOT NULL, digest TEXT NOT NULL,
                    PRIMARY KEY(grant_id,account,term,digest));
                CREATE TABLE IF NOT EXISTS sticker_collections(
                    grant_id TEXT NOT NULL,account TEXT NOT NULL,digest TEXT NOT NULL,operation_id TEXT NOT NULL,
                    PRIMARY KEY(grant_id,account,digest));
                CREATE TABLE IF NOT EXISTS sticker_usage(
                    operation_id TEXT PRIMARY KEY,grant_id TEXT NOT NULL,account TEXT NOT NULL,
                    digest TEXT NOT NULL,at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sticker_diagnostics(
                    session_id TEXT NOT NULL,code TEXT NOT NULL,count INTEGER NOT NULL DEFAULT 0,
                    at REAL NOT NULL,metrics TEXT NOT NULL DEFAULT '{}',PRIMARY KEY(session_id,code));
                CREATE TABLE IF NOT EXISTS sticker_offers(
                    session_id TEXT PRIMARY KEY,at REAL NOT NULL,count INTEGER NOT NULL,send_attempts INTEGER NOT NULL DEFAULT 0);
            ''')
            if 'ready' not in {r['name'] for r in db.execute('PRAGMA table_info(sticker_library)')}:
                db.execute('ALTER TABLE sticker_library ADD COLUMN ready INTEGER NOT NULL DEFAULT 0')
            db.executescript('''
                CREATE INDEX IF NOT EXISTS sticker_ready_frequent ON sticker_library(grant_id,account,scope_key,ready,use_count DESC,digest);
                CREATE INDEX IF NOT EXISTS sticker_ready_rotation ON sticker_library(grant_id,account,scope_key,ready,digest);
            ''')

    def identity(self, grant, row):
        return grant['id'], row['conversation_id'].split(':')[0]

    def find(self, grant, row, digest):
        with self.access.connect() as db:
            found = db.execute('SELECT * FROM sticker_library WHERE grant_id=? AND account=? AND digest=?',
                               (*self.identity(grant,row),digest)).fetchone()
        return dict(found) if found else None

    def valid(self, grant, item):
        if item['scope_key'] != scope_key(grant):return False
        try:self.media.chat.live_target(grant,item['source_cid'])
        except ValueError:return False
        return item['account'] == item['source_cid'].split(':')[0]

    def path(self, gid, account, digest):
        if not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('invalid digest')
        namespace = hashlib.sha256((gid+'\0'+account).encode()).hexdigest()[:32]
        path = self.root/namespace/(digest+'.bin')
        # Never follow a library directory replaced with a symlink/junction.
        expected = self.access.path.parent.resolve()/'mcp-chat-library'/namespace/(digest+'.bin')
        if path.resolve() != expected:raise ValueError('图库路径已改变，未读取或写入。')
        return path

    def remember(self, grant, row, asset, raw, info):
        gid, account = self.identity(grant,row);digest = hashlib.sha256(raw).hexdigest();now = time.time()
        with self.access.connect() as db:
            if not db.execute('SELECT 1 FROM sticker_library WHERE grant_id=? AND account=? AND digest=?',(gid,account,digest)).fetchone():
                count = db.execute('SELECT count(*) FROM sticker_library WHERE grant_id=? AND account=?',(gid,account)).fetchone()[0]
                if count >= 2000:return False  # The image can still be used in this session.
            db.execute('''INSERT INTO sticker_library(grant_id,account,digest,scope_key,source_cid,source_ref,origin,seen_at,info,updated)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(grant_id,account,digest) DO UPDATE SET
                scope_key=excluded.scope_key,source_cid=excluded.source_cid,source_ref=excluded.source_ref,
                origin=excluded.origin,seen_at=excluded.seen_at,info=excluded.info,updated=excluded.updated''',
                (gid,account,digest,scope_key(grant),row['conversation_id'],asset['ref'],asset['origin'],now,dump(info),now))
        return True

    def verified_bytes(self, grant, row, item):
        if not item or not self.valid(grant,item):return None,'source_scope_invalid'
        try:
            path = self.path(item['grant_id'],item['account'],item['digest'])
            if item['local_bytes']:
                if not path.is_file():return None,'local_missing'
                if path.stat().st_size > 6*1024*1024:return None,'hash_changed'
                raw = path.read_bytes()
                return (raw,'local') if hashlib.sha256(raw).hexdigest()==item['digest'] else (None,'hash_changed')
            raw = self.media.cached(item['digest'])
            return (raw,'cache') if raw is not None else (None,'cache_missing')
        except (OSError,ValueError):return None,'local_unavailable'

    def pin(self, grant, row, item, raw):
        if hashlib.sha256(raw).hexdigest()!=item['digest']:raise ValueError('图片内容变化，请重新看图。')
        gid, account = self.identity(grant,row);path = self.path(gid,account,item['digest'])
        # Keep the reservation and filesystem replacement serialized across processes.
        with self.lock, self.access.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT local_bytes FROM sticker_library WHERE grant_id=? AND account=? AND digest=?',(gid,account,item['digest'])).fetchone()
            used = db.execute('SELECT coalesce(sum(local_bytes),0) FROM sticker_library WHERE grant_id=? AND account=?',(gid,account)).fetchone()[0]
            total = db.execute('SELECT coalesce(sum(local_bytes),0) FROM sticker_library').fetchone()[0]
            extra = len(raw)-(old[0] if old else 0)
            if used+extra>self.policy['scope_mib']*1024**2 or total+extra>self.policy['total_mib']*1024**2:
                return dict(state='FAILED',code='local_capacity',note='本地图库已达容量上限；已有原件未被删除。')
            path.parent.mkdir(parents=True,exist_ok=True);temp = path.parent/(uuid.uuid4().hex+'.tmp')
            try:
                temp.write_bytes(raw);os.replace(temp,path)
                db.execute("UPDATE sticker_library SET local_bytes=?,availability='local' WHERE grant_id=? AND account=? AND digest=?",(len(raw),gid,account,item['digest']))
            finally:temp.unlink(missing_ok=True)
        return dict(state='SAVED',bytes=len(raw),sha256=item['digest'])

    def note(self, grant, row, asset, args):
        item = self.find(grant,row,asset['digest'])
        raw, state = self.verified_bytes(grant,row,item)
        if raw is None:raise ValueError('原件或已看图依据不可用，请重新 read_chat_sticker 后再记笔记。')
        fields = note_fields(args);gid,account = self.identity(grant,row);now = time.time()
        with self.access.connect() as db:
            db.execute('INSERT OR REPLACE INTO chat_sticker_notes VALUES(?,?,?,?,?,?)',(gid,account,asset['digest'],fields['description'],dump(fields['tags']),now))
            db.execute('UPDATE sticker_library SET semantic=?,ready=?,updated=? WHERE grant_id=? AND account=? AND digest=?',(dump(fields),int(bool(fields['description'] and not fields['uncertainty'])),now,gid,account,asset['digest']))
            db.execute('DELETE FROM sticker_terms WHERE grant_id=? AND account=? AND digest=?',(gid,account,asset['digest']))
            db.executemany('INSERT OR IGNORE INTO sticker_terms VALUES(?,?,?,?)',
                           [(gid,account,word,asset['digest']) for word in terms(' '.join([fields['description'],fields['emotion'],fields['usage'],*fields['tags']]))])
        return dict(state='SAVED',reusable=bool(fields['description'] and not fields['uncertainty']),resource=state)

    def hydrate_legacy(self, grant, row):
        """Bounded, one-time adoption of active legacy seen handles; notes alone never qualify."""
        if row['id'] in self.legacy_checked:return
        gid,account=self.identity(grant,row)
        with self.access.connect() as db:
            rows=db.execute('''SELECT a.*,n.description note,n.tags FROM chat_media_assets a
                JOIN chat_sticker_notes n ON n.digest=a.digest AND n.grant_id=? AND n.account=?
                WHERE a.session_id=? AND a.seen=1 AND NOT EXISTS
                (SELECT 1 FROM sticker_library l WHERE l.grant_id=? AND l.account=? AND l.digest=a.digest) LIMIT 8''',
                (gid,account,row['id'],gid,account)).fetchall()
        migrated=0
        for entry in rows:
            raw=self.media.cached(entry['digest'])
            if raw is not None:
                asset=dict(entry)
                if self.remember(grant,row,asset,raw,{}):
                    self.note(grant,row,asset,dict(description=entry['note'],tags=json.loads(entry['tags'])))
                    migrated+=1
        if len(rows)<8 or not migrated:
            if len(self.legacy_checked)>2000:self.legacy_checked.clear()
            self.legacy_checked.add(row['id'])

    @staticmethod
    def handle_id(row, item):
        return hashlib.sha256((row['id']+'\0library:'+item['digest']).encode()).hexdigest()[:32]

    def handle(self, grant, row, item):
        raw,state=self.verified_bytes(grant,row,item)
        if raw is None:return None,state
        aid=self.media.upsert(row['id'],'library:'+item['digest'],{},json.loads(item['semantic']).get('description',''))
        with self.access.connect() as db:
            db.execute('UPDATE chat_media_assets SET seen=1,digest=? WHERE id=?',(item['digest'],aid))
        return aid,state

    def pool(self, grant, row, text, now):
        gid,account=self.identity(grant,row);key=scope_key(grant);pool={}
        def add(rows, reason):
            for r in rows:
                if r['digest'] not in pool:pool[r['digest']]=(dict(r),reason)
        with self.access.connect() as db:
            for word in terms(text,12):
                add(db.execute('''SELECT l.* FROM sticker_terms t JOIN sticker_library l
                    ON l.grant_id=t.grant_id AND l.account=t.account AND l.digest=t.digest
                    WHERE t.grant_id=? AND t.account=? AND t.term=? AND l.scope_key=? AND l.ready=1 LIMIT 4''',(gid,account,word,key)), 'context')
                if len(pool)>=12:break
            add(db.execute('SELECT * FROM sticker_library WHERE grant_id=? AND account=? AND scope_key=? AND ready=1 ORDER BY use_count DESC,digest LIMIT 8',(gid,account,key)), 'frequent')
            cursor=hashlib.sha256(f'{gid}:{int(now//self.policy["rotation_seconds"])}'.encode()).hexdigest()
            rot=list(db.execute('SELECT * FROM sticker_library WHERE grant_id=? AND account=? AND scope_key=? AND ready=1 AND digest>=? ORDER BY digest LIMIT 8',(gid,account,key,cursor)))
            if len(rot)<8:rot+=list(db.execute('SELECT * FROM sticker_library WHERE grant_id=? AND account=? AND scope_key=? AND ready=1 AND digest<? ORDER BY digest LIMIT ?',(gid,account,key,cursor,8-len(rot))))
            add(rot,'rotation')
        # Reserve one of the few slots for rotation, even when many matches exist.
        ranked=list(pool.values());limit=self.policy['candidate_limit']
        context=[x for x in ranked if x[1]=='context']
        frequent=[x for x in ranked if x[1]=='frequent']
        rotation=[x for x in ranked if x[1]=='rotation']
        take=max(1,limit-2)
        return context[:take]+frequent[:1]+rotation+context[take:]+frequent[1:]

    def candidates(self, grant, row, messages):
        began=time.monotonic();now=time.time();items=[];verified=[];reasons={};scanned=0;checked=0
        code='empty';previous='none'
        try:
            self.media.guard(grant,row['id'],'send_chat_sticker',threading.Event())
        except ValueError:
            return [],dict(code='permission_or_session_unavailable',count=0,remote_calls=0)
        status=self.media.chat.receiver.status()
        if status.get('state')!='connected' or status.get('account')!=row['conversation_id'].split(':')[0]:
            return [],dict(code='source_unavailable',count=0,remote_calls=0)
        with self.access.connect() as db:
            limited=db.execute("SELECT count(*) FROM calls WHERE grant_id=? AND tool='send_chat_sticker' AND at>?",(grant['id'],now-60)).fetchone()[0]>=12
        if limited:return [],dict(code='send_rate_limited',count=0,remote_calls=0)
        self.hydrate_legacy(grant,row)
        text=' '.join(str(m.get('content',''))[:250] for m in messages[-8:] if not m.get('is_self'))
        for item,reason in self.pool(grant,row,text,now):
            scanned+=1;sem=json.loads(item['semantic'])
            if not sem.get('description') or sem.get('uncertainty'):
                reasons['needs_understanding']=reasons.get('needs_understanding',0)+1;continue
            if item['last_state'] in ('UNKNOWN','EXECUTING') or now-item['last_attempt']<self.policy['cooldown_seconds']:
                reasons['recent_or_unknown']=reasons.get('recent_or_unknown',0)+1;continue
            if checked>=12:break
            checked+=1;raw,availability=self.verified_bytes(grant,row,item)
            if raw is None:
                reasons[availability]=reasons.get(availability,0)+1;continue
            aid=self.handle_id(row,item);verified.append(item)
            items.append(dict(sticker_id=aid,model_note=sem['description'][:120],tags=sem.get('tags',[])[:6],
                emotion=sem.get('emotion','')[:60],usage=sem.get('usage','')[:100],avoid=sem.get('avoid','')[:100],
                reason=reason,resource=availability,sendable=True,use_count=item['use_count'],
                next_step='已核对本授权内看过的原件，可直接 send_chat_sticker；不必先收藏。'))
            if len(items)>=self.policy['candidate_limit']:break
        with self.access.connect() as db:
            # One transaction for the few verified handles, not a connection and
            # fsync per candidate. No image IO occurs while the write lock is held.
            db.executemany('''INSERT INTO chat_media_assets(id,session_id,origin,ref,digest,seen,description,updated)
                VALUES(?,?,?,'{}',?,1,?,?) ON CONFLICT(id) DO UPDATE SET
                digest=excluded.digest,seen=1,description=excluded.description,updated=excluded.updated''',
                [(self.handle_id(row,x),row['id'],'library:'+x['digest'],x['digest'],json.loads(x['semantic'])['description'],now) for x in verified])
            db.execute('DELETE FROM chat_media_assets WHERE session_id=? AND id NOT IN (SELECT id FROM chat_media_assets WHERE session_id=? ORDER BY updated DESC LIMIT 1500)',(row['id'],row['id']))
            old=db.execute('SELECT * FROM sticker_offers WHERE session_id=?',(row['id'],)).fetchone()
            if old and old['count']:previous='send_called' if old['send_attempts'] else 'no_send_observed'
            db.execute('INSERT OR REPLACE INTO sticker_offers VALUES(?,?,?,0)',(row['id'],now,len(items)))
            db.execute('DELETE FROM sticker_offers WHERE session_id IN (SELECT session_id FROM sticker_offers ORDER BY at DESC LIMIT -1 OFFSET 2000)')
        code='available' if items else 'no_eligible_candidates' if scanned else 'empty'
        if code=='empty':
            with self.access.connect() as db:
                exists=db.execute('SELECT 1 FROM sticker_library WHERE grant_id=? AND account=? AND scope_key=? LIMIT 1',(*self.identity(grant,row),scope_key(grant))).fetchone()
                legacy=db.execute('SELECT 1 FROM chat_sticker_notes WHERE grant_id=? AND account=? LIMIT 1',self.identity(grant,row)).fetchone()
            if exists:code='no_understood_candidates'
            elif legacy:code='legacy_notes_need_view'
        metrics=dict(code=code,count=len(items),examined=scanned,hash_checks=checked,remote_calls=0,
                     previous_offer=previous,excluded=reasons,elapsed_ms=round((time.monotonic()-began)*1000,2))
        self.diagnose(row['id'],'candidates_'+code,metrics)
        # A revoked grant may not receive notes after disk verification.
        self.media.guard(grant,row['id'],'send_chat_sticker',threading.Event())
        return items,metrics

    def record_attempt(self, db, grant, row, digest, oid):
        gid,account=self.identity(grant,row)
        db.execute("UPDATE sticker_library SET last_state='EXECUTING',last_attempt=? WHERE grant_id=? AND account=? AND digest=?",(time.time(),gid,account,digest))

    def observe_send(self, grant, sid):
        with self.access.connect() as db:
            db.execute('''UPDATE sticker_offers SET send_attempts=send_attempts+1 WHERE session_id=?
                AND EXISTS(SELECT 1 FROM chat_sessions WHERE id=? AND grant_id=?)''',(sid,sid,grant['id']))

    def record_result(self, db, grant, row, digest, oid, state):
        gid,account=self.identity(grant,row);now=time.time()
        db.execute('UPDATE sticker_library SET last_state=?,last_attempt=? WHERE grant_id=? AND account=? AND digest=?',(state,now,gid,account,digest))
        if state=='SUCCEEDED':
            inserted=db.execute('INSERT OR IGNORE INTO sticker_usage VALUES(?,?,?,?,?)',(oid,gid,account,digest,now)).rowcount
            if inserted:db.execute('UPDATE sticker_library SET use_count=use_count+1,last_used=? WHERE grant_id=? AND account=? AND digest=?',(now,gid,account,digest))

    def diagnose(self, sid, code, metrics=None):
        # Only enumerated codes and numeric/status metrics, never text/URLs/tokens.
        try:
            with self.access.connect() as db:
                db.execute('''INSERT INTO sticker_diagnostics VALUES(?,?,1,?,?)
                    ON CONFLICT(session_id,code) DO UPDATE SET count=count+1,at=excluded.at,metrics=excluded.metrics''',
                    (sid,code,time.time(),dump(metrics or {})))
                db.execute('DELETE FROM sticker_diagnostics WHERE rowid IN (SELECT rowid FROM sticker_diagnostics ORDER BY at DESC LIMIT -1 OFFSET 2000)')
        except (OSError,sqlite3.Error):pass
