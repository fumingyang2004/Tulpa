"""Generated-image intake: real MCP/OneBot wire, isolated data, no live QQ."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
from test_environment import system_environment
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_mcp_chat_media import StickerBot, fixture_image
from check_mcp_chat import Events, eventually, ROOT
from import_chat_image import connection, image_bytes, UploadError
from chatlocal.store import Store
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.mcp_chat_media import MAX_BYTES, MAX_BASE64_LENGTH, pixels
from chatlocal.onebot import invalidate_availability
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from PIL import Image


async def wire(service, grant, readonly, sid, raw):
    url = service.status()['url']
    auth = {'Authorization': 'Bearer ' + grant['token']}
    encoded = base64.b64encode(raw).decode('ascii')
    async with httpx.AsyncClient(headers=auth, timeout=30, trust_env=False) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as client:
                await client.initialize()
                names = {t.name: t for t in (await client.list_tools()).tools}
                assert 'import_chat_image' in names
                assert not names['import_chat_image'].annotations.readOnlyHint
                assert not names['import_chat_image'].annotations.openWorldHint

                async def call(name, **args):
                    return await client.call_tool(name, args)

                uploaded = await call('import_chat_image', session_id=sid, data_base64=encoded)
                assert not uploaded.isError, uploaded.structuredContent
                data = uploaded.structuredContent
                aid = data['sticker_id']
                assert data['bytes'] == len(raw) and data['sha256'] == hashlib.sha256(raw).hexdigest()
                images = [p for p in uploaded.content if p.type == 'image']
                assert len(images) == 1
                Image.open(io.BytesIO(base64.b64decode(images[0].data))).verify()
                assert not StickerBot.sent and not StickerBot.collected, 'Import sent or collected to QQ'
                duplicate = await call('import_chat_image', session_id=sid, data_base64=encoded)
                assert duplicate.structuredContent['sticker_id'] == aid
                assert not (await call('read_chat_sticker', session_id=sid, sticker_id=aid)).isError

                blocked = await call('send_chat_sticker', session_id=sid, sticker_id=aid, idempotency_key='without-plan')
                assert blocked.structuredContent['error_code'] == 'plan_required' and not StickerBot.sent

                async def plan(key):
                    current = (await call('get_chat_session', session_id=sid)).structuredContent
                    result = (await call('plan_chat_reply', session_id=sid, batch_id=current['input_batch']['id'],
                              action='reply', intent='发送导入的验收图片', expressions=['sticker'], idempotency_key=key)).structuredContent
                    assert result['state'] == 'READY', result
                    return result['plan_id']

                params = dict(session_id=sid, plan_id=await plan('wire-image-plan'), idempotency_key='wire-image-send',
                              bubbles=[dict(kind='sticker', sticker_id=aid)])
                result = (await call('send_chat_reply', **params)).structuredContent
                assert result['state'] == 'SUCCEEDED', result
                assert len(StickerBot.sent) == 1 and StickerBot.sent[0]['group_id'] == 222
                segment = StickerBot.sent[0]['message'][0]
                assert segment['type'] == 'image' and segment['data']['file'].startswith('base64://')
                assert base64.b64decode(segment['data']['file'][9:]) == raw
                repeated = (await call('send_chat_reply', **params)).structuredContent
                assert repeated['state'] == 'SUCCEEDED' and len(StickerBot.sent) == 1

                # Unknown sends remain blocked even after re-upload and replanning.
                StickerBot.uncertain = True
                params.update(plan_id=await plan('wire-unknown-plan'), idempotency_key='wire-unknown-send')
                unknown = (await call('send_chat_reply', **params)).structuredContent
                assert unknown['state'] == 'UNKNOWN' and len(StickerBot.sent) == 2, unknown
                assert (await call('send_chat_reply', **params)).structuredContent['state'] == 'UNKNOWN'
                StickerBot.uncertain = False
                assert (await call('import_chat_image', session_id=sid, data_base64=encoded)).structuredContent['sticker_id'] == aid
                params.update(plan_id=await plan('wire-no-retry-plan'), idempotency_key='wire-no-retry-send')
                rejected = (await call('send_chat_reply', **params)).structuredContent
                assert rejected['state'] != 'SUCCEEDED' and len(StickerBot.sent) == 2, rejected

        # The old cap still applies to every other request, even for upload grants.
        request = dict(jsonrpc='2.0', id=101, method='tools/call', params=dict(name='get_data_status', arguments={'padding': 'x'*40000}))
        assert (await http.post(url, json=request)).status_code == 413
        large = dict(jsonrpc='2.0', id=102, method='tools/call', params=dict(name='import_chat_image', arguments=dict(session_id=sid, data_base64=encoded)))
        assert (await http.post(url, headers={'Authorization': 'Bearer '+readonly['token']}, json=large)).status_code == 413
        assert (await http.post(url, headers={'Authorization': 'Bearer invalid'}, json=large)).status_code == 401

        # Chunked framing cannot evade the upload byte cap or the ordinary cap.
        async def chunks(total):
            while total:
                chunk = b' ' * min(65536, total)
                total -= len(chunk)
                yield chunk
        assert (await http.post(url, content=chunks(MAX_BASE64_LENGTH+32769))).status_code == 413
        ordinary = json.dumps(request).encode()
        async def ordinary_chunks():
            for offset in range(0, len(ordinary), 4096):
                yield ordinary[offset:offset+4096]
        assert (await http.post(url, content=ordinary_chunks())).status_code == 413
    return aid


def main():
    # Incompressible image exceeds the former 32 KiB MCP body limit by orders
    # of magnitude, while remaining cheap to decode and below the 6 MiB cap.
    buf = io.BytesIO()
    Image.frombytes('RGB', (800, 600), os.urandom(800*600*3)).save(buf, 'PNG')
    raw = buf.getvalue()
    assert 1000000 < len(raw) < MAX_BYTES
    events = Events()
    StickerBot.sent = []; StickerBot.collected = []; StickerBot.uncertain = False; StickerBot.block = False
    upstream = ThreadingHTTPServer(('127.0.0.1', 0), StickerBot)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    checks = []
    try:
        with tempfile.TemporaryDirectory(prefix='tulpa-image-import-') as tmp, patch.dict('os.environ', system_environment(), clear=True):
            root = Path(tmp)
            (root/'.env').write_text(f'REPLY_ONEBOT_URL=http://127.0.0.1:{upstream.server_port}\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:{events.port}\nREPLY_ONEBOT_WS_TOKEN=event-fixture\n')
            with patch('chatlocal.onebot.ROOT', root), patch('chatlocal.message_sender.ROOT', root), \
                    patch('chatlocal.mcp_chat_media.download', side_effect=AssertionError('Uploaded image used remote URL')):
                app = FastAPI(); service = install_mcp_routes(app, Store(root/'chats.sqlite3'))
                access = service.access; tools = service.tools
                with TestClient(app) as ui:
                    invalidate_availability()
                    with socket.socket() as sock:
                        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
                    access.configure(True, port); service.start()
                    policy = access.path.parent/'mcp-chat-policy.json'
                    policy.write_text('{"quiet_seconds":0.1,"reply_cooldown_seconds":0}')
                    cfg = dict(name='image-import-fixture', platforms=['qq'], conversations=[['qq','111:group:222'],['qq','111:group:223']],
                               send=True, chat=True, chat_images=True, chat_sticker_send=True, chat_sticker_collect=True)
                    g = access.create(cfg)
                    ro = access.create(cfg | dict(chat_sticker_send=False))
                    other = access.create(cfg)

                    def call(name, grant=g, cancel=None, **args):
                        return tools.call(grant['token'], name, args, cancel or threading.Event())

                    def start(cid='111:group:222', key='upload-start'):
                        response = call('start_chat_session', conversation_id=cid, persona='图片验收', idempotency_key=key)
                        sid = response.structuredContent['session']['id']
                        if cid == '111:group:222':
                            paused = ui.put(f'/api/mcp/chats/{sid}/learning', json=dict(enabled=False), headers={'X-ChatWeave-UI':'1'})
                            assert paused.status_code == 200, paused.text
                        return sid

                    sid = start()
                    eventually(lambda: tools.chat.receiver.status()['state'] == 'connected')
                    assert 'import_chat_image' not in {s['name'] for s in tools.schemas(access.authorize(ro['token'], source=False))}
                    aid = asyncio.run(wire(service, g, ro, sid, raw))
                    checks.append('real MCP upload >32 KiB, preview, original OneBot bytes, planner, dedup and UNKNOWN guard')
                    checks.append('authenticated request limits including chunked bodies and read-only grants')

                    encoded = base64.b64encode(fixture_image(True)).decode('ascii')
                    for value in ('data:image/png;base64,'+encoded, 'file:///private/key', 'https://example.invalid/secret', '秘密', base64.b64encode(b'not-image').decode()):
                        result = call('import_chat_image', session_id=sid, data_base64=value)
                        assert result.isError and value not in json.dumps(result.structuredContent), result.structuredContent
                    for extra in ({'path':'/private/key'}, {'url':'https://example.invalid/image'}):
                        result = call('import_chat_image', session_id=sid, data_base64=encoded, **extra)
                        assert result.structuredContent['error_code'] == 'invalid_arguments'
                    result = call('import_chat_image', session_id=sid, data_base64='A'*(MAX_BASE64_LENGTH+1))
                    assert result.structuredContent['error_code'] == 'invalid_arguments'
                    huge = io.BytesIO(); Image.new('1', (4000,4000)).save(huge, 'PNG')
                    result = call('import_chat_image', session_id=sid, data_base64=base64.b64encode(huge.getvalue()).decode())
                    assert result.isError
                    bmp = io.BytesIO(); Image.new('RGB', (4,4)).save(bmp, 'BMP')
                    assert call('import_chat_image', session_id=sid, data_base64=base64.b64encode(bmp.getvalue()).decode()).isError
                    assert call('import_chat_image', grant=ro, session_id=sid, data_base64=encoded).isError
                    assert call('import_chat_image', grant=other, session_id=sid, data_base64=encoded).isError
                    cancel = threading.Event(); cancel.set()
                    assert call('import_chat_image', session_id=sid, data_base64=encoded, cancel=cancel).isError
                    checks.append('invalid data, arbitrary paths/URLs, format/size/pixel caps, permission and cancellation')

                    animated = call('import_chat_image', session_id=sid, data_base64=encoded)
                    assert not animated.isError and animated.structuredContent['total_frames'] == 3
                    assert len([p for p in animated.content if p.type=='image']) == 3
                    gif_id = animated.structuredContent['sticker_id']
                    second = start('111:group:223', 'upload-second-group')
                    assert call('read_chat_sticker', session_id=second, sticker_id=gif_id).isError
                    second_image = call('import_chat_image', session_id=second, data_base64=encoded).structuredContent
                    assert second_image['sticker_id'] != gif_id
                    with access.connect() as db:
                        asset = dict(db.execute('SELECT * FROM chat_media_assets WHERE id=?', (gif_id,)).fetchone())
                        assert asset['origin'].startswith('upload:') and asset['ref'] == '{}'
                        for table in ('chat_media_assets','calls','operations','sticker_diagnostics'):
                            stored = json.dumps([dict(r) for r in db.execute('SELECT * FROM '+table)])
                            assert encoded not in stored and 'data_base64' not in stored and g['token'] not in stored
                    digest = animated.structuredContent['sha256']
                    cached = tools.chat.media.root/(digest+'.bin')
                    if os.name == 'posix':
                        assert cached.stat().st_mode & 0o077 == 0
                    cached.write_bytes(b'changed')
                    missing = call('read_chat_sticker', session_id=sid, sticker_id=gif_id)
                    assert missing.isError and 'import_chat_image' in missing.structuredContent['error']
                    assert call('import_chat_image', session_id=sid, data_base64=encoded).structuredContent['sticker_id'] == gif_id
                    pinned = call('collect_chat_sticker', session_id=sid, sticker_id=gif_id, idempotency_key='pin-upload-local', save_qq=False)
                    assert pinned.structuredContent['state'] == 'SUCCEEDED' and not StickerBot.collected
                    if os.name == 'posix':
                        assert all(p.stat().st_mode & 0o077 == 0 for p in tools.chat.media.library.root.rglob('*.bin'))
                    cached.unlink()
                    assert not call('read_chat_sticker', session_id=sid, sticker_id=gif_id).isError
                    checks.append('animation, per-session handles, private metadata, cache integrity and pinned-original recovery')

                    # Execute the documented local-file uploader against the same
                    # real service, so escaping, config formats and output are tested.
                    image_path = root/'generated image.png'; image_path.write_bytes(raw)
                    config_path = root/'client.json'
                    config_path.write_text(json.dumps({'mcpServers':{'tulpa':{'url':service.status()['url'],'headers':{'Authorization':'Bearer '+g['token']}}}}))
                    process = subprocess.run([sys.executable, str(ROOT/'scripts/import_chat_image.py'), '--config', str(config_path), '--session', sid, str(image_path)],
                                             capture_output=True, text=True, timeout=30)
                    assert process.returncode == 0, process.stderr
                    result = json.loads(process.stdout)
                    assert result['sticker_id'] == aid and result['preview']['tool'] == 'read_chat_sticker'
                    assert g['token'] not in process.stdout+process.stderr and base64.b64encode(raw).decode() not in process.stdout
                    assert len(StickerBot.sent) == 2
                    toml_path = root/'client.toml'
                    toml_path.write_text('[mcp_servers.tulpa]\nurl='+json.dumps(service.status()['url'])+'\nbearer_token_env_var="IMAGE_FIXTURE_TOKEN"\n')
                    with patch.dict('os.environ', {'IMAGE_FIXTURE_TOKEN':g['token']}):
                        assert connection(toml_path) == (service.status()['url'], 'Bearer '+g['token'])
                    for name, value in (('empty', b''), ('oversize', b'x'*(MAX_BYTES+1))):
                        path = root/name; path.write_bytes(value)
                        try: image_bytes(path); raise AssertionError('Invalid local file accepted')
                        except UploadError: pass
                    checks.append('local file CLI, JSON/TOML credentials, concise private output, no implicit send')

                    # Stopping during image decoding must prevent publication of
                    # a new usable handle; the ordinary stop path is independent.
                    entered = threading.Event(); release = threading.Event()
                    def blocked(raw):
                        entered.set(); assert release.wait(5)
                        return pixels(raw)
                    with patch('chatlocal.mcp_chat_media.pixels', side_effect=blocked), ThreadPoolExecutor(1) as pool:
                        pending = pool.submit(call, 'import_chat_image', session_id=second, data_base64=encoded)
                        assert entered.wait(3)
                        assert call('stop_chat_session', session_id=second).structuredContent['event'] == 'stopped'
                        release.set(); assert pending.result(5).isError
                    assert call('import_chat_image', session_id=second, data_base64=encoded).isError
                    with access.connect() as db:
                        db.executemany('INSERT INTO calls(grant_id,tool,at,status) VALUES(?,?,strftime("%s","now"),?)',
                                       [(other['id'],'import_chat_image','ok')]*60)
                    assert call('import_chat_image', grant=other, session_id=sid, data_base64=encoded).structuredContent['error_code'] == 'rate_limited'
                    assert not call('get_data_status', grant=other).isError
                    access.revoke(g['id'])
                    async def revoked():
                        async with httpx.AsyncClient(trust_env=False) as http:
                            response = await http.post(service.status()['url'], headers={'Authorization':'Bearer '+g['token']}, json={})
                            assert response.status_code == 401
                    asyncio.run(revoked())
                    assert len(StickerBot.sent) == 2
                    checks.append('stop during decode, stopped session, revocation and independent import rate limit')
    finally:
        StickerBot.uncertain = False
        events.close(); upstream.shutdown(); upstream.server_close()
    print(json.dumps(dict(ok=True, checks=checks, real_qq_messages=0), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
