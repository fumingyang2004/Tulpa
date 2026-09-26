"""Focused adversarial and reference-integrity checks; fixtures are not live NapCat."""
import hashlib
import json
import sqlite3
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from chatlocal.store import Store
from chatlocal.retrieval import Plan,scope_sql
from chatlocal.investigation_sql import query
from chatlocal.agent_tools import ChatTools
from chatlocal.collections import Collections
from chatlocal.group_knowledge import persist,short_id
from chatlocal.llm import validate_answer


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='investigation-') as tmp:
        root=Path(tmp);store=Store(root/'chats.sqlite3');cid='1:group:100'
        rows=[]
        for i,(sender,text) in enumerate([('a','服务器还续费吗？'),('b','感觉没有必要了。'),('c','月底关就好了。'),('a','那月底关闭？'),('b','可以。')]):
            rows.append(dict(platform='qq',conversation_id=cid,conversation='fixture group',conversation_type='group',sender=sender,sender_id=sender,
                source_id=str(1000+i),timestamp=f'2026-09-23 10:00:{i:02}',content=text,is_self=False))
        rows[1]['reply_to']=dict(source_id='1000',content=rows[0]['content'],sender='a')
        rows += [dict(rows[0],platform='wechat',content='EXCLUDED_WECHAT',source_id='wx'),
                 dict(rows[0],conversation_id='other',content='EXCLUDED_CONVERSATION',source_id='other'),
                 dict(rows[0],timestamp='2026-09-20',content='EXCLUDED_DATE',source_id='date'),
                 dict(rows[0],content='EXCLUDED_SNAPSHOT',source_id='late')]
        f=root/'messages.json';f.write_text(json.dumps(rows,ensure_ascii=False),'utf-8');store.import_file(f)
        plan=Plan(platforms=['qq'],conversations=[json.dumps(['qq',cid])],start=rows_ts(store,1)-1000,end=rows_ts(store,5)+1000,snapshot_max_id=8)
        good=query(store,plan,'WITH counts AS (SELECT platform,count(*) n FROM agent_messages GROUP BY platform) SELECT * FROM counts')
        assert good.get('rows')==[['qq',5]],good
        text=query(store,plan,'SELECT content FROM agent_messages')
        assert 'EXCLUDED' not in json.dumps(text),text
        blocked=["DELETE FROM agent_messages","UPDATE agent_messages SET content='x'","DROP TABLE agent_messages",
            "CREATE TABLE x(a)","ALTER TABLE agent_messages ADD COLUMN x", "INSERT INTO agent_messages(id) VALUES(100)",
            "ATTACH DATABASE ':memory:' AS other","DETACH DATABASE main","PRAGMA user_version=10","PRAGMA table_info(agent_messages)",
            "SELECT 1; SELECT 2","SELECT * FROM messages","SELECT * FROM sqlite_master","SELECT * FROM pragma_table_info('agent_messages')",
            "SELECT load_extension('x')","SELECT readfile('C:/Lab0921/.env')","SELECT randomblob(100000000)",
            "WITH agent_messages AS (SELECT * FROM messages) SELECT * FROM agent_messages",
            "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) SELECT sum(n) FROM x"]
        for sql in blocked:
            r=query(store,plan,sql);assert r.get('error'),(sql,r)
        limited=query(store,plan,'SELECT id FROM agent_messages',2);assert limited['truncated'] and limited['returned_rows']==2
        wide="SELECT replace('"+'a'*7000+"','a','ab') AS wide"
        capped=query(store,plan,wide)
        assert capped['truncated'] and capped['returned_rows']==0,capped
        bounded=query(store,plan,"SELECT replace(content,'','x') AS a,content AS b,content AS c FROM agent_messages a CROSS JOIN agent_messages b")
        assert bounded.get('error')  # Ambiguous column diagnosis is retryable.
        huge=query(store,plan,'SELECT count(*) FROM '+','.join('agent_messages m'+str(i) for i in range(12)))
        assert huge.get('error') and huge['total_time']<8,huge
        tools=ChatTools(store,plan,max_chars=50000,max_calls=50)
        fake=tools.execute('query_communication_db',dict(sql='SELECT 999999 AS message_id'))
        assert not tools.messages and fake.get('citation_id')
        assert not validate_answer(dict(claims=[dict(text='fake',evidence_ids=[999999])]),[],analysis_evidence=tools.analysis_evidence)['claims']
        statistical=validate_answer(dict(claims=[dict(text='计算结果',evidence_ids=[],analysis_evidence_ids=[fake['citation_id']])]),[],analysis_evidence=tools.analysis_evidence)
        assert statistical['claims']
        strict=tools.execute('get_interaction_threads',dict(platform='qq',participants=['a','b','c'],mode='strict'))
        assert strict['threads'] and any(e['basis']=='reply' for t in strict['threads'] for e in t['relations']),strict
        contextual=tools.execute('get_interaction_threads',dict(platform='qq',participants=['a','b','c'],mode='contextual'))
        assert any(e['basis']=='contextual' for t in contextual['threads'] for e in t['relations']),contextual
        assert tools.execute('get_interaction_threads',dict(platform='qq',participants=['a','b',7])).get('error')
        collection=tools.execute('evidence_collection',dict(action='create',title='fixture evidence'));saved=collection['id']
        assert tools.execute('evidence_collection',dict(action='add',collection_id=saved,items=[dict(source_type='message',source_id='6')])).get('error')
        added=tools.execute('evidence_collection',dict(action='add',collection_id=saved,items=[dict(source_type='message',source_id='1')]))
        assert added.get('added')==1,added
        atomic=tools.execute('evidence_collection',dict(action='create',title='atomic',items=[dict(source_type='message',source_id='1')]))
        assert atomic.get('added')==1,atomic
        bad_atomic=tools.execute('evidence_collection',dict(action='create',title='must not create',items=[dict(source_type='message',source_id='6')]))
        assert bad_atomic.get('error')
        layer=Collections(store);assert layer.get(saved)['items'][0]['status']=='available'
        assert layer.add(saved,[dict(source_type='message',source_id='1')])['added']==0
        # Re-opening the store demonstrates durable reference persistence.
        assert Collections(Store(store.path)).get(saved)['item_count']==1
        layer.rename(saved,'renamed');before=store.message(1)
        assert layer.delete(saved)['deleted']==1 and store.message(1)==before
        lost=layer.create('missing source')['id'];layer.add(lost,[dict(source_type='message',source_id='1')])
        with store.connect() as db:db.execute("UPDATE messages SET dedup_key='changed-identity' WHERE id=1")
        assert layer.get(lost)['items'][0]['status']=='unavailable'
        # An unrelated participant interrupts a contextual run.
        with store.connect() as db:db.execute("UPDATE messages SET sender_id='outsider' WHERE id=3")
        from chatlocal.interactions import extract
        w,p=scope_sql(plan);isolated=extract(store,w,p,['a','b'],mode='contextual')
        assert not any(e['basis']=='contextual' for t in isolated['threads'] for e in t['relations'])
        # Real adapter-shaped fixture, explicitly UNVALIDATED_LIVE.
        notice=dict(notice_id='n1',sender_id=1,publish_time=1790128800,message=dict(text='fixture notice',images=[dict(id='x',width=10,height=10)]))
        assert persist(store,cid,'fixture group','notice',[notice])['count']==1
        essence=dict(msg_seq=5,msg_random=8,message_id=short_id('1001','100'),sender_id=0,sender_nick='b',operator_time=1790128900,
                     content=[dict(type='text',data=dict(text=rows[1]['content']))])
        # Wrong sender must not link, even if NapCat's short ID matches.
        assert persist(store,cid,'fixture group','essence',[essence])['linked']==0
        with store.connect() as db:db.execute("UPDATE messages SET sender_id='2' WHERE id=2")
        essence['sender_id']=2
        assert persist(store,cid,'fixture group','essence',[essence])['linked']==1
        with store.connect() as db:
            k=dict(db.execute("SELECT * FROM qq_knowledge WHERE kind='essence'").fetchone())
            assert k['canonical_message_id']==2 and k['timestamp']==rows_ts(store,2)
            # No message content column in the reference tables.
            cols={r[1] for r in db.execute('PRAGMA table_info(evidence_collection_items)')}
            assert cols=={'id','collection_id','source_type','source_id','fingerprint','added_at'}
        sql=query(store,plan,'SELECT COUNT(*) FROM agent_messages')
        assert sql.get('rows')==[[5]],sql
        with store.connect() as db:
            knowledge_refs=[dict(source_type='qq_'+r['kind'],source_id=str(r['id'])) for r in db.execute('SELECT id,kind FROM qq_knowledge')]
        knowledge_set=layer.create('knowledge references',knowledge_refs)['id']
        assert len(layer.get(knowledge_set)['items'])==2
        # An unlinked essence remains a valid API source, never a fabricated message.
        with store.connect() as db:
            db.execute('DELETE FROM provenance WHERE message_id=2')
            db.execute('DELETE FROM messages WHERE id=2')
        sources=layer.get(knowledge_set)['items']
        assert all(i['status']=='available' for i in sources)
        assert next(i['source'] for i in sources if i['source_type']=='qq_essence')['canonical_message_id'] is None
        # Growth checks use isolated fixtures, not synthetic rows in the user's DB.
        now=time.time();heads=[];refs=[]
        fingerprint=layer.resolve('message','3')['fingerprint']
        for i in range(1000):
            cid_i=uuid.uuid4().hex;heads.append((cid_i,'growth fixture '+str(i),now,now))
            refs.append((uuid.uuid4().hex,cid_i,'message','3',fingerprint,now))
        with store.connect() as db:
            db.executemany('INSERT INTO evidence_collections VALUES(?,?,?,?)',heads)
            db.executemany('INSERT INTO evidence_collection_items VALUES(?,?,?,?,?,?)',refs)
        begin=time.perf_counter();page=layer.list(offset=900,limit=50);list_seconds=time.perf_counter()-begin
        assert len(page['collections'])==50 and page['total']>=1000 and page['next_offset']==950
        many=[dict(notice,notice_id='growth'+str(i)) for i in range(2000)]
        begin=time.perf_counter();bulk=persist(store,cid,'fixture group','notice',many);knowledge_seconds=time.perf_counter()-begin
        assert bulk['count']==2000
        fresh=ChatTools(store,Plan(platforms=['qq']),max_chars=50000)
        knowledge=fresh.execute('get_group_knowledge',dict(group_id=cid,limit=12))
        assert len(knowledge['sources'])==12 and knowledge['has_more'] and knowledge['next_offset']==12,knowledge
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from chatlocal.investigation_routes import install_investigation_routes
        app=FastAPI();install_investigation_routes(app,store);client=TestClient(app)
        # Enabling a never-synced group must persist, without fetching any API.
        assert client.post('/api/knowledge/schedule',json=dict(conversation_id=cid,enabled=True)).status_code==200
        state=client.get('/api/knowledge/status').json()
        assert next(r for r in state['sync'] if r['conversation_id']==cid)['enabled']==1
        assert client.post('/api/knowledge/schedule',json=dict(conversation_id=cid,enabled=False)).status_code==200
        assert client.post('/api/knowledge/schedule',json=dict(conversation_id='missing',enabled=True)).status_code==400
        client.close()
        print(json.dumps(dict(status='PASS',sql_rejections=len(blocked),scope='platform+conversation+time+snapshot',
            sql_example_seconds=good['total_time'],timeout_seconds=huge['total_time'],collections='reference-only, scoped, durable, delete-safe',
            fixture_1000_collection_list_seconds=round(list_seconds,4),fixture_2000_notice_persist_seconds=round(knowledge_seconds,4),
            knowledge='fixture contract only / UNVALIDATED_LIVE'),ensure_ascii=False))


def rows_ts(store,mid):return store.message(mid)['timestamp']
if __name__=='__main__':main()
