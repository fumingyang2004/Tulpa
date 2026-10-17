"""Headless Textual pilot checks for the terminal UI. Uses a fake client only; never live SL."""
import asyncio
import copy
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chatlocal.tui import model  # noqa: E402
from chatlocal.tui.app import TerminalError, TulpaApp  # noqa: E402
from textual.widgets import Input, Select, SelectionList  # noqa: E402

SECRET = 'tok_' + 'S' * 40
SIZE = (90, 28)


class FakeClient:
    """In-memory stand-in for TerminalClient. `offline=True` fails like a refused connection."""

    base_url = 'http://127.0.0.1:7861'

    def __init__(self):
        self.lock = threading.Lock()
        self.offline = False
        self.calls = []
        self.sl = dict(http_url='http://127.0.0.1:3000', ws_url='ws://127.0.0.1:3001',
                       has_http_token=True, has_ws_token=False, configured=True)
        self.mcp = dict(running=True, enabled=True, port=18777, url='http://127.0.0.1:18777/mcp', error='')
        # GET /groups returns ids that are already fully qualified (see doc/TERMINAL_API.md).
        self.groups = [dict(id='10001:group:111', name='测试群', account='10001'),
                       dict(id='10001:group:222', name='闲聊群', account='10001')]
        self.grants = [dict(id='g1', name='旧连接', created_at=1700000000, revoked=False, valid=True, calls=3,
                            scope=dict(platforms=['qq'], conversations=['["qq", "10001:group:111"]'], send=True, chat=True))]
        self.sessions = []
        self.learning = {}
        self.search_content = None  # Overrides the generated search result content.
        self.receiver = dict(state='connected', note='')

    def record(self, method, path, body=None):
        with self.lock:
            self.calls.append((method, path, copy.deepcopy(body)))
        if self.offline:
            raise TerminalError('无法连接 Tulpa，请先运行 ./tulpa serve，或核对服务端口。')

    def get(self, path, **params):
        self.record('GET', path, params or None)
        if path == '/overview':
            return dict(version='9.9-test', platform='linux-aarch64', mcp=self.mcp, sl=self.sl,
                        counts=dict(messages=12, connections=len(self.grants), active_sessions=sum(s['active'] for s in self.sessions)),
                        capabilities=dict(native_import=False, managed_snowluma=False),
                        receiver=dict(self.receiver), storage='data')
        if path == '/sl':
            return self.sl
        if path == '/groups':
            return dict(items=self.groups)
        if path == '/grants':
            return dict(items=self.grants)
        if path == '/personas':
            return dict(items=[dict(id='p1', name='温柔学姐', description='说话温和', content='人设全文')])
        if path == '/sessions':
            return dict(items=self.sessions)
        if path.startswith('/sessions/') and path.endswith('/learning'):
            return self.learning.setdefault(path.split('/')[2], dict(enabled=False, library='qq:10001:group:111', expression_count=4))
        if path == '/search':
            return dict(messages=[dict(id='m1', platform='qq', conversation='测试群', conversation_id='10001:group:111',
                                       sender='小明', timestamp=1700000000,
                                       content=self.search_content or f'关于 {params["query"]} 的消息')])
        if path == '/events':
            return dict(items=[dict(id=1, name='旧连接', tool='read_messages', at=1700000000, status='ok', seconds=0.12)])
        raise TerminalError('未找到', status_code=404)

    def post(self, path, body=None):
        self.record('POST', path, body)
        if path == '/sl/test':
            return dict(ok=True, account='10001', message='身份与事件连接正常')
        if path == '/grants':
            gid = f'g{len(self.grants) + 1}'
            self.grants.append(dict(id=gid, name=body['name'], created_at=1700000001, revoked=False, valid=True, calls=0,
                                    scope=dict(platforms=['qq'], conversations=[list(c) for c in body['conversations']])))
            return dict(id=gid, token=SECRET, mcp_url='http://127.0.0.1:18777/mcp', client_config={'url': 'x'})
        if path.endswith('/revoke'):
            next(g for g in self.grants if g['id'] == path.split('/')[2])['revoked'] = True
            return dict(ok=True)
        if path == '/sessions/start':
            sid = f's{len(self.sessions) + 1}'
            self.sessions.append(dict(id=sid, name='测试群', conversation_id=body['conversation_id'], grant_id=body['connection_id'],
                                      active=True, persona_name='温柔学姐', persona_preset=body['persona_preset'],
                                      participation='normal', state='waiting_agent', note='', stop_reason='',
                                      created=1, updated=1700000002))
            return dict(session_id=sid, note='会话已准备，等待 Agent。请让已连接 MCP 的外部 Agent 开始或继续此群聊天。')
        if path.endswith('/stop'):
            session = next(s for s in self.sessions if s['id'] == path.split('/')[2])
            session.update(active=False, state='stopped', stop_reason='手动停止')
            return dict(ok=True)
        raise TerminalError('未找到', status_code=404)

    def put(self, path, body):
        self.record('PUT', path, body)
        if path == '/sl':
            self.sl.update(http_url=body['http_url'], ws_url=body['ws_url'])
            self.sl['has_ws_token'] = self.sl['has_ws_token'] or 'ws_token' in body
            return self.sl
        if path == '/mcp':
            self.mcp.update(enabled=body['enabled'], port=body['port'], running=body['enabled'])
            return self.mcp
        if path.endswith('/learning'):
            self.learning[path.split('/')[2]] = dict(enabled=body['enabled'], library='qq:10001:group:111', expression_count=4)
            return self.learning[path.split('/')[2]]
        raise TerminalError('未找到', status_code=404)

    def close(self):
        pass

    def paths(self, method=None):
        return [p for m, p, _ in self.calls if method in (None, m)]


async def settle(app, pilot, rounds=4):
    """Let thread workers finish and their call_from_thread callbacks apply."""
    for _ in range(rounds):
        await app.workers.wait_for_complete()
        await pilot.pause()


def visible_text(app):
    """Status bar plus every notification: places a credential must never appear."""
    parts = [str(app.query_one('#status').render())]
    parts += [n.message for n in app._notifications]
    return '\n'.join(parts)


def check_model():
    flags = {f: False for f in model.FLAGS}
    flags.update(chat=True, chat_images=True, chat_sticker_send=True)
    assert model.normalize_flags(flags)['chat'] is False, 'chat must require send'
    assert model.normalize_flags(flags)['chat_sticker_send'] is False
    flags['send'] = True
    assert model.normalize_flags(flags)['chat_sticker_send'] is True
    # GET /groups ids arrive fully qualified and must be preserved verbatim.
    group = dict(id='10001:group:111', name='g', account='10001')
    assert model.conversation_id(group) == '10001:group:111'
    body = model.grant_body(' 名称 ', [group, group], dict(send=True))
    assert body['name'] == '名称' and body['platforms'] == ['qq']
    assert body['conversations'] == [['qq', '10001:group:111']]
    assert '10001:group:111' in model.group_label(group)
    assert all(body[f] is False for f in model.FLAGS if f != 'send') and body['send'] is True
    for bad in (dict(name='', groups=[group]), dict(name='x', groups=[])):
        try:
            model.grant_body(bad['name'], bad['groups'], {})
        except ValueError:
            pass
        else:
            raise AssertionError(f'grant_body accepted {bad}')
    try:
        model.grant_body('x', [group], dict(chat=True))
    except ValueError:
        pass
    else:
        raise AssertionError('chat without send must be rejected')
    assert model.sl_body('http://a:1', 'ws://a:2', '', '') == dict(http_url='http://a:1', ws_url='ws://a:2')
    assert model.sl_body('http://a:1', 'ws://a:2', 'h', '')['http_token'] == 'h'
    assert model.parse_port('18777') == 18777
    assert model.scope_conversations({'conversations': ['["qq", "1:group:2"]']}) == {'1:group:2'}
    # Native session states (chatlocal.mcp_chat.public) translate accurately.
    assert model.session_state(dict(active=True, state='waiting_agent')) == '等待 Agent'
    assert model.session_state(dict(active=True, state='')) == '等待 Agent'
    assert model.session_state(dict(active=False)) == '已停止'
    assert model.session_state(dict(active=False, state='stopped')) == '已停止'
    for state in ('waiting_messages', 'agent_connected', 'service_offline'):
        label = model.session_state(dict(active=True, state=state))
        assert label and label != state and '模型' not in label, (state, label)
    assert 'Agent 已接入' in model.session_state(dict(active=True, state='agent_connected'))
    assert model.session_state(dict(active=True, state='future_state')) == 'future_state'
    # Receiver states from chatlocal.onebot_events are translated too.
    assert model.receiver_state(dict(state='connected')) == '已连接'
    assert model.receiver_state(dict(state='upstream_offline')) != 'upstream_offline'
    assert model.receiver_state(dict(state='brand_new')) == 'brand_new'
    # Only grants that permit continuous chat can host a session.
    chatty = dict(id='g', scope=dict(send=True, chat=True))
    assert model.grant_chat_capable(chatty)
    assert not model.grant_chat_capable(dict(id='g', scope=dict(send=True)))
    assert not model.grant_chat_capable(dict(id='g', scope=dict(media=True)))
    assert not model.grant_chat_capable(dict(dict(chatty), revoked=True))


async def check_navigation():
    client = FakeClient()
    app = TulpaApp(client=client)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        assert app.conn_state == 'online'
        assert client.paths() == ['/overview'], f'only the overview loads at start: {client.paths()}'
        for key, page in zip('234567', ('sl', 'grants', 'personas', 'sessions', 'search', 'events')):
            await pilot.press('escape', key)  # Inputs keep digits; Esc returns to the menu first.
            await settle(app, pilot)
            assert app.current == page, (key, app.current)
        # SL page loads read-only; it never writes or tests on its own.
        assert '/sl' in client.paths('GET')
        assert '/sl/test' not in client.paths() and not client.paths('PUT')
        assert '/events' in client.paths('GET') and '/personas' in client.paths('GET')
        # Typing digits/letters in the search box must not navigate.
        await pilot.press('escape', '6')
        await settle(app, pilot)
        assert app.focused.id == 'search-input'
        await pilot.press(*'q2r', 'enter')
        await settle(app, pilot)
        assert app.current == 'search' and app.is_running
        assert ('GET', '/search', dict(query='q2r', limit=20)) in client.calls
        assert app.query_one('#search-table').row_count == 1
        # Sidebar navigation with arrows and Escape.
        await pilot.press('escape')
        assert app.focused.id == 'sidebar'
        await pilot.press('up', 'up')
        await settle(app, pilot)
        assert app.current == 'personas'
        await pilot.press('enter')
        await settle(app, pilot)
        await pilot.press('enter')  # Opens persona text.
        await pilot.pause()
        assert type(app.screen).__name__ == 'TextModal'
        await pilot.press('escape')
        await pilot.pause()
        # Resize down and up without errors.
        await pilot.resize_terminal(90, 28)
        await pilot.pause()
        assert app.has_class('-narrow')
        await pilot.resize_terminal(140, 40)
        await pilot.pause()
        assert not app.has_class('-narrow')


async def check_offline_recovery():
    client = FakeClient()
    client.offline = True
    app = TulpaApp(client=client)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        assert app.conn_state == 'offline'
        assert app.query_one('#offline').display and not app.query_one('#content').display
        assert 'tulpa serve' in str(app.query_one('#offline').render())
        await pilot.press('escape', '3')  # Navigation while offline must not crash or fake data.
        await settle(app, pilot)
        assert app.query_one('#grant-table').row_count == 0
        client.offline = False
        # One press of r after recovery must refresh the page data, not just the header.
        await pilot.press('r')
        await settle(app, pilot)
        assert app.conn_state == 'online'
        assert app.query_one('#content').display and not app.query_one('#offline').display
        assert app.current == 'grants' and app.query_one('#grant-table').row_count == 1
        assert '/grants' in client.paths('GET'), client.paths('GET')
        assert not app.query_one('#grants-state').display, str(app.query_one('#grants-state').render())
        # The same holds for a page that had already loaded before the outage.
        client.offline = True
        app.request(lambda c: c.get('/grants'))
        await settle(app, pilot)
        assert app.conn_state == 'offline'
        client.offline = False
        client.grants.append(dict(id='g2', name='断线后新增', created_at=1700000005, revoked=False,
                                  valid=True, calls=0, scope=dict(platforms=['qq'], conversations=[], send=True)))
        await pilot.press('r')
        await settle(app, pilot)
        assert app.conn_state == 'online'
        assert app.query_one('#grant-table').row_count == 2, 'r after recovery must reload page data'
        assert '断线后新增' in screen_text(app)
        # A server-side rejection (has status) keeps the app online.
        app.request(lambda c: c.get('/nope'))
        await settle(app, pilot)
        assert app.conn_state == 'online'


async def check_settings():
    client = FakeClient()
    app = TulpaApp(client=client)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await pilot.press('2')
        await settle(app, pilot)
        view = app.query_one('#sl')
        assert view.query_one('#sl-http').value == 'http://127.0.0.1:3000'
        assert view.query_one('#sl-http-token').password and view.query_one('#sl-ws-token').password
        view.query_one('#sl-http').value = 'bad'
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        assert not client.paths('PUT'), 'invalid URL must not be submitted'
        view.query_one('#sl-http').value = 'http://127.0.0.1:3100'
        view.query_one('#sl-ws-token').focus()
        await pilot.press(*'wsSecret1')
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        put = [b for m, p, b in client.calls if (m, p) == ('PUT', '/sl')]
        assert put == [dict(http_url='http://127.0.0.1:3100', ws_url='ws://127.0.0.1:3001', ws_token='wsSecret1')], put
        assert view.query_one('#sl-ws-token').value == '', 'token field clears after save'
        assert 'wsSecret1' not in visible_text(app)
        assert '/sl/test' not in client.paths()
        await pilot.press('ctrl+t')
        await settle(app, pilot)
        assert client.paths('POST') == ['/sl/test']
        assert '10001' in str(view.query_one('#sl-result').render())
        # MCP enable/port.
        await pilot.press('escape', '3')
        await settle(app, pilot)
        await pilot.press('m')
        await pilot.pause()
        screen = app.screen
        screen.query_one('#mcp-port').value = '80'
        await pilot.press('ctrl+s')
        await pilot.pause()
        assert app.screen is screen and not any(p == '/mcp' for p in client.paths('PUT'))
        screen.query_one('#mcp-port').value = '18800'
        screen.query_one('#mcp-enabled').value = False
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        assert ('PUT', '/mcp', dict(enabled=False, port=18800)) in client.calls
        assert not any(p == '/grants' for p in client.paths('POST')), 'MCP settings never create grants'


async def check_grants():
    client = FakeClient()
    app = TulpaApp(client=client)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await pilot.press('3')
        await settle(app, pilot)
        await pilot.press('n')
        await settle(app, pilot)
        form = app.screen
        assert type(form).__name__ == 'GrantForm'
        boxes = {f: form.query_one(f'#perm-{f}') for f in model.FLAGS}
        assert all(not b.value for b in boxes.values()), 'every permission starts off'
        assert boxes['chat'].disabled and boxes['chat_sticker_send'].disabled
        # Empty name / no group is rejected inside the form.
        await pilot.press('ctrl+s')
        await pilot.pause()
        assert app.screen is form and not any(p == '/grants' for p in client.paths('POST'))
        await pilot.press(*'新连接')
        await pilot.press('ctrl+s')
        await pilot.pause()
        assert app.screen is form and '至少选择一个群' in str(form.query_one('#grant-error').render())
        form.query_one('#group-list').select(0)
        # Enable the chain send -> chat -> images -> sticker send; turning send off clears it.
        for flag in ('send', 'chat', 'chat_images', 'chat_sticker_send'):
            boxes[flag].value = True
            await pilot.pause()
            assert boxes[flag].value, flag
        boxes['send'].value = False
        await pilot.pause()
        assert not boxes['chat'].value and not boxes['chat_sticker_send'].value and boxes['chat'].disabled
        boxes['send'].value = True
        await pilot.pause()
        boxes['chat'].value = True
        await pilot.pause()
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        created = [b for m, p, b in client.calls if (m, p) == ('POST', '/grants')]
        assert len(created) == 1, created
        body = created[0]
        assert body['name'] == '新连接' and body['conversations'] == [['qq', '10001:group:111']]
        assert set(body) == {'name', 'platforms', 'conversations', 'start', 'end', *model.FLAGS}
        assert body['send'] and body['chat'] and not body['chat_images'] and not body['manage']
        assert type(app.screen).__name__ == 'CredentialModal'
        assert app.screen.query_one('#token').text == SECRET
        assert SECRET not in visible_text(app), 'credential must stay out of status/notifications'
        await pilot.press('escape')
        await settle(app, pilot)
        assert SECRET not in visible_text(app)
        table = app.query_one('#grant-table')
        assert table.row_count == 2
        # Revoke: cancel first, then confirm.
        table.focus()
        table.move_cursor(row=0)
        await pilot.press('d')
        await pilot.pause()
        assert type(app.screen).__name__ == 'ConfirmModal' and '旧连接' in app.screen.body_text
        await pilot.press('n')
        await settle(app, pilot)
        assert not any(p.endswith('/revoke') for p in client.paths())
        await pilot.press('d')
        await pilot.pause()
        await pilot.press('y')
        await settle(app, pilot)
        assert ('POST', '/grants/g1/revoke', {}) in client.calls
        assert client.grants[0]['revoked']
        assert '已撤销' in str(table.get_row_at(0))
        # No groups available means no form and no grant.
        client.groups = []
        await pilot.press('n')
        await settle(app, pilot)
        assert type(app.screen).__name__ != 'GrantForm'
        assert '没有可选的群' in str(app.query_one('#grants-state').render())


async def check_sessions():
    client = FakeClient()
    app = TulpaApp(client=client)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await pilot.press('5')
        await settle(app, pilot)
        assert '还没有持续会话' in str(app.query_one('#sessions-state').render())
        await pilot.press('n')
        await settle(app, pilot)
        form = app.screen
        assert type(form).__name__ == 'StartForm'
        # Single grant and persona preselect; the grant's scope narrows groups to 111.
        await pilot.pause()
        group = form.query_one('#start-group')
        assert form.query_one('#start-grant').value == 'g1'
        assert group.value == '10001:group:111', group.value
        key = form.request_key
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        starts = [b for m, p, b in client.calls if (m, p) == ('POST', '/sessions/start')]
        assert starts == [dict(connection_id='g1', conversation_id='10001:group:111', persona_preset='p1', idempotency_key=key)]
        state = str(app.query_one('#sessions-state').render())
        assert '等待 Agent' in state and '模型已启动' not in state
        table = app.query_one('#session-table')
        assert table.row_count == 1 and '等待 Agent' in str(table.get_row_at(0))
        table.focus()
        # Learning: read, toggle on, JSON detail.
        await pilot.press('l')
        await settle(app, pilot)
        assert 'GET' in [m for m, p, _ in client.calls if p == '/sessions/s1/learning']
        await pilot.press('t')
        await settle(app, pilot)
        assert ('PUT', '/sessions/s1/learning', dict(enabled=True)) in client.calls
        assert '开启' in str(app.query_one('#session-detail').render())
        await pilot.press('j')
        await pilot.pause()
        assert type(app.screen).__name__ == 'TextModal' and '"enabled": true' in app.screen.text
        await pilot.press('escape')
        await pilot.pause()
        # Stop with confirmation.
        await pilot.press('s')
        await pilot.pause()
        assert type(app.screen).__name__ == 'ConfirmModal'
        await pilot.press('y')
        await settle(app, pilot)
        assert ('POST', '/sessions/s1/stop', {}) in client.calls
        assert '已停止' in str(table.get_row_at(0))
        # A read-only grant cannot host continuous chat: no form, and a clear empty state.
        client.grants[0]['scope'] = dict(platforms=['qq'], conversations=['["qq", "10001:group:111"]'], media=True)
        await pilot.press('n')
        await settle(app, pilot)
        assert type(app.screen).__name__ != 'StartForm'
        state = str(app.query_one('#sessions-state').render())
        assert '持续聊天' in state and 'MCP 授权' in state, state
        # A send-only grant is still not enough; chat must be granted as well.
        client.grants[0]['scope'] = dict(platforms=['qq'], conversations=['["qq", "10001:group:111"]'], send=True)
        await pilot.press('n')
        await settle(app, pilot)
        assert type(app.screen).__name__ != 'StartForm'
        # Revoked grants are not offered either.
        client.grants[0]['scope'] = dict(platforms=['qq'], conversations=['["qq", "10001:group:111"]'],
                                         send=True, chat=True)
        client.grants[0]['revoked'] = True
        await pilot.press('n')
        await settle(app, pilot)
        assert type(app.screen).__name__ != 'StartForm'
        assert '可持续聊天的 MCP 连接' in str(app.query_one('#sessions-state').render())
        # Only chat-capable grants reach the form when both kinds exist.
        client.grants[0]['revoked'] = False
        client.grants.append(dict(id='g9', name='只读连接', created_at=1700000003, revoked=False, valid=True,
                                  calls=0, scope=dict(platforms=['qq'],
                                                      conversations=['["qq", "10001:group:222"]'], media=True)))
        await pilot.press('n')
        await settle(app, pilot)
        form = app.screen
        assert type(form).__name__ == 'StartForm'
        assert [g['id'] for g in form.grants] == ['g1'], [g['id'] for g in form.grants]
        await pilot.press('escape')
        await settle(app, pilot)


FRIEND = '10001:direct:333'
OTHER_FRIEND = '10001:direct:444'


class SLClient(FakeClient):
    """FakeClient plus the SL extension endpoints (GET /friends, GET /grant-permissions)."""

    def __init__(self):
        super().__init__()
        # A bare number is not a verified friend id and must never be offered.
        self.friends = [dict(id=FRIEND, name='小红', account='10001'), dict(id=OTHER_FRIEND, name='小刚', account='10001'),
                        dict(id='555', name='裸号', account='10001')]
        self.permissions = [dict(id=f, label=label, scope='account' if f in model.ACCOUNT_FLAGS else 'conversation',
                                 default=False) for f, label, _ in model.QQ_PERMISSIONS]

    def get(self, path, **params):
        if path == '/friends':
            self.record('GET', path)
            return dict(items=self.friends)
        if path == '/grant-permissions':
            self.record('GET', path)
            return dict(items=self.permissions)
        return super().get(path, **params)


def check_extension_model():
    perms = model.extension_permissions(dict(items=[dict(id='qq_qzone', label='空间', scope='conversation'),
                                                    dict(id='chat_files', label='文件', scope='conversation'),
                                                    dict(id='mystery', label='未知')]))
    assert [p[0] for p in perms] == ['chat_files', 'qq_qzone'], perms
    assert dict((p[0], p[3]) for p in perms) == dict(chat_files='conversation', qq_qzone='account'), \
        'account-wide flags stay account scoped whatever the label says'
    assert model.extension_permissions(None) == []
    group = dict(id='10001:group:111', name='g', account='10001')
    friend = dict(id=FRIEND, name='f', account='10001')
    assert model.valid_targets([friend, dict(id='333', name='x', account='10001'), group], 'direct') == [friend]
    assert model.valid_targets([group, friend], 'group') == [group]
    assert model.group_label(friend).startswith('[好友私聊]') and FRIEND in model.group_label(friend)
    extra = list(model.QQ_FLAGS)
    for attachment in ('chat_voice', 'chat_video', 'chat_files'):
        for flags in ({attachment: True}, {attachment: True, 'send': True}):
            try:
                model.grant_body('x', [group], flags, extra=extra)
            except ValueError:
                pass
            else:
                raise AssertionError(f'{attachment} without chat+send must be rejected: {flags}')
        body = model.grant_body('x', [group], {attachment: True, 'send': True, 'chat': True}, extra=extra)
        assert body[attachment] is True
    body = model.grant_body('x', [group, friend], dict(qq_friends=True), extra=extra)
    assert body['conversations'] == [['qq', '10001:group:111'], ['qq', FRIEND]]
    assert all(body[f] is False for f in model.QQ_FLAGS if f != 'qq_friends') and body['qq_friends'] is True
    # Without the backend list no extension field is sent, and a stray one is refused.
    assert not set(model.QQ_FLAGS) & set(model.grant_body('x', [group], {}))
    try:
        model.grant_body('x', [group], dict(qq_profile=True))
    except ValueError:
        pass
    else:
        raise AssertionError('an extension flag the backend did not offer must not be sent')
    summary = model.scope_summary(dict(conversations=[['qq', '10001:group:1'], ['qq', FRIEND]], qq_qzone=True))
    assert '1 个群' in summary and '1 个好友' in summary and '账号级' in summary, summary


async def check_grant_extensions():
    client = SLClient()
    app = TulpaApp(client=client)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await pilot.press('escape', '3')
        await settle(app, pilot)
        await pilot.press('n')
        await settle(app, pilot)
        form = app.screen
        assert type(form).__name__ == 'GrantForm'
        assert {'/groups', '/friends', '/grant-permissions'} <= set(client.paths('GET'))
        group_labels = [str(s.prompt) for s in form.query_one('#group-list', SelectionList)._options]
        friend_labels = [str(s.prompt) for s in form.query_one('#friend-list', SelectionList)._options]
        assert all(l.startswith('[群]') for l in group_labels), group_labels
        assert len(friend_labels) == 2 and all(l.startswith('[好友私聊]') for l in friend_labels), friend_labels
        assert FRIEND in friend_labels[0] and not any('555' in l for l in friend_labels), 'no guessed friend ids'
        boxes = {f: form.query_one(f'#perm-{f}') for f in model.FLAGS + model.QQ_FLAGS}
        assert all(not b.value for b in boxes.values()), 'every permission, old and new, starts off'
        assert all(boxes[f].disabled for f in ('chat_voice', 'chat_video', 'chat_files')), 'attachments need chat+send'
        assert not boxes['qq_messages'].disabled and not boxes['qq_friends'].disabled
        hint = str(form.query_one('#account-scope-hint').render())
        assert '不局限' in hint and '账号' in hint, hint
        for flag in model.ACCOUNT_FLAGS:
            assert '账号级' in str(boxes[flag].label), (flag, boxes[flag].label)
        for flag in set(model.QQ_FLAGS) - set(model.ACCOUNT_FLAGS):
            assert '账号级' not in str(boxes[flag].label), flag
        # Dependency chain: send -> chat -> attachments; turning send off clears them all.
        for flag in ('send', 'chat', 'chat_voice', 'chat_files', 'chat_images', 'chat_sticker_send'):
            boxes[flag].value = True
            await pilot.pause()
            assert boxes[flag].value, flag
        boxes['send'].value = False
        await pilot.pause()
        assert not any(boxes[f].value for f in ('chat', 'chat_voice', 'chat_files', 'chat_sticker_send'))
        assert boxes['chat_voice'].disabled and boxes['chat_files'].disabled
        for flag in ('send', 'chat', 'chat_video', 'qq_friends', 'qq_qzone', 'qq_messages'):
            boxes[flag].value = True
            await pilot.pause()
        form.query_one('#grant-name', Input).value = '私聊连接'
        form.query_one('#group-list', SelectionList).select(0)
        form.query_one('#friend-list', SelectionList).select(0)
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        created = [b for m, p, b in client.calls if (m, p) == ('POST', '/grants')]
        assert len(created) == 1, created
        body = created[0]
        assert body['conversations'] == [['qq', '10001:group:111'], ['qq', FRIEND]], body['conversations']
        assert set(body) == {'name', 'platforms', 'conversations', 'start', 'end', *model.FLAGS, *model.QQ_FLAGS}
        on = {f for f in (*model.FLAGS, *model.QQ_FLAGS) if body[f] is True}
        assert on == {'send', 'chat', 'chat_video', 'qq_friends', 'qq_qzone', 'qq_messages'}, on
        assert all(body[f] is False for f in (*model.FLAGS, *model.QQ_FLAGS) if f not in on)
        assert type(app.screen).__name__ == 'CredentialModal'
        await pilot.press('escape')
        await settle(app, pilot)
        # The existing grant is never widened by creating a new one.
        assert client.grants[0]['scope'] == dict(platforms=['qq'], conversations=['["qq", "10001:group:111"]'],
                                                 send=True, chat=True)
        assert not any(p.startswith('/grants/g1') for p in client.paths('POST') + client.paths('PUT'))
        # Friends only (no groups) are still enough to create a connection.
        client.groups = []
        await pilot.press('n')
        await settle(app, pilot)
        assert type(app.screen).__name__ == 'GrantForm' and not app.screen.groups
        await pilot.press('escape')
        await settle(app, pilot)


class FailingSLClient(SLClient):
    """The new endpoints fail with a server error; /groups still works."""

    def get(self, path, **params):
        if path in ('/friends', '/grant-permissions'):
            self.record('GET', path)
            raise TerminalError('好友列表暂不可用', status_code=502)
        return super().get(path, **params)


async def check_grant_extensions_fallback():
    """An older or failing backend keeps the original group selection and permissions."""
    for client in (FakeClient(), FailingSLClient()):  # FakeClient answers 404, the other 502.
        app = TulpaApp(client=client)
        async with app.run_test(size=SIZE) as pilot:
            await settle(app, pilot)
            await pilot.press('escape', '3')
            await settle(app, pilot)
            await pilot.press('n')
            await settle(app, pilot)
            form = app.screen
            assert type(form).__name__ == 'GrantForm', type(client).__name__
            assert app.conn_state == 'online'
            assert not form.query('#friend-list') and not form.query('#perm-chat_voice')
            notices = ' '.join(str(w.render()) for w in form.query('.grant-notice'))
            assert '好友列表' in notices and '只能选择群' in notices and '扩展权限' in notices, notices
            form.query_one('#grant-name', Input).value = '旧后台'
            form.query_one('#group-list', SelectionList).select(1)
            await pilot.press('ctrl+s')
            await settle(app, pilot)
            body = [b for m, p, b in client.calls if (m, p) == ('POST', '/grants')][-1]
            assert set(body) == {'name', 'platforms', 'conversations', 'start', 'end', *model.FLAGS}, body
            assert body['conversations'] == [['qq', '10001:group:222']]
        # Sessions on the same backend: friends unavailable, groups unaffected.
        app = TulpaApp(client=client)
        async with app.run_test(size=SIZE) as pilot:
            await settle(app, pilot)
            await pilot.press('escape', '5')
            await settle(app, pilot)
            await pilot.press('n')
            await settle(app, pilot)
            form = app.screen
            assert type(form).__name__ == 'StartForm' and form.friends == []
            assert '只能选择群' in str(form.query_one('#start-notice').render())
            await pilot.pause()
            assert form.query_one('#start-group').value == '10001:group:111'


class EditClient(SLClient):
    """SLClient plus PUT /grants/{id} (permission edit with revision check)."""

    def __init__(self):
        super().__init__()
        self.grants[0].update(revision=3, scope=dict(platforms=['qq'], conversations=['["qq", "10001:group:111"]'],
                                                     send=True, chat=True, chat_files=True, qq_friends=True))
        self.grants.append(dict(id='g2', name='已撤销连接', created_at=1700000002, revoked=True, valid=False, calls=0,
                                revision=1, scope=dict(platforms=['qq'], conversations=['["qq", "10001:group:222"]'])))
        self.put_error = None  # TerminalError raised by the next PUT /grants/{id}.

    def put(self, path, body):
        if path.startswith('/grants/'):
            self.record('PUT', path, body)
            if self.put_error:
                raise self.put_error
            grant = next(g for g in self.grants if g['id'] == path.split('/')[2])
            if body.get('expected_revision', 0) != grant.get('revision', 0):
                raise TerminalError('连接已在别处修改，请刷新后重试。', status_code=409)
            changes = {k: v for k, v in body.items() if k != 'expected_revision'}
            grant['scope'].update(changes)
            grant['revision'] = grant.get('revision', 0) + 1
            return dict(ok=True, id=grant['id'], scope=grant['scope'], revision=grant['revision'],
                        changed=bool(changes), stopped_sessions=1, note='')
        return super().put(path, body)


async def open_grants(app, pilot, row=0):
    await settle(app, pilot)
    await pilot.press('escape', '3')
    await settle(app, pilot)
    table = app.query_one('#grant-table')
    table.focus()
    table.move_cursor(row=row)
    await pilot.pause()
    return table


def grant_puts(client):
    return [(p, b) for m, p, b in client.calls if m == 'PUT' and p.startswith('/grants/')]


async def check_grant_edit():
    client = EditClient()
    app = TulpaApp(client=client)
    async with app.run_test(size=SIZE) as pilot:
        await open_grants(app, pilot)
        assert 'e 修改所选权限' in screen_text(app)
        await pilot.press('e')
        await settle(app, pilot)
        form = app.screen
        assert type(form).__name__ == 'GrantEditForm'
        assert '/groups' not in client.paths('GET') and '/friends' not in client.paths('GET'), 'range is unchanged'
        text = screen_text(app)
        assert '旧连接' in text and '1 个群' in text and '保留原凭证' in text, text
        boxes = {f: form.query_one(f'#perm-{f}') for f in model.FLAGS + model.QQ_FLAGS}
        on = {f for f, b in boxes.items() if b.value}
        assert on == {'send', 'chat', 'chat_files', 'qq_friends'}, f'prefilled from the scope, got {on}'
        assert not boxes['chat_voice'].disabled
        hint = str(form.query_one('#account-scope-hint').render())
        assert '不局限' in hint and '账号' in hint, hint
        assert '账号级' in str(boxes['qq_qzone'].label)
        assert not form.query('#grant-name') and not form.query('#group-list'), 'only switches are editable'
        # Cancel writes nothing; neither does saving without a change.
        await pilot.press('escape')
        await settle(app, pilot)
        await pilot.press('e')
        await settle(app, pilot)
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        assert type(app.screen).__name__ != 'GrantEditForm' and not grant_puts(client)
        assert '没有改动' in str(app.query_one('#grants-state').render())
        # Turning chat off clears its dependants; only the real changes plus the revision are sent.
        await pilot.press('e')
        await settle(app, pilot)
        form = app.screen
        boxes = {f: form.query_one(f'#perm-{f}') for f in model.FLAGS + model.QQ_FLAGS}
        boxes['chat'].value = False
        await pilot.pause()
        assert not boxes['chat_files'].value and boxes['chat_files'].disabled and boxes['chat_voice'].disabled
        boxes['media'].value = True
        await pilot.pause()
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        puts = grant_puts(client)
        assert puts == [('/grants/g1', dict(chat=False, chat_files=False, media=True, expected_revision=3))], puts
        assert not any(p == '/grants' for p in client.paths('POST')), 'editing never creates a grant'
        assert not any(p.endswith('/revoke') for p in client.paths('POST')), 'editing never revokes'
        assert type(app.screen).__name__ not in ('GrantEditForm', 'CredentialModal')
        state = str(app.query_one('#grants-state').render())
        assert '已修改' in state and '原凭证' in state and '1 个会话' in state, state
        assert '已修改连接权限' in visible_text(app) and SECRET not in visible_text(app)
        assert '图片' in str(app.query_one('#grant-table').get_row_at(0)), 'list reloaded after saving'
        # The reopened form uses the new revision.
        await pilot.press('e')
        await settle(app, pilot)
        app.screen.query_one('#perm-qq_qzone').value = True
        await pilot.pause()
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        assert grant_puts(client)[-1] == ('/grants/g1', dict(qq_qzone=True, expected_revision=4))
        # 409: no false success, the list reloads and asks to reopen.
        client.grants[0]['revision'] = 9  # Saved elsewhere meanwhile.
        await pilot.press('e')
        await settle(app, pilot)
        client.grants[0]['revision'] = 10
        app.screen.query_one('#perm-manage').value = True
        await pilot.pause()
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        state = str(app.query_one('#grants-state').render())
        assert '已在别处被修改' in state and '重新按 e' in state and '已修改「' not in state, state
        assert not client.grants[0]['scope'].get('manage')
        # Another failure is reported as a failure.
        client.put_error = TerminalError('需要 SL 在线核对账号', status_code=503)
        await pilot.press('e')
        await settle(app, pilot)
        app.screen.query_one('#perm-voice').value = True
        await pilot.pause()
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        state = str(app.query_one('#grants-state').render())
        assert '保存失败' in state and 'SL 在线' in state, state
        assert not client.grants[0]['scope'].get('voice')
        client.put_error = None
        # A revoked grant cannot be edited, and no selection gives a hint.
        app.query_one('#grant-table').move_cursor(row=1)
        await pilot.pause()
        count = len(client.calls)
        await pilot.press('e')
        await settle(app, pilot)
        assert type(app.screen).__name__ != 'GrantEditForm' and len(client.calls) == count
        assert '已撤销' in str(app.query_one('#grants-state').render())
        client.grants = []
        await pilot.press('r')
        await settle(app, pilot)
        await pilot.press('e')
        await settle(app, pilot)
        assert type(app.screen).__name__ != 'GrantEditForm'
        assert '请先选择' in str(app.query_one('#grants-state').render())


class FailingEditClient(EditClient):
    def get(self, path, **params):
        if path == '/grant-permissions':
            self.record('GET', path)
            raise TerminalError('扩展权限暂不可用', status_code=502)
        return super().get(path, **params)


async def check_grant_edit_fallback():
    """Without /grant-permissions existing extensions are kept; without PUT nothing is replaced."""
    client = FailingEditClient()
    app = TulpaApp(client=client)
    async with app.run_test(size=SIZE) as pilot:
        await open_grants(app, pilot)
        await pilot.press('e')
        await settle(app, pilot)
        form = app.screen
        assert type(form).__name__ == 'GrantEditForm' and not form.query('#perm-chat_files')
        assert '扩展权限保持不变' in ' '.join(str(w.render()) for w in form.query('.grant-notice'))
        form.query_one('#perm-media').value = True
        await pilot.pause()
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        assert grant_puts(client) == [('/grants/g1', dict(media=True, expected_revision=3))], grant_puts(client)
        assert client.grants[0]['scope']['chat_files'] and client.grants[0]['scope']['qq_friends']
        # Turning chat off would silently drop the hidden chat_files: refused in the form.
        await pilot.press('e')
        await settle(app, pilot)
        form = app.screen
        form.query_one('#perm-chat').value = False
        await pilot.pause()
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        assert app.screen is form and '实时文件' in str(form.query_one('#grant-error').render())
        assert len(grant_puts(client)) == 1
        await pilot.press('escape')
        await settle(app, pilot)
    # An old backend without PUT /grants/{id}: tell to upgrade, never create or revoke instead.
    client = FakeClient()
    app = TulpaApp(client=client)
    async with app.run_test(size=SIZE) as pilot:
        await open_grants(app, pilot)
        await pilot.press('e')
        await settle(app, pilot)
        form = app.screen
        assert type(form).__name__ == 'GrantEditForm' and form.revision == 0
        form.query_one('#perm-media').value = True
        await pilot.pause()
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        assert ('PUT', '/grants/g1', dict(media=True, expected_revision=0)) in client.calls
        state = str(app.query_one('#grants-state').render())
        assert '不支持修改' in state and '升级' in state, state
        assert not any(p == '/grants' or p.endswith('/revoke') for p in client.paths('POST'))
        assert type(app.screen).__name__ != 'CredentialModal' and app.conn_state == 'online'


async def check_grant_edit_layout(width, height):
    client = EditClient()
    app = TulpaApp(client=client)
    async with app.run_test(size=(width, height)) as pilot:
        await open_grants(app, pilot)
        await pilot.press('e')
        await settle(app, pilot)
        form = app.screen
        assert_actions_visible(app, f'edit form {width}x{height}', '.form-scroll')
        scroll = form.query_one('.form-scroll')
        assert scroll.virtual_size.height > scroll.size.height
        for target in ('#edit-summary', '#perm-manage', '#account-scope-hint', '#perm-qq_qzone'):
            widget = form.query_one(target)
            scroll.scroll_to_widget(widget, animate=False)
            if widget.focusable:
                widget.focus()
            await settle(app, pilot)
            assert_inside(app, widget, f'edit {target} at {width}x{height}')
            assert_actions_visible(app, f'edit form with {target} in view')
        await pilot.click('#cancel')
        await settle(app, pilot)
        assert type(app.screen).__name__ != 'GrantEditForm' and not grant_puts(client)
        await pilot.press('e')
        await settle(app, pilot)
        app.screen.query_one('#perm-media').value = True
        await pilot.pause()
        await pilot.click('#save')
        await settle(app, pilot)
        assert grant_puts(client) == [('/grants/g1', dict(media=True, expected_revision=3))]


async def check_session_friends():
    client = SLClient()
    client.grants.append(dict(id='g2', name='私聊连接', created_at=1700000004, revoked=False, valid=True, calls=0,
                              scope=dict(platforms=['qq'], send=True, chat=True,
                                         conversations=['["qq", "10001:group:111"]', f'["qq", "{FRIEND}"]'])))
    # A chat grant with no stated conversation list must not expose any friend.
    client.grants.append(dict(id='g3', name='无列表连接', created_at=1700000005, revoked=False, valid=True, calls=0,
                              scope=dict(platforms=['qq'], send=True, chat=True)))
    client.grants.append(dict(id='g4', name='全部会话连接', revoked=False, valid=True,
                              scope=dict(platforms=['qq'], conversations=[], chat_account='10001', send=True, chat=True)))
    client.grants.append(dict(id='g5', name='其他账号连接', revoked=False, valid=True,
                              scope=dict(platforms=['qq'], conversations=[], chat_account='20002', send=True, chat=True)))
    client.grants.append(dict(id='g6', name='损坏的会话范围', revoked=False, valid=True,
                              scope=dict(platforms=['qq'], conversations=['invalid'], chat_account='10001', send=True, chat=True)))
    app = TulpaApp(client=client)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await pilot.press('escape', '5')
        await settle(app, pilot)
        await pilot.press('n')
        await settle(app, pilot)
        form = app.screen
        assert type(form).__name__ == 'StartForm'
        assert [f['id'] for f in form.friends] == [FRIEND, OTHER_FRIEND], 'bare numbers are not friends'
        ids = lambda gid: [model.conversation_id(g) for g in form.groups_for(gid)]  # noqa: E731
        assert ids('g1') == ['10001:group:111']
        assert ids('g2') == ['10001:group:111', FRIEND], ids('g2')
        assert ids('g3') == ['10001:group:111', '10001:group:222'], ids('g3')
        assert ids('g4') == ['10001:group:111', '10001:group:222', FRIEND, OTHER_FRIEND]
        assert ids('g5') == ids('g6') == ids('missing') == []
        assert '全部会话' in model.scope_summary(client.grants[3]['scope'])
        form.query_one('#start-grant', Select).value = 'g2'
        await settle(app, pilot)
        box = form.query_one('#start-group', Select)
        options = {str(v): str(p) for p, v in box._options}
        assert FRIEND in options and OTHER_FRIEND not in options, options
        assert options[FRIEND].startswith('[好友私聊]') and options['10001:group:111'].startswith('[群]')
        box.value = FRIEND
        form.query_one('#start-persona', Select).value = 'p1'
        await pilot.pause()
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        starts = [b for m, p, b in client.calls if (m, p) == ('POST', '/sessions/start')]
        assert starts == [dict(connection_id='g2', conversation_id=FRIEND, persona_preset='p1',
                               idempotency_key=form.request_key)], starts
        table = app.query_one('#session-table')
        assert table.row_count == 1 and FRIEND in str(table.get_row_at(0))
        # Stop and learning keep working for a private-chat session.
        table.focus()
        await pilot.press('t')
        await settle(app, pilot)
        assert ('PUT', '/sessions/s1/learning', dict(enabled=True)) in client.calls
        await pilot.press('s')
        await pilot.pause()
        assert '私聊' in app.screen.body_text
        await pilot.press('y')
        await settle(app, pilot)
        assert ('POST', '/sessions/s1/stop', {}) in client.calls
        # The submit guard refuses a target outside the chosen grant even if one slips in.
        await pilot.press('n')
        await settle(app, pilot)
        form = app.screen
        form.query_one('#start-grant', Select).value = 'g2'
        await settle(app, pilot)
        form.groups_for = lambda gid: []  # Simulates a stale option list.
        form.query_one('#start-group', Select).value = FRIEND
        form.query_one('#start-persona', Select).value = 'p1'
        await pilot.pause()
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        assert app.screen is form and '不在此连接的授权范围' in str(form.query_one('#start-error').render())
        assert len([1 for m, p, _ in client.calls if (m, p) == ('POST', '/sessions/start')]) == 1


async def check_extension_layout(width, height):
    """With friends and nine extra switches the body scrolls and both buttons stay usable."""
    client = SLClient()
    client.friends = [dict(id=f'10001:direct:{900 + i}', name=f'好友 {i}', account='10001') for i in range(10)]
    app = TulpaApp(client=client)
    async with app.run_test(size=(width, height)) as pilot:
        await settle(app, pilot)
        await pilot.press('escape', '3')
        await settle(app, pilot)
        await pilot.press('n')
        await settle(app, pilot)
        form = app.screen
        assert_actions_visible(app, f'extended grant form {width}x{height}', '#grant-name')
        scroll = form.query_one('.form-scroll')
        assert scroll.virtual_size.height > scroll.size.height
        for target in ('#friend-list', '#perm-chat_voice', '#account-scope-hint', '#perm-qq_qzone'):
            widget = form.query_one(target)
            scroll.scroll_to_widget(widget, animate=False)
            if widget.focusable:
                widget.focus()
            await settle(app, pilot)
            assert_inside(app, widget, f'{target} at {width}x{height}')
            assert_actions_visible(app, f'extended grant form with {target} in view')
        await pilot.click('#cancel')
        await settle(app, pilot)
        assert type(app.screen).__name__ != 'GrantForm', 'the cancel button must work at this size'
        await pilot.press('n')
        await settle(app, pilot)
        app.screen.query_one('#grant-name', Input).value = '窄屏'
        app.screen.query_one('#friend-list', SelectionList).select(0)
        await pilot.click('#create')
        await settle(app, pilot)
        assert type(app.screen).__name__ == 'CredentialModal', 'the create button must work at this size'
        body = [b for m, p, b in client.calls if (m, p) == ('POST', '/grants')][-1]
        assert body['conversations'] == [['qq', '10001:direct:900']]


def screen_text(app):
    return '\n'.join(''.join(s.text for s in strip).rstrip() for strip in app.screen._compositor.render_strips())


def visible_region(app, widget):
    """The part of a widget the compositor actually paints, in screen coordinates."""
    try:
        region, clip = app.screen._compositor.visible_widgets[widget]
    except KeyError:
        return None
    return region.intersection(clip)


def assert_inside(app, widget, label):
    """A control is usable only if it is painted whole, inside the screen and its dialog."""
    region = visible_region(app, widget)
    assert region is not None and region.area, f'{label} is not rendered at all'
    assert region == widget.region, f'{label} is clipped: shown {region}, placed {widget.region}'
    screen = app.screen.region
    assert screen.contains_region(region), f'{label} at {region} falls outside the screen {screen}'
    dialog = widget.ancestors_with_self
    frame = next((w for w in dialog if w.has_class('dialog')), None)
    if frame is not None:
        assert frame.region.contains_region(region), (
            f'{label} at {region} falls outside its dialog {frame.region}')


def assert_actions_visible(app, label, *extra):
    """Every button, the error line and any extra control of the current modal are on screen."""
    screen = app.screen
    buttons = list(screen.query('Button'))
    assert buttons, f'{label}: dialog has no buttons'
    for button in buttons:
        assert_inside(app, button, f'{label} button {button.id or button.label}')
    for selector in ('.error-text', *extra):
        for widget in screen.query(selector):
            assert_inside(app, widget, f'{label} {selector}')
    text = screen_text(app)
    for button in buttons:
        caption = str(button.label).split(' (')[0]
        assert caption in text, f'{label}: button text {caption!r} is not visible on screen'
    return text


async def check_modal_layout(width=90, height=28):
    """Action areas and error messages of every dialog stay visible, also after a resize."""
    client = FakeClient()
    # One long group name and many permissions: the body must scroll, not push buttons out.
    client.groups = [dict(id=f'10001:group:{i}', name=f'长名字的测试群 {i} ' * 2, account='10001')
                     for i in range(12)]
    app = TulpaApp(client=client)
    async with app.run_test(size=(width, height)) as pilot:
        await settle(app, pilot)
        await pilot.press('escape', '3')
        await settle(app, pilot)

        # Grant form: name, groups, dates and 11 permissions do not fit; buttons must still show.
        await pilot.press('n')
        await settle(app, pilot)
        form = app.screen
        text = assert_actions_visible(app, 'grant form', '#grant-name', '.form-scroll')
        assert '创建 (ctrl+s)' in text and '取消 (Esc)' in text
        assert form.query_one('.form-scroll').virtual_size.height > form.query_one('.form-scroll').size.height, \
            'the grant form body is expected to scroll at this size'
        # A validation error is useless if it cannot be read.
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        error = form.query_one('#grant-error')
        assert_inside(app, error, 'grant form error')
        assert str(error.render()).strip() and str(error.render()) in screen_text(app)
        # Keyboard focus reaches every input while the body scrolls.
        for target in ('#grant-name', '#group-list', '#grant-start', '#perm-media'):
            widget = form.query_one(target)
            widget.focus()
            await settle(app, pilot)
            assert app.focused is widget, target
            assert_inside(app, widget, f'focused {target}')
            assert_actions_visible(app, f'grant form with {target} focused')
        await pilot.resize_terminal(width + 40, height + 12)
        await settle(app, pilot)
        assert_actions_visible(app, 'grant form after growing')
        await pilot.resize_terminal(width, height)
        await settle(app, pilot)
        assert_actions_visible(app, 'grant form after shrinking back')
        await pilot.press('escape')
        await settle(app, pilot)

        # Credential modal: created with a long client config.
        await pilot.press('n')
        await settle(app, pilot)
        app.screen.query_one('#grant-name', Input).value = '布局检查'
        app.screen.query_one('#group-list', SelectionList).select(0)
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        assert type(app.screen).__name__ == 'CredentialModal'
        assert_actions_visible(app, 'credential modal', '#token')
        assert '我已保存，关闭' in screen_text(app)
        await pilot.press('escape')
        await settle(app, pilot)

        # MCP settings and the revoke confirmation.
        await pilot.press('m')
        await settle(app, pilot)
        assert_actions_visible(app, 'MCP settings', '#mcp-port')
        app.screen.query_one('#mcp-port', Input).value = '1'
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        assert_inside(app, app.screen.query_one('#mcp-error'), 'MCP settings error')
        await pilot.press('escape')
        await settle(app, pilot)
        app.query_one('#grant-table').focus()
        await pilot.press('d')
        await settle(app, pilot)
        assert_actions_visible(app, 'revoke confirmation')
        await pilot.press('n')
        await settle(app, pilot)

        # Persona / JSON text modal with content longer than the screen.
        await pilot.press('escape', '4')
        await settle(app, pilot)
        app.query_one('#persona-table').focus()
        await pilot.press('enter')
        await settle(app, pilot)
        assert type(app.screen).__name__ == 'TextModal'
        assert_actions_visible(app, 'persona text modal', '#text')
        await pilot.resize_terminal(width, 16)
        await settle(app, pilot)
        assert_actions_visible(app, 'persona text modal at 16 rows')
        await pilot.resize_terminal(width, height)
        await settle(app, pilot)
        await pilot.press('escape')
        await settle(app, pilot)

        # Start form.
        await pilot.press('escape', '5')
        await settle(app, pilot)
        await pilot.press('n')
        await settle(app, pilot)
        assert type(app.screen).__name__ == 'StartForm'
        assert_actions_visible(app, 'start form', '#start-grant', '#start-persona')
        app.screen.query_one('#start-persona', Select).clear()  # Submitting incomplete must explain why.
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        start_error = app.screen.query_one('#start-error')
        assert_inside(app, start_error, 'start form error')
        assert '请选择连接、群聊或私聊' in screen_text(app)

        # The SL settings page keeps its buttons on screen too.
        await pilot.press('escape', 'escape', '2')
        await settle(app, pilot)
        for button in app.query('#sl .buttons Button'):
            assert_inside(app, button, f'SL page button {button.id}')


async def check_layout(width=90, height=28, dump=False):
    """Every page and dialog renders at the minimum size; key labels stay on screen."""
    client = FakeClient()
    app = TulpaApp(client=client)
    steps = ((('escape',), 'overview', 'SnowLuma 传输'), (('escape', '2'), 'sl', '只读测试'),
             (('escape', '3'), 'grants', '旧连接'), (('n',), 'grant form', '创建 (ctrl+s)'),
             (('escape', 'escape', '4'), 'personas', '温柔学姐'), (('escape', '5'), 'sessions', '持续会话'),
             (('n',), 'start form', '准备会话 (ctrl+s)'), (('escape', 'escape', '7'), 'events', 'read_messages'))
    async with app.run_test(size=(width, height)) as pilot:
        await settle(app, pilot)
        for keys, name, expected in steps:
            await pilot.press(*keys)
            await settle(app, pilot)
            text = screen_text(app)
            if dump:
                print(f'===== {name} ({width}x{height})\n{text}')
            assert expected in text, (name, expected)
            assert 'Tulpa' in text or app.screen is not app.screen_stack[0], name
            assert SECRET not in text


async def check_overview_labels():
    """native_import only means native QQ/WeChat database reading; JSON import still works."""
    client = FakeClient()
    app = TulpaApp(client=client)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        text = screen_text(app)
        assert '本地导入 不可用' not in text and '本地导入' not in text, text
        assert '原生数据库读取' in text and '未适配' in text, text
        assert 'JSON 文件导入' in text and '可用' in text, text
        assert '事件接收' in text and '已连接' in text, text
        # Receiver states are translated, not shown as raw identifiers.
        for state, expected in (('upstream_offline', 'QQ 链路不健康'), ('disconnected', '已断开，正在重连')):
            client.receiver = dict(state=state, note='')
            await pilot.press('r')
            await settle(app, pilot)
            text = screen_text(app)
            assert expected in text, (state, text)
            assert state not in text, (state, text)


HOSTILE = '[red]危险[/] [/] [b]名字[blink]'


async def check_markup_is_literal():
    """Backend names and message bodies are data, not console markup."""
    from rich.text import Text
    client = FakeClient()
    client.groups = [dict(id='10001:group:111', name=HOSTILE, account='10001'),
                     dict(id='10001:group:222', name='[/]', account='10001')]
    client.grants = [dict(id='g1', name=HOSTILE, created_at=1700000000, revoked=False, valid=True, calls=1,
                          scope=dict(platforms=['qq'], conversations=['["qq", "10001:group:111"]'],
                                     send=True, chat=True))]
    app = TulpaApp(client=client)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        # Grants table: the literal name is shown, with its brackets intact.
        await pilot.press('escape', '3')
        await settle(app, pilot)
        table = app.query_one('#grant-table')
        cell = table.get_row_at(0)[0]
        assert isinstance(cell, Text), f'dynamic cells must be Text, got {type(cell).__name__}'
        assert '[red]' in cell.plain and '[/]' in cell.plain, cell.plain
        assert '[red]' in screen_text(app), 'the literal name must reach the screen'
        # SelectionList raises MarkupError on a plain '[/]' label, so the form must not use one.
        await pilot.press('n')
        await settle(app, pilot)
        form = app.screen
        assert type(form).__name__ == 'GrantForm', 'hostile group names must not break the form'
        labels = [str(s.prompt) for s in form.query_one('#group-list', SelectionList)._options]
        assert any('[red]' in label for label in labels), labels
        assert '[red]' in screen_text(app)
        form.query_one('#grant-name', Input).value = '安全名称'
        form.query_one('#group-list', SelectionList).select(0)
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        assert type(app.screen).__name__ == 'CredentialModal'
        await pilot.press('escape')
        await settle(app, pilot)
        # Search results: message content with markup stays literal in the cell and the detail.
        client.search_content = f'课程设计：{HOSTILE}，不能被终端样式吞掉'
        await pilot.press('escape', '6')
        await settle(app, pilot)
        app.query_one('#search-input', Input).value = '课程'
        await pilot.press('enter')
        await settle(app, pilot)
        content = app.query_one('#search-table').get_row_at(0)[-1]
        assert isinstance(content, Text) and '[red]' in content.plain, content
        assert '[red]' in screen_text(app)
        # Sessions: a hostile group/session name must not break the table or the start form.
        client.sessions.append(dict(id='s1', name=HOSTILE, conversation_id='10001:group:111', grant_id='g1',
                                    active=True, persona_name=HOSTILE, persona_preset='p1',
                                    participation='normal', state='waiting_agent', note=HOSTILE,
                                    stop_reason='', created=1, updated=1700000002))
        await pilot.press('escape', '5')
        await settle(app, pilot)
        assert app.query_one('#session-table').row_count == 1
        assert '[red]' in str(app.query_one('#session-table').get_row_at(0)[0])
        await pilot.press('n')
        await settle(app, pilot)
        assert type(app.screen).__name__ == 'StartForm'
        assert '[red]' in screen_text(app)
        # Personas and events too.
        await pilot.press('escape', 'escape', '4')
        await settle(app, pilot)
        assert app.query_one('#persona-table').row_count == 1
        await pilot.press('escape', '7')
        await settle(app, pilot)
        assert app.query_one('#events-table').row_count == 1
        assert app.is_running


async def check_learning_targets_selected_session():
    """A cursor move while the status request is in flight must not toggle another session."""
    client = FakeClient()
    client.sessions = [dict(id=f's{i}', name=f'会话{i}', conversation_id=f'10001:group:{i}', grant_id='g1',
                            active=True, persona_name='温柔学姐', persona_preset='p1', participation='normal',
                            state='waiting_agent', note='', stop_reason='', created=1, updated=1700000002)
                       for i in (1, 2)]
    app = TulpaApp(client=client)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await pilot.press('escape', '5')
        await settle(app, pilot)
        table = app.query_one('#session-table')
        table.focus()
        table.move_cursor(row=0)
        view = app.query_one('#sessions')
        assert view.selected()['id'] == 's1'
        # Toggle with no cached status: it reads first, and the selection changes meanwhile.
        app.action_reload  # noqa: B018 - keep the reference obvious for readers
        view.action_toggle_learning()
        table.move_cursor(row=1)
        await settle(app, pilot)
        assert view.selected()['id'] == 's2', 'the test must really move the cursor'
        writes = [(p, b) for m, p, b in client.calls if m == 'PUT']
        assert writes == [('/sessions/s1/learning', dict(enabled=True))], writes
        assert 's2' not in client.learning, 'the unselected session must stay untouched'
        assert '会话1' in str(app.query_one('#sessions-state').render())


async def check_resolved_base_url():
    """The header shows the address of the client that is actually used, including TULPA_URL."""
    import os
    import tempfile
    from unittest.mock import patch
    from chatlocal.tui.app import default_client_factory
    with tempfile.TemporaryDirectory() as tmp:
        token = Path(tmp) / 'missing.token'
        with patch.dict(os.environ, {'TULPA_URL': 'http://127.0.0.1:7999'}):
            # No base_url is passed: the lazily built client resolves the environment itself.
            app = TulpaApp(client_factory=default_client_factory(None, str(token)))
            async with app.run_test(size=SIZE) as pilot:
                await settle(app, pilot)
                assert app.base_url == 'http://127.0.0.1:7999', app.base_url
                text = screen_text(app)
                assert '127.0.0.1:7999' in text, text
                assert app.conn_state == 'offline'
                assert '127.0.0.1:7999' in str(app.query_one('#offline').render())


async def check_real_client_offline():
    """The backend TerminalClient plugs in; a missing credential shows the offline screen, not a crash."""
    import tempfile
    from chatlocal.tui.app import default_client_factory
    with tempfile.TemporaryDirectory() as tmp:
        factory = default_client_factory('http://127.0.0.1:9', str(Path(tmp) / 'missing.token'))
        app = TulpaApp(client_factory=factory, base_url='http://127.0.0.1:9')
        async with app.run_test(size=SIZE) as pilot:
            await settle(app, pilot)
            assert app.conn_state == 'offline'
            assert 'tulpa start' in str(app.query_one('#offline').render())


# ---- full Linux port pages ---------------------------------------------------

API_KEY = 'sk-' + 'K' * 40
PREVIEW_TOKEN = 'preview-' + 'P' * 30
CONV = '10001:group:111'


def native_message(i):
    return dict(id=i, platform='qq', conversation_id=CONV, conversation='测试群', sender='小明', timestamp=1700000000000 + i,
                content=f'第 {i} 条消息', is_self=0, source_id=str(i), reply_to=None, time='2023-11-15 06:13',
                media=[], voice=None, files=[])


class FullClient(FakeClient):
    """FakeClient plus the native /api/* shapes (chat_view, replies, tulpa, watches, data, live, ...)."""

    def __init__(self):
        super().__init__()
        self.model_cfg = dict(api_base='https://api.example.com/v1', model='m-1', has_api_key=True, reasoning_effort='high')
        self.agent_sessions, self.turns = [], {}
        self.draft = dict(id='r1', message_id=3, status='DRAFT', revision=2, ai_text='你好', final_text='你好',
                          active_run=None, error='', instruction='', result={}, receipt={}, generations=[],
                          target=dict(id=3, platform='qq', conversation_id=CONV, conversation='测试群', sender='小明',
                                      timestamp=1700000000003, content='第 3 条消息'),
                          sender=dict(available=True, note=''))
        self.memory = dict(id=1, platform='qq', conversation_id=CONV, kind='group', total=10, processed=5, revision=5,
                           automatic='喜欢简短回复', manual='', status='collecting', valid=1, failures=0, retry_at=0,
                           updated_at=None, episodes=[], history=[], sources=[])
        self.memory_enabled = 1
        self.card = dict(id='w1', title='答辩进展', watch_query='答辩时间', platform_scope=['qq'], conversation_scope=[],
                         status='active', schedule_minutes=0, message_threshold=0, profile='deep', load_stickers=0,
                         baseline=[dict(text='答辩在周五', evidence_ids=[1])], uncertainties=[], last_result={},
                         evidence_ids=[1], pending_messages=0, trigger_messages=0, last_checked_at=None, next_run_at=None,
                         error='', revision=1)
        self.downloads = []

    # ---- /api/terminal additions ----
    def get(self, path, **params):
        if path == '/model':
            self.record('GET', path)
            return dict(self.model_cfg)
        if path == '/agent/sessions':
            self.record('GET', path)
            return dict(items=list(self.agent_sessions))
        if path.startswith('/agent/sessions/'):
            self.record('GET', path)
            sid = path.split('/')[3]
            return dict(id=sid, turns=list(self.turns.get(sid, [])))
        if path == '/native':
            self.record('GET', path)
            return dict(platforms=dict(qq=dict(data_root='/home/u/.config/QQ', keys_file='', configured=True),
                                       wechat=dict(data_root='', keys_file='', configured=False)))
        if path == '/components':
            self.record('GET', path)
            return dict(agent=dict(installed=True), ocr=dict(installed=False), office=dict(installed=False),
                        readers=dict(installed=True, architecture='aarch64', active_key_supported=False, detail=''),
                        voice=dict(installed=False, executable=False, build_tools=False,
                                   model=dict(status='missing', downloaded=0, total=1, message='尚未安装')),
                        service=dict(available=True, installed=False, active=False))
        if path == '/service':
            self.record('GET', path)
            return dict(available=True, installed=False, active=False)
        return super().get(path, **params)

    def post(self, path, body=None):
        if path == '/agent/sessions':
            self.record('POST', path, body)
            sid = f'a{len(self.agent_sessions) + 1}'
            self.agent_sessions.insert(0, dict(id=sid, title='新对话', updated='2026-10-14T10:00:00'))
            self.turns[sid] = []
            return dict(id=sid)
        if path.startswith('/agent/sessions/') and path.endswith('/ask'):
            self.record('POST', path, body)
            sid = path.split('/')[3]
            tid = f't{len(self.turns[sid]) + 1}'
            self.turns[sid].append(dict(id=tid, question=body['question'], status='completed', options=body,
                                        record=dict(result=dict(claims=[dict(text='答案正文', evidence_ids=[1])]),
                                                    seconds=1.5, requests=1)))
            return dict(turn_id=tid)
        return super().post(path, body)

    def put(self, path, body):
        if path == '/model':
            self.record('PUT', path, body)
            self.model_cfg.update(api_base=body['api_base'], model=body['model'])
            if body.get('api_key'):
                self.model_cfg['has_api_key'] = True
            if body.get('clear_api_key'):
                self.model_cfg['has_api_key'] = False
            return dict(self.model_cfg)
        return super().put(path, body)

    # ---- native /api/* routes ----
    def api_get(self, path, **params):
        self.record('GET', path, params or None)
        conversations = [dict(platform='qq', conversation_id=CONV, conversation='测试群', count=3, last_message_id=3,
                              timestamp=1700000000003, time='2023-11-15 06:13', preview='第 3 条消息', sender='小明')]
        if path == '/api/chat-conversations':
            return dict(conversations=conversations, has_more=False, next_offset=200, epoch='e', high_water=3)
        if path == '/api/chat-context':
            return dict(platform='qq', conversation_id=CONV, anchor_message_id=3, messages=[native_message(i) for i in (1, 2, 3)],
                        has_before=False, has_after=False, latest_message_id=3, conversation='测试群', epoch='e', high_water=3)
        if path == '/api/replies/r1':
            return copy.deepcopy(self.draft)
        if path == '/api/reply-receipts':
            return dict(receipts=[], note='本工具的发送回执。')
        if path == '/api/data/accounts':
            return dict(qq=dict(accounts=['10001'], selected='10001', detail=''),
                        wechat=dict(accounts=[], selected=None, detail='未找到 微信 本机账号数据。'))
        if path == '/api/data/settings':
            return dict(scope=dict(qq=dict(enabled=True, conversations=None), wechat=dict(enabled=False, conversations=None)),
                        conversations=[dict(platform='qq', conversation_id=CONV, conversation='测试群')],
                        imported=[dict(platform='qq', conversation_id=CONV, conversation='测试群', count=3)],
                        pending_imports=[], catalog_accounts=dict(qq='10001', wechat=None))
        if path == '/api/live-status':
            return dict(platforms=[dict(platform='qq', status='listening', detail='数据库监听中', mode='database'),
                                   dict(platform='wechat', status='off', detail='已关闭', mode='off')],
                        settings=dict(qq='database', wechat='off', load_stickers=False), server_time=1)
        if path == '/api/refresh-status':
            return dict(platforms=[], auto_refresh=dict(minutes=0, next_at=None, load_stickers=False),
                        running_jobs=[], running_job=None)
        if path.startswith('/api/watch-jobs/'):
            return dict(id=path.split('/')[3], status='completed', events=['完成'], result=dict(summary='已完成'), error=None)
        if path == '/api/tulpa':
            scope = {k: v for k, v in self.memory.items() if k not in ('automatic', 'manual', 'episodes', 'history', 'sources')}
            scope.update(name='测试群', episodes=0, has_memory=True, has_manual=bool(self.memory['manual']),
                         progress=5, pending_batches=0)
            return dict(control=dict(enabled=self.memory_enabled, epoch=1, since=1700000000000), scopes=[scope], directory=[])
        if path == '/api/tulpa/scopes/1':
            return dict(copy.deepcopy(self.memory), control=dict(enabled=self.memory_enabled))
        if path == '/api/watch-cards':
            return dict(cards=[copy.deepcopy(self.card)])
        if path == '/api/watch-cards/w1/thread':
            before = (params or {}).get('before')
            ids = range(1, 41) if before == 41 else range(41, 81)
            return dict(card=copy.deepcopy(self.card), older=None if before == 41 else 41,
                        entries=[dict(id=i, created_at='2026-10-01T10:00:00', status='completed', kind='check', question='',
                                      revision=1, trigger='manual', html=f'<p>第 {i} 次检查 &amp; 结论</p>') for i in ids])
        if path == '/api/group-admin':
            return dict(available=False, policy=dict(mode='ask', revision=0), operations=[])
        if path == '/api/artifacts/status':
            return dict(running=False, result=None, error=None, counts={}, unique_artifacts=0, cache_bytes=0,
                        inventories=[], conversations=[], onebot_configured=False, note='文件消息随实时读取自动登记。')
        if path == '/api/artifacts':
            return dict(files=[], match_count=0, has_more=False, next_offset=None, note='只返回文件元数据。')
        if path == '/api/collections':
            return dict(collections=[], total=0, next_offset=None)
        if path == '/api/knowledge/status':
            return dict(running=False, result=None, error=None, configured=False, groups=[], sync=[], note='可选来源。')
        if path == '/api/knowledge':
            return dict(sources=[], next_offset=None)
        if path == '/api/workspaces':
            return dict(workspaces=[])
        if path == '/api/desktop/snowluma/managed':
            return dict(phase='idle', phase_label='请选择 SnowLuma 文件夹', enabled=False, version='1.14.22',
                        real_pc_verified=False, available=True, consent_current=False, has_consent=False, agreements=[],
                        scope=[], qq_ready=False, folder='', storage='data/snowluma-managed', choices=[],
                        selection_required=False, history=[])
        if path == '/api/components/voice':
            return dict(status='missing', downloaded=0, total=190085487, message='尚未安装本地语音模型')
        raise TerminalError('未找到', status_code=404)

    def api_post(self, path, body=None):
        self.record('POST', path, body)
        if path == '/api/replies':
            return copy.deepcopy(self.draft)
        if path == '/api/replies/r1/preview':
            if body.get('revision') != self.draft['revision']:
                raise TerminalError('草稿已变化，请重新预览。', status_code=400)
            return dict(token=PREVIEW_TOKEN, text=self.draft['final_text'], quote=body.get('quote'), expires_in=300,
                        revision=self.draft['revision'],
                        destination=dict(account='10001', kind='group', peer='111', name='测试群', quote_id=None))
        if path == '/api/replies/r1/send':
            if body != dict(token=PREVIEW_TOKEN, confirm=True):
                raise TerminalError('需要用户确认。', status_code=400)
            self.draft.update(status='SENT', receipt=dict(message_id=99))
            return copy.deepcopy(self.draft)
        if path == '/api/data/delete-preview':
            return dict(token='del-token', messages=3, media=0, platform=body['platform'], conversations=1,
                        all_conversations=not body['conversations'], start=body['start'], end=body['end'])
        if path == '/api/data/delete':
            return dict(job_id='j1', kind='delete')
        if path == '/api/tulpa/control':
            self.memory_enabled = int(body['enabled'])
            return dict(enabled=self.memory_enabled, epoch=2, since=1)
        raise TerminalError('未找到', status_code=404)

    def api_patch(self, path, body):
        self.record('PATCH', path, body)
        if path == '/api/replies/r1':
            if body.get('revision') != self.draft['revision']:
                raise TerminalError('草稿状态已改变，请重新打开，不能覆盖新版本。', status_code=400)
            self.draft.update(final_text=body['text'], revision=self.draft['revision'] + 1)
            return copy.deepcopy(self.draft)
        if path == '/api/tulpa/scopes/1':
            if body.get('revision') != self.memory['revision']:
                raise TerminalError('记忆已更新，请重新打开后再保存。', status_code=400)
            self.memory.update(manual=body['manual'].strip(), revision=self.memory['revision'] + 1)
            return dict(copy.deepcopy(self.memory), control={})
        raise TerminalError('未找到', status_code=404)

    def api_put(self, path, body):
        self.record('PUT', path, body)
        raise TerminalError('未找到', status_code=404)

    def api_delete(self, path, body=None):
        self.record('DELETE', path, body)
        raise TerminalError('未找到', status_code=404)

    def download(self, path, output):
        self.record('DOWNLOAD', path, output)
        self.downloads.append((path, output))
        return dict(path=output)

    def bodies(self, method, path):
        return [b for m, p, b in self.calls if (m, p) == (method, path)]


NEW_PAGES = ('agent', 'chats', 'native', 'memories', 'watches', 'artifacts', 'collections', 'knowledge',
             'workspaces', 'managed', 'components')


def state_text(app, key):
    line = app.query_one(f'#{key}-state')
    return str(line.render()) if line.display else ''


async def check_full_navigation():
    """Every page is reachable from the scrolling sidebar; 8/9/0 are single-key shortcuts."""
    from chatlocal.tui.app import SHORTCUTS, VIEWS
    client = FullClient()
    app = TulpaApp(client=client)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        sidebar = app.query_one('#sidebar')
        assert sidebar.option_count == len(VIEWS) == 19, sidebar.option_count
        assert all(len(k) == 1 for k in SHORTCUTS), 'Textual cannot bind multi-digit sequences'
        assert [SHORTCUTS[str(i)] for i in range(1, 8)] == ['overview', 'sl', 'grants', 'personas', 'sessions', 'search', 'events']
        for key, page in (('8', 'agent'), ('9', 'chats'), ('0', 'native')):
            await pilot.press('escape', key)
            await settle(app, pilot)
            assert app.current == page, (key, app.current)
        # Arrow navigation reaches the last page; the sidebar scrolls instead of clipping.
        await pilot.press('escape')
        sidebar.highlighted = len(VIEWS) - 1
        await settle(app, pilot)
        assert app.current == 'components'
        # `size` is the content box (padding excluded): the last option must be inside the viewport.
        assert sidebar.virtual_size.height >= len(VIEWS)
        assert sidebar.scroll_offset.y + sidebar.size.height >= sidebar.virtual_size.height, \
            (sidebar.scroll_offset, sidebar.size, sidebar.virtual_size)
        for size in ((90, 28), (80, 24)):
            await pilot.resize_terminal(*size)
            for page in NEW_PAGES:
                app.action_goto(page)
                await settle(app, pilot)
                assert app.current == page and app.is_running, page
                assert '✕' not in state_text(app, page), (page, size, state_text(app, page))
                assert SECRET not in screen_text(app) and API_KEY not in screen_text(app)
        # No page loads anything it does not need: the new pages only read.
        writes = [(m, p) for m, p, _ in client.calls if m in ('POST', 'PUT', 'PATCH', 'DELETE')]
        assert not writes, writes


async def check_global_keys_do_not_leak():
    """q / r / digits typed into inputs and dialogs stay there."""
    client = FullClient()
    app = TulpaApp(client=client)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        app.action_goto('chats')
        await settle(app, pilot)
        box = app.query_one('#chat-filter', Input)
        box.focus()
        await pilot.press(*'qr8')
        await settle(app, pilot)
        assert app.is_running and app.current == 'chats' and box.value == 'qr8', box.value
        # A read-only text dialog: r must not reload the page behind it, q must not quit the app.
        await pilot.press('escape', '4')
        await settle(app, pilot)
        app.query_one('#persona-table').focus()
        await pilot.press('enter')
        await settle(app, pilot)
        assert type(app.screen).__name__ == 'TextModal'
        before = client.paths('GET').count('/personas')
        app.screen.query_one('#text').focus()
        await pilot.press('r', '3')
        await settle(app, pilot)
        assert client.paths('GET').count('/personas') == before, 'r behind a dialog must not reload'
        assert app.current == 'personas' and type(app.screen).__name__ == 'TextModal'
        await pilot.press('escape')
        await settle(app, pilot)
        assert app.is_running


async def check_agent_page():
    client = FullClient()
    app = TulpaApp(client=client)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await pilot.press('8')
        await settle(app, pilot)
        assert '还没有对话' in state_text(app, 'agent')
        assert ('GET', '/model', None) in client.calls
        # Asking without a session is refused locally; nothing reaches the model.
        view = app.query_one('#agent')
        view.ask()
        assert not [p for p in client.paths('POST') if p.endswith('/ask')]
        await pilot.press('n')
        await settle(app, pilot)
        assert ('POST', '/agent/sessions', {}) in client.calls
        assert app.focused.id == 'agent-input'
        await pilot.press(*'qr 是什么')
        await pilot.press('enter')
        await settle(app, pilot)
        asks = client.bodies('POST', '/agent/sessions/a1/ask')
        assert len(asks) == 1 and asks[0]['question'] == 'qr 是什么', asks
        assert set(asks[0]) == {'question', 'platforms', 'conversations', 'start', 'end', 'profile', 'vision', 'include_reasoning'}
        assert asks[0]['profile'] == 'standard' and asks[0]['vision'] == 'ocr' and asks[0]['include_reasoning'] is False
        assert '答案正文' in str(app.query_one('#agent-log').render())
        # Model settings: the key is a password input, sent once, never displayed.
        app.query_one('#agent-table').focus()
        await pilot.press('m')
        await settle(app, pilot)
        form = app.screen
        assert type(form).__name__ == 'FormModal' and form.query_one('#f-api-key', Input).password
        assert form.query_one('#f-api-key', Input).value == ''
        form.query_one('#f-api-key', Input).value = API_KEY
        form.action_submit()
        await settle(app, pilot)
        puts = client.bodies('PUT', '/model')
        assert puts == [dict(api_base='https://api.example.com/v1', model='m-1', reasoning_effort='high', api_key=API_KEY)], puts
        assert API_KEY not in visible_text(app) and API_KEY not in screen_text(app)
        # Leaving the key blank preserves it: no api_key field is sent.
        await pilot.press('m')
        await settle(app, pilot)
        app.screen.action_submit()
        await settle(app, pilot)
        assert 'api_key' not in client.bodies('PUT', '/model')[-1]
        # Deleting asks first; cancelling sends nothing.
        await pilot.press('d')
        await settle(app, pilot)
        assert type(app.screen).__name__ == 'ConfirmModal'
        await pilot.press('n')
        await settle(app, pilot)
        assert not [m for m, p, _ in client.calls if m == 'DELETE']


async def check_reply_review():
    """Edit carries the revision; preview is version-checked; send needs a separate confirmation."""
    client = FullClient()
    app = TulpaApp(client=client)
    async with app.run_test(size=(100, 34)) as pilot:
        await settle(app, pilot)
        await pilot.press('9')
        await settle(app, pilot)
        table = app.query_one('#chat-conv-table')
        table.focus()
        await pilot.press('enter')
        await settle(app, pilot)
        context = [b for m, p, b in client.calls if p == '/api/chat-context']
        assert context and context[-1] == dict(platform='qq', conversation_id=CONV, before=30, after=0), context
        assert app.query_one('#chat-msg-table').row_count == 3
        app.query_one('#chats').action_reply()
        await settle(app, pilot)
        modal = app.screen
        assert type(modal).__name__ == 'ReplyModal'
        assert client.bodies('POST', '/api/replies') == [dict(message_id=3, fresh=False)]
        # Typing q / r inside the draft edits text; it neither quits nor reloads.
        area = modal.query_one('#reply-text')
        area.focus()
        area.move_cursor(area.document.end)
        await pilot.press('q', 'r')
        await settle(app, pilot)
        assert app.is_running and area.text == '你好qr', area.text
        # Unsaved edits cannot be previewed.
        modal.action_preview()
        await settle(app, pilot)
        assert not client.bodies('POST', '/api/replies/r1/preview')
        modal.action_save()
        await settle(app, pilot)
        assert client.bodies('PATCH', '/api/replies/r1') == [dict(text='你好qr', revision=2)]
        assert modal.draft['revision'] == 3
        modal.action_preview()
        await settle(app, pilot)
        assert type(app.screen).__name__ == 'ChoiceModal'
        app.screen.dismiss(False)
        await settle(app, pilot)
        assert client.bodies('POST', '/api/replies/r1/preview') == [dict(revision=3, quote=False)]
        confirm = app.screen
        assert type(confirm).__name__ == 'ConfirmModal' and '你好qr' in confirm.body_text and '测试群' in confirm.body_text
        assert PREVIEW_TOKEN not in confirm.body_text and PREVIEW_TOKEN not in screen_text(app)
        await pilot.press('n')
        await settle(app, pilot)
        assert not client.bodies('POST', '/api/replies/r1/send'), 'cancel must not send'
        assert app.screen is modal
        modal.action_preview()
        await settle(app, pilot)
        app.screen.dismiss(False)
        await settle(app, pilot)
        await pilot.press('y')
        await settle(app, pilot)
        assert client.bodies('POST', '/api/replies/r1/send') == [dict(token=PREVIEW_TOKEN, confirm=True)]
        assert modal.draft['status'] == 'SENT'
        assert modal.query_one('#preview').disabled and modal.query_one('#save').disabled
        assert PREVIEW_TOKEN not in visible_text(app)


async def check_memory_and_watch():
    client = FullClient()
    app = TulpaApp(client=client)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        app.action_goto('memories')
        await settle(app, pilot)
        assert app.query_one('#mem-table').row_count == 1
        assert app.focused.id == 'mem-table'
        # The switch asks first; cancelling changes nothing.
        await pilot.press('t')
        await settle(app, pilot)
        assert type(app.screen).__name__ == 'ConfirmModal'
        await pilot.press('n')
        await settle(app, pilot)
        assert not client.bodies('POST', '/api/tulpa/control')
        # Manual edit sends the revision it was based on.
        await pilot.press('e')
        await settle(app, pilot)
        form = app.screen
        assert type(form).__name__ == 'FormModal'
        form.query_one('#f-manual').load_text('只用中文回复')
        form.action_submit()
        await settle(app, pilot)
        assert client.bodies('PATCH', '/api/tulpa/scopes/1') == [dict(manual='只用中文回复', revision=5)]
        assert client.memory['revision'] == 6
        # Watch timeline pages back with the native `older` cursor.
        app.action_goto('watches')
        await settle(app, pilot)
        assert '答辩在周五' in str(app.query_one('#watch-detail').render())
        await pilot.press('w')
        await settle(app, pilot)
        modal = app.screen
        assert type(modal).__name__ == 'TimelineModal' and not modal.query_one('#older').disabled
        assert '第 80 次检查 & 结论' in modal.query_one('#text').text and '<p>' not in modal.query_one('#text').text
        modal.action_older()
        await settle(app, pilot)
        threads = [b for m, p, b in client.calls if p == '/api/watch-cards/w1/thread']
        assert threads == [None, dict(before=41)], threads
        text = modal.query_one('#text').text
        assert text.index('第 1 次检查') < text.index('第 80 次检查') and modal.query_one('#older').disabled
        await pilot.press('escape')
        await settle(app, pilot)
        # Deleting a card asks for that card by name; cancel sends nothing.
        app.query_one('#watch-table').focus()
        await pilot.press('delete')
        await settle(app, pilot)
        assert type(app.screen).__name__ == 'ConfirmModal' and '答辩进展' in app.screen.body_text
        await pilot.press('n')
        await settle(app, pilot)
        assert not [m for m, p, _ in client.calls if m == 'DELETE']


async def check_native_deletion():
    """Deletion goes through the native preview token and a separate confirmation of exact counts."""
    client = FullClient()
    app = TulpaApp(client=client)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await pilot.press('0')
        await settle(app, pilot)
        assert '10001' in str(app.query_one('#native-qq').render())
        assert app.query_one('#native-table').row_count == 1
        for attempt in ('cancel', 'confirm'):
            app.query_one('#native-table').focus()
            await pilot.press('delete')
            await settle(app, pilot)
            app.screen.dismiss('qq')
            await settle(app, pilot)
            form = app.screen
            form.query_one('#f-start', Input).value = '2024-01-01'
            form.query_one('#f-end', Input).value = '2023-01-01'
            form.action_submit()
            await settle(app, pilot)
            assert app.screen is form, 'an inverted range is rejected in the form'
            form.query_one('#f-end', Input).value = '2024-01-31'
            form.action_submit()
            await settle(app, pilot)
            confirm = app.screen
            assert type(confirm).__name__ == 'ConfirmModal' and '3 条消息' in confirm.body_text
            await pilot.press('n' if attempt == 'cancel' else 'y')
            await settle(app, pilot)
            if attempt == 'cancel':
                assert not client.bodies('POST', '/api/data/delete')
        assert client.bodies('POST', '/api/data/delete-preview')[-1] == dict(platform='qq', conversations=[],
                                                                              start='2024-01-01', end='2024-01-31')
        assert client.bodies('POST', '/api/data/delete') == [dict(token='del-token')]
        assert '/api/watch-jobs/j1' in client.paths('GET')


def check_event_lines():
    assert model.event_line(dict(type='tool_start', name='read_messages')) == '调用工具：read_messages'
    unknown = model.event_line(dict(type='mystery', name='x', payload=dict(token=SECRET), headers='Bearer ' + SECRET))
    assert unknown and SECRET not in unknown and 'mystery' in unknown, unknown
    assert SECRET not in model.event_line(dict(type='other', name='Bearer ' + SECRET))
    assert model.event_line(dict()) == '进度更新'
    assert '证据检查' in model.event_line(dict(type='validation'))


def main():
    if '--dump' in sys.argv:
        asyncio.run(check_layout(*(int(v) for v in sys.argv[2:4]), dump=True) if len(sys.argv) > 3
                    else check_layout(dump=True))
        return
    check_model()
    asyncio.run(check_layout())
    asyncio.run(check_layout(140, 40))
    print('ok check_layout')
    for size in ((90, 28), (80, 24), (120, 40)):
        asyncio.run(check_modal_layout(*size))
        print('ok check_modal_layout', size)
    for check in (check_navigation, check_offline_recovery, check_settings, check_grants, check_sessions,
                  check_overview_labels, check_markup_is_literal, check_learning_targets_selected_session,
                  check_resolved_base_url, check_real_client_offline):
        asyncio.run(check())
        print('ok', check.__name__)
    check_event_lines()
    print('ok check_event_lines')
    check_extension_model()
    print('ok check_extension_model')
    for check in (check_grant_extensions, check_grant_extensions_fallback, check_session_friends,
                  check_grant_edit, check_grant_edit_fallback):
        asyncio.run(check())
        print('ok', check.__name__)
    for size in ((80, 24), (60, 20), (120, 40)):
        asyncio.run(check_grant_edit_layout(*size))
        print('ok check_grant_edit_layout', size)
    for size in ((80, 24), (60, 20), (120, 40)):
        asyncio.run(check_extension_layout(*size))
        print('ok check_extension_layout', size)
    for check in (check_full_navigation, check_global_keys_do_not_leak, check_agent_page, check_reply_review,
                  check_memory_and_watch, check_native_deletion):
        asyncio.run(check())
        print('ok', check.__name__)
    print('terminal TUI checks passed')


if __name__ == '__main__':
    main()
