"""Key paths only: identity, bounded parsing, scope, cache and citation checks."""
import json
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from chatlocal.store import Store
from chatlocal.artifacts import Artifacts,ArtifactError
from chatlocal.retrieval import Plan
from chatlocal.agent_tools import ChatTools
from chatlocal.llm import validate_answer
from chatlocal.artifact_onebot import OneBot
from chatlocal.artifact_metadata import wechat_file


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='artifact-check-') as tmp:
        root=Path(tmp);s=Store(root/'test.sqlite3');a=Artifacts(s)
        body='课程任务书\n截止时间是10月15日。\n端云协同需要本地Router选择推理节点。\n'
        raw=(root/'任务书.txt');raw.write_text(body,'utf-8')
        a._roots=lambda p:[root]
        values=[]
        for n,p in enumerate(['qq','wechat']):
            values.append(dict(platform=p,conversation_id='c',conversation='课程群',sender='老师',sender_id='teacher',
                source_id=str(n),timestamp='2026-09-22 10:00:00',content='[文件] 任务书.txt',
                files=[dict(filename=raw.name,size=raw.stat().st_size)],is_self=False))
        export=root/'messages.json';export.write_text(json.dumps(values,ensure_ascii=False),'utf-8')
        assert s.import_file(export)['imported']==2
        assert s.import_file(export)['duplicate']==2
        assert a.search()['match_count']==2
        assert a.locate(1)['availability']=='AVAILABLE_LOCAL'
        a.prepare(1);a.prepare(2)
        with s.connect() as db:
            assert db.execute('SELECT count(*) FROM artifacts').fetchone()[0]==1
            assert db.execute('SELECT count(*) FROM artifact_chunks').fetchone()[0]==1
        assert a.search(dict(query='Router'),body=True)['match_count']==2
        before=a.chunks(1);assert before['files'][0]['text']==body.strip()
        assert a.evict(1)['released_bytes']==raw.stat().st_size
        assert a.chunks(1)['files']==before['files'] or a.chunks(1)['files'][0]['text']==body.strip()
        a.prepare(1)  # A retained parse must not re-download.
        with s.connect() as db:assert db.execute('SELECT local_path FROM artifacts').fetchone()[0] is None
        a.materialize(2)
        with s.connect() as db:assert db.execute('SELECT count(*) FROM artifacts').fetchone()[0]==1
        t=ChatTools(s,Plan(platforms=['qq'],conversations=[json.dumps(['qq','c'])]))
        meta=t.execute('search_files',{'query':'任务书'})
        assert len(meta['files'])==1
        assert t.execute('read_file_chunks',{'file_id':2}).get('error')
        chunks=t.execute('read_file_chunks',{'file_id':1})
        ref=chunks['files'][0]['citation_id']
        answer={'claims':[dict(text='截止10月15日。',evidence_ids=[],artifact_evidence_ids=[ref])],'insufficient':False}
        assert validate_answer(answer,[],file_evidence=t.file_evidence)['claims']
        assert not validate_answer(answer,[])['claims']
        assert not a.search({},Plan(platforms=['wechat'],start=1900000000000))['files']
        a.mark_cited(chunks['files'])
        try:a.evict(1);raise AssertionError('cited binary was evicted')
        except ArtifactError:pass
        missing=dict(values[0],source_id='missing',content='[文件] 不存在.pdf',files=[dict(filename='不存在.pdf',size=42)])
        export.write_text(json.dumps([missing],ensure_ascii=False),'utf-8');s.import_file(export)
        sid=a.search(dict(query='不存在'))['files'][0]['id']
        try:a.materialize(sid);raise AssertionError('missing file treated as available')
        except ArtifactError:pass
        assert a.get(sid)['availability']=='MISSING'
        assert wechat_file('<msg><appmsg><type>6</type><title>报告.pdf</title><appattach><totallen>123</totallen></appattach></appmsg></msg>')['size']==123
        assert wechat_file('<!DOCTYPE x [<!ENTITY foo "bad">]><msg/>') is None
        # Files without server IDs keep distinct stable sources; file-only
        # records receive a searchable placeholder instead of being discarded.
        with s.connect() as db:blank_before=db.execute("SELECT count(*) FROM artifact_sources WHERE original_message_id=''").fetchone()[0]
        blank=[dict(values[0],source_id='',content='',timestamp=f'2026-09-22 11:0{n}:00') for n in range(2)]
        export.write_text(json.dumps(blank,ensure_ascii=False),'utf-8');assert s.import_file(export)['imported']==2
        with s.connect() as db:assert db.execute("SELECT count(*) FROM artifact_sources WHERE original_message_id=''").fetchone()[0]==blank_before+2
        # Observed upload ACK: a nonzero WX FileMessage ID is finalized later.
        import hashlib,time
        xml='<msg><appmsg><type>6</type><title>任务书.txt</title><appattach><totallen>10</totallen><filemd5>'+'a'*32+'</filemd5></appattach></appmsg></msg>'
        raw=dict(chat='self',sender_username='self',sender_name='本人',create_time=1790067000,server_id=111,
            type_code=49,content=xml,local_id=7,source_db='message/message_1.db',source_table='Msg_'+'a'*32)
        head=dict(reader='wechatauto-replica',wxid='self',identity_method='per-shard',chats=[dict(username='self',name='本人')],messages=[raw])
        cp=dict(platform='wechat',previous={},next={'account':'self'},observed_at=time.time())
        export.write_text(json.dumps(head,ensure_ascii=False),'utf-8');assert s.import_file(export,live=cp)['imported']==1
        with s.connect() as db:source=dict(db.execute("SELECT * FROM artifact_sources WHERE original_message_id='111'").fetchone())
        head['messages'][0]['server_id']=222;cp['previous']=cp['next']
        export.write_text(json.dumps(head,ensure_ascii=False),'utf-8');assert s.import_file(export,live=cp)['duplicate']==1
        with s.connect() as db:
            assert db.execute('SELECT original_message_id FROM artifact_sources WHERE id=?',(source['id'],)).fetchone()[0]=='222'
            assert db.execute('SELECT count(*) FROM artifact_sources WHERE message_id=?',(source['message_id'],)).fetchone()[0]==1
        head['messages'][0].update(server_id=333,content=xml.replace('a'*32,'b'*32))
        export.write_text(json.dumps(head,ensure_ascii=False),'utf-8')
        try:s.import_file(export,live=cp);raise AssertionError('Different file accepted as upload acknowledgement')
        except ValueError:pass
        # Metadata-only OneBot contract fixture; explicitly not a live QQ test.
        class Bot(OneBot):
            def __init__(self):self.count=2
            def call(self,action,payload):
                if action=='get_login_info':return dict(user_id=123)
                if action=='get_group_file_system_info':return dict(file_count=self.count)
                if action=='get_group_root_files':return dict(files=[dict(file_id='A',file_name='根目录.pdf',file_size=50)],folders=[dict(folder_id='d',folder_name='目录')])
                if action=='get_group_files_by_folder':return dict(files=[dict(file_id='B',file_name='任务.docx',file_size=80)],folders=[])
                raise AssertionError(action)
        b=Bot();assert b.reconcile(s,'123:group:456','测试群')['files']==2
        assert b.reconcile(s,'123:group:456','测试群')['files']==2
        b.count=3
        try:b.reconcile(s,'123:group:456','测试群');raise AssertionError('incomplete inventory accepted')
        except ArtifactError:pass
        with s.connect() as db:
            assert db.execute("SELECT count(*) FROM artifact_sources WHERE source_type='qq_group_file' AND remote_status='present'").fetchone()[0]==2
        from unittest.mock import patch
        import hmac
        from chatlocal.artifact_onebot import receive_event
        notice=dict(post_type='notice',notice_type='group_upload',self_id=123,group_id=456,user_id=789,time=1790067000,
            file=dict(id='event-file',name='上传.pdf',size=900,busid=102))
        wire=json.dumps(notice).encode();sig='sha1='+hmac.new(b'fixture-secret',wire,hashlib.sha1).hexdigest()
        with patch('chatlocal.artifact_onebot.config',return_value={'ARTIFACT_ONEBOT_EVENT_SECRET':'fixture-secret'}):
            assert receive_event(s,wire,sig)['accepted']
            assert receive_event(s,wire,sig)['accepted']
            try:receive_event(s,wire,'sha1=bad');raise AssertionError('unsigned event accepted')
            except ArtifactError:pass
        with s.connect() as db:
            eventrow=db.execute("SELECT sha256,availability FROM artifact_sources WHERE file_id='event-file'").fetchall()
            assert len(eventrow)==1 and tuple(eventrow[0])==(None,'METADATA_ONLY')
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from chatlocal.artifact_routes import install_artifact_routes
        app=FastAPI();install_artifact_routes(app,s)
        with TestClient(app) as client:
            assert client.get('/api/artifacts').status_code==200
            assert client.get('/api/artifacts',headers={'Origin':'https://evil.example'}).status_code==403
            assert client.get('/api/artifacts/1/content').headers['content-disposition'].startswith('attachment;')
            assert client.get('/api/artifacts/999/content').status_code==404
        # Purging chat sources releases only unshared, unprotected owned blobs.
        disposable=root/'临时.txt';disposable.write_text('不同的附件正文','utf-8')
        export.write_text(json.dumps([dict(values[0],source_id='disposable',content='[文件] 临时.txt',
            files=[dict(filename=disposable.name,size=disposable.stat().st_size)])],ensure_ascii=False),'utf-8')
        s.import_file(export);sid=a.search({'query':'临时.txt'})['files'][0]['id'];a.prepare(sid)
        own_blob=a.cache_path(a.get(sid));retained_blob=a.cache_path(a.get(2))
        from chatlocal.data_delete import preview,purge
        deletion=dict(platform='qq',conversations=['c'],start='2026-09-22',end='2026-09-22')
        purge(s,deletion,preview(s,deletion)['fingerprint'])
        assert not own_blob.exists() and disposable.exists() and retained_blob.exists()
        assert a.chunks(2)['files'][0]['text']==body.strip()
        assert not a.search({'query':'临时.txt'})['files']
    print('PASS: metadata/idempotency, SHA256 shared parse, retained index after eviction, reacquisition, hard scope, real chunk citations, missing files, XML limits, recursive OneBot fixture and local HTTP boundaries. No cloud.')


if __name__=='__main__':main()
