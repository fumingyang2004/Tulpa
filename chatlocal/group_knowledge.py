"""Optional NapCat enriched sources; uses the existing authenticated client."""
import hashlib
import json
import time

from .artifact_onebot import OneBot,config
from .artifacts import ArtifactError
from .normalize import display_time


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS qq_knowledge(
        id INTEGER PRIMARY KEY AUTOINCREMENT,conversation_id TEXT NOT NULL,conversation TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('notice','essence')),native_id TEXT NOT NULL,
        publisher TEXT NOT NULL,publisher_id TEXT NOT NULL,timestamp INTEGER,content TEXT NOT NULL,
        metadata_json TEXT NOT NULL,canonical_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
        fetched_at REAL NOT NULL,source_api TEXT NOT NULL,link_basis TEXT,
        UNIQUE(conversation_id,kind,native_id));
      CREATE INDEX IF NOT EXISTS knowledge_scope ON qq_knowledge(conversation_id,kind,timestamp);
      CREATE TABLE IF NOT EXISTS qq_knowledge_sync(
        conversation_id TEXT PRIMARY KEY,conversation TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,
        last_attempt REAL,last_success REAL,status TEXT NOT NULL DEFAULT 'pending',detail TEXT NOT NULL DEFAULT '');
    ''')


def short_id(native_id,group):
    # NapCat MessageUnique.createUniqueMsgId (native NT id | chatType | group).
    return int.from_bytes(hashlib.md5(f'{native_id}|2|{group}'.encode()).digest()[:4],'big') & 0x7fffffff


def normalize(kind,item):
    if not isinstance(item,dict):raise ArtifactError('QQ资料格式不兼容；旧缓存保留。')
    if kind=='notice':
        msg=item.get('message')
        if not item.get('notice_id') or not isinstance(msg,dict) or not isinstance(msg.get('text'),str):
            raise ArtifactError('公告缺少稳定编号或正文。')
        ts=item.get('publish_time')
        if not isinstance(ts,(int,float)) or ts<=0:raise ArtifactError('公告时间无效。')
        meta=dict(images=msg.get('images',msg.get('image',[])),read_num=item.get('read_num'))
        return dict(native_id=str(item['notice_id']),publisher=str(item.get('sender_id','')),publisher_id=str(item.get('sender_id','')),
            timestamp=int(ts*1000),content=msg['text'],metadata=meta)
    if any(type(item.get(k)) is not int for k in ('msg_seq','msg_random','message_id','sender_id')):
        raise ArtifactError('精华缺少原生seq/random或发送者身份。')
    parts=item.get('content')
    if not isinstance(parts,list):raise ArtifactError('精华消息段格式不兼容。')
    text=''.join(str(p.get('data',{}).get('text','')) for p in parts if isinstance(p,dict) and p.get('type')=='text')
    # Metadata is retained locally, with no resource fetch or original rich XML.
    images=[{k:p.get('data',{}).get(k) for k in ('file','url')} for p in parts if isinstance(p,dict) and p.get('type')=='image']
    meta={k:item.get(k) for k in ('msg_seq','msg_random','message_id','operator_id','operator_nick','operator_time')}
    meta.update(images=images,pure_text=bool(parts) and all(isinstance(p,dict) and p.get('type')=='text' for p in parts))
    sent=item.get('sender_time')
    timestamp=int(sent*1000) if type(sent) in (int,float) and sent>0 else None
    return dict(native_id=f"{item['msg_seq']}:{item['msg_random']}",publisher=str(item.get('sender_nick') or item['sender_id']),
        publisher_id=str(item['sender_id']),timestamp=timestamp,content=text,metadata=meta)


def persist(store,cid,name,kind,items):
    if not isinstance(items,list) or len(items)>3000:raise ArtifactError('资料超过3000项或接口格式不兼容；未替换旧缓存。')
    values=[normalize(kind,item) for item in items]
    if sum(len(json.dumps(v,ensure_ascii=False)) for v in values)>4*1024*1024:raise ArtifactError('资料超过4MiB预算。')
    now=time.time();linked=0
    with store.connect() as db:
        candidates={}
        if kind=='essence':
            for row in db.execute("SELECT id,source_id,sender_id,content,timestamp FROM messages WHERE platform='qq' AND conversation_id=?",(cid,)):
                if str(row['source_id']).isdigit():candidates.setdefault(short_id(row['source_id'],cid.split(':')[-1]),[]).append(row)
        for value in values:
            canonical=None;basis=None;meta=value['metadata']
            if kind=='essence' and meta['pure_text'] and value['content'].strip():
                matches=[r for r in candidates.get(meta['message_id'],[]) if r['sender_id']==value['publisher_id'] and r['content']==value['content']
                         and (value['timestamp'] is None or r['timestamp']==value['timestamp'])]
                if len(matches)==1:
                    canonical=matches[0]['id'];value['timestamp']=matches[0]['timestamp'];linked+=1
                    basis='napcat_short_id+same_group+sender_id+exact_text_unique'
            db.execute('''INSERT INTO qq_knowledge(conversation_id,conversation,kind,native_id,publisher,publisher_id,timestamp,
                content,metadata_json,canonical_message_id,fetched_at,source_api,link_basis) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(conversation_id,kind,native_id) DO UPDATE SET publisher=excluded.publisher,
                publisher_id=excluded.publisher_id,timestamp=excluded.timestamp,content=excluded.content,
                metadata_json=excluded.metadata_json,canonical_message_id=excluded.canonical_message_id,
                fetched_at=excluded.fetched_at,link_basis=excluded.link_basis''',
                (cid,name,kind,value['native_id'],value['publisher'],value['publisher_id'],value['timestamp'],value['content'],
                 json.dumps(meta,ensure_ascii=False),canonical,now,'_get_group_notice' if kind=='notice' else 'get_essence_msg_list',basis))
    return dict(count=len(values),linked=linked,note='缓存保留已获取版本；接口当前未返回的旧条目不会被视作已撤回。')


def refresh(store,cid,client=None):
    with store.connect() as db:
        row=db.execute("SELECT conversation FROM messages WHERE platform='qq' AND conversation_type='group' AND conversation_id=? LIMIT 1",(cid,)).fetchone()
        if not row:raise ArtifactError('请选择已导入的QQ群。')
        name=row[0]
        db.execute('''INSERT INTO qq_knowledge_sync(conversation_id,conversation,last_attempt) VALUES(?,?,?)
            ON CONFLICT(conversation_id) DO UPDATE SET last_attempt=excluded.last_attempt''',(cid,name,time.time()))
    results={}
    try:
        client=OneBot(client=client);group=client.account(cid)
        for kind,action in [('notice','_get_group_notice'),('essence','get_essence_msg_list')]:
            try:results[kind]=persist(store,cid,name,kind,client.call(action,dict(group_id=group)))
            except ArtifactError as exc:results[kind]=dict(error=str(exc))
        ok=all('error' not in v for v in results.values())
        with store.connect() as db:db.execute('''UPDATE qq_knowledge_sync SET status=?,detail=?,
            last_success=CASE WHEN ? THEN ? ELSE last_success END WHERE conversation_id=?''',
            ('ok' if ok else 'partial',json.dumps(results,ensure_ascii=False),ok,time.time(),cid))
        return results
    except ArtifactError as exc:
        with store.connect() as db:db.execute("UPDATE qq_knowledge_sync SET status='error',detail=? WHERE conversation_id=?",(str(exc),cid))
        raise


def scope(plan,alias='k'):
    parts=['1=1' if 'qq' in plan.platforms else '0=1'];args=[]
    if plan.conversations:
        cids=[c for p,c in map(json.loads,plan.conversations) if p=='qq']
        parts.append(alias+'.conversation_id IN ('+','.join('?' for _ in cids)+')');args+=cids
    for value,op in [(plan.start,'>='),(plan.end,'<')]:
        if value is not None:parts.append(alias+'.timestamp'+op+'?');args.append(value)
    if plan.snapshot_max_id is not None:
        parts.append(f'({alias}.canonical_message_id IS NULL OR {alias}.canonical_message_id<=?)');args.append(plan.snapshot_max_id)
    return ' AND '.join(parts),args


def public(row):
    row=dict(row);meta=json.loads(row['metadata_json'])
    result={k:row[k] for k in ('id','conversation_id','conversation','kind','native_id','publisher','publisher_id','timestamp',
        'content','canonical_message_id','fetched_at','source_api','link_basis')}
    # Do not expose remote image URLs or local locators to the language model/UI.
    result.update(platform='qq',citation_id='K'+str(row['id']),source_type='qq_'+row['kind'],
        image_count=len(meta.get('images') or []),operator_time=meta.get('operator_time'),
        time=display_time(row['timestamp']) if row['timestamp'] else '原消息时间未知；加精时间不是发送时间',
        provenance='QQ / OneBot '+row['source_api'])
    return result
