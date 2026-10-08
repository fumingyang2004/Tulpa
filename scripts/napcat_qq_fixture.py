"""Synthetic history service, not a NapCat implementation or real QQ evidence.

Models inclusive pages and a volatile short-ID map for adversarial client tests.
Group direction (true=older, false=newer) was checked on NapCat 4.18.33;
this remains a synthetic fixture, not proof of all native pagination behavior.
All accounts, messages and media below are invented. No network/client access.
"""
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime
import json

from napcat_qq_probe import ProbeError, TZ


def messages(count=8, peer='222', kind='group'):
    start = int(datetime(2026, 2, 27, tzinfo=TZ).timestamp())
    result = []
    for n in range(1, count + 1):
        row = dict(self_id=111, user_id=333 if kind == 'group' else int(peer),
                   message_type=kind, message_id=700000000+n,
                   message_seq=700000000+n, real_id=700000000+n, real_seq=str(n),
                   time=start+n, message=[dict(type='text', data=dict(text=f'fixture message {n}'))])
        if kind == 'group':
            row['group_id'] = int(peer)
        result.append(row)
    return result


class HistoryFixture:
    def __init__(self, rows=None, capacity=5000):
        self.rows = deepcopy(rows if rows is not None else messages())
        self.capacity = capacity
        self.mapping = OrderedDict()
        self.actions = []
        self.response_bytes = 0

    def restart(self):
        self.mapping.clear()

    def call(self, action, params, remaining=None):
        self.actions.append((action, deepcopy(params)))
        if action == 'get_login_info':
            data = {'user_id': 111}
        elif action == 'get_version_info':
            data = {'app_name': 'NapCat.Onebot', 'app_version': '4.18.32', 'protocol_version': 'v11'}
        elif action in ('get_group_msg_history', 'get_friend_msg_history'):
            cursor = str(params.get('message_seq', '0'))
            end = len(self.rows)
            if cursor != '0':
                native = self.mapping.get(cursor, cursor)
                # Fixture native IDs are deliberately not short IDs or seqs.
                end = next((i+1 for i, row in enumerate(self.rows)
                            if str(9000000000000000000+int(row['real_seq'])) == native), None)
                if end is None:
                    raise ProbeError('onebot_failed')
            if cursor == '0' or params.get('reverse_order', False):
                page = deepcopy(self.rows[max(0, end-params['count']):end])
            else:
                page = deepcopy(self.rows[end-1:end-1+params['count']])
            for row in page:
                self.mapping[str(row['message_id'])] = str(9000000000000000000+int(row['real_seq']))
                while len(self.mapping) > self.capacity:
                    self.mapping.popitem(last=False)
            data = {'messages': page}
        else:
            raise ProbeError('fixture_action_not_allowed')
        self.response_bytes += len(json.dumps(data).encode())
        return data


class ScriptedFixture(HistoryFixture):
    """Explicit pages/errors, including sequences a native service may return."""
    def __init__(self, pages):
        super().__init__()
        self.pages = deepcopy(pages)

    def call(self, action, params, remaining=None):
        if not action.endswith('_msg_history'):
            return super().call(action, params, remaining)
        self.actions.append((action, deepcopy(params)))
        if not self.pages:
            raise AssertionError('Unexpected extra history request')
        page = self.pages.pop(0)
        if isinstance(page, str):
            raise ProbeError(page)
        return {'messages': deepcopy(page)}
