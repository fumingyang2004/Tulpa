"""First-import preauthorization, pinning and invalidation on isolated data."""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chatlocal.store import Store
from chatlocal.mcp_access import MCPAccess, AccessDenied


def denied(fn, error=AccessDenied):
    try:
        fn()
    except error:
        return
    raise AssertionError('Expected rejection')


with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as tmp:
    folder = Path(tmp)
    store = Store(folder/'chats.sqlite3')
    access = MCPAccess(store)
    access.configure(True, 18777)
    accounts = {'qq': '', 'wechat': ''}
    body = dict(name='Future readonly', platforms=['qq', 'wechat'],
                all_conversations=True, allow_future_imports=True, media=True, prepare=True)
    with patch.object(access, 'accounts', side_effect=lambda platforms: {p: accounts[p] for p in platforms}):
        for flag in ('send', 'manage', 'onebot'):
            denied(lambda: access.create(dict(body, **{flag: True})), ValueError)
        denied(lambda: access.create(dict(body, all_conversations=False)), ValueError)
        first = access.create(body)
        token = first['token']
        assert access.authorize(token)['scope']['pending_platforms'] == ['qq', 'wechat']
        rows = [dict(platform='qq', conversation_id='test', conversation='Test', sender='Fixture',
                     timestamp='2026-10-10', content='Synthetic only')]
        source = folder/'fixture.json'
        source.write_text(json.dumps(rows))
        store.import_file(source)
        assert access.authorize(token)['scope']['pending_platforms'] == ['wechat']
        accounts['qq'] = '123456'
        denied(lambda: access.authorize(token))
        accounts['qq'] = ''
        accounts['wechat'] = 'first_account'
        assert access.authorize(token)['scope']['pending_platforms'] == []
        accounts['wechat'] = 'different_account'
        denied(lambda: access.authorize(token))
        accounts['wechat'] = 'first_account'
        access.revoke(first['id'])
        denied(lambda: access.authorize(token))
        pending = access.create(dict(body, platforms=['wechat']))
        with store.connect() as db:
            db.execute("UPDATE local_change_meta SET epoch='changed' WHERE id=1")
        denied(lambda: access.authorize(pending['token']))
print('PASS: empty source stays pending, first import pins, later account changes/epoch/revocation reject, write scopes excluded')
