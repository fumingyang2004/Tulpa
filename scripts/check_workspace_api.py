"""Isolated HTTP contract checks, explicitly not browser acceptance."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from chatlocal.config import ROOT
from chatlocal.store import Store
from chatlocal.workspace_routes import install_workspace_routes
from chatlocal.workspaces import Workspaces


with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='workspace-api-') as folder:
    store=Store(Path(folder)/'chat.sqlite3');app=FastAPI();install_workspace_routes(app,store)
    with TestClient(app) as c:
        assert c.post('/api/workspaces',headers={'Origin':'https://invalid.example'},json={'goal':'x'}).status_code==403
        r=c.post('/api/workspaces',json={'goal':'孤立API验收','scope':{'platforms':['qq']}});assert r.status_code==200,r.text
        wid=r.json()['id'];base='/api/workspaces/'+wid
        home=c.get(base+'/home');assert home.status_code==200 and not home.json()['understanding']
        assert c.get(base+'/home',headers={'Origin':'https://invalid.example'}).status_code==403
        r=c.post(base+'/tasks',json={'mode':'changes','question':'无变化检查'});assert r.json()['api_called'] is False
        tid=r.json()['task_id'];assert c.get(base+'/tasks/'+tid).json()['record']['requests']==0
        # Real bytes are exported from owned DB content, never arbitrary paths.
        layer=Workspaces(store)
        with store.connect() as db:db.execute("UPDATE workspace_tasks SET state='running' WHERE id=?",(tid,))
        p=layer.draft(wid,tid,name='报告',format='md',body='# 报告\n\n未取得原始证据。\n',refs=[],summary='无证据的说明')
        with store.connect() as db:db.execute("UPDATE workspace_tasks SET state='completed' WHERE id=?",(tid,))
        assert c.post(base+'/apply',json={'ids':[p['proposal_id']]}).status_code==400
        assert c.post(base+'/apply',json={'ids':[p['proposal_id']],'confirm':True}).status_code==200
        oid=p['output_id'];r=c.get(base+'/outputs/'+oid+'/download')
        assert r.status_code==200 and r.text.startswith('# 报告') and 'attachment;' in r.headers['content-disposition']
        edit=c.patch(base+'/outputs/'+oid,json={'body':'手动编辑','base_version':1}).json()
        assert edit['requires_confirmation'] and layer.output(wid,oid)['version']==1
        assert c.post(base+'/apply',json={'ids':[edit['proposal_id']],'confirm':True}).status_code==200
        assert layer.output(wid,oid)['version']==2
        assert c.patch(base+'/outputs/'+oid,json={'body':'过期覆盖','base_version':1}).status_code==400
        r=c.post(base+'/outputs/'+oid+'/restore',json={'version':1,'base_version':2,'confirm':True});assert r.json()['version']==3
        # Re-instantiation sees the same revisions and collection, no task replay.
        assert Workspaces(Store(store.path)).output(wid,oid)['version']==3
        r=c.post('/api/workspaces',json={'goal':'另一个工作区'});other='/api/workspaces/'+r.json()['id']
        assert c.get(other+'/outputs/'+oid).status_code==400
        assert c.get(base+'/outputs/..%2F..%2F.env/download').status_code in (400,404)
        assert c.request('DELETE',base,json={}).status_code==400
        assert c.request('DELETE',base,json={'confirm':True}).status_code==200
        assert c.get(other).status_code==200
print('PASS: isolated HTTP create/no-change-without-model/confirm/download/edit/conflict/restore/reopen/scope/path/owned-delete. NOT browser acceptance.')
