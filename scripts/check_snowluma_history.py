"""Offline source/import contracts; no real QQ, Docker or network calls."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chatlocal import snowluma_history as h
from chatlocal.snowluma_history_live import SnowLumaLiveWorker
from chatlocal.native_sources import save_source
from chatlocal.store import Store

ACCOUNT = '12345'
CID = ACCOUNT+':group:67890'
CONV = dict(conversation_id=CID, conversation='fixture', conversation_type='group')
BASE = 1792200000


def row(mid, ts=None, **updates):
    return dict(dict(message_id=mid, time=ts or BASE+mid, message_seq=mid, message_type='group',
                     sub_type='normal', group_id=67890, user_id=45678,
                     sender=dict(user_id=45678, nickname='fixture'),
                     message=[dict(type='text', data=dict(text='fixture'))]), **updates)


class FakeClient:
    account = ACCOUNT
    def __init__(self, pages=None):
        self.pages = pages or {0:[row(3), row(2)], 2:[row(2), row(1)], 1:[row(1)]}
        self.calls = []; self.valid = True
    def assert_current(self):
        if not self.valid: raise ValueError('revoked')
    def verify_account(self): self.assert_current()
    def call(self, action, params):
        self.assert_current(); self.calls.append((action, params))
        if action == 'get_group_list': return [dict(group_id=67890, group_name='fixture')]
        if action == 'get_friend_list': return []
        if action == 'get_group_msg_history': return dict(messages=[dict(m) for m in self.pages[params['message_id']]])
        raise ValueError('unexpected action')


class Receiver:
    def __init__(self, on_event, on_change): self.started = False
    def start(self): self.started = True
    def stop(self): self.started = False


class Checks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp', prefix='sl-history-check-')
        self.root = Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def args(self, **values):
        return argparse.Namespace(**dict(dict(account=ACCOUNT, list=False, chat=[CID], start='', end='',
            date_range=True, days=30, per_chat=10, output=str(self.root/'export.json'), no_stickers=True,
            incremental_request=None, batch_dir=None, all_messages=False), **values))
    def state(self, **values):
        path = self.root/'data/snowluma-managed/state.json'; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(dict(backend='docker', phase='waiting_login', account=ACCOUNT, enabled=True,
                                             instance='instance', generation=1, owner=dict(id='container')), **values)))

    def test_docker_discovery_without_login_or_native_reader(self):
        self.state()
        with patch.object(Path, 'home', return_value=self.root):
            self.assertTrue(h.selected(self.root))
            self.assertEqual(h.accounts(self.root), [ACCOUNT])
            info = h.source_info(self.root)
            self.assertFalse(info['source_ready']); self.assertFalse(info['requires_key'])
            self.assertIn('扫码', info['detail'])

    def test_explicit_native_path_and_mode_win(self):
        self.state()
        save_source('qq', dict(data_root=str(self.root)), self.root)
        self.assertFalse(h.selected(self.root))
        save_source('qq', dict(source='snowluma'), self.root)
        self.assertTrue(h.selected(self.root))
        save_source('qq', dict(source='database',data_root=''), self.root)
        self.assertFalse(h.selected(self.root))

    def test_account_binding_revocation(self):
        import time
        self.state(phase='ready', verified_at=time.time())
        c = h.HistoryClient.__new__(h.HistoryClient); c.root = self.root
        c.binding = c.identity(h.state(self.root)); c.assert_current()
        self.state(phase='ready', verified_at=time.time(), generation=2)
        with self.assertRaisesRegex(ValueError, '已变化'): c.assert_current()

    def test_device_entries_are_not_chat_histories(self):
        client = FakeClient()
        friends = [dict(user_id=int(uid), nickname='device') for uid in h.DATALINE_DEVICES]
        friends.append(dict(user_id=45678, nickname='friend'))
        call = client.call
        with patch.object(client, 'call', side_effect=lambda a,p: friends if a == 'get_friend_list' else call(a,p)):
            result = h.catalog(client)
        self.assertEqual([r['conversation_id'] for r in result], [CID, ACCOUNT+':direct:45678'])

    def test_malformed_segments_fail_explicitly(self):
        for message in ([None], [dict(type='text', data=['invalid'])], [dict(type=[]) ]):
            with self.assertRaisesRegex(ValueError, '消息片段'):
                h.convert_message(row(1, message=message), CONV, ACCOUNT)

    def test_pagination_short_overlapping_pages(self):
        client = FakeClient()
        self.assertEqual([m['message_id'] for m in h.history(client, CONV)], [3,2,1])
        self.assertEqual([p['message_id'] for a,p in client.calls], [0,2,1])

    def test_date_limit_and_progress_failure(self):
        self.assertEqual([m['message_id'] for m in h.history(FakeClient(), CONV, start=BASE+2, end=BASE+3)], [2])
        self.assertEqual([m['message_id'] for m in h.history(FakeClient(), CONV, limit=1)], [3])
        stuck = FakeClient({0:[row(3),row(2)],2:[row(3),row(2)]})
        with self.assertRaisesRegex(ValueError, '分页重复'): list(h.history(stuck, CONV))

    def test_cross_group_page_is_rejected_before_yield(self):
        c = FakeClient({0:[row(2),row(1, group_id=99999)]})
        with self.assertRaisesRegex(ValueError, '其他会话'): next(h.history(c, CONV))

    def test_private_self_requires_recipient(self):
        conv = dict(conversation_id=ACCOUNT+':direct:45678', conversation='friend', conversation_type='direct')
        value = row(1, message_type='private', sub_type='friend', user_id=12345,
                    sender=dict(user_id=12345), target_id=45678)
        self.assertTrue(h.convert_message(value, conv, ACCOUNT)['is_self'])
        del value['target_id']
        with self.assertRaisesRegex(ValueError, '其他会话'): h.convert_message(value, conv, ACCOUNT)

    def test_import_is_scoped_and_idempotent(self):
        args = self.args(); c = FakeClient()
        receipt = h.export_history(args, client=c, data_dir=self.root/'data')
        self.assertEqual(receipt['messages'], 3)
        store = Store(self.root/'data/fixture.sqlite3')
        first = store.import_file(args.output); second = store.import_file(args.output)
        self.assertEqual(first['imported'], 3); self.assertEqual(second['duplicate'], 3)
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT DISTINCT conversation_id FROM messages').fetchall()[0][0], CID)
        with self.assertRaisesRegex(ValueError, '未扩大'):
            h.export_history(self.args(chat=[ACCOUNT+':group:99999']), client=FakeClient(), data_dir=self.root/'data')

    def test_failed_export_does_not_advance_source(self):
        self.state(); meta = self.root/'data/qq-snapshot-info.json'; meta.write_text('{"sentinel":1}')
        c = FakeClient({0:[row(3),row(2)],2:[row(3),row(2)]})
        with self.assertRaises(ValueError): h.export_history(self.args(), client=c, data_dir=self.root/'data')
        self.assertEqual(json.loads(meta.read_text()), dict(sentinel=1))
        self.assertFalse((self.root/'export.json').exists())

    def test_incremental_checkpoint_keeps_unselected_conversation(self):
        request = self.root/'request.json'
        old = dict(method=h.METHOD, account=ACCOUNT, cursors={'other':dict(at=BASE), CID:dict(at=BASE+2)})
        request.write_text(json.dumps(dict(checkpoint=old, conversations=[CID])))
        h.export_history(self.args(incremental_request=str(request)),client=FakeClient(),data_dir=self.root/'data')
        result=json.loads((self.root/'export.json').read_text())['sync_checkpoint']
        self.assertEqual(result['cursors']['other'], dict(at=BASE))
        self.assertEqual(result['method'],h.METHOD)

    def test_batch_receipt_and_sync_commit_without_native_inventory(self):
        from types import SimpleNamespace
        from chatlocal.import_scope import save_scope
        from chatlocal.sync import sync_messages
        batch = self.root/'batches'; batch.mkdir()
        args = self.args(batch_dir=str(batch))
        h.export_history(args, client=FakeClient(), data_dir=self.root/'data')
        store = Store(self.root/'data/fixture.sqlite3')
        for path in batch.glob('batch-*.json'): store.import_file(path)
        store.import_file(args.output)
        save_scope(store, dict(qq=dict(enabled=True, account=ACCOUNT, conversations=[CID]),
                               wechat=dict(enabled=False, conversations=None)))

        def runner(cmd, **kwargs):
            h.export_history(self.args(incremental_request=cmd[cmd.index('--incremental-request')+1],
                                       output=cmd[cmd.index('--output')+1]),
                             client=FakeClient(), data_dir=self.root/'data')
            return SimpleNamespace(returncode=0, stderr='', stdout='')

        with patch('chatlocal.client_accounts.discover_accounts', return_value=[ACCOUNT]):
            self.assertEqual(sync_messages(store, ['qq'], runner=runner)[0]['status'], 'ok')
            self.assertEqual(sync_messages(store, ['qq'], runner=runner)[0]['status'], 'ok')
        with store.connect() as db:
            checkpoint = json.loads(db.execute("SELECT checkpoint FROM sync_state WHERE platform='qq'").fetchone()[0])
            self.assertEqual(checkpoint['method'], h.METHOD)
            self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0], 3)
            self.assertEqual(db.execute('SELECT count(*) FROM sync_inventory').fetchone()[0], 0)

    def test_live_replays_until_committed_and_ignores_other_accounts(self):
        worker = SnowLumaLiveWorker(client=FakeClient(), receiver_factory=Receiver)
        try:
            worker.receive(row(1, self_id=12345, post_type='message'))
            worker.receive(row(2, self_id=99999, post_type='message'))
            one=worker.read(dict(checkpoint={}))
            two=worker.read(dict(checkpoint={}))
            self.assertEqual(one['payload']['messages'],two['payload']['messages'])
            self.assertEqual(len(one['payload']['messages']),1)
            self.assertEqual(one['verification_state'],'onebot_received')
            self.assertEqual(worker.read(dict(checkpoint=one['checkpoint']))['payload']['messages'],[])
        finally: worker.close()
        self.assertFalse(worker.receiver.started)


if __name__ == '__main__': unittest.main()
