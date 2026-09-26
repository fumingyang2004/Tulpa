"""Same-origin, message-addressed local voice actions; jobs never block the UI."""
import json
import subprocess
import sys
import threading
from fastapi import Request,HTTPException
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from .config import ROOT,local_path
from .store import Store
from .voice import service,public,config

def install_voice_routes(app,store=None):
    store=store or Store();worker=service(store);discovery=dict(running=False,result=None,error=None);lock=threading.Lock()
    def origin(request):
        if request.headers.get('origin') and request.headers['origin']!=str(request.base_url).rstrip('/') or request.headers.get('sec-fetch-site')=='cross-site':raise HTTPException(403,'仅限本机同源页面')
    @app.on_event('startup')
    def startup():worker.start()
    @app.on_event('shutdown')
    def shutdown():worker.close()
    @app.get('/api/voice/settings')
    def settings(request:Request):
        origin(request)
        with store.connect() as db:
            strategy=db.execute('SELECT strategy FROM voice_settings WHERE id=1').fetchone()[0]
            counts=dict(db.execute('SELECT status,count(*) FROM voice_sources GROUP BY status'))
            queued=db.execute('SELECT count(*) FROM voice_jobs').fetchone()[0]
        try:
            cfg=config();ready=cfg['model'].is_file() and cfg['executable'].is_file()
            options=dict(model=cfg['model'].name,threads=cfg['threads'],max_seconds=cfg['max_seconds'],max_bytes=cfg['max_bytes'])
        except (OSError,ValueError):ready=False;options={}
        return dict(strategy=strategy,counts=counts,queued=queued,local_asr_ready=ready,discovery=discovery,**options)
    @app.put('/api/voice/settings')
    async def save(request:Request):
        origin(request);body=await request.json()
        if not isinstance(body,dict) or body.get('strategy') not in ('lazy','new','manual-backfill'):raise HTTPException(400,'未知转写策略')
        def write():
            with store.connect() as db:db.execute('UPDATE voice_settings SET strategy=? WHERE id=1',(body['strategy'],))
            return settings(request)
        return await run_in_threadpool(write)
    @app.post('/api/voice/backfill')
    async def backfill(request:Request):
        origin(request);body=await request.json()
        if not isinstance(body,dict):raise HTTPException(400)
        platform=body.get('platform');start=body.get('start');end=body.get('end');cids=body.get('conversations')
        from .import_scope import bounds
        try:
            lo,hi=bounds(start,end)
            if platform not in ('qq','wechat') or lo is None or hi is None or not 0<hi-lo<=366*86400000:raise ValueError()
            if cids is not None and (not isinstance(cids,list) or len(cids)>200 or any(not isinstance(c,str) or len(c)>300 for c in cids)):raise ValueError()
            if type(body.get('transcribe',False)) is not bool:raise ValueError()
        except (ValueError,TypeError):raise HTTPException(400,'请填写平台、会话范围和一年以内的起止日期。') from None
        with lock:
            if discovery['running']:raise HTTPException(409,'语音发现正在运行')
            discovery.update(running=True,result=None,error=None)
        def run():
            try:
                command=[sys.executable,str(ROOT/'scripts/discover_voice.py'),'--platform',platform,'--start',start,'--end',end]
                for cid in cids or []:command+=['--conversation',cid]
                result=subprocess.run(command,cwd=ROOT,capture_output=True,encoding='utf-8',timeout=120,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if result.returncode:raise ValueError()
                discovery['result']=json.loads(result.stdout)
                if body.get('transcribe'):
                    from .import_scope import get_scope
                    scope=get_scope(store)[platform]
                    selected=cids or scope['conversations'];where='m.platform=? AND m.timestamp>=? AND m.timestamp<?';args=[platform,lo,hi]
                    if not scope['enabled']:raise ValueError()
                    if selected is not None:
                        if scope['conversations'] is not None:selected=[c for c in selected if c in scope['conversations']]
                        where+=' AND m.conversation_id IN ('+','.join('?' for _ in selected)+')';args+=selected
                    with store.connect() as db:
                        ids=[r[0] for r in db.execute(f"SELECT m.id FROM messages m JOIN voice_sources v ON v.message_id=m.id WHERE {where} AND v.status NOT IN ('ready','queued','running') ORDER BY m.timestamp DESC LIMIT 100",args)]
                    for mid in ids:worker.enqueue(mid)
                    discovery['result'].update(enqueued=len(ids),queue_limit=100)
            except Exception:discovery['error']='语音发现失败或超时；请检查已启用平台、范围和本地读取快照后重试。已有消息保留。'
            finally:discovery['running']=False
        threading.Thread(target=run,daemon=True,name='voice-discovery').start()
        return dict(started=True)
    @app.get('/api/voice/{mid}')
    def get(request:Request,mid:int):
        origin(request);result=public(store,mid)
        if result is None:raise HTTPException(404,'原语音消息已删除或不存在')
        return result
    @app.post('/api/voice/{mid}')
    async def action(request:Request,mid:int):
        origin(request);body=await request.json()
        if not isinstance(body,dict) or type(body.get('retry',False)) is not bool:raise HTTPException(400)
        try:return await run_in_threadpool(worker.enqueue,mid,body.get('action','transcribe'),body.get('retry',False))
        except ValueError as exc:raise HTTPException(400,str(exc)) from None
    @app.get('/api/voice/{mid}/audio')
    def audio(request:Request,mid:int):
        origin(request)
        with store.connect() as db:row=db.execute('SELECT wav_path FROM voice_sources WHERE message_id=?',(mid,)).fetchone()
        if not row or not row[0]:raise HTTPException(404,'音频尚未准备')
        path=local_path(row[0])
        if not path.is_relative_to(store.path.parent/'voice/audio') or not path.is_file():raise HTTPException(404,'音频缓存已清理')
        return FileResponse(path,media_type='audio/wav',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Cross-Origin-Resource-Policy':'same-origin'})
