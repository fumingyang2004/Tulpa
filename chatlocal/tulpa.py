"""Behavior-gated local memory. Archives are never a growth queue.

All collection writes share the message transaction. No provider call occurs on
the ingestion path. References, not copied chats, are the source of episodes.
"""
import hashlib
import json
import math
import logging
import re
import threading
import time


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def initialize(db):
    db.executescript('''
    CREATE INDEX IF NOT EXISTS msg_native_quote ON messages(platform,conversation_id,source_id);
    CREATE TABLE IF NOT EXISTS tulpa_control(
      id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL DEFAULT 1,
      epoch INTEGER NOT NULL DEFAULT 1,since INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS tulpa_scopes(
      id INTEGER PRIMARY KEY,platform TEXT NOT NULL,conversation_id TEXT NOT NULL,
      kind TEXT NOT NULL,total INTEGER NOT NULL DEFAULT 0,processed INTEGER NOT NULL DEFAULT 0,
      automatic TEXT NOT NULL DEFAULT '',manual TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 0,
      valid INTEGER NOT NULL DEFAULT 1,updated_at REAL,last_event_time INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'collecting',retry_at REAL NOT NULL DEFAULT 0,
      failures INTEGER NOT NULL DEFAULT 0,UNIQUE(platform,conversation_id));
    CREATE TABLE IF NOT EXISTS tulpa_events(
      seq INTEGER PRIMARY KEY AUTOINCREMENT,message_id INTEGER NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
      scope_id INTEGER NOT NULL REFERENCES tulpa_scopes(id),epoch INTEGER NOT NULL,
      counted INTEGER NOT NULL,ordinal INTEGER,created_at REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS tulpa_event_scope ON tulpa_events(scope_id,ordinal);
    CREATE TABLE IF NOT EXISTS tulpa_episodes(
      id INTEGER PRIMARY KEY,scope_id INTEGER NOT NULL REFERENCES tulpa_scopes(id),
      person_id TEXT NOT NULL,episode_key TEXT NOT NULL UNIQUE,
      incoming TEXT NOT NULL,outgoing TEXT NOT NULL,context TEXT NOT NULL,
      fingerprints TEXT NOT NULL,timestamp INTEGER NOT NULL,epoch INTEGER NOT NULL,
      valid INTEGER NOT NULL DEFAULT 1,scenario TEXT NOT NULL,intent TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS tulpa_episode_scope ON tulpa_episodes(scope_id,person_id,timestamp);
    CREATE VIRTUAL TABLE IF NOT EXISTS tulpa_episode_fts USING fts5(terms, tokenize='unicode61');
    CREATE TABLE IF NOT EXISTS tulpa_episode_sources(
      episode_id INTEGER NOT NULL REFERENCES tulpa_episodes(id) ON DELETE CASCADE,
      message_id INTEGER NOT NULL,PRIMARY KEY(episode_id,message_id));
    CREATE INDEX IF NOT EXISTS tulpa_episode_source ON tulpa_episode_sources(message_id);
    CREATE TABLE IF NOT EXISTS tulpa_memory_sources(
      scope_id INTEGER NOT NULL REFERENCES tulpa_scopes(id),message_id INTEGER NOT NULL,
      fingerprint TEXT NOT NULL,PRIMARY KEY(scope_id,message_id));
    CREATE INDEX IF NOT EXISTS tulpa_memory_source ON tulpa_memory_sources(message_id);
    CREATE TABLE IF NOT EXISTS tulpa_history(
      id INTEGER PRIMARY KEY,scope_id INTEGER NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL,
      created_at REAL NOT NULL,details TEXT NOT NULL);
    ''')
    db.execute('INSERT OR IGNORE INTO tulpa_control(id,since) VALUES(1,?)', (int(time.time()*1000),))
    # Source changes invalidate derived interpretations; never silently retarget.
    for operation,condition in [('DELETE',''),('UPDATE', '''WHEN
      OLD.content IS NOT NEW.content OR OLD.sender_id IS NOT NEW.sender_id OR
      OLD.is_self IS NOT NEW.is_self OR OLD.timestamp IS NOT NEW.timestamp OR
      OLD.reply_to IS NOT NEW.reply_to OR OLD.conversation_id IS NOT NEW.conversation_id OR
      OLD.platform IS NOT NEW.platform OR OLD.conversation_type IS NOT NEW.conversation_type''')]:
        db.executescript(f'''CREATE TRIGGER IF NOT EXISTS tulpa_source_{operation.lower()}
        BEFORE {operation} ON messages {condition} BEGIN
          UPDATE tulpa_episodes SET valid=0 WHERE id IN
            (SELECT episode_id FROM tulpa_episode_sources WHERE message_id=OLD.id);
          UPDATE tulpa_scopes SET valid=0 WHERE id IN
            (SELECT scope_id FROM tulpa_memory_sources WHERE message_id=OLD.id);
        END;''')


def fingerprint(row):
    return hashlib.sha256(dumps([row[k] for k in ('platform','conversation_id','sender_id',
        'timestamp','content','is_self','reply_to','conversation_type')]).encode()).hexdigest()


def effective(row):
    text=row['content'].strip()
    # Exclude placeholders, system notices and pure emoji. Mixed meaningful text is allowed.
    if re.match(r'^\[(?:文件|视频|语音|系统)[^\]]*\]',text):return False
    text=re.sub(r'\[(?:图片|动画表情|表情|语音|文件|视频|系统)[^\]]*\]', '', text).strip()
    return row['is_self'] in (0,1) and bool(re.search(r'[\w\u3400-\u9fff]',text))


def signals(text):
    groups={'invitation':'一起|来不来|玩吗|吃饭|打游戏|出去|约|几点',
            'thanks':'谢谢|感谢|多谢', 'help':'帮|怎么|请教|咋|报错|代码|解决',
            'banter':'哈哈|笑死|草|臭小子|绷|乐|炸|蚌', 'arrangement':'提交|老师|报告|开会|时间|作业|收到'}
    scene=[k for k,v in groups.items() if re.search(v,text)]
    intent=[k for k,v in {'decline':'不去|不来|不了|没空|算了|拒绝|不打',
      'accept':'好的|可以|行啊|没问题|收到', 'question':'[?？]|怎么|咋|几点|为啥',
      'tease':'调戏|打趣|笑|哈哈|臭小子'}.items() if re.search(v,text)]
    return scene,intent


def _episode(db,sid,epoch,incoming,outgoing,context,kind):
    rows=incoming+outgoing+context
    person=incoming[-1]['sender_id']
    if not person or any(r['is_self']!=0 for r in incoming):return
    key=f'{sid}:{kind}:{outgoing[0]["id"]}'
    scenario,_=signals(' '.join(r['content'] for r in incoming+context))
    _,intent=signals(' '.join(r['content'] for r in outgoing))
    values=(sid,person,key,dumps([r['id'] for r in incoming]),dumps([r['id'] for r in outgoing]),
      dumps([r['id'] for r in context]),dumps({r['id']:fingerprint(r) for r in rows}),
      outgoing[-1]['timestamp'],epoch,dumps(scenario),dumps(intent))
    db.execute('''INSERT INTO tulpa_episodes(scope_id,person_id,episode_key,incoming,outgoing,context,
      fingerprints,timestamp,epoch,scenario,intent) VALUES(?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(episode_key) DO UPDATE SET outgoing=excluded.outgoing,fingerprints=excluded.fingerprints,
      timestamp=excluded.timestamp,intent=excluded.intent''',values)
    eid=db.execute('SELECT id FROM tulpa_episodes WHERE episode_key=?',(key,)).fetchone()[0]
    db.executemany('INSERT OR IGNORE INTO tulpa_episode_sources VALUES(?,?)',[(eid,r['id']) for r in rows])
    from .store import tokens
    db.execute('DELETE FROM tulpa_episode_fts WHERE rowid=?',(eid,))
    db.execute('INSERT INTO tulpa_episode_fts(rowid,terms) VALUES(?,?)',
      (eid,tokens(' '.join(r['content'] for r in incoming+outgoing))))


def collect(db,mid):
    """New automatic live/incremental messages only, inside their commit transaction."""
    control=db.execute('SELECT * FROM tulpa_control WHERE id=1').fetchone()
    row=db.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    if not control['enabled'] or row['timestamp']<control['since'] or not effective(row):return
    kind=row['conversation_type']
    if kind not in ('group','direct'):return
    if kind=='group' and row['is_self']!=1:return
    db.execute('INSERT OR IGNORE INTO tulpa_scopes(platform,conversation_id,kind) VALUES(?,?,?)',
      (row['platform'],row['conversation_id'],kind))
    scope=db.execute('SELECT * FROM tulpa_scopes WHERE platform=? AND conversation_id=?',
      (row['platform'],row['conversation_id'])).fetchone();sid=scope['id']
    counted=int(kind=='direct' or row['is_self']==1)
    n=scope['total']+counted
    added=db.execute('INSERT OR IGNORE INTO tulpa_events(message_id,scope_id,epoch,counted,ordinal,created_at) VALUES(?,?,?,?,?,?)',
      (mid,sid,control['epoch'],counted,n if counted else None,time.time())).rowcount
    if not added:return
    db.execute('UPDATE tulpa_scopes SET total=?,last_event_time=max(last_event_time,?) WHERE id=?',(n,row['timestamp'],sid))
    if row['is_self']!=1:return
    if kind=='group':
        try:reply=json.loads(row['reply_to'] or '{}')
        except (ValueError,TypeError):return
        source=reply.get('source_id')
        if not source:return
        targets=db.execute('''SELECT * FROM messages WHERE platform=? AND conversation_id=? AND source_id=?
          AND timestamp<=? AND id!=? LIMIT 2''',(row['platform'],row['conversation_id'],str(source),row['timestamp'],mid)).fetchall()
        if len(targets)!=1 or targets[0]['is_self']!=0 or not effective(targets[0]):return
        target=targets[0]
        # A directly quoted old message is legitimate local context; still only use preceding text.
        context=db.execute('''SELECT * FROM messages WHERE platform=? AND conversation_id=? AND timestamp<=?
          AND timestamp>=? AND (timestamp<? OR id<?) AND id NOT IN (?,?) ORDER BY timestamp DESC,id DESC LIMIT 2''',
          (row['platform'],row['conversation_id'],row['timestamp'],row['timestamp']-300000,row['timestamp'],mid,mid,target['id'])).fetchall()
        _episode(db,sid,control['epoch'],[target],[row],list(reversed(context)),kind)
    else:
        # A bounded turn: incoming block -> outgoing block, at most 30 minutes apart.
        # Do not join across disabled periods or infer a turn from a late/out-of-order row.
        if row['timestamp']<scope['last_event_time']:return
        recent=db.execute('''SELECT m.* FROM messages m INDEXED BY msg_timeline
          WHERE m.platform=? AND m.conversation_id=? AND (m.timestamp<? OR (m.timestamp=? AND m.id<=?))
          AND m.timestamp>=? AND EXISTS(SELECT 1 FROM tulpa_events e WHERE e.message_id=m.id AND e.scope_id=? AND e.epoch=?)
          ORDER BY m.timestamp DESC,m.id DESC LIMIT 25''',
          (row['platform'],row['conversation_id'],row['timestamp'],row['timestamp'],mid,row['timestamp']-1800000,sid,control['epoch'])).fetchall()
        outgoing=[];incoming=[]
        for r in recent:
            if r['is_self']==1 and not incoming:outgoing.append(r)
            elif r['is_self']==0:incoming.append(r)
            else:break
        # A truncated block cannot claim to be a complete episode.
        if incoming and len(recent)<25:
            _episode(db,sid,control['epoch'],list(reversed(incoming)),list(reversed(outgoing)),[],kind)


class Tulpa:
    def __init__(self,store):self.store=store

    def control(self):
        with self.store.connect() as db:return dict(db.execute('SELECT * FROM tulpa_control').fetchone())

    def toggle(self,enabled):
        if type(enabled) is not bool:raise ValueError('开关需要为开或关。')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT * FROM tulpa_control').fetchone()
            if bool(current['enabled'])!=enabled:
                db.execute('UPDATE tulpa_control SET enabled=?,epoch=epoch+1,since=?', (int(enabled),int(time.time()*1000)))
        return self.control()

    def listing(self,query=''):
        with self.store.connect() as db:
            # Directory is local metadata only; no profiling or history scan.
            rows=db.execute('''SELECT s.*,coalesce((SELECT conversation FROM messages m WHERE m.platform=s.platform
              AND m.conversation_id=s.conversation_id ORDER BY timestamp DESC,id DESC LIMIT 1),s.conversation_id) name,
              (SELECT count(*) FROM tulpa_episodes e WHERE e.scope_id=s.id AND e.valid=1) episodes
              FROM tulpa_scopes s ORDER BY s.last_event_time DESC,s.id DESC''').fetchall()
        result=[]
        for r in rows:
            r=dict(r)
            if query.casefold() not in r['name'].casefold():continue
            r['progress']=min(100,r['total']-r['processed']);r['pending_batches']=(r['total']-r['processed'])//100
            r['has_memory']=bool(r.pop('automatic'));r['has_manual']=bool(r.pop('manual'));result.append(r)
        # Allow inspecting/correcting a cold contact without starting any profiling.
        directory=[]
        if query:
            with self.store.connect() as db:
                directory=[dict(r) for r in db.execute('''SELECT m.platform,m.conversation_id,m.conversation name,
                  m.conversation_type kind,max(m.timestamp) latest FROM messages m
                  WHERE m.conversation_type IN ('group','direct') AND instr(lower(m.conversation),lower(?))>0
                  AND NOT EXISTS(SELECT 1 FROM tulpa_scopes s WHERE s.platform=m.platform AND s.conversation_id=m.conversation_id)
                  GROUP BY m.platform,m.conversation_id ORDER BY latest DESC LIMIT 40''',(query,))]
        return dict(control=self.control(),scopes=result,directory=directory)

    def open_conversation(self,platform,cid):
        if platform not in ('qq','wechat') or not isinstance(cid,str):raise ValueError('会话无效。')
        with self.store.connect() as db:
            r=db.execute('SELECT conversation_type FROM messages WHERE platform=? AND conversation_id=? ORDER BY timestamp DESC LIMIT 1',(platform,cid)).fetchone()
            if not r or r[0] not in ('group','direct'):raise ValueError('尚无可识别的聊天记录。')
            db.execute('INSERT OR IGNORE INTO tulpa_scopes(platform,conversation_id,kind) VALUES(?,?,?)',(platform,cid,r[0]))
            sid=db.execute('SELECT id FROM tulpa_scopes WHERE platform=? AND conversation_id=?',(platform,cid)).fetchone()[0]
        return dict(id=sid)

    def detail(self,sid):
        with self.store.connect() as db:
            r=db.execute('SELECT * FROM tulpa_scopes WHERE id=?',(sid,)).fetchone()
            if not r:raise ValueError('尚未收集到这个会话的行为。')
            result=dict(r)
            result['episodes']=[dict(e) for e in db.execute('''SELECT id,incoming,outgoing,context,timestamp,valid,person_id
              FROM tulpa_episodes WHERE scope_id=? ORDER BY timestamp DESC,id DESC LIMIT 20''',(sid,))]
            result['history']=[dict(h) for h in db.execute('SELECT * FROM tulpa_history WHERE scope_id=? ORDER BY id DESC LIMIT 10',(sid,))]
            result['sources']=[r[0] for r in db.execute('SELECT message_id FROM tulpa_memory_sources WHERE scope_id=? LIMIT 100',(sid,))]
        result['control']=self.control();return result

    def edit(self,sid,text,revision):
        if not isinstance(text,str) or len(text)>6000:raise ValueError('人工记忆最多 6000 字。')
        with self.store.connect() as db:
            cur=db.execute('UPDATE tulpa_scopes SET manual=?,revision=revision+1 WHERE id=? AND revision=?',(text.strip(),sid,revision))
            if not cur.rowcount:raise ValueError('记忆已更新，请重新打开后再保存。')
            db.execute('INSERT INTO tulpa_history(scope_id,kind,body,created_at,details) VALUES(?,?,?,?,?)',
              (sid,'manual',text.strip(),time.time(),'{}'))
        return self.detail(sid)

    def consolidate(self,sid,provider=None):
        """One bounded batch, optimistic commit after network; failures never consume it."""
        control=self.control()
        if not control['enabled']:return dict(status='off',api_called=False)
        with self.store.connect() as db:
            s=db.execute('SELECT * FROM tulpa_scopes WHERE id=?',(sid,)).fetchone()
            if not s or s['total']-s['processed']<100:return dict(status='collecting',api_called=False)
            rows=db.execute('''SELECT m.* FROM tulpa_events e JOIN messages m ON m.id=e.message_id
              WHERE e.scope_id=? AND e.ordinal>? AND e.ordinal<=? ORDER BY e.ordinal''',
              (sid,s['processed'],s['processed']+100)).fetchall()
            # A deleted message can leave an ordinal gap. Process remaining evidence honestly.
            if not rows or (s['kind']=='direct' and not any(r['is_self']==1 for r in rows)):
                db.execute('UPDATE tulpa_scopes SET processed=processed+100,status=? WHERE id=?',('no_self_participation',sid))
                return dict(status='no_self_participation',api_called=False)
            end=max(r['timestamp'] for r in rows)
            context=[]
            if s['kind']=='group':
                # A few actual neighboring messages, never a full group transcript.
                for row in rows[::20]:
                    context.extend(db.execute('''SELECT * FROM messages WHERE platform=? AND conversation_id=?
                      AND timestamp BETWEEN ? AND ? AND is_self=0 ORDER BY timestamp DESC,id DESC LIMIT 1''',
                      (s['platform'],s['conversation_id'],row['timestamp']-120000,row['timestamp'])).fetchall())
            refs={r['id']:fingerprint(r) for r in list(rows)+context}
            previous=s['automatic'] if s['valid'] else ''
        payload=dict(kind=s['kind'],previous=previous,manual_corrections=s['manual'],
          stage=[dict(id=r['id'],self=r['is_self'],text=r['content'][:140],truncated=len(r['content'])>140) for r in rows],
          context=[dict(id=r['id'],self=r['is_self'],text=r['content'][:140],truncated=len(r['content'])>140) for r in context])
        started=time.monotonic()
        if provider is None:
            from .tulpa_model import summarize
            provider=summarize
        result=provider(payload)
        memory=result.get('memory');citations=result.get('evidence_ids')
        if not isinstance(memory,str) or not memory.strip() or len(memory)>4000 or not isinstance(citations,list) or not citations or any(type(i)!=int or i not in refs for i in citations):
            logging.getLogger(__name__).warning('tulpa consolidation rejected: scope=%s stage_size=%s reason=invalid_memory_or_reference',sid,len(rows))
            raise ValueError('记忆整理结果无效，保留本批待重试。')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            c=db.execute('SELECT * FROM tulpa_control').fetchone()
            fresh=db.execute('SELECT * FROM tulpa_scopes WHERE id=?',(sid,)).fetchone()
            if not c['enabled'] or c['epoch']!=control['epoch'] or fresh['revision']!=s['revision'] or fresh['processed']!=s['processed'] or fresh['valid']!=s['valid']:
                return dict(status='superseded',api_called=True)
            for mid,digest in refs.items():
                r=db.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
                if not r or fingerprint(r)!=digest:raise ValueError('原文已变化，保留本批待重试。')
            if not s['valid']:db.execute('DELETE FROM tulpa_memory_sources WHERE scope_id=?',(sid,))
            db.executemany('INSERT OR REPLACE INTO tulpa_memory_sources VALUES(?,?,?)',[(sid,mid,d) for mid,d in refs.items()])
            db.execute('''UPDATE tulpa_scopes SET automatic=?,valid=1,processed=processed+100,revision=revision+1,
              updated_at=?,status='collecting',failures=0,retry_at=0 WHERE id=?''',(memory.strip(),time.time(),sid))
            details=dict(evidence_ids=citations,through=end,processed=s['processed']+100,
              seconds=round(time.monotonic()-started,2),usage=result.get('usage',{}))
            db.execute('INSERT INTO tulpa_history(scope_id,kind,body,created_at,details) VALUES(?,?,?,?,?)',
              (sid,'automatic',memory.strip(),time.time(),dumps(details)))
        return dict(status='updated',api_called=True,**details)

    def retrieve(self,target,instruction='',as_of=None,limit=8):
        if not self.control()['enabled']:return dict(enabled=False,memory='',manual='',episodes=[])
        from .store import tokens
        now=int(as_of or time.time()*1000);limit=max(1,min(8,limit))
        with self.store.connect() as db:
            s=db.execute('SELECT * FROM tulpa_scopes WHERE platform=? AND conversation_id=?',
              (target['platform'],target['conversation_id'])).fetchone()
            if not s:return dict(enabled=True,memory='',manual='',episodes=[])
            query=target['content']+' '+instruction
            words=list(dict.fromkeys(tokens(query).split()))[:30]
            scene,intent=signals(query)
            match=' OR '.join('"'+w.replace('"','""')+'"' for w in words)
            candidates={}
            # Indexed BM25 + same-person recency, bounded even after thousands of episodes.
            if match:
                rows=db.execute('''SELECT e.*,bm25(tulpa_episode_fts) rank FROM tulpa_episode_fts
                  JOIN tulpa_episodes e ON e.id=tulpa_episode_fts.rowid WHERE tulpa_episode_fts MATCH ?
                  AND e.scope_id=? AND e.valid=1 AND e.timestamp<? ORDER BY rank LIMIT 64''',(match,s['id'],now))
                for r in rows:candidates[r['id']]=dict(r,lexical=True)
            for r in db.execute('''SELECT * FROM tulpa_episodes WHERE scope_id=? AND valid=1 AND timestamp<?
              AND (?='direct' OR person_id=?) ORDER BY timestamp DESC LIMIT 64''',
              (s['id'],now,s['kind'],target['sender_id'])):candidates.setdefault(r['id'],dict(r,lexical=False))
            def score(e):
                similar=bool(set(scene)&set(json.loads(e['scenario']))) or e['lexical']
                same=e['person_id']==target['sender_id']
                tier=3 if same and similar else 2 if same else 1 if similar else 0
                return (tier,len(set(intent)&set(json.loads(e['intent']))),int(e['lexical']),
                        math.exp(-max(0,now-e['timestamp'])/(45*86400000)))
            selected=[]
            for e in sorted(candidates.values(),key=score,reverse=True):
                if score(e)[0]==0:continue
                refs=json.loads(e['fingerprints']);rows=[]
                for mid,digest in refs.items():
                    r=db.execute('SELECT * FROM messages WHERE id=?',(int(mid),)).fetchone()
                    if not r or r['timestamp']>=now or fingerprint(r)!=digest:break
                    rows.append(dict(r))
                else:
                    if target['id'] not in [r['id'] for r in rows]:
                        selected.append(dict(id=e['id'],incoming=json.loads(e['incoming']),outgoing=json.loads(e['outgoing']),
                          context=json.loads(e['context']),message_ids=[r['id'] for r in rows]))
                if len(selected)>=limit:break
            manual=s['manual']
            if as_of is not None:
                last_edit=db.execute("SELECT created_at FROM tulpa_history WHERE scope_id=? AND kind='manual' ORDER BY id DESC LIMIT 1",(s['id'],)).fetchone()
                if last_edit and last_edit[0]*1000>=now:manual=''
            return dict(enabled=True,scope_id=s['id'],memory=s['automatic'] if s['valid'] else '',manual=manual,episodes=selected)


class MemoryWorker:
    """One process owner (SQLite lease), bounded cadence and retry backoff."""
    def __init__(self,layer):self.layer=layer;self.stop_event=threading.Event();self.thread=None

    def start(self):
        self.thread=threading.Thread(target=self.run,name='tulpa-memory',daemon=True);self.thread.start()

    def run(self):
        while not self.stop_event.wait(15):
            if not self.layer.control()['enabled']:continue
            from .config import settings
            if not settings()['API_KEY']:
                with self.layer.store.connect() as db:
                    db.execute("UPDATE tulpa_scopes SET status='await_model' WHERE total-processed>=100")
                continue
            with self.layer.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                s=db.execute('SELECT id FROM tulpa_scopes WHERE total-processed>=100 AND retry_at<=? ORDER BY retry_at,id LIMIT 1',(time.time(),)).fetchone()
                if not s:continue
                sid=s['id'];db.execute("UPDATE tulpa_scopes SET status='updating',retry_at=? WHERE id=?",(time.time()+180,sid))
            try:
                result=self.layer.consolidate(sid)
                logging.getLogger(__name__).info('tulpa consolidation scope=%s status=%s api_called=%s',sid,result['status'],result['api_called'])
            except Exception as exc:
                logging.getLogger(__name__).warning('tulpa consolidation failed: scope=%s error_type=%s retry_seconds=900',sid,type(exc).__name__)
                # No model response, private text, paths or credentials in routine logs.
                with self.layer.store.connect() as db:
                    if db.execute('SELECT enabled FROM tulpa_control').fetchone()[0]:
                        db.execute("UPDATE tulpa_scopes SET status='update_failed',failures=failures+1,retry_at=? WHERE id=?",(time.time()+900,sid))

    def stop(self):self.stop_event.set()
