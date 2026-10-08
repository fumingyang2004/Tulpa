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
        headers={'X-ChatWeave-UI':'1'}
        onebot_only=dict(sender_url='http://127.0.0.1:3000',sender_token='private-sender-token')
        response=c.put('/api/desktop/settings',json=onebot_only,headers=headers)
        assert response.status_code==200 and not response.json()['configured'],response.text
        assert 'API_KEY' not in (root/'.env').read_text(encoding='utf-8')
        event_settings=dict(events_url='ws://127.0.0.1:3001/',events_token='event-secret')
        events=c.put('/api/desktop/settings',json=event_settings,headers=headers)
        assert events.status_code==200 and events.json()['has_events_token'] and 'event-secret' not in events.text
        assert c.put('/api/desktop/settings',json=dict(events_url=event_settings['events_url'],events_token=''),headers=headers).json()['has_events_token']
        for url in ('ws://evil.test:3001/','ws://127.0.0.1:3001/?token=bad','ws://user:pass@127.0.0.1:3001/'):
            assert c.put('/api/desktop/settings',json=dict(events_url=url),headers=headers).status_code==400
        assert c.put('/api/desktop/settings',json={'model':'some-model'},headers=headers).status_code==400
        with patch('chatlocal.onebot.Client.login',return_value='12345'),patch('chatlocal.onebot_events.probe_connection',return_value=dict(state='connected',note='fixture')):
            assert c.post('/api/desktop/onebot/test',json={},headers=headers).json()['ok']
        assert c.post('/api/desktop/onebot/test',json={}).status_code==403
        assert c.get('/api/desktop/status',headers={'Host':'evil.test'}).status_code==403
        assert c.get('/api/desktop/preferences').json()==dict(mode='',background=False)
        assert c.put('/api/desktop/preferences',json=dict(mode='mcp',background=True),headers=headers).status_code==200
        assert c.put('/api/desktop/preferences',json=dict(background='false'),headers=headers).status_code==400
        assert c.put('/api/desktop/preferences',json=dict(mode='chat')).status_code==403
        assert c.get('/api/desktop/preferences').json()==dict(mode='mcp',background=True)
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
        assert TestClient(app2).get('/api/desktop/preferences').json()['background']
        diagnostics=c.get('/api/desktop/diagnostics')
        assert diagnostics.status_code==200 and 'attachment;' in diagnostics.headers['content-disposition']
        assert 'private-fixture-key' not in diagnostics.text and str(root) not in diagnostics.text
        assert c.get('/api/desktop/diagnostics',headers={'Origin':'https://evil.test'}).status_code==403
    print('PASS isolated: first-run configuration, persistence, blank-key preservation, write-only secrets, invalid values, same-origin and explicit UI header. No real credential writes or API calls.')


if __name__=='__main__':main()
