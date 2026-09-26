"""Same-origin local workspace UI API. Only user routes can approve writes."""
import json
import threading
from urllib.parse import quote

from fastapi import HTTPException,Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from .store import Store
from .workspaces import Workspaces, dump
from .workspace_runner import WorkspaceRunner
from .changes import water
from .workspace_view import home_view, material_view


def install_workspace_routes(app,store=None):
    store=store or Store();layer=Workspaces(store);runner=WorkspaceRunner(store);stop=threading.Event()
    app.state.workspace_runner=runner
    def origin(request):
        if request.headers.get('origin') and request.headers['origin']!=str(request.base_url).rstrip('/') or request.headers.get('sec-fetch-site')=='cross-site':
            raise HTTPException(403,'仅限本机同源页面。')
    def guard(fn,*args,**kwargs):
        try:return fn(*args,**kwargs)
        except (ValueError,KeyError,TypeError) as exc:
            raise HTTPException(400,str(exc) if isinstance(exc,ValueError) else '参数无效。') from None
    async def body(request):
        origin(request);raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>500000:raise HTTPException(413,'请求过大。')
        try:value=json.loads(raw)
        except ValueError:raise HTTPException(400,'JSON格式无效。') from None
        if not isinstance(value,dict):raise HTTPException(400,'需要JSON对象。')
        return value
    @app.get('/api/workspaces')
    def listing(request:Request):origin(request);return dict(workspaces=layer.list())
    @app.post('/api/workspaces')
    async def create(request:Request):
        b=await body(request)
        return await run_in_threadpool(guard,layer.create,b.get('goal'),b.get('title',''),b.get('scope'),b.get('seeds'),b.get('collection_id'))
    @app.get('/api/workspaces/{wid}')
    def overview(request:Request,wid:str):origin(request);return guard(layer.overview,wid)
    @app.get('/api/workspaces/{wid}/home')
    def home(request:Request,wid:str):origin(request);return guard(home_view,layer,wid)
    @app.patch('/api/workspaces/{wid}')
    async def update(request:Request,wid:str):b=await body(request);return await run_in_threadpool(guard,layer.update,wid,b)
    @app.delete('/api/workspaces/{wid}')
    async def delete(request:Request,wid:str):b=await body(request);return await run_in_threadpool(guard,layer.delete,wid,b.get('confirm') is True)
    @app.post('/api/workspaces/{wid}/tasks')
    async def task(request:Request,wid:str):
        b=await body(request)
        return await run_in_threadpool(guard,runner.start,wid,b.get('question'),mode=b.get('mode','auto'),profile=b.get('profile','deep'),vision=b.get('vision','ocr'))
    @app.get('/api/workspaces/{wid}/thread')
    def thread(request:Request,wid:str):
        origin(request);w=guard(layer.get,wid)
        with store.connect() as db:ids=[r[0] for r in db.execute('SELECT id FROM workspace_tasks WHERE workspace_id=? AND scope_epoch=? ORDER BY created_at DESC LIMIT 40',(wid,w['scope_epoch']))]
        return dict(turns=[runner.task(wid,tid) for tid in reversed(ids)])
    @app.get('/api/workspaces/{wid}/schedule')
    def schedule(request:Request,wid:str):origin(request);return guard(layer.schedule,wid)
    @app.put('/api/workspaces/{wid}/schedule')
    async def set_schedule(request:Request,wid:str):return guard(layer.set_schedule,wid,await body(request))
    @app.get('/api/workspaces/{wid}/tasks/{tid}')
    def get_task(request:Request,wid:str,tid:str):origin(request);return guard(runner.task,wid,tid)
    @app.post('/api/workspaces/{wid}/tasks/{tid}/stop')
    async def cancel(request:Request,wid:str,tid:str):await body(request);return guard(runner.stop,wid,tid)
    @app.get('/api/workspaces/{wid}/materials')
    def materials(request:Request,wid:str):origin(request);return dict(materials=guard(material_view,layer,wid),candidates=guard(layer.candidates,wid))
    @app.post('/api/workspaces/{wid}/materials')
    async def add(request:Request,wid:str):
        b=await body(request)
        if b.get('release'):return guard(layer.release,wid,b.get('source_type'),b.get('source_id'))
        return await run_in_threadpool(guard,layer.material,wid,b,b.get('state','accepted'),b.get('reason','用户选择'))
    @app.post('/api/workspaces/{wid}/backfill')
    async def backfill(request:Request,wid:str):
        b=await body(request);return await run_in_threadpool(guard,layer.backfill,wid,b.get('start'),b.get('end'),b.get('limit',200),b.get('offset',0),b.get('new_scope_only') is True)
    @app.post('/api/workspaces/{wid}/reconnect')
    async def reconnect(request:Request,wid:str):
        b=await body(request)
        if b.get('confirm') is not True:raise HTTPException(400,'需要确认补查覆盖存在的限制。')
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE');w=guard(layer._get,db,wid)
            if db.execute("SELECT 1 FROM workspace_tasks WHERE workspace_id=? AND state='running'",(wid,)).fetchone():raise HTTPException(409,'请先停止任务。')
            if not db.execute("SELECT 1 FROM workspace_audit WHERE workspace_id=? AND kind='bounded_backfill'",(wid,)).fetchone():raise HTTPException(400,'请先明确日期执行有界补查。')
            mark=water(db);db.execute('UPDATE workspaces SET change_epoch=?,discovery_cursor=?,gap=0 WHERE id=?',(mark['epoch'],mark['high_water'],wid))
            layer.audit(db,wid,'gap_reconnected',dict(old=w['discovery_cursor'],new=mark['high_water'],note='用户确认有界补查，未声称恢复完整历史。'))
        return dict(reconnected=True)
    @app.get('/api/workspaces/{wid}/outputs/{oid}')
    def output(request:Request,wid:str,oid:str,version:int|None=None):origin(request);return guard(layer.output,wid,oid,version)
    @app.get('/api/workspaces/{wid}/outputs/{oid}/download')
    def download(request:Request,wid:str,oid:str,version:int|None=None):
        origin(request);value=guard(layer.output,wid,oid,version)
        suffix='.'+value['format'];filename=value['name'] if value['name'].endswith(suffix) else value['name']+suffix
        # Generated bytes only. No user/model path can cause filesystem reads or writes.
        text=value['body'];issues=[r for r in value['refs'] if not r.get('available') or r.get('changed')]
        if issues:
            note='导出时核对：'+ '；'.join(f'{r["source_type"]} {r["source_id"]} '+('来源已变化' if r.get('changed') else '已删除或不可用') for r in issues)+'。历史成果不等于源资料快照。'
            if value['format']=='md':text+='\n\n> '+note+'\n'
            else:
                import csv,io
                buf=io.StringIO();csv.writer(buf).writerow(['来源状态',note,'']);text+='\r\n'+buf.getvalue()
        data=text.encode('utf-8-sig' if value['format']=='csv' else 'utf-8')
        return Response(data,media_type='text/csv' if value['format']=='csv' else 'text/markdown',headers={
          'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename),'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'})
    @app.patch('/api/workspaces/{wid}/outputs/{oid}')
    async def edit(request:Request,wid:str,oid:str):
        b=await body(request);return await run_in_threadpool(guard,layer.manual_draft,wid,oid,b.get('body'),b.get('base_version'))
    @app.post('/api/workspaces/{wid}/outputs/{oid}/restore')
    async def restore(request:Request,wid:str,oid:str):
        b=await body(request);return await run_in_threadpool(guard,layer.restore,wid,oid,b.get('version'),b.get('base_version'),b.get('confirm') is True)
    @app.get('/api/workspaces/{wid}/proposals')
    def proposals(request:Request,wid:str):origin(request);return dict(proposals=guard(layer.proposals,wid))
    @app.post('/api/workspaces/{wid}/apply')
    async def apply(request:Request,wid:str):
        b=await body(request);return await run_in_threadpool(guard,layer.apply,wid,b.get('ids'),confirm=b.get('confirm') is True)
    @app.post('/api/workspaces/{wid}/reject')
    async def reject(request:Request,wid:str):b=await body(request);return guard(layer.reject,wid,b.get('ids'))
    @app.get('/api/workspaces/{wid}/history')
    def history(request:Request,wid:str):origin(request);return guard(layer.history,wid)
    def discovery():
        while not stop.wait(30):
            for w in layer.list():
                if stop.is_set():return
                try:layer.discover(w['id'])
                except Exception:pass  # Cursor transaction rolls back; next UI read surfaces the failure.
            if not stop.is_set():
                try:runner.maintain()
                except Exception:pass  # Durable pending candidates and schedule remain.
    @app.on_event('startup')
    def start():runner.recover();threading.Thread(target=discovery,daemon=True,name='workspace-discovery').start()
    @app.on_event('shutdown')
    def close():
        stop.set()
        with runner.lock:
            for c in runner.active.values():c.set()
