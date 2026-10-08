"""NapCat probe contracts on invented messages and loopback fake HTTP only."""
from copy import deepcopy
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest

from napcat_qq_fixture import HistoryFixture, ScriptedFixture, messages
from napcat_qq_probe import LocalHTTP, MAX_RESPONSE, ProbeError, Spec, observe, probe


BASE = Spec('111', '222', 'group', '2026-02-27', 'fixture message 1', count=3, max_pages=10)


class HistoryTests(unittest.TestCase):
    def test_inclusive_pages_and_target(self):
        client = HistoryFixture()
        out = probe(client, BASE)
        self.assertTrue(out['target_found'])
        self.assertEqual((out['pages'], out['unique_messages'], out['overlaps']), (4, 8, 3))
        self.assertFalse(out['complete_history'])
        self.assertFalse(out['durable_resume_supported'])
        params = client.actions[2][1]
        self.assertTrue(params['disable_get_url'])
        self.assertFalse(params['parse_mult_msg'])
        self.assertTrue(params['quick_reply'])
        self.assertTrue(params['reverse_order'])
        self.assertNotIn('keyword', params)
        self.assertNotIn('date', params)
        private = probe(HistoryFixture(messages(kind='private')), replace(BASE, kind='private'))
        self.assertTrue(private['target_found'])
        self.assertEqual(private['private_peer_binding'], 'response_has_no_recipient')

    def test_ordering_boundary_and_short_page_not_exhaustion(self):
        rows = messages()
        # Same text outside the requested day must not count as the target.
        rows[-1]['message'] = deepcopy(rows[0]['message'])
        rows[-1]['time'] += 86400
        # An older timestamp is not proof that all later-dated rows were seen.
        rows[-2]['time'] -= 86400
        client = ScriptedFixture([rows[5:][::-1], rows[3:6], rows[:3][::-1]])
        out = probe(client, BASE)
        self.assertTrue(out['target_found'])
        self.assertEqual(out['pages'], 3)
        self.assertIn('mixed', out['ordering'])
        out = probe(ScriptedFixture([rows[-1:], rows[:1]]), BASE)
        self.assertEqual((out['pages'], out['short_pages']), (2, 2))
        self.assertTrue(out['target_found'])

    def test_empty_error_and_repeated_page_are_not_complete(self):
        cases = [([], 'empty_page_not_proof_of_exhaustion'),
                 ('onebot_failed', 'onebot_failed')]
        for page, reason in cases:
            out = probe(ScriptedFixture([page]), BASE)
            self.assertEqual(out['stop_reason'], reason)
            self.assertFalse(out['complete_history'])
        out = probe(ScriptedFixture([messages()[5:]] * 2), BASE)
        self.assertEqual(out['stop_reason'], 'nonprogress_or_direction_mismatch')
        self.assertEqual(out['history_calls'], 2)

    def test_short_id_conflict_and_expiring_media_url(self):
        first = messages()[5:]
        changed = deepcopy(first)
        changed[0]['message'][0]['data']['text'] = 'edited or collision'
        self.assertEqual(probe(ScriptedFixture([first, changed]), BASE)['stop_reason'],
                         'id_conflict_or_edit')
        first[0]['message'].append({'type': 'image', 'data': {'url': 'https://example.invalid/old'}})
        changed = deepcopy(first)
        changed[0]['message'][1]['data']['url'] = 'https://example.invalid/new'
        out = probe(ScriptedFixture([first, changed]), BASE)
        self.assertEqual(out['stop_reason'], 'nonprogress_or_direction_mismatch')
        self.assertEqual(out['segment_counts']['image'], 1)

    def test_scope_identity_time_and_format_fail_closed(self):
        mutations = [('self_id', 999, 'account_changed'), ('group_id', 999, 'peer_changed'),
                     ('message_type', 'private', 'peer_kind_changed'),
                     ('time', 1790000000000, 'invalid_timestamp'),
                     ('message', '[CQ:image,file=x]', 'array_messages_required'),
                     ('real_seq', None, 'sequence_missing_cannot_choose_cursor')]
        for key, value, reason in mutations:
            rows = messages(3)
            rows[0][key] = value
            out = probe(ScriptedFixture([rows]), BASE)
            self.assertEqual(out['stop_reason'], reason)
            self.assertEqual(out['unique_messages'], 0)
        rows = messages(3)
        rows[1]['real_seq'] = rows[0]['real_seq']
        self.assertEqual(probe(ScriptedFixture([rows]), BASE)['stop_reason'], 'sequence_ambiguous')

    def test_budget_and_no_persisted_short_cursor(self):
        out = probe(HistoryFixture(), replace(BASE, max_pages=1))
        self.assertEqual(out['stop_reason'], 'page_budget')
        self.assertEqual(out['history_calls'], 1)
        for kwargs in ({'count': 1}, {'max_pages': 21}, {'deadline_seconds': 61},
                       {'start_native_id': '700000001'}):
            with self.assertRaises(ProbeError):
                replace(BASE, **kwargs)
        native = replace(BASE, start_native_id='9000000000000000003')
        self.assertTrue(probe(HistoryFixture(), native)['target_found'])

    def test_anchor_direction_matches_observed_group_contract(self):
        client = HistoryFixture()
        params = {'group_id':'222', 'message_seq':'9000000000000000004', 'count':3}
        older = client.call('get_group_msg_history', dict(params, reverse_order=True))['messages']
        newer = client.call('get_group_msg_history', dict(params, reverse_order=False))['messages']
        self.assertEqual([r['real_seq'] for r in older], ['2','3','4'])
        self.assertEqual([r['real_seq'] for r in newer], ['4','5','6'])

    def test_references_and_segments_only_observed_not_downloaded(self):
        rows = messages(3)
        rows[2]['message'].extend([
            {'type': 'reply', 'data': {'id': rows[0]['message_id']}},
            {'type': 'image', 'data': {'url': 'https://example.invalid/private-image'}},
            {'type': 'file', 'data': {'file_id': 'fixture-file', 'name': 'fixture.pdf'}},
        ])
        client = ScriptedFixture([rows])
        out = probe(client, BASE)
        self.assertEqual((out['references_seen'], out['references_in_window']), (1, 1))
        self.assertEqual(out['segment_counts'], {'text': 3, 'reply': 1, 'image': 1, 'file': 1})
        self.assertEqual(len(client.actions), 3)  # login, version, one history call

    def test_account_provider_errors_and_sanitized_report(self):
        class Wrong(HistoryFixture):
            def call(self, action, params, remaining=None):
                if action == 'get_login_info':
                    return {'user_id': 999}
                raise AssertionError('History must not be queried for the wrong account')
        self.assertEqual(probe(Wrong(), BASE)['stop_reason'], 'account_mismatch')
        class Other(HistoryFixture):
            def call(self, action, params, remaining=None):
                if action == 'get_version_info':
                    return {'app_name': 'Different provider'}
                return super().call(action, params, remaining)
        self.assertEqual(probe(Other(), BASE)['stop_reason'], 'not_napcat')
        out = json.dumps(probe(HistoryFixture(), BASE))
        for private in ('fixture message', '700000001', 'peer_id', 'match_text', 'token', 'http://'):
            self.assertNotIn(private, out)

    def test_mapping_capacity_is_not_a_history_count_limit_in_fixture(self):
        client = HistoryFixture(messages(5200))
        params = {'group_id': '222', 'message_seq': '0', 'count': 100, 'reverse_order': True}
        seen = set()
        while True:
            rows = client.call('get_group_msg_history', params)['messages']
            seen.update(row['message_id'] for row in rows)
            oldest = min(rows, key=lambda row: int(row['real_seq']))
            params['message_seq'] = str(oldest['message_id'])
            if oldest['real_seq'] == '1':
                break
        self.assertEqual(len(seen), 5200)
        self.assertLessEqual(len(client.mapping), 5000)
        client.restart()
        with self.assertRaisesRegex(ProbeError, 'onebot_failed'):
            client.call('get_group_msg_history', params)
        params['message_seq'] = '9000000000000000001'
        self.assertEqual(len(client.call('get_group_msg_history', params)['messages']), 1)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                cls.requests.append(self.path)
                body = self.rfile.read(int(self.headers['Content-Length']))
                if self.headers.get('Authorization') != 'Bearer fixture-secret':
                    self.send_response(403)
                    self.end_headers()
                    return
                if cls.behavior == 'redirect':
                    self.send_response(302)
                    self.send_header('Location', cls.url + '/must-not-follow')
                    self.end_headers()
                    return
                if cls.behavior == 'oversized':
                    raw = b'x' * (MAX_RESPONSE + 1)
                elif cls.behavior == 'failed':
                    raw = json.dumps({'status': 'failed', 'retcode': 1400,
                                      'message': 'fixture-secret and private message'}).encode()
                elif cls.behavior == 'invalid':
                    raw = b'not json: fixture-secret'
                else:
                    data = cls.backend.call(self.path.lstrip('/'), json.loads(body))
                    raw = json.dumps({'status': 'ok', 'retcode': 0, 'data': data}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (ConnectionError, OSError):
                    pass  # Size guard may close the synthetic connection early.

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)

    def setUp(self):
        type(self).requests, type(self).behavior, type(self).backend = [], 'ok', HistoryFixture()

    def test_real_loopback_http_bearer_history_and_no_write_api(self):
        client = LocalHTTP(self.url, 'fixture-secret')
        self.assertTrue(probe(client, BASE)['target_found'])
        before = len(self.requests)
        for action in ('send_group_msg', 'set_group_ban', 'get_group_msg_history_async', 'query_db'):
            with self.assertRaisesRegex(ProbeError, 'action_not_readonly'):
                client.call(action, {})
        self.assertEqual(len(self.requests), before)

    def test_auth_error_redirect_size_and_server_messages_are_sanitized(self):
        self.assertEqual(probe(LocalHTTP(self.url, 'wrong'), BASE)['stop_reason'], 'http_auth_denied')
        for behavior, reason in (('redirect', 'redirect_refused'), ('oversized', 'response_too_large'),
                                 ('failed', 'onebot_failed'), ('invalid', 'transport_or_json_error')):
            type(self).behavior = behavior
            out = probe(LocalHTTP(self.url, 'fixture-secret'), BASE)
            self.assertEqual(out['stop_reason'], reason)
            self.assertNotIn('fixture-secret', json.dumps(out))
        self.assertNotIn('/must-not-follow', self.requests)

    def test_endpoint_and_token_validation(self):
        for endpoint in (123, None, 'http://example.com', 'http://127.0.0.1@evil.invalid', 'https://127.0.0.1',
                         self.url+'?access_token=secret', self.url+'/#fragment', self.url+'/../x'):
            with self.assertRaises(ProbeError):
                LocalHTTP(endpoint, 'fixture-secret')
        with self.assertRaisesRegex(ProbeError, 'invalid_token'):
            LocalHTTP(self.url, 'secret\r\nInjected: header')


if __name__ == '__main__':
    unittest.main(verbosity=2)
