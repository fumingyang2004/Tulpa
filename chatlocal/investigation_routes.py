"""Small local reference library and optional enriched-source reconciliation."""
import json
import threading
import time

from fastapi import HTTPException,Request
from starlette.concurrency import run_in_threadpool

from .collections import Collections
from .group_knowledge import refresh,public
from .artifact_onebot import config
from .store import Store


def install_investigation_routes(app,store=None):
    store=store or Store();layer=Collections(store)
    stop=threading.Event();lock=threading.Lock();state=dict(running=False,result=None,error=None)
    def origin(request):
        if request.headers.get('origin') and request.headers['origin']!=str(request.base_url).rstrip('/'):
            raise HTTPException(403,'仅限本机同源页面')
        if request.headers.get('sec-fetch-site')=='cross-site':raise HTTPException(403,'仅限本机同源页面')
    def guarded(fn,*args,**kwargs):
        try:return fn(*args,**kwargs)
        except ValueError as exc:raise HTTPException(400,str(exc)) from None
    async def body(request):
        origin(request);data=bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data)>32768:raise HTTPException(413,'请求过大')
        try:value=json.loads(data)
        except ValueError:raise HTTPException(400,'JSON格式无效') from None
        if not isinstance(value,dict):raise HTTPException(400,'需要JSON对象')
        return value
    @app.get('/api/collections')
    def list_collections(request:Request,offset:int=0):
        origin(request)
        if not 0<=offset<=100000:raise HTTPException(400)
        return layer.list(offset)
    @app.post('/api/collections')
    async def create(request:Request):
        b=await body(request);return await run_in_threadpool(guarded,layer.create,b.get('title'))
    @app.get('/api/collections/{cid}')
    def show(request:Request,cid:str,offset:int=0):
        origin(request)
        if not 0<=offset<=2000:raise HTTPException(400)
        return guarded(layer.get,cid,offset=offset)
    @app.patch('/api/collections/{cid}')
    async def rename(request:Request,cid:str):
        b=await body(request);return await run_in_threadpool(guarded,layer.rename,cid,b.get('title'))
    @app.delete('/api/collections/{cid}')
    def delete(request:Request,cid:str):
        origin(request);return layer.delete(cid)
    @app.post('/api/collections/{cid}/items')
    async def add(request:Request,cid:str):
        b=await body(request);return await run_in_threadpool(guarded,layer.add,cid,b.get('items'))
    @app.delete('/api/collections/{cid}/items/{item_id}')
    def remove(request:Request,cid:str,item_id:str):
        origin(request);return layer.remove(cid,item_id)

    def launch(cid):
        with lock:
            if state['running']:raise HTTPException(409,'QQ群资料同步正在运行')
            state.update(running=True,result=None,error=None)
        def work():
            try:state['result']=refresh(store,cid)
            except ValueError as exc:state['error']=str(exc)
            except Exception:state['error']='QQ群资料同步失败，已保存的资料仍在。'
            finally:state['running']=False
        threading.Thread(target=work,daemon=True,name='knowledge-refresh').start()
        return dict(started=True)
    def timer():
        if stop.wait(15):return
        first=True
        while not stop.is_set():
            if config()['ARTIFACT_ONEBOT_URL'] and not state['running']:
                with store.connect() as db:
                    rows=db.execute('SELECT conversation_id FROM qq_knowledge_sync WHERE enabled=1 AND (? OR coalesce(last_attempt,0)<?) ORDER BY last_attempt LIMIT 20',
                                    (first,time.time()-6*3600)).fetchall()
                for row in rows:
                    if stop.is_set():return
                    try:launch(row[0])
                    except HTTPException:break
                    while state['running'] and not stop.wait(1):pass
                first=False
            stop.wait(60)
    @app.on_event('startup')
    def startup():threading.Thread(target=timer,daemon=True,name='knowledge-reconciliation').start()
    @app.on_event('shutdown')
    def shutdown():stop.set()
    @app.get('/api/knowledge/status')
    def status(request:Request):
        origin(request)
        with store.connect() as db:
            groups=[dict(r) for r in db.execute("SELECT conversation_id,max(conversation) conversation FROM messages WHERE platform='qq' AND conversation_type='group' GROUP BY conversation_id ORDER BY conversation")]
            sync=[dict(r) for r in db.execute('SELECT * FROM qq_knowledge_sync')]
        return dict(state,configured=bool(config()['ARTIFACT_ONEBOT_URL']),groups=groups,sync=sync,
            note='可选本机NapCat来源。仅指定群可启用启动/每6小时同步；不发送消息、不下载公告图片。未返回的旧条目保留，不代表其当前仍有效。')
    @app.post('/api/knowledge/refresh')
    async def sync(request:Request):
        b=await body(request);cid=b.get('conversation_id');enabled=b.get('periodic',False)
        if not isinstance(cid,str) or type(enabled) is not bool:raise HTTPException(400,'参数无效')
        def work():
            with store.connect() as db:
                row=db.execute("SELECT conversation FROM messages WHERE platform='qq' AND conversation_type='group' AND conversation_id=? LIMIT 1",(cid,)).fetchone()
                if not row:raise HTTPException(400,'需要已导入的QQ群')
                db.execute('''INSERT INTO qq_knowledge_sync(conversation_id,conversation,enabled) VALUES(?,?,?)
                    ON CONFLICT(conversation_id) DO UPDATE SET enabled=excluded.enabled''',(cid,row[0],enabled))
            return launch(cid)
        return await run_in_threadpool(work)
    @app.post('/api/knowledge/schedule')
    async def schedule(request:Request):
        b=await body(request)
        if type(b.get('enabled')) is not bool or not isinstance(b.get('conversation_id'),str):raise HTTPException(400)
        def work():
            with store.connect() as db:
                row=db.execute("SELECT conversation FROM messages WHERE platform='qq' AND conversation_type='group' AND conversation_id=? LIMIT 1",(b['conversation_id'],)).fetchone()
                if not row:raise HTTPException(400,'需要已导入的QQ群')
                db.execute('''INSERT INTO qq_knowledge_sync(conversation_id,conversation,enabled) VALUES(?,?,?)
                    ON CONFLICT(conversation_id) DO UPDATE SET enabled=excluded.enabled''',
                    (b['conversation_id'],row[0],b['enabled']))
            return dict(updated=True)
        return await run_in_threadpool(work)
    @app.get('/api/knowledge')
    def knowledge(request:Request,conversation_id:str='',offset:int=0):
        origin(request)
        if not 0<=offset<=10000:raise HTTPException(400)
        with store.connect() as db:
            rows=db.execute('SELECT * FROM qq_knowledge WHERE (?=\'\' OR conversation_id=?) ORDER BY fetched_at DESC,id DESC LIMIT 31 OFFSET ?',
                (conversation_id,conversation_id,offset)).fetchall()
        return dict(sources=[public(r) for r in rows[:30]],next_offset=offset+30 if len(rows)>30 else None)
    @app.get('/api/knowledge/{kid}')
    def knowledge_item(request:Request,kid:int):
        origin(request)
        with store.connect() as db:row=db.execute('SELECT * FROM qq_knowledge WHERE id=?',(kid,)).fetchone()
        if not row:raise HTTPException(404,'QQ资料不存在')
        return public(row)
