"""UI-only decisions; neither approval nor policy settings are in the bridge."""
import json
from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool
from .store import Store
from .agent_sessions import Sessions
from .group_admin import GroupAdmin
from .onebot import available_client


def install_group_admin_routes(app, store=None, sessions=None, client_factory=None, probe=None):
    layer = GroupAdmin(store or Store(), client_factory)
    sessions = sessions or Sessions()
    probe = probe or available_client
    app.state.group_admin = layer

    def origin(request):
        if request.headers.get('origin') and request.headers['origin'] != str(request.base_url).rstrip('/') or request.headers.get('sec-fetch-site') == 'cross-site':
            raise HTTPException(403, '仅限本机同源页面。')

    def session(sid):
        if not isinstance(sid,str) or len(sid)>64:
            raise HTTPException(400, '对话编号无效。')
        with sessions.connect() as db:
            if not db.execute('SELECT 1 FROM sessions WHERE id=?', (sid,)).fetchone():
                raise HTTPException(404, '对话不存在。')

    async def body(request):
        origin(request)
        if request.headers.get('x-group-admin-ui') != '1' or request.headers.get('content-type', '').split(';')[0] != 'application/json':
            raise HTTPException(403, '请从群管理审批栏操作。')
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 4096:
                raise HTTPException(413, '请求过大。')
        try:
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except ValueError:
            raise HTTPException(400, '请求格式无效。') from None

    def guard(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get('/api/group-admin')
    def status(request: Request, session_id: str = ''):
        origin(request)
        if session_id:
            session(session_id)
        return dict(available=bool(probe()), policy=layer.policy(session_id),
                    operations=layer.list(session_id, ui=True) if session_id else [])

    @app.put('/api/group-admin/policy')
    async def policy(request: Request):
        b = await body(request)
        sid = b.get('session_id', '')
        session(sid)
        return guard(layer.set_policy, sid, b.get('mode'))

    @app.post('/api/group-admin/{oid}/decision')
    async def decision(request: Request, oid: str):
        b = await body(request)
        sid = b.get('session_id', '')
        session(sid)
        return await run_in_threadpool(guard, layer.decide, oid, sid, b.get('allow'), b.get('token'))

    @app.on_event('startup')
    def recover():
        layer.recover()
