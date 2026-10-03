"""Local jobs and small Watch Card UI API. No remote delivery or message sending."""
import threading
import time
import uuid
import json
from fastapi import HTTPException,Request
from .store import Store
from .sync import sync_messages,status,ingestion_lock
from .agent_tools import LABELS
from .render import message_html
from .watch_view import entry_view


def install_watch_routes(app,store=None,*,data_only=False):
    store=store or Store()
    if not data_only:
        from .watches import Watches
        watches=Watches(store)
    jobs={};cancellations={};guard=threading.RLock();schedule=dict(minutes=0,next_at=None,load_stickers=False)
    stopped=threading.Event()

    def same_origin(request):
        if request.headers.get('sec-fetch-site')=='cross-site':raise HTTPException(403)
        origin=request.headers.get('origin')
        if origin and origin!=str(request.base_url).rstrip('/'):raise HTTPException(403,'仅允许本机同源页面')

    def public_card(card):
        ids=set(card['evidence_ids'])
        for claim in card['baseline']+card['uncertainties']+card['last_result'].get('changes',[])+card['last_result'].get('uncertainties',[]):
            ids.update(claim['evidence_ids']);ids.update(claim.get('attachment_ids',[]))
        card=dict(card)
        # Local pending counts do not trigger an Agent or model request.
        counts=watches.message_counts(card)
        card.update({k:counts[k] for k in ('pending_messages','trigger_messages')})
        card['evidence_html']={str(mid):message_html(message) for mid in sorted(ids) if (message:=store.message(mid))}
        return card

    def running_jobs():
        return [job for job in jobs.values() if job['status']=='running']

    def idle(kind=None):
        # A read-only follow-up and an append-only sync can coexist. Checking
        # baselines, changing scope and deleting still require exclusive jobs.
        allowed={'sync':{'question'},'read':{'question'},'media-refresh':{'question'},'question':{'sync','read','media-refresh'}}.get(kind,set())
        if any(job['kind'] not in allowed for job in running_jobs()):
            raise ValueError('当前操作与运行中的任务冲突，请等待完成。')

    def start(work,card_id=None,kind='sync',trigger='manual',cancelled=None):
        with guard:
            idle(kind)
            job_id=uuid.uuid4().hex
            job=dict(id=job_id,card_id=card_id,kind=kind,trigger=trigger,status='running',events=[],result=None,error=None);jobs[job_id]=job
            if cancelled:cancellations[job_id]=cancelled
            for old in list(jobs)[:-20]:
                if jobs[old]['status']!='running':jobs.pop(old,None);cancellations.pop(old,None)
        def progress(event):
            if isinstance(event,str):text=event
            elif event.get('type')=='import_progress':
                job['progress']=event
                text=event.get('text','')
            elif event['type']=='tool_start':text=LABELS.get(event['name'],event['name'])
            elif event['type']=='model_start':text=f"Agent 正在分析（第 {event['request']} 次请求）"
            elif event['type']=='tool_end':
                text=event['result'].get('error') or '查询完成'
            else:text=event.get('text','')
            if text:job['events']=(job['events']+[text])[-100:]
        def run():
            try:
                job['result']=work(progress);job['status']='completed'
                if kind=='read' and job['result'].get('status') in ('error','partial'):
                    job['status']='error';job['error']=job['result']['summary']
                if kind=='sync' and isinstance(job['result'],list):
                    failed=[r for r in job['result'] if r.get('status')!='ok']
                    if failed:
                        job['status']='error'
                        job['error']='；'.join(('QQ' if r['platform']=='qq' else '微信')+'：'+r.get('detail','刷新未完成') for r in failed)
            except Exception as exc:
                job['error']=str(exc) if isinstance(exc,ValueError) else '操作未完成；同步/关注卡进度保留，请重试。'
                job['status']='error'
            finally:cancellations.pop(job_id,None)
        threading.Thread(target=run,daemon=True,name='watch-job').start()
        return dict(job_id=job_id,card_id=card_id,kind=kind)

    from .data_routes import install_data_routes
    install_data_routes(app,store,start,idle,same_origin)
    from .live import install_live_routes
    live=install_live_routes(app,store,same_origin)

    if data_only:
        @app.get('/api/refresh-status')
        def data_refresh_status(request:Request):
            same_origin(request)
            with guard:running=[{k:j[k] for k in ('id','kind','card_id','status','trigger')} for j in running_jobs()]
            return dict(platforms=status(store),auto_refresh=dict(schedule),running_jobs=running,running_job=running[0]['id'] if running else None)

        @app.get('/api/watch-jobs/{job_id}')
        def data_job(request:Request,job_id:str):
            same_origin(request)
            with guard:
                if job_id not in jobs:raise HTTPException(404,'任务不存在，已入库内容仍保留。')
                return dict(jobs[job_id])

        @app.post('/api/watch-jobs/{job_id}/stop')
        def data_stop(request:Request,job_id:str):
            same_origin(request)
            with guard:
                if job_id in cancellations:cancellations[job_id].set()
            return dict(ok=True)

        @app.post('/api/sync')
        def data_sync(request:Request,body:dict):
            same_origin(request);cancelled=threading.Event()
            try:return start(lambda progress:sync_messages(store,body.get('platforms'),load_stickers=body.get('load_stickers',False),progress=progress,cancelled=cancelled),cancelled=cancelled)
            except ValueError as exc:raise HTTPException(409,str(exc)) from None

        @app.post('/api/auto-refresh')
        def data_schedule(request:Request,body:dict):
            same_origin(request);minutes=body.get('minutes')
            if type(minutes) is not int or minutes not in (0,5,15,30,60,360,720,1440,4320,10080) or type(body.get('load_stickers',False)) is not bool:raise HTTPException(400,'自动刷新设置无效')
            schedule.update(minutes=minutes,next_at=time.time()+minutes*60 if minutes else None,load_stickers=body.get('load_stickers',False))
            return schedule

        @app.post('/api/sync/reconnect')
        def data_reconnect(request:Request,body:dict):
            same_origin(request);platform=body.get('platform')
            if platform not in ('qq','wechat'):raise HTTPException(400,'无效平台')
            with ingestion_lock(),store.connect() as db:
                db.execute("UPDATE sync_state SET checkpoint='{}',status='never',detail='等待接续已有快照' WHERE platform=?",(platform,))
                db.execute("UPDATE live_state SET checkpoint='{}' WHERE platform=?",(platform,))
            return dict(ok=True)

        def data_timer():
            while not stopped.wait(5):
                try:
                    with guard:
                        if schedule['next_at'] and time.time()>=schedule['next_at']:
                            stickers=schedule['load_stickers']
                            start(lambda progress:sync_messages(store,load_stickers=stickers,progress=progress),trigger='scheduled')
                            schedule['next_at']=time.time()+schedule['minutes']*60
                except ValueError:continue

        @app.on_event('startup')
        def data_startup():
            with store.connect() as db:db.execute("UPDATE sync_state SET status='error',detail='上次读取中断，进度保留' WHERE status='running'")
            live.start();threading.Thread(target=data_timer,daemon=True,name='data-refresh-timer').start()

        @app.on_event('shutdown')
        def data_shutdown():stopped.set();live.close()
        return

    def start_check(card_id,body=None,*,refresh=True,trigger='manual'):
        body=body or {};card=watches.get(card_id);cancelled=threading.Event()
        return start(lambda progress:public_card(watches.check(card_id,refresh=refresh,
            load_stickers=body.get('load_stickers',bool(card['load_stickers'])),profile=body.get('profile',card['profile']),
            progress=progress,trigger=trigger,cancelled=cancelled)),card_id,kind='check',trigger=trigger,cancelled=cancelled)

    @app.get('/api/watch-cards')
    def cards(request:Request):
        same_origin(request)
        return dict(cards=[public_card(c) for c in watches.list()])

    @app.get('/api/watch-cards/{card_id}/thread')
    def thread(request:Request,card_id:str,before:int|None=None):
        same_origin(request)
        try:
            if before is not None and before<1:raise ValueError('历史位置无效')
            card=watches.get(card_id);rows,older=watches.timeline(card_id,before)
            return dict(card=public_card(card),entries=[entry_view(store,row) for row in rows],older=older)
        except ValueError as exc:raise HTTPException(404,str(exc)) from None

    @app.get('/api/refresh-status')
    def refresh_status(request:Request):
        same_origin(request)
        with guard:running=[{k:j[k] for k in ('id','kind','card_id','status','trigger')} for j in running_jobs()]
        return dict(platforms=status(store),auto_refresh=dict(schedule),running_job=running[0]['id'] if running else None,running_jobs=running)

    @app.get('/api/watch-jobs/{job_id}')
    def job(request:Request,job_id:str):
        same_origin(request)
        with guard:
            if job_id not in jobs:raise HTTPException(404,'任务不存在；请查看关注卡保存结果')
            return dict(jobs[job_id])

    @app.post('/api/sync')
    def sync(request:Request,body:dict):
        same_origin(request)
        cancelled=threading.Event()
        try:return start(lambda progress:sync_messages(store,body.get('platforms'),load_stickers=body.get('load_stickers',False),progress=progress,cancelled=cancelled),cancelled=cancelled)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None

    @app.post('/api/watch-cards')
    def create(request:Request,body:dict):
        same_origin(request)
        try:
            with guard:
                idle()
                card=watches.create(body.get('title'),body.get('watch_query'),body.get('platforms'),body.get('conversations',[]),body.get('seed_ids',[]),
                    schedule_minutes=body.get('schedule_minutes',0),profile=body.get('profile','deep'),load_stickers=body.get('load_stickers',False),
                    message_threshold=body.get('message_threshold',0))
                return start_check(card['id'],refresh=False)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None

    @app.put('/api/watch-cards/{card_id}')
    def update(request:Request,card_id:str,body:dict):
        same_origin(request)
        try:
            with guard:
                idle()
                card=watches.update(card_id,body.get('title'),body.get('watch_query'),body.get('platforms'),body.get('conversations',[]),
                    schedule_minutes=body.get('schedule_minutes',0),profile=body.get('profile','deep'),load_stickers=body.get('load_stickers',False),
                    message_threshold=body.get('message_threshold',watches.get(card_id)['message_threshold']))
                return public_card(card)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None

    @app.post('/api/watch-cards/{card_id}/ask')
    def ask(request:Request,card_id:str,body:dict):
        same_origin(request)
        try:
            card=watches.get(card_id);cancelled=threading.Event()
            return start(lambda progress:public_card(watches.ask(card_id,body.get('question'),profile=body.get('profile',card['profile']),
                vision_mode=body.get('vision_mode'),progress=progress,cancelled=cancelled)),card_id,kind='question',cancelled=cancelled)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None

    @app.post('/api/watch-jobs/{job_id}/stop')
    def stop(request:Request,job_id:str):
        same_origin(request)
        with guard:
            if job_id not in cancellations:raise HTTPException(409,'任务已结束或当前同步步骤无法取消')
            cancellations[job_id].set()
        return dict(ok=True)

    @app.post('/api/watch-cards/{card_id}/check')
    def check(request:Request,card_id:str,body:dict):
        same_origin(request)
        try:
            return start_check(card_id,body)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None

    @app.delete('/api/watch-cards/{card_id}')
    def delete(request:Request,card_id:str):
        same_origin(request)
        try:
            # Serialize with job creation too: a queued check must not recreate
            # results after the card has been removed.
            with guard:
                idle()
                removed=watches.delete(card_id)
                for job_id,job in list(jobs.items()):
                    result=job.get('result')
                    if job.get('card_id')==card_id or isinstance(result,dict) and result.get('id')==card_id:
                        jobs.pop(job_id)
            return dict(ok=True,deleted=removed,id=card_id)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None

    @app.post('/api/watch-cards/{card_id}/pause')
    def pause(request:Request,card_id:str,body:dict):
        same_origin(request)
        if type(body.get('paused')) is not bool:raise HTTPException(400,'暂停状态无效')
        try:
            with guard:idle();watches.pause(card_id,body['paused'])
            return dict(ok=True)
        except ValueError as exc:raise HTTPException(400,str(exc)) from None

    @app.post('/api/auto-refresh')
    def auto_refresh(request:Request,body:dict):
        same_origin(request)
        minutes=body.get('minutes')
        if type(minutes) is not int or minutes not in (0,5,15,30,60,360,720,1440,4320,10080) or type(body.get('load_stickers',False)) is not bool:raise HTTPException(400,'自动刷新设置无效')
        schedule.update(minutes=minutes,next_at=time.time()+minutes*60 if minutes else None,load_stickers=body.get('load_stickers',False))
        return schedule

    @app.post('/api/sync/reconnect')
    def reconnect(request:Request,body:dict):
        same_origin(request)
        platform=body.get('platform')
        if platform not in ('qq','wechat'):raise HTTPException(400,'无效平台')
        try:
            with ingestion_lock(),store.connect() as db:
                db.execute("UPDATE sync_state SET checkpoint='{}',status='never',detail='等待接续最近成功导入的快照；旧消息保留' WHERE platform=?",(platform,))
                db.execute("UPDATE live_state SET checkpoint='{}' WHERE platform=?",(platform,))
            return dict(ok=True)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None

    def timer():
        while not stopped.wait(5):
            try:
                with guard:
                    idle()
                    due=watches.due()
                    if due and (reason:=watches.claim_due(due[0])):
                        start_check(due[0],trigger=reason)
                    elif schedule['next_at'] and time.time()>=schedule['next_at']:
                        stickers=schedule['load_stickers']
                        start(lambda progress:sync_messages(store,load_stickers=stickers,progress=progress),trigger='scheduled')
                        schedule['next_at']=time.time()+schedule['minutes']*60
            except ValueError:continue

    @app.on_event('startup')
    def startup():
        with store.connect() as db:
            db.execute("UPDATE sync_state SET status='error',detail='上次刷新被中断，进度保留，请重试' WHERE status='running'")
            db.execute("UPDATE watch_cards SET error='上次检查被中断，结论与进度保留，请重试' WHERE id IN (SELECT card_id FROM watch_runs WHERE status='running' AND kind='check')")
            for row in db.execute("SELECT id,result FROM watch_runs WHERE status='running'").fetchall():
                result=json.loads(row['result']);result['error']='上次回答被中断，可以重试；之前成功的回答仍保留。'
                db.execute("UPDATE watch_runs SET status='error',result=? WHERE id=?",(json.dumps(result,ensure_ascii=False),row['id']))
        threading.Thread(target=timer,daemon=True,name='message-refresh-timer').start()
        live.start()

    @app.on_event('shutdown')
    def shutdown():stopped.set();live.close()
