"""Local file library API and a modest metadata-only reconciliation worker."""
import json
import subprocess
import sys
import threading
import time

from fastapi import Request,HTTPException
from fastapi.responses import FileResponse
from .config import ROOT
from .store import Store
from .artifacts import Artifacts,ArtifactError
from .artifact_onebot import OneBot,config,receive_event


def install_artifact_routes(app,store=None):
    store=store or Store();layer=Artifacts(store);stop=threading.Event();lock=threading.Lock()
    state=dict(running=False,result=None,error=None)
    def origin(request):
        if request.headers.get('origin') and request.headers['origin']!=str(request.base_url).rstrip('/'):
            raise HTTPException(403,'仅限本机同源页面')
        if request.headers.get('sec-fetch-site')=='cross-site':raise HTTPException(403)
    def start(fn):
        with lock:
            if state['running']:raise HTTPException(409,'文件任务正在运行')
            state.update(running=True,result=None,error=None)
        def run():
            try:state['result']=fn()
            except ArtifactError as exc:state['error']=str(exc)
            except Exception:state['error']='文件任务失败，原数据保留；请检查来源后重试。'
            finally:state['running']=False
        threading.Thread(target=run,daemon=True,name='artifact-job').start()
        return dict(started=True)
    def reconcile(cid,name):
        try:return OneBot().reconcile(store,cid,name)
        except ArtifactError as exc:
            with store.connect() as db:db.execute("UPDATE artifact_inventory SET status='error',last_attempt=?,detail=? WHERE platform='qq' AND conversation_id=?",(time.time(),str(exc),cid))
            raise
    def timer():
        if stop.wait(12):return
        while not stop.is_set():
            if config()['ARTIFACT_ONEBOT_URL'] and not state['running']:
                with store.connect() as db:
                    rows=db.execute("SELECT * FROM artifact_inventory WHERE platform='qq' AND coalesce(last_attempt,0)<?",(time.time()-3600,)).fetchall()
                for row in rows:
                    if stop.is_set():return
                    try:
                        start(lambda row=row:reconcile(row['conversation_id'],row['conversation']))
                    except HTTPException:pass
                    while state['running'] and not stop.wait(1):pass
            stop.wait(30)
    @app.on_event('startup')
    def startup():
        # Only directories the user explicitly enabled; no default group crawl.
        with store.connect() as db:db.execute('UPDATE artifact_inventory SET last_attempt=NULL')
        threading.Thread(target=timer,daemon=True,name='artifact-reconciliation').start()
    @app.on_event('shutdown')
    def shutdown():stop.set()

    @app.get('/api/artifacts/status')
    def status(request:Request):
        origin(request)
        with store.connect() as db:
            counts=dict(db.execute('SELECT platform,count(*) FROM artifact_sources GROUP BY platform'))
            cache=db.execute('SELECT count(*),coalesce(sum(CASE WHEN local_path IS NOT NULL THEN size ELSE 0 END),0) FROM artifacts').fetchone()
            inventories=[dict(r) for r in db.execute('SELECT * FROM artifact_inventory')]
            groups=[dict(r) for r in db.execute('SELECT DISTINCT platform,conversation_id,conversation FROM messages ORDER BY conversation')]
        return dict(state,counts=counts,unique_artifacts=cache[0],cache_bytes=cache[1],inventories=inventories,conversations=groups,
            onebot_configured=bool(config()['ARTIFACT_ONEBOT_URL']),
            note='文件消息随实时读取自动登记。历史附件需发现一次；元数据不会触发下载、解析或模型。'+
            ('启用的QQ目录在启动及每小时对账。' if config()['ARTIFACT_ONEBOT_URL'] else '未配置OneBot：当前QQ仅包含本地文件消息，不是完整群文件目录。'))

    @app.get('/api/artifacts')
    def search(request:Request,query:str='',platform:str='',conversation_id:str='',extension:str='',sender:str='',start:str='',end:str='',offset:int=0,body:bool=False):
        origin(request)
        try:return layer.search(dict(query=query[:600],platform=platform,conversation_id=conversation_id,extension=extension,sender=sender[:200],start=start,end=end,offset=offset,limit=20),body=body)
        except (ValueError,TypeError):raise HTTPException(400,'筛选格式无效') from None

    @app.get('/api/artifacts/{sid}/chunks')
    def chunks(request:Request,sid:int,offset:int=0):
        origin(request)
        try:return layer.chunks(sid,offset,8)
        except ArtifactError as exc:raise HTTPException(404,str(exc)) from None

    @app.get('/api/artifacts/{sid}/chunk/{chunk_id}')
    def exact_chunk(request:Request,sid:int,chunk_id:int):
        origin(request)
        try:
            source=layer.get(sid)
            with store.connect() as db:
                row=db.execute('SELECT ordinal FROM artifact_chunks WHERE id=? AND sha256=?',(chunk_id,source['sha256'])).fetchone()
            if not row:raise ArtifactError('该来源的原文片段不存在或索引已改变。')
            return layer.chunks(sid,row[0],1)
        except ArtifactError as exc:raise HTTPException(404,str(exc)) from None

    @app.get('/api/artifacts/{sid}')
    def get(request:Request,sid:int):
        origin(request)
        try:return layer.public(layer.get(sid))
        except ArtifactError as exc:raise HTTPException(404,str(exc)) from None

    @app.get('/api/artifacts/{sid}/content')
    def content(request:Request,sid:int):
        origin(request)
        try:
            row=layer.get(sid);path=layer.cache_path(row)
            if not row['local_path'] or not path.is_file():raise ArtifactError('原文件未缓存，请先在文件页按需取得。')
        except ArtifactError as exc:raise HTTPException(404,str(exc)) from None
        # Download as an attachment. Never render untrusted HTML/SVG in our origin.
        return FileResponse(path,filename=row['filename'],media_type='application/octet-stream',
            headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Cross-Origin-Resource-Policy':'same-origin'})

    @app.post('/api/artifacts/{sid}/action')
    async def action(request:Request,sid:int):
        origin(request);body=await request.json()
        if not isinstance(body,dict):raise HTTPException(400,'操作格式无效')
        action=body.get('action')
        if action not in ('prepare','materialize','locate','pin','evict'):raise HTTPException(400,'未知操作')
        if type(body.get('confirmed',False)) is not bool:raise HTTPException(400,'确认格式无效')
        def work():
            if action=='prepare':return layer.prepare(sid,confirmed=body.get('confirmed',False))
            if action=='materialize':return layer.public(layer.materialize(sid,confirmed=body.get('confirmed',False)))
            if action=='pin':return layer.pin(sid,confirmed=body.get('confirmed',False))
            return getattr(layer,action)(sid)
        return start(work)

    @app.post('/api/artifacts-discover')
    async def discover(request:Request):
        origin(request)
        def work():
            try:
                run=subprocess.run([sys.executable,str(ROOT/'scripts/discover_artifacts.py')],cwd=ROOT,capture_output=True,
                    encoding='utf-8',timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if run.returncode:raise ArtifactError('本地文件消息发现失败，已登记的来源保留。')
                return json.loads(run.stdout)
            except subprocess.TimeoutExpired:raise ArtifactError('发现附件超过180秒；可稍后重试，已有记录保留。') from None
        return start(work)

    @app.post('/api/artifacts-inventory')
    async def inventory(request:Request):
        origin(request);body=await request.json();cid=body.get('conversation_id')
        with store.connect() as db:
            row=db.execute("SELECT conversation FROM messages WHERE platform='qq' AND conversation_type='group' AND conversation_id=? LIMIT 1",(cid,)).fetchone()
            if not row:raise HTTPException(400,'请先选择已知QQ群')
            db.execute("INSERT OR IGNORE INTO artifact_inventory(platform,conversation_id,conversation) VALUES('qq',?,?)",(cid,row[0]))
        return start(lambda:reconcile(cid,row[0]))

    @app.post('/api/artifacts-onebot-event')
    async def event(request:Request):
        if int(request.headers.get('content-length','0'))>65536:raise HTTPException(413)
        body=bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body)>65536:raise HTTPException(413)
        try:return receive_event(store,bytes(body),request.headers.get('x-signature'))
        except (ArtifactError,ValueError,KeyError,TypeError):raise HTTPException(403,'事件无效') from None
