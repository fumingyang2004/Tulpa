"""Isolated MCP original-file handoff and optional model installer checks."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT=Path(sys.argv[sys.argv.index('--package')+1]).resolve() if '--package' in sys.argv else Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from chatlocal.store import Store
from chatlocal.artifacts import Artifacts
from chatlocal.artifact_onebot import OneBot, upsert
from chatlocal.mcp_access import MCPAccess, RateLimited
from chatlocal.mcp_tools import MCPTools
from chatlocal.optional_components import install_component_routes


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='mcp-download-') as name:
        root=Path(name);store=Store(root/'chats.sqlite3')
        raw=root/'fixture.json';raw.write_text(json.dumps([dict(platform='qq',conversation_id='111:group:222',conversation='Fixture',conversation_type='group',sender='User',sender_id='333',source_id='1',timestamp='2026-10-01',content='Fixture')]),'utf-8');store.import_file(raw)
        data=b'Fixture original bytes: not parsed or executed.'
        with store.connect() as db:
            for cid in ('111:group:222','111:group:444'):
                upsert(db,cid,'Fixture',dict(file_id=cid,file_name='fixture.docx',file_size=len(data),upload_time=1790784000))
        access=MCPAccess(store);access.configure(True,18779)
        access.accounts=lambda platforms:{'qq':'111'}
        adapter=MCPTools(access)
        def grant(**extra):return access.create(dict(name='Fixture',platforms=['qq'],conversations=[['qq','111:group:222']],**extra))
        readonly=grant();local=grant(prepare=True);remote=grant(prepare=True,onebot=True)
        with patch('chatlocal.mcp_access.bound_account',return_value='111'),patch.object(adapter,'onebot',return_value=object()),patch.object(Artifacts,'local_candidate',return_value=None):
            def call(g,sid):return adapter.call(g['token'],'download_file',dict(file_id=sid),threading.Event())
            assert call(readonly,1).isError
            assert call(remote,2).isError
            with patch.object(OneBot,'__init__',return_value=None),patch.object(OneBot,'download',lambda self,row,path,cap:path.write_bytes(data)):
                assert call(local,1).isError, 'Remote fetch bypassed OneBot permission'
                result=call(remote,1)
                assert not result.isError,result
                file=result.structuredContent['file'];path=Path(file['local_path'])
                assert path.is_relative_to(root) and path.read_bytes()==data and file['sha256']==hashlib.sha256(data).hexdigest()
                assert file['parse_status']=='PENDING',file
            # Cached original bytes stay readable without a live OneBot connection.
            with patch.object(adapter,'onebot',return_value=None):assert not call(local,1).isError
            # Download and parse share the UI's one hourly quota.
            for _ in range(10):access.begin_call(local['id'],'prepare_file')
            try:access.begin_call(local['id'],'download_file')
            except RateLimited:pass
            else:raise AssertionError('File quota split across two tools')
        app=FastAPI();installer=install_component_routes(app,root)
        model=b'isolated-model-fixture'
        installer.asset=dict(installer.asset,size=len(model),sha256=hashlib.sha256(model).hexdigest())
        installer.opener=lambda *a,**k:io.BytesIO(model)
        installer.path.parent.mkdir(parents=True,exist_ok=True)
        stale=installer.path.with_name(installer.path.name+'.interrupted.part')
        stale.write_bytes(b'Interrupted download')
        unrelated=installer.path.parent/'other-component.part';unrelated.write_bytes(b'Keep')
        headers={'X-ChatWeave-UI':'1'}
        with TestClient(app) as ui:
            assert ui.post('/api/components/voice',json={'action':'install'}).status_code==403
            assert ui.post('/api/components/voice',headers=dict(headers,Origin='https://evil.test'),json={'action':'install'}).status_code==403
            assert ui.post('/api/components/voice',headers=headers,json={'action':'install','url':'https://evil.test'}).status_code==400
            assert ui.post('/api/components/voice',headers=headers,json={'action':'install'}).status_code==200
            installer.thread.join(5);assert installer.status()['status']=='installed'
            assert installer.path.read_bytes()==model and not stale.exists() and unrelated.read_bytes()==b'Keep'
            installer.path.write_bytes(b'broken');installer.opener=lambda *a,**k:io.BytesIO(b'bad')
            installer.start();installer.thread.join(5)
            assert installer.status()['status']=='failed' and installer.path.read_bytes()==b'broken'
            gate=threading.Event()
            class Slow(io.BytesIO):
                def read(self,*a):gate.wait(2);return super().read(*a)
            installer.opener=lambda *a,**k:Slow(model)
            installer.start();installer.cancel();gate.set();installer.thread.join(5)
            assert installer.path.read_bytes()==b'broken' and not list(installer.path.parent.glob(installer.path.name+'.*.part'))
    print('PASS original-file scope, raw bytes, no parser, OneBot permission, shared quota; model install/hash/failure/cancel/origin. Isolated fixtures only.')


if __name__=='__main__':main()
