"""Same-origin UI routes, deliberately absent from the LLM bridge."""
import json
from fastapi import Request,HTTPException
from starlette.concurrency import run_in_threadpool
from .store import Store
from .replies import Replies,ReplyRunner


def install_reply_routes(app,store=None,sender_factory=None,agent=None):
    store=store or Store();layer=Replies(store,**({'sender_factory':sender_factory} if sender_factory else {}));runner=ReplyRunner(layer,agent)
    app.state.reply_runner=runner
    def origin(request):
        if request.headers.get('origin') and request.headers['origin']!=str(request.base_url).rstrip('/') or request.headers.get('sec-fetch-site')=='cross-site':
            raise HTTPException(403,'仅限本机同源页面。')
    def guard(fn,*args,**kwargs):
        try:return fn(*args,**kwargs)
        except (ValueError,TypeError,KeyError) as exc:raise HTTPException(400,str(exc) if isinstance(exc,ValueError) else '参数无效。') from None
    async def body(request):
        origin(request)
        if request.headers.get('content-type','').split(';')[0]!='application/json' or request.headers.get('x-reply-ui')!='1':raise HTTPException(403,'需要从回复审核界面操作。')
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>16000:raise HTTPException(413,'请求过大。')
        try:value=json.loads(raw)
        except ValueError:raise HTTPException(400,'JSON格式无效。') from None
        if not isinstance(value,dict):raise HTTPException(400,'需要JSON对象。')
        return value
    @app.post('/api/replies')
    async def create(request:Request):b=await body(request);return await run_in_threadpool(guard,layer.create,b.get('message_id'),b.get('fresh') is True)
    @app.get('/api/reply-receipts')
    def receipts(request:Request,platform:str,conversation_id:str):origin(request);return guard(layer.sent,platform,conversation_id)
    @app.get('/api/replies/{sid}')
    def get(request:Request,sid:str):origin(request);return guard(layer.get,sid)
    @app.post('/api/replies/{sid}/generate')
    async def generate(request:Request,sid:str):
        b=await body(request);return await run_in_threadpool(guard,runner.start,sid,b.get('instruction',''),b.get('profile','standard'),b.get('vision','ocr'))
    @app.post('/api/replies/{sid}/stop')
    async def stop(request:Request,sid:str):await body(request);return guard(runner.stop,sid)
    @app.post('/api/replies/{sid}/reject')
    async def reject(request:Request,sid:str):
        b=await body(request);return guard(runner.reject,sid,b.get('revision'))
    @app.patch('/api/replies/{sid}')
    async def edit(request:Request,sid:str):b=await body(request);return guard(layer.edit,sid,b.get('text'),b.get('revision'))
    @app.post('/api/replies/{sid}/preview')
    async def preview(request:Request,sid:str):
        b=await body(request);return await run_in_threadpool(guard,layer.preview,sid,b.get('revision'),b.get('quote',False))
    @app.post('/api/replies/{sid}/send')
    async def send(request:Request,sid:str):
        b=await body(request);return await run_in_threadpool(guard,layer.send,sid,b.get('token'),b.get('confirm'))
    @app.on_event('startup')
    def recover():layer.recover()
    @app.on_event('shutdown')
    def shutdown():
        with runner.lock:
            for cancel in runner.active.values():cancel.set()
