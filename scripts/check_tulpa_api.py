"""UI boundaries and live import integration, no real client or provider."""
import json,sys,tempfile,time,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from fastapi import FastAPI
from fastapi.testclient import TestClient
from chatlocal.store import Store
from chatlocal.tulpa import Tulpa
from chatlocal.tulpa_routes import install_tulpa_routes
from reader_media import resolve_qq_quote
from types import SimpleNamespace


with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as tmp:
    folder=Path(tmp);store=Store(folder/'test.sqlite3');app=FastAPI();install_tulpa_routes(app,store,background=False)
    payload=[dict(platform='qq',conversation_id='g',conversation='测试群',conversation_type='group',sender='本人',sender_id='1',
      timestamp=int(time.time()*1000)+1000,content='今天写代码',is_self=True,source_id='1')]
    path=folder/'messages.json';path.write_text(json.dumps(payload),encoding='utf-8')
    store.import_file(path);assert not Tulpa(store).listing()['scopes']
    payload[0]['source_id']='2';path.write_text(json.dumps(payload),encoding='utf-8')
    transition=dict(platform='qq',previous={},next={'test':1},observed_at=time.time())
    store.import_file(path,live=transition)
    assert Tulpa(store).listing()['scopes'][0]['total']==1
    store.import_file(path,live=dict(transition,previous={'test':1}))
    assert Tulpa(store).listing()['scopes'][0]['total']==1
    payload[0]['source_id']='3';payload[0]['content']='自动增量文字';path.write_text(json.dumps(payload),encoding='utf-8')
    store.import_file(path,discovery=True)
    assert Tulpa(store).listing()['scopes'][0]['total']==2
    payload[0].update(source_id='4',content='引用回复',reply_to={'source_id':'3','content':'自动增量文字'})
    path.write_text(json.dumps(payload),encoding='utf-8');store.import_file(path,discovery=True)
    payload[0]['reply_to']={'content':'旧导出只有预览'}
    path.write_text(json.dumps(payload),encoding='utf-8');store.import_file(path,discovery=True)
    with store.connect() as local:
        quoted=json.loads(local.execute("SELECT reply_to FROM messages WHERE source_id='4'").fetchone()[0])
        assert quoted=={'source_id':'3','content':'自动增量文字'}
    payload[0]['reply_to']={'source_id':'2','content':'错误的目标'}
    path.write_text(json.dumps(payload),encoding='utf-8')
    try:store.import_file(path,discovery=True)
    except ValueError:pass
    else:raise AssertionError('conflicting quote identity must roll back')
    assert Tulpa(store).listing()['scopes'][0]['total']==3
    with TestClient(app) as client:
        headers={'X-Tulpa-UI':'1'}
        assert client.get('/api/tulpa',headers={'Origin':'https://outside.test'}).status_code==403
        assert client.post('/api/tulpa/control',json={'enabled':False}).status_code==403
        directory=client.get('/api/tulpa?q=测试').json()['scopes'];assert len(directory)==1
        sid=client.post('/api/tulpa/conversation',headers=headers,json={'platform':'qq','conversation_id':'g'}).json()['id']
        saved=client.patch(f'/api/tulpa/scopes/{sid}',headers=headers,json={'manual':'人工修正','revision':0});assert saved.status_code==200
        assert client.patch(f'/api/tulpa/scopes/{sid}',headers=headers,json={'manual':'旧编辑','revision':0}).status_code==400
        assert client.post('/api/tulpa/control',headers=headers,json={'enabled':False}).json()['enabled']==0
        assert client.get(f'/api/tulpa/scopes/{sid}').json()['manual']=='人工修正'
        client.post('/api/tulpa/control',headers=headers,json={'enabled':True})
    # Exact native quote lookup must use indexed peer + seq, cross-check author/time.
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE group_msg_table("40001" INTEGER,"40027" INTEGER,"40021" TEXT,"40003" INTEGER,"40033" INTEGER,"40050" INTEGER)')
    db.execute('CREATE INDEX native_peer_seq ON group_msg_table("40027","40003")')
    db.execute('INSERT INTO group_msg_table VALUES(42,1,\'1\',7,3,100)')
    spec=SimpleNamespace(table='group_msg_table',conversation_column='40021')
    reply=dict(content='引用',_native_quote=dict(seq=7,sender=3,time=100))
    assert resolve_qq_quote(db,spec,'1',reply)['source_id']=='42'
    assert 'source_id' not in resolve_qq_quote(db,spec,'2',reply)
    reply['_native_quote']['sender']=4
    assert 'source_id' not in resolve_qq_quote(db,spec,'1',reply)
    db.close()
print('PASS: cold import, directory without profiling, same-origin controls, manual version conflict, ON/OFF, native quote identity.')
