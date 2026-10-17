"""Permission edits over real local management/MCP transports; isolated QQ fixtures only."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch
import uuid
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'scripts'))
import check_terminal_backend as backend
from check_mcp import client as mcp_client
from test_http import management_http
from chatlocal.mcp_access import MCPAccess, AccessDenied
from chatlocal.mcp_qq import QQExtensions
from chatlocal.onebot import Client, OneBotError


class GrantEditTests(unittest.TestCase):
    def setUp(self):
        self.fixture = backend.TerminalTests()
        self.fixture.setUp()
        self.fixture.configure(); self.fixture.enable()
        self.service = self.fixture.app.state.tulpa_mcp
        self.access = self.service.access
        self.g = self.fixture.grant()
        (self.fixture.root/'data/mcp-chat-policy.json').write_text(json.dumps(
            dict(quiet_seconds=.1, reply_cooldown_seconds=0, bubble_min_seconds=.1)))

    def tearDown(self):
        self.fixture.tearDown()

    def edit(self, body, expected=200):
        return self.fixture.api('PUT', '/grants/'+self.g['id'], body, expected=expected)

    def row(self):
        with self.access.connect() as db:
            return dict(db.execute('SELECT * FROM grants WHERE id=?', (self.g['id'],)).fetchone())

    def data(self, name, **args):
        result = self.service.tools.call(self.g['token'], name, args, threading.Event())
        self.assertFalse(result.isError, result.structuredContent)
        return result.structuredContent

    def start(self, grant=None, cid='111:group:222'):
        return self.fixture.api('POST', '/sessions/start', dict(connection_id=(grant or self.g)['id'],
            conversation_id=cid, persona_preset='little_whale_v2', idempotency_key=uuid.uuid4().hex))['session_id']

    def test_schema_migration_and_credential_preservation(self):
        original = self.row()
        legacy = self.fixture.root/'legacy-access.sqlite3'
        with sqlite3.connect(legacy) as db:
            db.execute('CREATE TABLE grants(id TEXT PRIMARY KEY,name TEXT NOT NULL,token_hash TEXT NOT NULL UNIQUE,'
                       'scope TEXT NOT NULL,epoch TEXT NOT NULL,accounts TEXT NOT NULL,created_at REAL NOT NULL,revoked INTEGER NOT NULL DEFAULT 0)')
            fields = ('id','name','token_hash','scope','epoch','accounts','created_at','revoked')
            db.execute('INSERT INTO grants VALUES(?,?,?,?,?,?,?,?)', tuple(original[k] for k in fields))
        db.close()
        migrated = MCPAccess(self.fixture.store, path=legacy)
        migrated.configure(True,18777)
        self.assertEqual(migrated.authorize(self.g['token'], source=False)['revision'], 0)
        saved = self.edit(dict(expected_revision=0, chat_voice=True, qq_profile=True))
        self.assertEqual(saved['revision'], 1)
        self.assertTrue(saved['changed'])
        self.assertNotIn('token', saved)
        current = self.row()
        for key in ('id','name','token_hash','epoch','accounts','created_at','revoked'):
            self.assertEqual(original[key],current[key],key)
        old_scope = json.loads(original['scope']);scope = json.loads(current['scope'])
        for key in ('platforms','conversations','start','end','chat_account'):
            self.assertEqual(old_scope[key],scope[key],key)
        self.assertTrue(self.access.authorize(self.g['token'], source=False)['scope']['chat_voice'])
        # An outdated editor never overwrites the committed revision.
        self.edit(dict(expected_revision=0, qq_profile=False),expected=409)
        self.assertEqual(self.row(),current)
        self.assertFalse(self.edit(dict(expected_revision=1, chat_voice=True))['changed'])
        self.assertEqual(self.row(),current)

    def test_validation_account_pin_and_offline_reductions(self):
        self.edit(dict(qq_profile=True, chat_voice=True))
        before = self.row()
        for body in (dict(chat=False),dict(chat_voice=1),dict(token='replace'),dict(conversations=[]),
                     dict(expected_revision=None),dict(expected_revision=True)):
            self.edit(body,expected=400)
            self.assertEqual(before,self.row())
        with patch.object(Client,'login',return_value='999'):
            self.edit(dict(qq_qzone=True),expected=400)
        self.assertEqual(before,self.row())
        with patch.object(Client,'login',side_effect=OneBotError('fixture offline')) as login:
            reduced = self.edit(dict(qq_profile=False))
            login.assert_not_called()
        self.assertTrue(reduced['changed'])
        self.fixture.api('PUT','/mcp',dict(enabled=False,port=self.access.settings()['port']))
        self.assertTrue(self.edit(dict(chat_voice=False))['changed'])
        self.assertEqual(self.fixture.client.put('/api/terminal/grants/'+self.g['id'],
            headers={'Authorization':''},json=dict(media=True)).status_code,401)
        self.fixture.api('POST','/grants/'+self.g['id']+'/revoke',{})
        self.edit(dict(qq_profile=True),expected=400)
        self.assertTrue(self.row()['revoked'])
        self.assertEqual(backend.SL.sent,[])

    def test_wire_tool_discovery_and_inflight_read_cancellation(self):
        async def flow():
            async with mcp_client(self.service.status()['url'],self.g['token']) as session:
                before = {t.name for t in (await session.list_tools()).tools}
                self.assertNotIn('qq_profile_action',before)
                await asyncio.to_thread(self.edit,dict(qq_profile=True))
                after = {t.name for t in (await session.list_tools()).tools}
                self.assertIn('qq_profile_action',after)
                entered = threading.Event();release = threading.Event()
                original = QQExtensions.call
                def paused(extension, grant, name, args, cancel):
                    if name=='get_qq_capabilities':
                        entered.set()
                        if not release.wait(8):raise RuntimeError('fixture release timed out')
                        return dict(private_marker='must-not-be-returned'),[]
                    return original(extension,grant,name,args,cancel)
                with patch.object(QQExtensions,'call',paused):
                    task = asyncio.create_task(session.call_tool('get_qq_capabilities',{}))
                    try:
                        self.assertTrue(await asyncio.to_thread(entered.wait,5))
                        await asyncio.to_thread(self.edit,dict(qq_profile=False))
                    finally:release.set()
                    result = await task
                self.assertTrue(result.isError)
                self.assertNotIn('must-not-be-returned',result.model_dump_json())
                self.assertNotIn('qq_profile_action',{t.name for t in (await session.list_tools()).tools})
                rejected = await session.call_tool('qq_profile_action',dict(action='get_profile',params={}))
                self.assertTrue(rejected.isError)
                self.assertFalse((await session.call_tool('get_qq_capabilities',{})).isError)
        asyncio.run(flow())
        self.assertEqual(backend.SL.sent,[])

    def test_stops_old_plans_and_prevents_prepared_send(self):
        self.edit(dict(chat_voice=True))
        sid = self.start()
        other = self.fixture.api('POST','/grants',dict(name='unrelated',platforms=['qq'],
            conversations=[['qq','111:direct:333']],chat=True,send=True))
        other_sid = self.start(other,'111:direct:333')
        unchanged = self.edit(dict(chat_voice=True))
        self.assertFalse(unchanged['changed']);self.assertEqual(unchanged['stopped_sessions'],0)
        self.assertTrue(self.service.tools.chat.row(sid)['active'])
        raw = io.BytesIO()
        with wave.open(raw,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0\0'*1600)
        asset = self.data('import_qq_media',kind='voice',filename='test.wav',data_base64=base64.b64encode(raw.getvalue()).decode())['asset_id']
        time.sleep(.12)
        batch = self.data('get_chat_session',session_id=sid)['input_batch']['id']
        plan = self.data('plan_chat_reply',session_id=sid,batch_id=batch,action='reply',expressions=['voice'],intent='isolated permission edit race')
        self.assertEqual(plan['state'],'READY')
        entered = threading.Event();release = threading.Event()
        media = self.service.tools.chat.qq.media;original = media.get;reads = 0
        def paused(*args,**kwargs):
            nonlocal reads
            reads += 1
            # The first read validates the queue. Pause the second read, while
            # preparing the actual native send under the operation write lock.
            if reads == 2:
                entered.set()
                if not release.wait(8):raise RuntimeError('fixture release timed out')
            return original(*args,**kwargs)
        old_grant = self.access.by_id(self.g['id'],source=False)
        with patch.object(media,'get',paused),ThreadPoolExecutor(1) as pool:
            future = pool.submit(self.service.tools.call,self.g['token'],'send_chat_reply',
                dict(session_id=sid,plan_id=plan['plan_id'],bubbles=[dict(kind='voice',asset_id=asset)]),threading.Event())
            try:
                self.assertTrue(entered.wait(5))
                saved = self.edit(dict(chat_voice=False))
                self.assertEqual(saved['stopped_sessions'],1)
            finally:release.set()
            result = future.result(8)
        self.assertNotEqual(result.structuredContent.get('state'),'SUCCEEDED')
        self.assertFalse(self.service.tools.chat.row(sid)['active'])
        self.assertTrue(self.service.tools.chat.row(other_sid)['active'])
        self.assertEqual(backend.SL.sent,[])
        with self.assertRaises(AccessDenied):
            self.service.tools.chat.start(old_grant,dict(conversation_id='111:group:222',
                persona='fixture',idempotency_key=uuid.uuid4().hex),threading.Event())
        # The same credential can start a fresh session with the new permissions.
        self.assertNotEqual(self.start(),sid)

    def test_cli_and_native_route_keep_token(self):
        original_hash = self.row()['token_hash']
        with management_http(self.fixture.app) as url:
            launcher = [sys.executable,'-m','chatlocal.terminal_cli'] if sys.platform=='win32' else [str(ROOT/'tulpa')]
            prefix = launcher+['--json','--url',url,'--token-file',str(self.fixture.root/'data/terminal-admin.token'),
                      'connections','edit',self.g['id']]
            for flag, expected in (('--qq-profile',True),('--no-qq-profile',False)):
                result = subprocess.run(prefix+[flag],cwd=ROOT,capture_output=True,text=True,timeout=20)
                self.assertEqual(result.returncode,0,result.stderr)
                output = json.loads(result.stdout)
                self.assertEqual(output['scope']['qq_profile'],expected)
                self.assertNotIn(self.g['token'],result.stdout+result.stderr)
        response = self.fixture.client.put('/api/mcp/connections/'+self.g['id'],json=dict(qq_profile=True,expected_revision=2))
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['revision'],3)
        response = self.fixture.client.put('/api/mcp/connections/'+self.g['id'],json=dict(qq_profile=False,expected_revision=2))
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.row()['token_hash'],original_hash)
        self.assertEqual(backend.SL.sent,[])


if __name__=='__main__':
    unittest.main(verbosity=2)
