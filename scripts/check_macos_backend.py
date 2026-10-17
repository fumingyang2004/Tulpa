"""Isolated shared-UI authentication, launchd ownership and Mac source checks."""
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from chatlocal.terminal_server import create_app
from chatlocal.desktop_session import DesktopSessions
from chatlocal import macos_service as service
from chatlocal.macos_distribution import source_files, release_path
from chatlocal.store import Store
from chatlocal.mcp_access import PERMISSION_FLAGS


class MacBackendTests(unittest.TestCase):
    def test_browser_ticket_replay_csrf_and_api_parity(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as tmp:
            root = Path(tmp).resolve()
            shutil.copytree(ROOT/'web', root/'web')
            store = Store(root/'data/chats.sqlite3')
            token = 'fixture-private-token-for-mac-desktop-only'
            app = create_app(root=root, store=store, token=token, desktop=True, legacy=False)
            with TestClient(app) as client:
                self.assertEqual(client.get('/').status_code, 401)
                self.assertEqual(client.get('/api/terminal/overview').status_code, 401)
                self.assertEqual(client.post('/api/terminal/desktop-ticket',json={}).status_code,401)
                auth = {'Authorization':'Bearer '+token}
                direct = client.get('/api/terminal/overview',headers=auth).json()
                ticket = client.post('/api/terminal/desktop-ticket',json={},headers=auth).json()['path']
                self.assertNotIn(token, ticket)
                cookie = client.get(ticket,follow_redirects=False)
                self.assertEqual(cookie.status_code,303)
                self.assertIn('HttpOnly',cookie.headers['set-cookie'])
                self.assertIn('SameSite=strict',cookie.headers['set-cookie'])
                self.assertEqual(client.get(ticket).status_code,401)
                self.assertEqual(client.get('/?desktop=1').status_code,200)
                from types import SimpleNamespace
                with patch('chatlocal.web_frontend.sys',SimpleNamespace(platform='darwin')):
                    self.assertIn('data-platform="macos"',client.get('/?desktop=1').text)
                self.assertIn('type="module"',client.get('/?desktop=1').text)
                self.assertEqual(client.get('/ui/app.js').content,(ROOT/'web/desktop/app.js').read_bytes())
                self.assertEqual(client.get('/api/terminal/overview').json()['storage'],direct['storage'])
                self.assertEqual(client.get('/ui/snowluma-macos.js').status_code,200)
                catalog=client.get('/api/mcp/permissions').json()['items']
                self.assertEqual({p['id'] for p in catalog},set(PERMISSION_FLAGS))
                self.assertEqual(next(p['scope'] for p in catalog if p['id']=='qq_profile'),'account')
                with patch('chatlocal.onebot.Client') as sl:
                    sl.return_value.login.return_value='111'
                    sl.return_value.call.return_value=[dict(user_id=222,nickname='Fixture'),dict(user_id='invalid')]
                    friends=client.get('/api/mcp/live-friends').json()['items']
                    self.assertEqual([r['conversation_id'] for r in friends],['111:direct:222'])
                    sl.return_value.call.assert_called_once_with('get_friend_list',{})
                self.assertEqual(client.post('/api/terminal/desktop-ticket',json={}).status_code,403)
                self.assertEqual(client.get('/api/terminal/overview',headers={'Origin':'https://evil.invalid'}).status_code,403)
                self.assertEqual(client.get('/api/terminal/overview',headers={'Sec-Fetch-Site':'cross-site'}).status_code,403)
                self.assertEqual(client.post('/api/terminal/mcp',content='{}').status_code,415)
                client.cookies.clear()
                self.assertEqual(client.get('/api/terminal/overview').status_code,401)

    def test_ticket_expiry_and_session_expiry(self):
        browser = DesktopSessions(ROOT)
        with patch('chatlocal.desktop_session.time.monotonic',return_value=0):
            expired = browser.issue()
            good = browser.issue()
            session = browser.exchange(good)
            self.assertTrue(browser.valid(session))
        with patch('chatlocal.desktop_session.time.monotonic',return_value=61):
            self.assertIsNone(browser.exchange(expired))
        with patch('chatlocal.desktop_session.time.monotonic',return_value=86401):
            self.assertFalse(browser.valid(session))

    def test_launchd_space_paths_and_foreign_config(self):
        with tempfile.TemporaryDirectory(prefix='Tulpa mac space ',dir=ROOT/'.tmp') as tmp:
            root = Path(tmp).resolve()/'project with spaces'
            root.mkdir()
            home = Path(tmp)/'home'; home.mkdir()
            with patch.object(Path,'home',return_value=home):
                cfg = service.description(root,17861)
                self.assertEqual(cfg['ProgramArguments'],[str(root/'tulpa'),'serve','--port','17861'])
                self.assertEqual(cfg['WorkingDirectory'],str(root))
                enabled, manual = service.paths(root)
                manual.parent.mkdir(parents=True)
                manual.write_bytes(plistlib.dumps(cfg))
                output=f'path = {manual}\nprogram = {root}/tulpa\nstate = running\npid = 999\n'
                result=subprocess.CompletedProcess([],0,output,'')
                with patch.object(service,'launchctl',return_value=result),patch.object(service.shutil,'which',return_value='/bin/launchctl'):
                    status=service.service_status(root)
                    self.assertTrue(status['active'])
                    self.assertFalse(status['enabled'])
                    self.assertTrue(service.installed_service(root,17861,root/'data/terminal-admin.token'))
                    with self.assertRaises(ValueError): service.installed_service(root,17862,root/'data/terminal-admin.token')
                    cfg['TulpaManagedRoot']='/other'
                    manual.write_bytes(plistlib.dumps(cfg))
                    with self.assertRaises(ValueError): service.service_action('stop',root=root)

    def test_mac_package_keeps_both_frontends_and_excludes_private_files(self):
        chosen={release_path(p.relative_to(ROOT)).as_posix() for p in source_files(ROOT)}
        for name in ('tulpa','setup-macos.sh','start-macos.sh','macos/launch.py',
                     'requirements/macos.txt','requirements.txt','script/build_and_run.sh',
                     'web/desktop/index.html','web/desktop/app.js','web/desktop/snowluma.js'):
            self.assertIn(name,chosen)
        self.assertFalse(any(p.startswith(('data/','.cache/','.runtime/','.git/','.venv/')) or p.endswith('.ps1') for p in chosen))


if __name__ == '__main__': unittest.main()
