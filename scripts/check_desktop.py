"""Isolated desktop settings, write-only credentials and relative asset routing."""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from chatlocal.desktop_routes import install_desktop_routes


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='desktop-settings-') as name,patch.dict('os.environ',{},clear=True):
        root=Path(name);(root/'.tmp').mkdir();app=FastAPI();install_desktop_routes(app,root);c=TestClient(app)
        assert c.get('/api/ui-preferences').json()==dict(show_reasoning=True)
        assert c.put('/api/ui-preferences',json=dict(show_reasoning=False)).status_code==403
        assert c.put('/api/ui-preferences',json=dict(show_reasoning=False),headers={'X-ChatWeave-UI':'1','Origin':'https://evil.test'}).status_code==403
        assert c.put('/api/ui-preferences',json=dict(show_reasoning=False),headers={'X-ChatWeave-UI':'1'}).status_code==200
        assert not c.get('/api/ui-preferences').json()['show_reasoning']
        assert not (root/'.env').exists(), 'Display preference touched model credentials'
        assert c.put('/api/ui-preferences',json=dict(show_reasoning='false'),headers={'X-ChatWeave-UI':'1'}).status_code==400
        assert not c.get('/api/desktop/status').json()['settings']['configured']
        settings=dict(api_base='https://example.test/v1',api_key='private-fixture-key',model='fixture-model',sender_url='http://127.0.0.1:3000',sender_token='private-sender-token')
        headers={'X-ChatWeave-UI':'1'}
        assert c.put('/api/desktop/settings',json=settings).status_code==403
        assert c.put('/api/desktop/settings',json=settings,headers=dict(headers,Origin='https://evil.test')).status_code==403
        response=c.put('/api/desktop/settings',json=settings,headers=headers)
        assert response.status_code==200 and response.json()['configured']
        for r in (response,c.get('/api/desktop/status')):
            assert 'private-fixture-key' not in r.text and 'private-sender-token' not in r.text
        content=(root/'.env').read_text(encoding='utf-8');assert 'private-fixture-key' in content
        change=dict(settings,api_key='',sender_token='',model='new-model')
        assert c.put('/api/desktop/settings',json=change,headers=headers).status_code==200
        assert 'private-fixture-key' in (root/'.env').read_text(encoding='utf-8')
        for invalid in [dict(api_base='file:///secrets'),dict(api_base='https://user:pw@example.test'),dict(api_key='key\nEVIL=1'),dict(model=''),dict(sender_url='https://external.test')]:
            assert c.put('/api/desktop/settings',json=dict(change,**invalid),headers=headers).status_code==400
        app2=FastAPI();install_desktop_routes(app2,root)
        assert not TestClient(app2,base_url='http://testserver:54321').get('/api/ui-preferences').json()['show_reasoning']
        assert TestClient(app2).get('/api/desktop/status').json()['settings']['model']=='new-model'
        diagnostics=c.get('/api/desktop/diagnostics')
        assert diagnostics.status_code==200 and 'attachment;' in diagnostics.headers['content-disposition']
        assert 'private-fixture-key' not in diagnostics.text and str(root) not in diagnostics.text
        assert c.get('/api/desktop/diagnostics',headers={'Origin':'https://evil.test'}).status_code==403
    print('PASS isolated: first-run configuration, persistence, blank-key preservation, write-only secrets, invalid values, same-origin and explicit UI header. No real credential writes or API calls.')


if __name__=='__main__':main()
