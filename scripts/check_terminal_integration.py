"""Exercise the Claude Code TUI against the actual Tulpa management API.

Only SnowLuma is replaced by isolated HTTP/WS fixtures. No QQ messages or model
calls are made. This catches API-shape mismatches that a fake UI client cannot.
"""
import asyncio
from test_http import management_http
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import check_terminal_backend as backend
from chatlocal.terminal_client import TerminalClient
from chatlocal.tui import model
from chatlocal.tui.app import TulpaApp, VIEWS
from chatlocal.tui.grants import GrantForm, GrantEditForm
from chatlocal.tui.modals import ConfirmModal, CredentialModal, TextModal
from chatlocal.tui.sessions import StartForm
from textual.widgets import Checkbox, DataTable, Input, Select, SelectionList


async def until(pilot, predicate, label, seconds=8):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        await pilot.pause(.04)
        if predicate():
            return
    raise AssertionError('Timed out: '+label)


def shown(app, kind):
    # push_screen publishes the Screen before its children finish mounting.
    # Wait until real controls are ready before driving a modal from a test.
    return isinstance(app.screen, kind) and app.screen.is_mounted


class IntegrationTests(unittest.TestCase):
    def test_extension_form_and_private_session_against_real_api(self):
        fixture = backend.TerminalTests()
        fixture.setUp()
        try:
            fixture.configure(); fixture.enable()
            with management_http(fixture.app) as url:
                asyncio.run(self.private_flow(fixture, url))
            self.assertEqual(backend.SL.sent, [])
        finally:
            fixture.tearDown()

    async def private_flow(self, fixture, url):
        from chatlocal.mcp_qq_permissions import QQ_FLAGS
        app = TulpaApp(client=TerminalClient(url, fixture.root/'data/terminal-admin.token'))
        async with app.run_test(size=(72, 24)) as pilot:
            await until(pilot, lambda: app.conn_state=='online', 'extension overview')
            app.action_goto('grants')
            await until(pilot, lambda: app.page('grants').loaded, 'extension grants')
            app.page('grants').action_create()
            await until(pilot, lambda: shown(app, GrantForm), 'extension grant form')
            for flag in QQ_FLAGS:
                self.assertFalse(app.screen.query_one('#perm-'+flag, Checkbox).value)
            self.assertTrue(app.screen.query_one('#perm-chat_voice', Checkbox).disabled)
            app.screen.query_one('#grant-name', Input).value = '私聊与附件联调'
            app.screen.query_one('#friend-list', SelectionList).select(0)
            app.screen.query_one('#perm-send', Checkbox).value = True
            await until(pilot, lambda: not app.screen.query_one('#perm-chat', Checkbox).disabled, 'private send prerequisite')
            app.screen.query_one('#perm-chat', Checkbox).value = True
            await until(pilot, lambda: not app.screen.query_one('#perm-chat_voice', Checkbox).disabled, 'private media prerequisite')
            for flag in QQ_FLAGS:
                app.screen.query_one('#perm-'+flag, Checkbox).value = True
            await pilot.pause()
            await pilot.press('ctrl+s')
            await until(pilot, lambda: shown(app, CredentialModal), 'extension grant submitted')
            grant = fixture.api('GET','/grants')['items'][0]
            self.assertEqual(model.scope_conversations(grant['scope']), {'111:direct:333'})
            self.assertTrue(all(grant['scope'][f] for f in QQ_FLAGS))
            await pilot.press('escape')
            app.action_goto('sessions')
            await until(pilot, lambda: app.page('sessions').loaded, 'private sessions')
            app.page('sessions').action_start()
            await until(pilot, lambda: shown(app, StartForm), 'private session form')
            await until(pilot, lambda: app.screen.query_one('#start-group', Select).value=='111:direct:333', 'authorized friend selected')
            app.screen.query_one('#start-persona', Select).value = 'little_whale_v2'
            await pilot.press('ctrl+s')
            await until(pilot, lambda: bool(app.page('sessions').sessions), 'private session prepared')
            session = next(iter(app.page('sessions').sessions.values()))
            self.assertEqual(session['conversation_id'], '111:direct:333')
            self.assertEqual(session['grant_id'], grant['id'])
            self.assertEqual(session['state'], 'waiting_agent')
            access = fixture.app.state.tulpa_mcp.access
            with access.connect() as db:
                token_hash = db.execute('SELECT token_hash FROM grants WHERE id=?',(grant['id'],)).fetchone()[0]
            app.action_goto('grants')
            await until(pilot, lambda: bool(app.page('grants').grants), 'grant available for editing')
            app.page('grants').action_edit()
            await until(pilot, lambda: shown(app, GrantEditForm), 'permission edit form')
            self.assertTrue(app.screen.query_one('#perm-qq_profile', Checkbox).value)
            app.screen.query_one('#perm-qq_profile', Checkbox).value = False
            await pilot.press('ctrl+s')
            await until(pilot, lambda: app.page('grants').grants.get(grant['id'],{}).get('revision')==1,
                        'permission change saved and list refreshed')
            saved = app.page('grants').grants[grant['id']]
            self.assertFalse(saved['scope']['qq_profile'])
            self.assertTrue(saved['scope']['chat_voice'])
            self.assertEqual(model.scope_conversations(saved['scope']), {'111:direct:333'})
            self.assertFalse(fixture.app.state.tulpa_mcp.tools.chat.row(session['id'])['active'])
            self.assertNotIsInstance(app.screen, CredentialModal)
            with access.connect() as db:
                self.assertEqual(db.execute('SELECT token_hash FROM grants WHERE id=?',(grant['id'],)).fetchone()[0],token_hash)

    def test_full_navigation_against_real_api(self):
        fixture = backend.TerminalTests()
        fixture.setUp()
        try:
            fixture.configure()
            # Empty native fixtures keep this check independent of any real
            # client installation and never attempt key acquisition.
            with patch('chatlocal.client_accounts.discover_accounts',return_value=[]), management_http(fixture.app) as url:
                asyncio.run(self.navigation(fixture,url))
            self.assertEqual(backend.SL.sent,[])
        finally:
            fixture.tearDown()

    async def navigation(self,fixture,url):
        app=TulpaApp(client=TerminalClient(url,fixture.root/'data/terminal-admin.token'))
        async with app.run_test(size=(90,28)) as pilot:
            await until(pilot,lambda:app.conn_state=='online','full overview')
            for key,label,_ in VIEWS:
                app.action_goto(key)
                await until(pilot,lambda:app.page(key).loaded,'real API page '+key,seconds=15)
                await pilot.pause(.1)
                self.assertFalse(app.page(key).state_line.has_class('-error'),label)
            self.assertGreater(len(VIEWS),7,'Full frontend navigation must be registered')
            app.save_screenshot('terminal-full-navigation.svg',path=str(ROOT/'.tmp'))

    def test_real_api_grant_session_learning_stop_and_revoke(self):
        fixture = backend.TerminalTests()
        fixture.setUp()
        try:
            fixture.configure()
            fixture.enable()
            with management_http(fixture.app) as url:
                asyncio.run(self.flow(fixture, url))
            self.assertEqual(backend.SL.sent, [])
        finally:
            fixture.tearDown()

    async def flow(self, fixture, url):
        app = TulpaApp(client=TerminalClient(url, fixture.root/'data/terminal-admin.token'))
        async with app.run_test(size=(90, 28)) as pilot:
            await until(pilot, lambda: app.conn_state == 'online', 'overview')
            app.save_screenshot('terminal-overview.svg', path=str(ROOT/'.tmp'))

            app.action_goto('grants')
            await until(pilot, lambda: app.page('grants').loaded, 'grants')
            app.page('grants').action_create()
            await until(pilot, lambda: shown(app, GrantForm), 'grant form')
            app.screen.query_one('#grant-name', Input).value = '终端联调'
            app.screen.query_one('#group-list', SelectionList).select(0)
            app.screen.query_one('#perm-send', Checkbox).value = True
            await until(pilot, lambda: not app.screen.query_one('#perm-chat', Checkbox).disabled,
                        'chat permission prerequisite')
            app.screen.query_one('#perm-chat', Checkbox).value = True
            await pilot.pause()
            app.save_screenshot('terminal-grant-form.svg', path=str(ROOT/'.tmp'))
            await pilot.press('ctrl+s')
            await until(pilot, lambda: shown(app, CredentialModal), 'grant creation')
            grants = fixture.api('GET', '/grants')['items']
            self.assertEqual(len(grants), 1)
            self.assertEqual(model.scope_conversations(grants[0]['scope']), {'111:group:222'})
            self.assertTrue(grants[0]['valid'])
            await pilot.press('escape')

            app.action_goto('sessions')
            await until(pilot, lambda: app.page('sessions').loaded, 'sessions')
            app.page('sessions').action_start()
            await until(pilot, lambda: shown(app, StartForm), 'start form')
            await until(pilot, lambda: app.screen.query_one('#start-group', Select).value == '111:group:222',
                        'authorized group selection')
            app.screen.query_one('#start-persona', Select).value = 'little_whale_v2'
            await pilot.press('ctrl+s')
            await until(pilot, lambda: bool(app.page('sessions').sessions), 'prepared session')
            session = next(iter(app.page('sessions').sessions.values()))
            self.assertEqual(session['state'], 'waiting_agent')
            self.assertEqual(model.session_state(session), '等待 Agent')
            self.assertEqual(session['grant_id'], grants[0]['id'])
            app.save_screenshot('terminal-sessions.svg', path=str(ROOT/'.tmp'))

            view = app.page('sessions')
            view.query_one(DataTable).focus()
            await pilot.press('l')
            sid = session['id']
            await until(pilot, lambda: sid in view.learning, 'learning status')
            enabled = view.learning[sid]['enabled']
            await pilot.press('t')
            await until(pilot, lambda: view.learning[sid]['enabled'] != enabled, 'learning toggle')
            self.assertEqual(fixture.api('GET', f'/sessions/{sid}/learning')['enabled'], not enabled)

            await pilot.press('s')
            await until(pilot, lambda: shown(app, ConfirmModal), 'stop confirmation')
            await pilot.press('n')
            self.assertTrue(fixture.api('GET', '/sessions')['items'][0]['active'])
            await pilot.press('s')
            await until(pilot, lambda: shown(app, ConfirmModal), 'second stop confirmation')
            await pilot.press('y')
            await until(pilot, lambda: not view.sessions[sid]['active'], 'stopped session')

            app.action_goto('personas')
            await until(pilot, lambda: app.page('personas').loaded, 'personas')
            await pilot.press('enter')
            await until(pilot, lambda: shown(app, TextModal), 'persona content')
            await pilot.press('escape')

            app.action_goto('grants')
            app.page('grants').query_one(DataTable).focus()
            await pilot.press('d')
            await until(pilot, lambda: shown(app, ConfirmModal), 'revoke confirmation')
            await pilot.press('y')
            await until(pilot, lambda: bool(next(iter(app.page('grants').grants.values()))['revoked']),
                        'revoked grant')
            self.assertTrue(fixture.api('GET', '/grants')['items'][0]['revoked'])

            source = fixture.root/'imports/integration.json'
            content = '课程设计：[red]保持原始文本[/]，不能被终端样式吞掉'
            source.write_text(json.dumps([dict(platform='qq', conversation_id='course',
                conversation='课程群', sender='教师', source_id='ui-search-1', timestamp='2026-10-02',
                content=content)], ensure_ascii=False), encoding='utf-8')
            fixture.api('POST', '/import', dict(path=str(source)))
            app.action_goto('search')
            app.page('search').query_one('#search-input', Input).value = '课程设计'
            await pilot.press('enter')
            await until(pilot, lambda: bool(app.page('search').messages), 'local search')
            found = next(iter(app.page('search').messages.values()))
            self.assertEqual(found['content'], content)
            cell = app.page('search').query_one(DataTable).get_row_at(0)[-1]
            self.assertIn('[red]', str(cell))


if __name__ == '__main__':
    unittest.main(verbosity=2)
