"""Local UI-only memory controls. No model tool can edit or enable memory."""
import json
import time
from fastapi import Request,HTTPException
from starlette.concurrency import run_in_threadpool
from .store import Store
from .tulpa import Tulpa,MemoryWorker


def install_tulpa_routes(app,store=None,background=True):
    layer=Tulpa(store or Store());worker=MemoryWorker(layer);app.state.tulpa=layer
    def origin(request):
        if (request.headers.get('origin') and request.headers['origin']!=str(request.base_url).rstrip('/')) or request.headers.get('sec-fetch-site')=='cross-site':
            raise HTTPException(403,'仅限本机同源页面。')
    async def body(request):
        origin(request)
        if request.headers.get('x-tulpa-ui')!='1' or request.headers.get('content-type','').split(';')[0]!='application/json':
            raise HTTPException(403,'请从 Tulpa 记忆界面操作。')
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>30000:raise HTTPException(413,'请求过大。')
        try:b=json.loads(raw)
        except ValueError:raise HTTPException(400,'JSON格式无效。') from None
        if not isinstance(b,dict):raise HTTPException(400,'需要JSON对象。')
        return b
    def guard(fn,*args):
        try:return fn(*args)
        except ValueError as e:raise HTTPException(400,str(e)) from None
    @app.get('/api/tulpa')
    def listing(request:Request,q:str=''):origin(request);return layer.listing(q[:100])
    @app.post('/api/tulpa/control')
    async def toggle(request:Request):
        b=await body(request)
        result=await run_in_threadpool(guard,layer.toggle,b.get('enabled'))
        if not result['enabled'] and hasattr(app.state,'reply_runner'):
            # Already supplied context cannot be retracted from an in-flight API.
            # Cancel that generation, so OFF never publishes a memory-derived draft.
            runner=app.state.reply_runner
            with runner.lock:
                for cancel in runner.active.values():cancel.set()
        return result
    @app.get('/api/tulpa/scopes/{sid}')
    def detail(request:Request,sid:int):origin(request);return guard(layer.detail,sid)
    @app.post('/api/tulpa/conversation')
    async def open_conversation(request:Request):
        b=await body(request);return await run_in_threadpool(guard,layer.open_conversation,b.get('platform'),b.get('conversation_id'))
    @app.patch('/api/tulpa/scopes/{sid}')
    async def edit(request:Request,sid:int):
        b=await body(request);return await run_in_threadpool(guard,layer.edit,sid,b.get('manual'),b.get('revision'))
    @app.post('/api/tulpa/scopes/{sid}/retry')
    async def retry(request:Request,sid:int):
        await body(request)
        with layer.store.connect() as db:
            db.execute("UPDATE tulpa_scopes SET retry_at=0,status='collecting' WHERE id=? AND (status!='updating' OR retry_at<?)",(sid,time.time()))
        return guard(layer.detail,sid)
    @app.on_event('startup')
    def startup():
        if background:worker.start()
    @app.on_event('shutdown')
    def shutdown():worker.stop()
