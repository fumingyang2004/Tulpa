"""Authenticated loopback Streamable HTTP MCP, owned by the existing app."""
import json
import socket
import threading
import time
from contextlib import asynccontextmanager

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .mcp_access import AccessDenied
from .mcp_tools import MCPTools, INSTRUCTIONS
from .mcp_chat import CHAT_TOOLS
from .mcp_chat_media import MEDIA_TOOLS, MEDIA_WRITES
from .version import VERSION


class MCPService:
    def __init__(self, access):
        self.access = access
        self.tools = MCPTools(access)
        self.tools.actions.recover()
        self.server = self.thread = self.listener = None
        self.error = ''
        self.source_base = ''
        self.lock = threading.RLock()
        self.pending_lock = threading.Lock()
        self.pending = {}
        self.wait_limiter = None
        self.media_limiter = None

    def application(self, port):
        protocol = Server('tulpa', version=VERSION, instructions=INSTRUCTIONS)
        manager = StreamableHTTPSessionManager(protocol, json_response=True, stateless=True,
            security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=True,
                allowed_hosts=[f'127.0.0.1:{port}', f'localhost:{port}'],
                allowed_origins=[f'http://127.0.0.1:{port}', f'http://localhost:{port}']))

        def credential():
            request = protocol.request_context.request
            return request.headers.get('authorization', '').removeprefix('Bearer ')

        @protocol.list_tools()
        async def list_tools():
            token = credential()
            # Listing contains only static tool descriptions/permission flags.
            # Native source/epoch validation belongs to every historical tool
            # call, not the transport; live chat must work without that database.
            grant = self.access.authorize(token, source=False)
            rows = await anyio.to_thread.run_sync(self.tools.schemas, grant)
            self.access.authorize(token, source=False)
            return [types.Tool(name=s['name'], description=s['description'], inputSchema=s['parameters'],
                    annotations=types.ToolAnnotations(readOnlyHint=s['name'] not in CHAT_TOOLS and s['name'] not in MEDIA_WRITES and s['name'] not in ('prepare_file', 'download_file', 'transcribe_voice', 'read_qq_group', 'get_group_knowledge','send_qq_message','manage_qq_group'),
                        destructiveHint=s['name']=='manage_qq_group', openWorldHint=s['name'] in ('send_qq_message','manage_qq_group','send_chat_message','send_chat_sticker','collect_chat_sticker'))) for s in rows]

        @protocol.call_tool(validate_input=False)
        async def call_tool(name, arguments):
            request = protocol.request_context.request
            cancel = request.scope['state']['mcp_cancel']
            token = credential()
            try:
                # Worker IO is bounded by the underlying SQL/parser/ASR limits.
                # On disconnect/cancellation don't orphan disclosure or reset quota.
                with anyio.fail_after(240):
                    # Isolate long polls from Starlette's shared thread pool too.
                    if name=='wait_chat_messages' and self.wait_limiter is None:
                        self.wait_limiter=anyio.CapacityLimiter(16)
                    if name in MEDIA_TOOLS and self.media_limiter is None:
                        self.media_limiter=anyio.CapacityLimiter(4)
                    return await anyio.to_thread.run_sync(self.tools.call, token, name, arguments, cancel, self.source_base,
                        abandon_on_cancel=True, limiter=self.wait_limiter if name=='wait_chat_messages' else self.media_limiter if name in MEDIA_TOOLS else None)
            except AccessDenied:
                return self.tools.result(dict(error='连接已失效，请重新授权。'))
            except TimeoutError:
                return self.tools.result(dict(error='读取超时；已完成的本地解析缓存可供下次使用。'))
            except Exception:
                # Never let SDK error serialization expose local paths or keys.
                return self.tools.result(dict(error='资料读取失败；请在 Tulpa 检查数据和连接状态。'))
            finally:
                cancel.set()

        service = self

        class Endpoint:
            async def __call__(self, scope, receive, send):
                request = Request(scope, receive)
                def rejected(code):
                    return JSONResponse({'error': 'MCP request denied'}, status_code=code, headers={'Cache-Control': 'no-store'})
                host = request.headers.get('host', '')
                origin = request.headers.get('origin')
                if host not in (f'127.0.0.1:{port}', f'localhost:{port}') or (origin and origin != 'http://' + host):
                    return await rejected(403)(scope, receive, send)
                if request.client and request.client.host not in ('127.0.0.1', '::1', 'testclient'):
                    return await rejected(403)(scope, receive, send)
                auth = request.headers.get('authorization', '')
                if not auth.startswith('Bearer '):
                    return await rejected(401)(scope, receive, send)
                try:
                    grant = service.access.authorize(auth[7:], source=False)
                except AccessDenied:
                    return await rejected(401)(scope, receive, send)
                # Bound bodies without relying on Content-Length (chunked supported).
                body = bytearray()
                if request.method == 'POST':
                    try:
                        with anyio.fail_after(10):
                            async for chunk in request.stream():
                                body.extend(chunk)
                                if len(body) > 32768:
                                    return await rejected(413)(scope, receive, send)
                        payload = json.loads(body)
                    except (ValueError, TimeoutError):
                        return await rejected(400)(scope, receive, send)
                    if not isinstance(payload, dict):
                        return await rejected(400)(scope, receive, send)
                else:
                    payload = {}
                key = (grant['id'], json.dumps(payload.get('id'), sort_keys=True))
                cancel = threading.Event()
                scope.setdefault('state', {})['mcp_cancel'] = cancel
                method = payload.get('method')
                if method == 'notifications/cancelled':
                    params = payload.get('params')
                    if isinstance(params, dict):
                        cancelled_key = (grant['id'], json.dumps(params.get('requestId'), sort_keys=True))
                        with service.pending_lock:
                            found = service.pending.get(cancelled_key)
                            if found:
                                found.set()
                registered = method == 'tools/call' and 'id' in payload
                duplicate = False
                with service.pending_lock:
                    if registered:
                        duplicate = key in service.pending
                        if not duplicate:
                            service.pending[key] = cancel
                if duplicate:
                    return await rejected(409)(scope, receive, send)
                first = True
                async def replay():
                    nonlocal first
                    if first:
                        first = False
                        return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
                    return await receive()
                try:
                    await manager.handle_request(scope, replay, send)
                finally:
                    if registered:
                        with service.pending_lock:
                            service.pending.pop(key, None)
                        cancel.set()

        @asynccontextmanager
        async def lifespan(app):
            async with manager.run():
                yield

        return Starlette(routes=[Route('/mcp', Endpoint(), methods=['POST', 'GET', 'DELETE'])], lifespan=lifespan)

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                if self.server.should_exit:
                    self.error = '旧 MCP 服务正在停止，请稍后再保存服务设置。'
                return
            setting = self.access.settings()
            if not setting['enabled']:
                return
            self.tools.chat.resume()
            self.wait_limiter=None
            import uvicorn
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                listener.bind(('127.0.0.1', setting['port']))
                listener.listen(128)
            except OSError:
                listener.close()
                self.tools.chat.available=False
                self.error = 'MCP 端口无法使用，可能已被其他 Tulpa 实例或程序占用。请更换端口。'
                return
            self.listener = listener
            self.error = ''
            self.server = uvicorn.Server(uvicorn.Config(self.application(setting['port']), host='127.0.0.1',
                port=setting['port'], log_level='critical', access_log=False, timeout_graceful_shutdown=3))
            def run():
                try:
                    self.server.run(sockets=[listener])
                finally:
                    self.tools.chat.suspend()
                    listener.close()
            self.thread = threading.Thread(target=run, name='tulpa-mcp', daemon=True)
            self.thread.start()
            until = time.monotonic() + 5
            while self.thread.is_alive() and not self.server.started and time.monotonic() < until:
                time.sleep(.02)
            if not self.server.started:
                self.error = 'MCP 服务未能启动，请查看依赖安装与端口设置。'

    def cancel(self, gid=None):
        with self.pending_lock:
            for (owner, _), event in self.pending.items():
                if gid is None or gid == owner:
                    event.set()

    def stop(self):
        with self.lock:
            self.tools.chat.suspend()
            self.cancel()
            if self.server:
                self.server.should_exit = True
            if self.thread:
                self.thread.join(5)
            if not self.thread or not self.thread.is_alive():
                self.thread = self.server = self.listener = None
                self.error = ''

    def status(self):
        setting = self.access.settings()
        return dict(**setting, running=bool(self.thread and self.thread.is_alive() and self.server.started and not self.server.should_exit),
                    url=f'http://127.0.0.1:{setting["port"]}/mcp', error=self.error)
