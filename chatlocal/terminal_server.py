"""Authenticated terminal management, using Tulpa's original MCP application core."""
import os
from pathlib import Path
import platform
import secrets
import sys
import threading
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .config import ROOT
from .mcp_routes import install_mcp_routes
from .mcp_access import GrantConflict
from .platform_lock import file_lock
from .store import Store
from .terminal_client import read_token
from .version import VERSION


def ensure_token(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path.with_suffix('.lock'), blocking=True):
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(secrets.token_urlsafe(48)+'\n')
                stream.flush()
                os.fsync(stream.fileno())
        return read_token(path)


def create_app(*, root=ROOT, store=None, token=None, desktop=None, legacy=True):
    root = Path(root).resolve()
    store = store or Store(root/'data/chats.sqlite3')
    token = token or ensure_token(root/'data/terminal-admin.token')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    service = install_mcp_routes(app, store)
    access, chat = service.access, service.tools.chat
    app.state.terminal_root = root
    mutation_lock = threading.RLock()
    desktop = sys.platform == 'darwin' if desktop is None else desktop
    from .desktop_session import DesktopSessions
    browser = DesktopSessions(root) if desktop else None

    if browser:
        @app.post('/api/terminal/desktop-ticket')
        def desktop_ticket(request: Request, body: dict):
            if body or not secrets.compare_digest(request.headers.get('authorization', '').encode(), ('Bearer '+token).encode()):
                raise HTTPException(403, '请从本机 Tulpa 启动桌面窗口。')
            return dict(path='/_desktop/open?ticket='+browser.issue())

    @app.middleware('http')
    async def authorize(request: Request, call_next):
        host = urlparse(str(request.base_url)).hostname
        if (host not in ('127.0.0.1', 'localhost', '::1', 'testserver')
                or (request.client and request.client.host not in ('127.0.0.1', '::1', 'testclient'))
                or request.headers.get('origin') not in (None, str(request.base_url).rstrip('/'))
                or request.headers.get('sec-fetch-site') == 'cross-site'):
            return JSONResponse({'detail': '仅允许本机同源管理请求。'}, status_code=403)
        if browser and request.url.path == '/_desktop/open' and request.method == 'GET':
            session = browser.exchange(request.query_params.get('ticket'))
            if not session:
                return JSONResponse({'detail': '桌面启动凭证已过期，请重新打开 Tulpa。'}, status_code=401)
            response = RedirectResponse('/?desktop=1', status_code=303)
            response.set_cookie(browser.cookie, session, httponly=True, samesite='strict', max_age=86400)
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Referrer-Policy'] = 'no-referrer'
            return response
        if request.url.path != '/health':
            bearer = secrets.compare_digest(request.headers.get('authorization', '').encode(), ('Bearer '+token).encode())
            if not bearer and not (browser and browser.valid(request.cookies.get(browser.cookie))):
                return JSONResponse({'detail': '管理凭据无效。'}, status_code=401)
            if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
                legacy_upload = bool(browser and request.url.path == '/legacy/gradio_api/upload')
                if not legacy_upload and request.headers.get('content-type', '').split(';')[0] != 'application/json':
                    return JSONResponse({'detail': '请求须为 JSON。'}, status_code=415)
                raw = bytearray()
                limit = 101*1024*1024 if legacy_upload else (65536 if request.url.path.startswith('/api/terminal/') else 512000)
                async for chunk in request.stream():
                    raw.extend(chunk)
                    if len(raw) > limit:
                        return JSONResponse({'detail': '管理请求过大。'}, status_code=413)
                request._body = bytes(raw)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({'detail': str(error)}, status_code=400)

    @app.exception_handler(GrantConflict)
    async def grant_conflict(request, error):
        return JSONResponse({'detail': str(error)}, status_code=409)

    @app.exception_handler(OSError)
    async def unavailable(request, error):
        return JSONResponse({'detail': '本机文件或服务不可用，请检查路径、权限及运行状态。'}, status_code=503)

    def sl_config():
        from .desktop_routes import read_product_settings
        cfg = read_product_settings(root)
        return dict(http_url=cfg['sender_url'], ws_url=cfg['events_url'],
                    has_http_token=cfg['has_sender_token'], has_ws_token=cfg['has_events_token'],
                    configured=bool(cfg['sender_url']))

    @app.get('/health')
    def health():
        return dict(product='Tulpa', version=VERSION, management='terminal-v1')

    @app.post('/api/terminal/shutdown')
    def shutdown(body: dict):
        if body:
            raise ValueError('停止后台不接受额外参数。')
        callback = getattr(app.state, 'terminal_shutdown', None)
        if callback is None:
            raise HTTPException(409, '该后台由外部进程管理器运行，请通过原管理器停止。')
        callback()
        return dict(stopping=True)

    @app.get('/api/terminal/overview')
    def overview():
        from .terminal_instances import identity
        with store.connect() as db:
            messages = db.execute('SELECT count(*) FROM messages').fetchone()[0]
        with access.connect() as db:
            connections = db.execute('SELECT count(*) FROM grants WHERE revoked=0').fetchone()[0]
            sessions = db.execute('SELECT count(*) FROM chat_sessions WHERE active=1').fetchone()[0]
        return dict(version=VERSION, platform=f'{platform.system()} {platform.machine()}', instance=identity(root),
                    storage=str(root/'data'), mcp=service.status(), sl=sl_config(),
                    counts=dict(messages=messages, connections=connections, active_sessions=sessions),
                    capabilities=dict(native_import=True, managed_snowluma=True,
                                      managed_snowluma_docker=platform.system() in ('Linux', 'Darwin'),full_application=True,
                                      multi_instance=True),
                    receiver=chat.receiver.status())

    @app.get('/api/terminal/sl')
    def sl():
        return sl_config()

    @app.put('/api/terminal/sl')
    def save_sl(body: dict):
        from .desktop_routes import save_product_settings
        from .onebot import configuration, invalidate_availability
        from .onebot_events import configuration as event_configuration
        if set(body)-{'http_url','ws_url','http_token','ws_token'} or not {'http_url','ws_url'} <= set(body):
            raise ValueError('请填写 SL 的 HTTP 与正向 WebSocket 地址。')
        if any(not isinstance(value, str) for value in body.values()):
            raise ValueError('连接字段必须是文本。')
        with mutation_lock:
            before = (configuration(root), event_configuration(root))
            cfg = dict(sender_url=body['http_url'], events_url=body['ws_url'],
                       sender_token=body.get('http_token') or before[0]['token'],
                       events_token=body.get('ws_token') or before[1]['token'])
            save_product_settings(cfg, root, replace_onebot_secrets=True)
            if before != (configuration(root), event_configuration(root)):
                chat.stop_all(reason='SL 连接设置已更改，请核对账号后重新开始。')
                chat.receiver.stop()
                invalidate_availability()
                with service.tools.qq_lock:
                    service.tools.qq_at = 0
        return sl_config()

    @app.post('/api/terminal/sl/test')
    def test_sl(body: dict):
        from .onebot import configuration
        from .onebot_events import configuration as event_configuration
        from .snowluma_setup import test_connection
        if body:
            raise ValueError('连接检测不接受额外参数。')
        return test_connection(configuration(root), event_configuration(root), service=service)

    @app.get('/api/terminal/groups')
    def groups():
        from .onebot import Client, configuration
        client = Client(configuration(root), timeout=4)
        account = client.login()
        rows = client.call('get_group_list', {})
        return dict(items=[dict(id=f'{account}:group:{r["group_id"]}',
                                name=str(r.get('group_name') or r['group_id']), account=account)
                           for r in rows if isinstance(r, dict) and str(r.get('group_id','')).isdigit()])

    @app.put('/api/terminal/mcp')
    def configure_mcp(body: dict):
        if set(body) != {'enabled','port'}:
            raise ValueError('MCP 设置需要 enabled 与 port。')
        with mutation_lock:
            old = access.settings()
            access.configure(body['enabled'], body['port'])
            if not body['enabled'] or old['port'] != body['port']:
                service.stop()
            if body['enabled']:
                service.start()
            result = service.status()
        if result['error']:
            raise HTTPException(409, result['error'])
        return result

    @app.get('/api/terminal/friends')
    def friends():
        from .onebot import Client, configuration
        client=Client(configuration(root),timeout=4);account=client.login()
        rows=client.call('get_friend_list',{})
        return dict(items=[dict(id=f'{account}:direct:{r["user_id"]}',name=str(r.get('remark') or r.get('nickname') or r['user_id']),account=account)
                           for r in rows if isinstance(r,dict) and str(r.get('user_id','')).isdigit()])

    @app.get('/api/terminal/grant-permissions')
    def grant_permissions():
        from .mcp_qq_permissions import QQ_FLAGS, ACCOUNT_FLAGS
        return dict(items=[dict(id=k,label=v,scope='account' if k in ACCOUNT_FLAGS else 'conversation',default=False) for k,v in QQ_FLAGS.items()])

    @app.get('/api/terminal/grants')
    def grants():
        return dict(items=access.list()['connections'])

    @app.post('/api/terminal/grants')
    def create_grant(body: dict):
        from .mcp_qq_permissions import live_grant
        if not service.status()['running']:
            raise ValueError('请先启用 MCP 服务，再创建连接。')
        platforms = body.get('platforms')
        if not isinstance(platforms, list) or any(p not in ('qq','wechat') for p in platforms):
            raise ValueError('请选择有效平台。')
        with store.connect() as db:
            for source in platforms:
                if body.get('allow_future_imports') or (source == 'qq' and live_grant(body)):
                    continue
                if not db.execute('SELECT 1 FROM messages WHERE platform=? LIMIT 1', (source,)).fetchone():
                    raise ValueError('请先导入所选平台的聊天，确认来源账号后再创建连接。')
        # Use the original scope, source-account and OneBot validation unchanged.
        result = access.create(body)
        if body.get('qq_friends'):chat.receiver.start()
        url = service.status()['url']
        return dict(**result, mcp_url=url, client_config={'mcpServers': {'tulpa': {
            'type': 'http', 'url': url, 'timeout': 240000,
            'headers': {'Authorization': 'Bearer '+result['token']}}}})

    @app.post('/api/terminal/grants/{gid}/revoke')
    def revoke(gid: str, body: dict):
        if body:
            raise ValueError('撤销连接不接受额外参数。')
        chat.stop_all(gid, '连接已撤销')
        service.tools.actions.revoke(gid)
        service.cancel(gid)
        return dict(ok=True)

    @app.put('/api/terminal/grants/{gid}')
    def update_grant(gid: str, body: dict):
        return service.update_permissions(gid, body)

    @app.get('/api/terminal/personas')
    def personas():
        from .mcp_chat_prompts import persona_catalog
        catalog = persona_catalog()
        return dict(items=[dict(id=p['id'], name=p['name'], description=p['summary'], content=p['prompt'])
                           for p in catalog['personas']], warnings=catalog['warnings'])

    @app.get('/api/terminal/sessions')
    def sessions():
        rows = chat.listed()
        with access.connect() as db:
            bindings = dict(db.execute('SELECT id,grant_id FROM chat_sessions'))
        return dict(items=[dict(row, grant_id=bindings[row['id']]) for row in rows])

    @app.post('/api/terminal/sessions/start')
    def start_session(body: dict):
        if set(body) != {'connection_id','conversation_id','persona_preset','idempotency_key'}:
            raise ValueError('请选择授权连接、群和人格，并提供操作编号。')
        grant = access.by_id(body['connection_id'], source=False)
        # Do not hold chat.lock while start() waits for the WS receiver: receiver
        # status callbacks acquire that same lock from a different thread.
        with mutation_lock:
            with access.connect() as db:
                contacts = dict(db.execute('SELECT id,last_contact FROM chat_sessions WHERE active=1'))
            result = chat.start(grant, {k:v for k,v in body.items() if k!='connection_id'}, threading.Event())
            sid = result['session']['id']
            with access.connect() as db:
                db.execute('UPDATE chat_sessions SET last_contact=? WHERE id=?', (contacts.get(sid,0), sid))
        return dict(session_id=sid, note='会话已准备，等待 Agent。请让已连接 MCP 的外部 Agent 开始或继续此群聊天。')

    @app.post('/api/terminal/sessions/{sid}/stop')
    def stop_session(sid: str, body: dict):
        if body:
            raise ValueError('停止会话不接受额外参数。')
        return chat.stop(sid)

    @app.get('/api/terminal/sessions/{sid}/learning')
    def learning(sid: str):
        row = chat.row(sid)
        return chat.learning.status(sid, row['grant_id'], records=True)

    @app.put('/api/terminal/sessions/{sid}/learning')
    def configure_learning(sid: str, body: dict):
        if set(body) != {'enabled'} or type(body['enabled']) is not bool:
            raise ValueError('学习开关必须为 enabled 布尔值。')
        return chat.learning.configure(sid, body)

    @app.get('/api/terminal/search')
    def search(query: str, limit: int=20):
        from .retrieval import make_plan, retrieve
        if not 1 <= limit <= 100:
            raise ValueError('检索条数须为 1–100。')
        bundle = retrieve(store, make_plan(query), seed_limit=limit, max_messages=limit)
        return dict(messages=bundle['messages'])

    @app.get('/api/terminal/events')
    def events():
        return dict(items=access.list()['recent'])

    @app.post('/api/terminal/import')
    def import_file(body: dict):
        from .sync import ingestion_lock
        if set(body)-{'path','platform'} or not isinstance(body.get('path'), str):
            raise ValueError('请提供 imports 目录中的 JSON/JSONL 路径。')
        path = Path(body['path']).expanduser()
        if not path.is_absolute():
            path = root/path
        path = path.resolve()
        if not path.is_relative_to((root/'imports').resolve()):
            raise ValueError('请先把导出文件放入本实例的 imports 目录。')
        if body.get('platform','') not in ('','qq','wechat'):
            raise ValueError('平台须为 qq 或 wechat。')
        with ingestion_lock():
            return store.import_file(path, platform=body.get('platform',''))

    from .terminal_instances import install_instance_routes
    install_instance_routes(app, root, overview)
    from .chat_view import install_chat_routes
    from .reply_routes import install_reply_routes
    from .group_admin_routes import install_group_admin_routes
    from .tulpa_routes import install_tulpa_routes
    from .watch_routes import install_watch_routes
    from .artifact_routes import install_artifact_routes
    from .voice_routes import install_voice_routes
    from .investigation_routes import install_investigation_routes
    from .workspace_routes import install_workspace_routes
    from .desktop_routes import install_desktop_routes
    from .optional_components import install_component_routes
    from .terminal_features import install_features
    for install in (install_chat_routes,install_reply_routes,install_group_admin_routes,
                    install_tulpa_routes,install_watch_routes,install_artifact_routes,
                    install_voice_routes,install_investigation_routes,install_workspace_routes):
        install(app,store=store)
    install_features(app,store,root)
    install_component_routes(app,root=root)
    install_desktop_routes(app,root=root)
    # Native chat media URLs keep their original shape; the terminal download
    # client can also address them under the authenticated /api namespace.
    for route in list(app.routes):
        if getattr(route,'path','').startswith('/media/'):
            app.add_api_route('/api'+route.path,route.endpoint,methods=['GET'])
    if desktop:
        from .web_frontend import install_web_ui
        app = install_web_ui(app, root=root, store=store, legacy=legacy)
    return app


def run(port=7861, token_file=None):
    import uvicorn
    if not 1024 <= port <= 65535:
        raise ValueError('管理端口范围为 1024–65535。')
    # A separate management process must never race the same persistent chat loop.
    try:
        with file_lock(ROOT/'.tmp/terminal-service.lock'):
            token = ensure_token(token_file or os.environ.get('TULPA_TOKEN_FILE') or ROOT/'data/terminal-admin.token')
            app = create_app(token=token)
            app.state.tulpa_mcp.source_base = f'http://127.0.0.1:{port}'
            server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
                access_log=False, timeout_graceful_shutdown=15))
            app.state.terminal_shutdown = lambda: setattr(server, 'should_exit', True)
            server.run()
    except BlockingIOError:
        raise ValueError('此目录已有 Tulpa 终端服务运行。') from None
