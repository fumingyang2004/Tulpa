"""Linux terminal acceptance: real local HTTP/WS fixtures, no QQ or paid models."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'scripts'))
from fastapi.testclient import TestClient
from check_mcp_chat import Events
from chatlocal.platform_lock import file_lock
from chatlocal.store import Store
from chatlocal.terminal_client import TerminalClient, TerminalError, read_token, validate_url
from chatlocal.terminal_server import create_app, ensure_token


class SL(BaseHTTPRequestHandler):
    sent = []
    def log_message(self, *args): pass
    def do_POST(self):
        assert self.headers.get('Authorization') == 'Bearer http-terminal-fixture'
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        action = self.path.lstrip('/')
        if action == 'get_login_info': data = dict(user_id=111)
        elif action == 'get_group_list': data = [dict(group_id=222, group_name='Terminal fixture')]
        elif action == 'get_friend_list': data = [dict(user_id=333, nickname='Friend fixture', remark='测试好友')]
        elif action == 'get_group_info': data = dict(group_id=body['group_id'], group_name='Terminal fixture')
        elif action == 'send_group_msg':
            self.sent.append(body); data = dict(message_id=900)
        else: raise AssertionError(action)
        raw = json.dumps(dict(status='ok', retcode=0, data=data)).encode()
        self.send_response(200); self.send_header('Content-Length', str(len(raw))); self.end_headers()
        self.wfile.write(raw)


class TerminalTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp', prefix='terminal-test-')
        self.root = Path(self.tmp.name)
        (self.root/'imports').mkdir()
        self.events = Events()
        self.sl = ThreadingHTTPServer(('127.0.0.1', 0), SL)
        self.thread = threading.Thread(target=self.sl.serve_forever, daemon=True); self.thread.start()
        from test_environment import system_environment
        self.env = patch.dict(os.environ, system_environment(), clear=True); self.env.start()
        self.roots = [patch('chatlocal.onebot.ROOT', self.root), patch('chatlocal.message_sender.ROOT', self.root)]
        for value in self.roots: value.start()
        self.store = Store(self.root/'data/chats.sqlite3')
        self.token = ensure_token(self.root/'data/terminal-admin.token')
        self.app = create_app(root=self.root, store=self.store, token=self.token)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.client.headers.update({'Authorization': 'Bearer '+self.token, 'X-ChatWeave-UI': '1'})
        SL.sent = []

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.events.close()
        self.sl.shutdown(); self.sl.server_close(); self.thread.join(3)
        for value in reversed(self.roots): value.stop()
        self.env.stop()
        self.tmp.cleanup()

    def api(self, method, path, body=None, expected=200):
        result = self.client.request(method, '/api/terminal'+path, json=body)
        self.assertEqual(result.status_code, expected, result.text)
        return result.json()

    def configure(self):
        result = self.api('PUT', '/sl', dict(http_url=f'http://127.0.0.1:{self.sl.server_port}',
            ws_url=f'ws://127.0.0.1:{self.events.port}', http_token='http-terminal-fixture', ws_token='event-fixture'))
        self.assertNotIn('http-terminal-fixture', json.dumps(result))
        self.assertTrue(result['has_http_token'])

    def enable(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        return self.api('PUT', '/mcp', dict(enabled=True, port=port))

    def grant(self):
        return self.api('POST', '/grants', dict(name='terminal-fixture', platforms=['qq'],
            conversations=[['qq','111:group:222']], send=True, chat=True))

    def test_authorization_and_private_credentials(self):
        self.assertEqual(self.client.get('/health', headers={'Authorization': ''}).status_code, 200)
        self.assertEqual(self.client.get('/api/terminal/overview', headers={'Authorization': ''}).status_code, 401)
        self.assertEqual(self.client.get('/api/terminal/overview', headers={'Origin': 'https://example.invalid'}).status_code, 403)
        self.assertEqual(self.client.get('/api/terminal/overview', headers={'Host': 'example.invalid'}).status_code, 403)
        self.assertEqual(self.client.put('/api/terminal/sl', content='{}').status_code, 415)
        self.assertEqual(self.client.put('/api/terminal/sl', content=' '*65537, headers={'Content-Type':'application/json'}).status_code, 413)
        self.assertEqual(ensure_token(self.root/'data/terminal-admin.token'), self.token)
        self.assertEqual(read_token(self.root/'data/terminal-admin.token'), self.token)
        for bad in ('https://localhost:7861', 'http://example.org:7861', 'http://u:p@localhost:7861', 'http://localhost:7861/path'):
            with self.assertRaises(TerminalError): validate_url(bad)
        if os.name != 'nt':
            path = self.root/'data/terminal-admin.token'
            path.chmod(0o644)
            with self.assertRaises(TerminalError): read_token(path)
            path.chmod(0o600)
            link = self.root/'alias-token'; link.symlink_to(path)
            with self.assertRaises(TerminalError): read_token(link)

    def test_sl_settings_and_real_event_probe(self):
        self.configure()
        before = (self.root/'.env').read_text()
        self.api('PUT', '/sl', dict(http_url='http://example.org:3000', ws_url=''), expected=400)
        self.assertEqual((self.root/'.env').read_text(), before)
        cfg = self.api('GET','/sl')
        self.api('PUT','/sl', dict(http_url=cfg['http_url'], ws_url=cfg['ws_url']))
        self.assertIn('http-terminal-fixture', (self.root/'.env').read_text())
        checked = self.api('POST', '/sl/test', {})
        self.assertTrue(checked['ok'], checked)
        self.assertEqual(checked['account'], '111')
        self.assertEqual(self.api('GET','/groups')['items'][0]['id'], '111:group:222')
        self.assertEqual(SL.sent, [])

    def test_shutdown_requires_admin_and_an_owned_server_callback(self):
        from unittest.mock import Mock
        self.assertEqual(self.client.post('/api/terminal/shutdown', json={},
            headers={'Authorization': ''}).status_code, 401)
        self.api('POST', '/shutdown', {}, expected=409)
        callback = Mock()
        self.app.state.terminal_shutdown = callback
        self.api('POST', '/shutdown', {'force': True}, expected=400)
        callback.assert_not_called()
        self.assertTrue(self.api('POST', '/shutdown', {})['stopping'])
        callback.assert_called_once_with()

    def test_grants_sessions_learning_and_revocation(self):
        self.configure(); self.enable()
        self.api('POST','/grants', dict(name='empty', platforms=['qq'], conversations=[], chat=True, send=True), expected=400)
        grant = self.grant()
        config = grant['client_config']['mcpServers']['tulpa']
        self.assertEqual(config['type'], 'http')
        self.assertEqual(config['url'], grant['mcp_url'])
        self.assertEqual(config['headers']['Authorization'], 'Bearer '+grant['token'])
        listed = self.api('GET','/grants')
        self.assertNotIn(grant['token'], json.dumps(listed))
        self.assertEqual(listed['items'][0]['id'], grant['id'])
        self.assertGreaterEqual(len(self.api('GET','/personas')['items']), 3)
        body = dict(connection_id=grant['id'], conversation_id='111:group:222',
                    persona_preset='little_whale_v2', idempotency_key=secrets.token_hex(16))
        started = self.api('POST','/sessions/start',body)
        self.assertEqual(self.api('POST','/sessions/start',body)['session_id'], started['session_id'])
        sid = started['session_id']
        session = self.api('GET','/sessions')['items'][0]
        self.assertEqual(session['state'], 'waiting_agent')
        self.assertEqual(session['grant_id'], grant['id'])
        state = self.api('PUT',f'/sessions/{sid}/learning',dict(enabled=False))
        self.assertFalse(state['enabled'])
        self.api('POST',f'/grants/{grant["id"]}/revoke',{})
        self.assertFalse(self.api('GET','/sessions')['items'][0]['active'])
        self.assertTrue(self.api('GET','/grants')['items'][0]['revoked'])
        self.api('POST','/sessions/start',body,expected=400)
        self.assertEqual(SL.sent, [])

    def test_import_search_and_path_boundary(self):
        path = self.root/'imports/messages.json'
        path.write_text(json.dumps([dict(platform='qq', conversation_id='course', conversation='课程群',
            sender='老师', sender_id='teacher', source_id='terminal-1', timestamp='2026-10-02',
            content='课程设计使用 Tulpa 终端工具')]), encoding='utf-8')
        self.api('POST','/import',dict(path='imports/messages.json'))
        found = self.api('GET','/search?query=课程设计&limit=10')
        self.assertTrue(found['messages'])
        self.assertEqual(self.api('GET','/overview')['counts']['messages'], 1)
        self.api('POST','/import',dict(path='../outside.json'),expected=400)

    def test_extension_permissions_and_private_sessions(self):
        from chatlocal.mcp_qq_permissions import QQ_FLAGS, ACCOUNT_FLAGS
        self.configure(); self.enable()
        options = self.api('GET','/grant-permissions')['items']
        self.assertEqual({row['id'] for row in options}, set(QQ_FLAGS))
        self.assertTrue(all(row['default'] is False for row in options))
        self.assertEqual({row['id'] for row in options if row['scope']=='account'}, set(ACCOUNT_FLAGS))
        friend = self.api('GET','/friends')['items'][0]
        self.assertEqual(friend, dict(id='111:direct:333', name='测试好友', account='111'))
        old = self.grant()
        body = dict(name='private-extensions', platforms=['qq'], conversations=[['qq',friend['id']]],
                    send=True, chat=True, **{flag: True for flag in QQ_FLAGS})
        self.api('POST','/grants',body | dict(chat=False),expected=400)
        self.api('POST','/grants',body | dict(conversations=[['qq','111:direct:444']]),expected=400)
        grant = self.api('POST','/grants',body)
        grants = {g['id']:g for g in self.api('GET','/grants')['items']}
        self.assertTrue(all(grants[grant['id']]['scope'][flag] for flag in QQ_FLAGS))
        self.assertFalse(any(grants[old['id']]['scope'][flag] for flag in QQ_FLAGS))
        start = dict(connection_id=grant['id'], conversation_id=friend['id'],
                     persona_preset='little_whale_v2', idempotency_key=secrets.token_hex(16))
        sid = self.api('POST','/sessions/start',start)['session_id']
        self.assertEqual(self.api('GET','/sessions')['items'][0]['conversation_id'],friend['id'])
        self.api('POST',f'/sessions/{sid}/stop',{})
        self.assertFalse(self.api('GET','/sessions')['items'][0]['active'])
        self.assertEqual(SL.sent, [])

    def test_lock_excludes_other_process(self):
        path = self.root/'test.lock'
        code = 'from chatlocal.platform_lock import file_lock\nimport sys\ntry:\n with file_lock(sys.argv[1]): pass\nexcept OSError:\n sys.exit(7)\n'
        with file_lock(path):
            result = subprocess.run([sys.executable,'-c',code,str(path)], cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 7, result.stderr)
        result = subprocess.run([sys.executable,'-c',code,str(path)], cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_and_client_over_real_management_http(self):
        import uvicorn
        sock = socket.socket(); sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(self.app, log_level='error', lifespan='off'))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        try:
            deadline = time.monotonic()+5
            while not server.started and time.monotonic() < deadline: time.sleep(.02)
            self.assertTrue(server.started)
            url = f'http://127.0.0.1:{port}'
            token_file = self.root/'data/terminal-admin.token'
            with patch.dict(os.environ, {'HTTP_PROXY':'http://127.0.0.1:9','ALL_PROXY':'http://127.0.0.1:9'}):
                client = TerminalClient(url, token_file)
                self.assertEqual(client.get('/overview')['counts']['messages'], 0)
            result = subprocess.run([str(ROOT/'tulpa'),'--json','--url',url,
                '--token-file',str(token_file),'personas'],cwd=ROOT,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertGreaterEqual(len(json.loads(result.stdout)['items']), 3)
            self.assertNotIn(self.token, result.stdout+result.stderr)
        finally:
            server.should_exit = True
            thread.join(5); sock.close()
            self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main(verbosity=2)
