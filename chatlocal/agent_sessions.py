"""Local conversation persistence, separate from the imported chat database."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime

from .config import DATA, local_path
from .normalize import TZ


def turn_memory(turn):
    """Keep referents and read cursors, not old chat bodies or raw tool logs."""
    record=turn['record']
    bundle=record.get('bundle',{})
    claims=record.get('result',{}).get('claims',[])[:6]
    cited={mid for claim in claims for mid in claim.get('evidence_ids',[])}
    rows=bundle.get('messages',[])
    conversations={}
    # Cited conversations precede incidental search hits; neither is a hard filter.
    for row in sorted(rows,key=lambda row:row['id'] not in cited):
        key=(row['platform'],row['conversation_id'])
        if key not in conversations and len(conversations)<8:
            conversations[key]={k:row.get(k) for k in
                ('platform','conversation_id','conversation','conversation_type')}
    reads=[]
    for event in record.get('events',[]):
        if not isinstance(event,dict) or event.get('type')!='tool_end' or event.get('name')!='read_conversation':
            continue
        result=event.get('result',{})
        if result.get('error'): continue
        args=event.get('arguments',{})
        read={k:args[k] for k in ('platform','conversation_id','start','end','limit','offset','order','snapshot_max_id','has_media') if k in args}
        read.setdefault('order','oldest')
        read.setdefault('offset',0)
        read.update(returned=len(result.get('messages',[])),
                    next_offset=result.get('next_offset'),has_more=result.get('has_more'),
                    budget_limited=result.get('budget_limited',False))
        if result.get('snapshot_max_id') is not None:
            read['snapshot_max_id']=result['snapshot_max_id']
        reads.append(read)
    scope=bundle.get('plan',{})
    file_refs={ref for claim in claims for ref in claim.get('artifact_evidence_ids',[])}
    files={}
    for ref,item in bundle.get('file_evidence',{}).items():
        if ref in file_refs and len(files)<6:
            files[item['id']]={k:item.get(k) for k in ('id','filename','platform','conversation_id','conversation','message_id')}
    return dict(question=turn['question'],status=turn['status'],claims=claims,
                query_state=dict(conversations=list(conversations.values()),reads=reads[-3:],
                    files=list(files.values()),
                    people=bundle.get('people',[])[:6],
                    time_range={k:scope.get(k) for k in ('start','end')}))


def history_messages(memory):
    """Explicit user/assistant roles make ellipses act like conversation turns."""
    messages=[]
    for turn in (memory or {}).get('previous_turns',[]):
        messages.append(dict(role='user',content=turn['question']))
        summary={k:turn[k] for k in ('status','claims','query_state') if k in turn}
        summary['note']='历史回答和定位信息，仅用于承接语义；不是本轮证据。'
        messages.append(dict(role='assistant',content=json.dumps(summary,ensure_ascii=False)))
    return messages


class Sessions:
    def __init__(self,path=None):
        self.path=local_path(path or DATA/'agent-sessions.sqlite3')
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,title TEXT,updated TEXT);
            CREATE TABLE IF NOT EXISTS turns(id TEXT PRIMARY KEY,session_id TEXT,question TEXT,
              status TEXT,created TEXT,options TEXT,record TEXT);
            CREATE INDEX IF NOT EXISTS turns_session ON turns(session_id,created);
            ''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self):
        sid=uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO sessions VALUES(?,?,?)',(sid,'新对话',datetime.now(TZ).isoformat()))
        return sid

    def choices(self):
        with self.connect() as db:
            return [(r['updated'][5:16]+' · '+r['title'],r['id']) for r in
                    db.execute('SELECT * FROM sessions ORDER BY updated DESC LIMIT 100')]

    def delete(self,sid):
        """Delete only this local Agent dialogue, including its saved turns."""
        if not isinstance(sid,str) or not sid:raise ValueError('请选择要删除的对话。')
        with self.connect() as db:
            db.execute('DELETE FROM turns WHERE session_id=?',(sid,))
            return bool(db.execute('DELETE FROM sessions WHERE id=?',(sid,)).rowcount)

    def history(self,sid):
        with self.connect() as db:
            rows=[dict(r,options=json.loads(r['options']),record=json.loads(r['record'])) for r in
                  db.execute('SELECT * FROM turns WHERE session_id=? ORDER BY created,id',(sid,))]
        for row in rows:
            bundle=row['record'].get('bundle',{})
            if 'contexts' in bundle:
                bundle['contexts']={int(k):v for k,v in bundle['contexts'].items()}
        return rows

    def start(self,sid,question,options):
        tid=uuid.uuid4().hex
        now=datetime.now(TZ).isoformat()
        with self.connect() as db:
            if db.execute('SELECT 1 FROM sessions WHERE id=?',(sid,)).fetchone() is None:
                raise ValueError('对话不存在，请新建对话。')
            count=db.execute('SELECT count(*) FROM turns WHERE session_id=?',(sid,)).fetchone()[0]
            db.execute('UPDATE sessions SET updated=?,title=CASE WHEN ?=0 THEN ? ELSE title END WHERE id=?',
                       (now,count,question[:35],sid))
            db.execute('INSERT INTO turns VALUES(?,?,?,?,?,?,?)',
                       (tid,sid,question,'running',now,json.dumps(options,ensure_ascii=False),'{}'))
        return tid

    def finish(self,tid,status,record):
        with self.connect() as db:
            db.execute('UPDATE turns SET status=?,record=? WHERE id=?',
                       (status,json.dumps(record,ensure_ascii=False),tid))

    def memory(self,sid):
        """Bounded dialogue plus verified conversation IDs and pagination state.

        Whole previous turns are omitted rather than splitting tool-call pairs.
        Previous answers supply referents, never substitute for current evidence.
        """
        history=[r for r in self.history(sid) if r['status']!='running']
        selected=[]
        for turn in reversed(history[-6:]):
            item=turn_memory(turn)
            if len(json.dumps(selected+[item],ensure_ascii=False))>12000: break
            selected.insert(0,item)
        return dict(previous_turns=selected,older_turns_omitted=len(history)>len(selected),
                    note='历史回答仅用于理解追问和指代；其中证据必须通过工具重新读取后才能引用。')
