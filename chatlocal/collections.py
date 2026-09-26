"""Named reference sets. No chat text, media, transcripts or binaries are copied."""
import hashlib
import json
import re
import time
import uuid

from .media import hydrate,media_at,public_media
from .retrieval import scope_sql

TYPES=('message','media','voice','artifact_source','artifact_chunk','qq_notice','qq_essence')


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS evidence_collections(
        id TEXT PRIMARY KEY,title TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS evidence_collection_items(
        id TEXT PRIMARY KEY,collection_id TEXT NOT NULL REFERENCES evidence_collections(id) ON DELETE CASCADE,
        source_type TEXT NOT NULL,source_id TEXT NOT NULL,fingerprint TEXT NOT NULL,added_at REAL NOT NULL,
        UNIQUE(collection_id,source_type,source_id));
      CREATE INDEX IF NOT EXISTS collection_item_set ON evidence_collection_items(collection_id,added_at);
    ''')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


class Collections:
    def __init__(self,store):self.store=store

    def resolve(self,source_type,source_id,plan=None):
        """Resolve afresh and fingerprint stable identity, never trust a display ID."""
        if source_type not in TYPES or not isinstance(source_id,str) or not re.fullmatch(r'[0-9]+(?::[0-9]+)?',source_id):
            raise ValueError('来源类型或ID格式无效。')
        parts=list(map(int,source_id.split(':')))
        if len(parts)!=(2 if source_type in ('media','artifact_chunk') else 1):raise ValueError('来源ID格式无效。')
        if source_type in ('message','media','voice'):
            mid=parts[0];where,args=scope_sql(plan) if plan else ('1=1',[])
            with self.store.connect() as db:
                row=db.execute(f'SELECT * FROM messages m WHERE {where} AND m.id=?',args+[mid]).fetchone()
                if not row:raise ValueError('来源已删除或不在当前范围内。')
                row=hydrate(row);identity=[row['dedup_key']]
                data=dict(message_id=mid,platform=row['platform'],conversation_id=row['conversation_id'],
                    conversation=row['conversation'],sender=row['sender'],timestamp=row['timestamp'],
                    title=row['content'][:240],url=f'/?anchor={mid}')
                if source_type=='media':
                    item=media_at(row['media'],parts[1])
                    if item is None:raise ValueError('原媒体引用已不存在。')
                    meta=item.get('metadata',{});identity+=[parts[1],meta.get('md5') or item.get('sha256') or meta.get('filename')]
                    data['media']=next(v for v in public_media(mid,row['media']) if v['index']==parts[1])
                    data['title']=row['content'][:240] or '聊天图片 / 表情'
                elif source_type=='voice':
                    voice=db.execute('SELECT message_id FROM voice_sources WHERE message_id=?',(mid,)).fetchone()
                    if not voice:raise ValueError('原生语音引用已不存在。')
                    data['title']='原生语音 · '+row['sender']
            return dict(data,fingerprint=digest(identity))
        if source_type.startswith('artifact_'):
            from .artifacts import Artifacts
            layer=Artifacts(self.store);row=layer.get(parts[0],plan);data=layer.public(row)
            identity=[row['source_key']];data['title']=row['filename'];data['url']=f'/?file={parts[0]}'
            if source_type=='artifact_chunk':
                with self.store.connect() as db:
                    chunk=db.execute('SELECT * FROM artifact_chunks WHERE id=? AND sha256=?',(parts[1],row['sha256'])).fetchone()
                if not chunk:raise ValueError('原文件片段已删除或索引已改变。')
                identity += [row['sha256'],chunk['ordinal'],digest(chunk['text'])]
                data.update(chunk_id=chunk['id'],locator=json.loads(chunk['locator']),excerpt=chunk['text'][:500],
                    url=f'/?file={parts[0]}&chunk={parts[1]}')
            return dict(data,fingerprint=digest(identity))
        from .group_knowledge import scope,public
        where,args=scope(plan) if plan else ('1=1',[])
        with self.store.connect() as db:
            row=db.execute(f'SELECT * FROM qq_knowledge k WHERE {where} AND k.id=? AND k.kind=?',
                           args+[parts[0],source_type.removeprefix('qq_')]).fetchone()
        if not row:raise ValueError('QQ资料已删除或不在当前范围内。')
        data=public(row);data['title']=data['content'][:240] or data['source_type'];data['url']=f'/?knowledge={parts[0]}'
        return dict(data,fingerprint=digest([row['conversation_id'],row['kind'],row['native_id']]))

    def list(self,offset=0,limit=50):
        with self.store.connect() as db:
            total=db.execute('SELECT count(*) FROM evidence_collections').fetchone()[0]
            rows=db.execute('''SELECT c.*,count(i.id) item_count FROM evidence_collections c
                LEFT JOIN evidence_collection_items i ON i.collection_id=c.id GROUP BY c.id
                ORDER BY c.updated_at DESC,c.id LIMIT ? OFFSET ?''',(limit,offset)).fetchall()
        return dict(collections=[dict(r) for r in rows],total=total,next_offset=offset+limit if offset+limit<total else None)

    def create(self,title,items=None,*,plan=None):
        title=self._title(title);cid=uuid.uuid4().hex;now=time.time()
        refs=[]
        if items is not None:
            if not isinstance(items,list) or not 1<=len(items)<=30:raise ValueError('每次可添加1–30个引用。')
            for item in items:
                if not isinstance(item,dict) or set(item)!={'source_type','source_id'}:raise ValueError('引用格式无效。')
                refs.append((item,self.resolve(item['source_type'],item['source_id'],plan)['fingerprint']))
        with self.store.connect() as db:
            db.execute('INSERT INTO evidence_collections VALUES(?,?,?,?)',(cid,title,now,now));added=0
            for item,fingerprint in refs:
                added+=db.execute('INSERT OR IGNORE INTO evidence_collection_items VALUES(?,?,?,?,?,?)',
                    (uuid.uuid4().hex,cid,item['source_type'],item['source_id'],fingerprint,now)).rowcount
        return dict(id=cid,title=title,added=added,url='/?collection='+cid)

    @staticmethod
    def _title(value):
        if not isinstance(value,str) or not value.strip() or len(value)>100:raise ValueError('集合名称需要1–100字。')
        return value.strip()

    def get(self,cid,*,offset=0,limit=30,plan=None):
        with self.store.connect() as db:
            head=db.execute('SELECT * FROM evidence_collections WHERE id=?',(cid,)).fetchone()
            if not head:raise ValueError('证据集合不存在。')
            rows=db.execute('SELECT * FROM evidence_collection_items WHERE collection_id=? ORDER BY added_at,id LIMIT ? OFFSET ?',
                            (cid,limit,offset)).fetchall()
            total=db.execute('SELECT count(*) FROM evidence_collection_items WHERE collection_id=?',(cid,)).fetchone()[0]
        items=[]
        for row in rows:
            item={k:row[k] for k in ('id','source_type','source_id','added_at')}
            try:
                source=self.resolve(row['source_type'],row['source_id'],plan)
                if source.pop('fingerprint')!=row['fingerprint']:raise ValueError('来源身份已改变；旧引用未自动改指向新内容。')
                item.update(status='available',source=source)
            except ValueError as exc:item.update(status='unavailable',reason=str(exc))
            items.append(item)
        return dict(head,items=items,item_count=total,next_offset=offset+limit if offset+limit<total else None,url='/?collection='+cid)

    def add(self,cid,items,*,plan=None):
        if not isinstance(items,list) or not 1<=len(items)<=30:raise ValueError('每次可添加1–30个引用。')
        from .activity import evidence_access
        with evidence_access(self.store):
            refs=[]
            for item in items:
                if not isinstance(item,dict) or set(item)!={'source_type','source_id'}:raise ValueError('引用需要source_type和source_id。')
                resolved=self.resolve(item['source_type'],item['source_id'],plan)
                refs.append((item,resolved['fingerprint']))
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if not db.execute('SELECT 1 FROM evidence_collections WHERE id=?',(cid,)).fetchone():raise ValueError('证据集合不存在。')
                count=db.execute('SELECT count(*) FROM evidence_collection_items WHERE collection_id=?',(cid,)).fetchone()[0]
                if count+len(refs)>2000:raise ValueError('每个集合最多2000个引用，请另建集合。')
                added=0
                for item,fingerprint in refs:
                    added+=db.execute('INSERT OR IGNORE INTO evidence_collection_items VALUES(?,?,?,?,?,?)',
                        (uuid.uuid4().hex,cid,item['source_type'],item['source_id'],fingerprint,time.time())).rowcount
                db.execute('UPDATE evidence_collections SET updated_at=? WHERE id=?',(time.time(),cid))
        return dict(id=cid,added=added,url='/?collection='+cid,note='仅保存来源引用；未复制原文或文件。')

    def rename(self,cid,title):
        title=self._title(title)
        with self.store.connect() as db:
            if not db.execute('UPDATE evidence_collections SET title=?,updated_at=? WHERE id=?',(title,time.time(),cid)).rowcount:
                raise ValueError('证据集合不存在。')
        return dict(id=cid,title=title)

    def remove(self,cid,item_id):
        with self.store.connect() as db:
            n=db.execute('DELETE FROM evidence_collection_items WHERE collection_id=? AND id=?',(cid,item_id)).rowcount
        return dict(removed=n,note='只移除引用，原始资料保留。')

    def delete(self,cid):
        with self.store.connect() as db:n=db.execute('DELETE FROM evidence_collections WHERE id=?',(cid,)).rowcount
        return dict(deleted=n,note='只删除集合及引用，原始聊天、文件、图片和语音保留。')
