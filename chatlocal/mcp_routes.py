"""Browser-owned MCP settings; never registered as model-callable tools."""
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import anyio
from fastapi import HTTPException, Request

from .mcp_access import MCPAccess
from .mcp_server import MCPService
from .store import Store


def install_mcp_routes(app, store=None):
    store = store or Store()
    access = MCPAccess(store)
    service = MCPService(access)
    app.state.tulpa_mcp = service
    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with previous(application):
            await anyio.to_thread.run_sync(service.start)
            try:
                yield
            finally:
                await anyio.to_thread.run_sync(service.stop)
    app.router.lifespan_context = lifespan

    def local(request, write=False):
        if request.client and request.client.host not in ('127.0.0.1', '::1', 'testclient'):
            raise HTTPException(403)
        if urlparse(str(request.base_url)).hostname not in ('127.0.0.1', 'localhost', '::1', 'testserver'):
            raise HTTPException(403)
        if request.headers.get('origin') not in (None, str(request.base_url).rstrip('/')) or request.headers.get('sec-fetch-site') == 'cross-site':
            raise HTTPException(403)
        if write and (request.headers.get('x-chatweave-ui') != '1' or request.headers.get('content-type', '').split(';')[0] != 'application/json'):
            raise HTTPException(403)
        if urlparse(str(request.base_url)).hostname != 'testserver':
            service.source_base = str(request.base_url).rstrip('/')

    @app.get('/api/mcp')
    def status(request: Request):
        local(request)
        return dict(**service.status(), **access.list(), storage_label=store.path.parent.parent.name)

    @app.put('/api/mcp')
    def configure(request: Request, body: dict):
        local(request, True)
        if set(body) != {'enabled', 'port'}:
            raise HTTPException(400, 'MCP 配置字段无效。')
        try:
            old = access.settings()
            access.configure(body['enabled'], body['port'])
            if not body['enabled'] or body['port'] != old['port']:
                service.stop()
            if body['enabled']:
                service.start()
            return service.status()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post('/api/mcp/connections')
    def create(request: Request, body: dict):
        local(request, True)
        try:
            return access.create(body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post('/api/mcp/connections/{gid}/revoke')
    def revoke(gid: str, request: Request, body: dict):
        local(request, True)
        access.revoke(gid)
        service.cancel(gid)
        return dict(revoked=True)

    @app.get('/api/mcp/conversations')
    def conversations(request: Request, query: str='', offset: int=0):
        local(request)
        if len(query) > 100 or offset < 0:
            raise HTTPException(400)
        with store.connect() as db:
            rows = [dict(r) for r in db.execute('''SELECT platform,conversation_id,max(conversation) name,count(*) count
                FROM messages WHERE instr(lower(conversation),lower(?))>0
                GROUP BY platform,conversation_id ORDER BY platform,name LIMIT 81 OFFSET ?''', (query, offset))]
        return dict(items=rows[:80], has_more=len(rows)>80, next_offset=offset+80)

    return service
