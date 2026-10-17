"""Check native login failures and request deadlines without opening a browser."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chatlocal.terminal_client import TerminalClient, TerminalError
from macos.ui_bridge import DesktopUI, LoginError


class LoginBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.tmp.cleanup)
        self.client = Mock()
        self.value = dict(ok=True, novnc_url='http://127.0.0.1:60282/?resize=scale', password='fixture-only')
        self.client.api_post.return_value = self.value
        self.ui = DesktopUI(self.tmp.name, self.client)
        self.addCleanup(self.ui._executor.shutdown)

    def test_login_uses_long_request_and_opens_only_backend_url(self):
        with patch('macos.ui_bridge.subprocess.run') as run:
            result = self.ui.open_qq_login()
        self.client.api_post.assert_called_once_with('/api/desktop/snowluma/managed/docker-login', {}, timeout=60)
        self.assertTrue(result['browser_opened'])
        self.assertEqual(run.call_args.args[0], ['/usr/bin/open', self.value['novnc_url']])
        self.assertNotIn(self.value['password'], str(run.call_args))

    def test_browser_failure_keeps_login_panel_data(self):
        errors = [OSError('fixture'), subprocess.CalledProcessError(1, ['open']),
                  subprocess.TimeoutExpired(['open'], 10)]
        for error in errors:
            with self.subTest(error=type(error).__name__), patch('macos.ui_bridge.subprocess.run', side_effect=error):
                result = self.ui.open_qq_login()
            self.assertFalse(result['browser_opened'])
            self.assertEqual(result['novnc_url'], self.value['novnc_url'])
            self.assertEqual(result['password'], self.value['password'])
            self.assertNotIn(self.value['password'], result['browser_error'])

    def test_management_failure_is_reported_without_opening_browser(self):
        with patch('macos.ui_bridge.subprocess.run') as run:
            self.client.api_post.side_effect = TerminalError('fixture timeout')
            with self.assertRaisesRegex(LoginError, 'fixture timeout'):
                self.ui.open_qq_login()
            self.client.api_post.side_effect = None
            self.client.api_post.return_value = dict(ok=False, message='fixture unavailable')
            with self.assertRaisesRegex(LoginError, 'fixture unavailable'):
                self.ui.open_qq_login()
        run.assert_not_called()

    def test_rejects_external_or_malformed_login_destinations(self):
        invalid = ['https://example.test/', 'http://127.0.0.1/', 'http://127.0.0.1:bad/',
                   'http://user:secret@127.0.0.1:60282/', 'http://127.0.0.1:60282/#bad', None]
        with patch('macos.ui_bridge.subprocess.run') as run:
            for url in invalid:
                with self.subTest(url=url):
                    self.client.api_post.return_value = dict(self.value, novnc_url=url)
                    with self.assertRaises(LoginError):
                        self.ui.open_qq_login()
        run.assert_not_called()

    def test_timeout_override_does_not_change_other_requests(self):
        client = object.__new__(TerminalClient)
        client.base_url = 'http://127.0.0.1:17888'
        client.token_file = Path(self.tmp.name)/'fixture-token'
        client.timeout = 12
        response = Mock(status_code=200, is_redirect=False, is_error=False)
        response.json.return_value = dict(ok=True)
        with patch('chatlocal.terminal_client.read_token', return_value='fixture-only'), patch('httpx.Client') as http:
            http.return_value.__enter__.return_value.request.return_value = response
            client.api_post('/api/desktop/snowluma/managed/docker-login', {}, timeout=60)
            self.assertEqual(http.call_args.kwargs['timeout'], 60)
            client.api_get('/api/desktop/status')
            self.assertEqual(http.call_args.kwargs['timeout'], 12)
        self.assertEqual(client.timeout, 12)


if __name__ == '__main__':
    unittest.main()
