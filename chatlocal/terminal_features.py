"""Full application services for the terminal, independent of Gradio."""
import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlparse

from fastapi import HTTPException

from .config import ROOT, settings
from .platform_lock import file_lock


def model_settings(root):
    from dotenv import dotenv_values
    values=dotenv_values(Path(root)/'.env')
    defaults={'API_BASE':'https://api.deepseek.com','API_KEY':'','MODEL':'deepseek-flash','AGENT_REASONING_EFFORT':'high'}
    return {k:os.environ.get(k,values.get(k) or v).strip() for k,v in defaults.items()}


def save_model(root,body):
    from dotenv import set_key
    from .snowluma_secure import atomic_bytes
    if set(body)-{'api_base','model','api_key','reasoning_effort','clear_api_key'}:
        raise ValueError('未知模型设置字段。')
    if type(body.get('clear_api_key',False)) is not bool:raise ValueError('清除密钥开关无效。')
    for k in ('api_base','model','api_key','reasoning_effort'):
        if k in body and (not isinstance(body[k],str) or '\n' in body[k] or '\r' in body[k] or len(body[k])>4096):
            raise ValueError('模型设置必须是单行文本。')
    root=Path(root)
    with file_lock(root/'.tmp/model-settings.lock',blocking=True):
        cfg=model_settings(root)
        mapping={'api_base':'API_BASE','model':'MODEL','reasoning_effort':'AGENT_REASONING_EFFORT'}
        updates={v:body[k].strip() for k,v in mapping.items() if k in body}
        if body.get('api_key'):updates['API_KEY']=body['api_key'].strip()
        if body.get('clear_api_key'):updates['API_KEY']=''
        cfg.update(updates)
        base=urlparse(cfg['API_BASE'])
        if (not base.hostname or base.username or base.password or base.query or base.fragment
                or not (base.scheme=='https' or base.scheme=='http' and base.hostname in ('127.0.0.1','localhost','::1'))):
            raise ValueError('模型地址必须是 HTTPS，或本机 HTTP 地址。')
        if not cfg['MODEL'] or cfg['AGENT_REASONING_EFFORT'] not in ('none','low','high','max'):
            raise ValueError('模型名称或推理强度无效。')
        # dotenv preserves unrelated reader, voice and OneBot configuration.
        path=root/'.env'
        if path.is_symlink():raise ValueError('模型配置不能是符号链接。')
        temp=root/'.tmp/model-settings.env'
        atomic_bytes(temp,path.read_bytes() if path.exists() else b'')
        try:
            for key,value in updates.items():set_key(str(temp),key,value,quote_mode='always')
            atomic_bytes(path,temp.read_bytes())
            path.chmod(0o600)
        finally:temp.unlink(missing_ok=True)
        # Existing explicit environment overrides remain authoritative.
        return model_public(root)


def model_public(root):
    cfg=model_settings(root)
    return dict(api_base=cfg['API_BASE'],model=cfg['MODEL'],has_api_key=bool(cfg['API_KEY']),
                reasoning_effort=cfg['AGENT_REASONING_EFFORT'])


class AgentDialogues:
    def __init__(self,store,root):
        from .agent_sessions import Sessions
        self.store,self.root=store,Path(root)
        self.sessions=Sessions(store.path.parent/'agent-sessions.sqlite3')
        self.lock=threading.RLock();self.running={};self.closed=False
        with self.sessions.connect() as db:
            db.execute("UPDATE turns SET status='interrupted' WHERE status='running'")

    def listing(self):
        with self.sessions.connect() as db:
            return dict(items=[dict(r) for r in db.execute('SELECT * FROM sessions ORDER BY updated DESC LIMIT 100')])

    def history(self,sid):
        with self.sessions.connect() as db:
            if not db.execute('SELECT 1 FROM sessions WHERE id=?',(sid,)).fetchone():raise HTTPException(404,'对话不存在。')
        return dict(id=sid,turns=self.sessions.history(sid))

    def delete(self,sid):
        with self.lock:
            if any(v['session']==sid for v in self.running.values()):raise HTTPException(409,'请先停止本轮并等待退出。')
            self.history(sid);self.sessions.delete(sid)
        return dict(ok=True)

    def ask(self,sid,body):
        from .retrieval import make_plan
        from .agent_profiles import profile_config
        from .vision import with_vision_mode
        allowed={'question','platforms','conversations','start','end','profile','vision','include_reasoning'}
        if set(body)-allowed:raise ValueError('未知提问选项。')
        question=body.get('question')
        if not isinstance(question,str):raise ValueError('请填写问题。')
        platforms=body.get('platforms',['qq','wechat']);conversations=body.get('conversations',[])
        if not isinstance(platforms,list) or not platforms or any(p not in ('qq','wechat') for p in platforms):raise ValueError('请选择平台。')
        if not isinstance(conversations,list) or any(not isinstance(c,str) for c in conversations) or len(conversations)>200:raise ValueError('会话范围无效。')
        if type(body.get('include_reasoning',False)) is not bool:raise ValueError('推理显示开关无效。')
        plan=make_plan(question,platforms,conversations,body.get('start',''),body.get('end',''))
        cfg=with_vision_mode(profile_config(dict(settings(),**model_settings(self.root)),body.get('profile','standard')),body.get('vision','ocr'))
        with self.lock:
            if self.closed:raise HTTPException(503,'服务正在退出。')
            if any(v['session']==sid for v in self.running.values()):raise HTTPException(409,'当前对话正在回答。')
            if len(self.running)>=4:raise HTTPException(409,'同时运行的对话已达上限。')
            self.history(sid);memory=self.sessions.memory(sid)
            tid=self.sessions.start(sid,question.strip(),body)
            cancel=threading.Event()
            worker=threading.Thread(target=self._run,args=(sid,tid,question,plan,memory,cfg,body,cancel),daemon=True,name='terminal-agent')
            self.running[tid]=dict(session=sid,cancel=cancel,thread=worker);worker.start()
        return dict(turn_id=tid)

    def _run(self,sid,tid,question,plan,memory,cfg,options,cancel):
        events=[];record=dict(events=events);status='interrupted';last=0
        try:
            from .agent import run_agent
            from .group_admin import GroupAdmin
            management=dict(session_id=sid,turn_id=tid,policy=GroupAdmin(self.store).policy(sid))
            for event in run_agent(self.store,plan,question,memory,cancel,config=cfg,
                    include_reasoning=options.get('include_reasoning',False),management_context=management,show_unverified=True):
                if event['type']=='done':
                    record=event['record'];status=event['status']
                else:
                    events.append(event);events[:]=events[-200:]
                    if time.monotonic()-last>.5:
                        self.sessions.finish(tid,'running',dict(events=events));last=time.monotonic()
            record.update(profile=options.get('profile','standard'),vision_mode=options.get('vision','ocr'))
        except Exception as exc:
            status='failed'
            # Never put provider exceptions containing headers/keys in history.
            record=dict(events=events,result=dict(error='本轮未完成（'+type(exc).__name__+'）。请检查模型设置及组件状态。'))
        finally:
            self.sessions.finish(tid,status,record)
            with self.lock:self.running.pop(tid,None)

    def stop(self,tid):
        with self.lock:
            running=self.running.get(tid)
            if running:running['cancel'].set()
        return dict(ok=True)

    def close(self):
        with self.lock:
            self.closed=True;running=list(self.running.values())
            for item in running:item['cancel'].set()
        for item in running:item['thread'].join(timeout=2)


def install_features(app,store,root):
    from .linux_components import ComponentJobs, component_status
    from .native_sources import source_settings, save_source
    from .linux_service import service_status
    dialogues=AgentDialogues(store,root);jobs=ComponentJobs(root)
    app.state.terminal_dialogues=dialogues

    @app.get('/api/terminal/model')
    def model():return model_public(root)
    @app.put('/api/terminal/model')
    def set_model(body:dict):return save_model(root,body)
    @app.get('/api/terminal/agent/sessions')
    def sessions():return dialogues.listing()
    @app.post('/api/terminal/agent/sessions')
    def create(body:dict):
        if body:raise ValueError('新对话不接受额外参数。')
        return dict(id=dialogues.sessions.create())
    @app.get('/api/terminal/agent/sessions/{sid}')
    def history(sid:str):return dialogues.history(sid)
    @app.delete('/api/terminal/agent/sessions/{sid}')
    def delete(sid:str):return dialogues.delete(sid)
    @app.post('/api/terminal/agent/sessions/{sid}/ask')
    def ask(sid:str,body:dict):return dialogues.ask(sid,body)
    @app.post('/api/terminal/agent/turns/{tid}/stop')
    def stop(tid:str):return dialogues.stop(tid)
    @app.get('/api/terminal/components')
    def components():return component_status(root)
    @app.get('/api/terminal/native')
    def native():
        from .snowluma_history import source_info
        result=source_settings(root)
        result['platforms']['qq'].update(source_info(root))
        return result
    @app.put('/api/terminal/native/{platform}')
    def configure_native(platform:str,body:dict):
        previous=source_settings(root)['platforms'].get(platform)
        result=save_source(platform,body,root)
        if previous!=result['platforms'][platform]:
            # An old baseline must not keep listening to the previous source
            # after a directory/account change. A new history import establishes
            # the source; the user can then re-enable live listening.
            live=getattr(app.state,'tulpa_live',None)
            if live:live.configure({platform:'off'});live.reset_worker(platform)
        return result
    @app.post('/api/terminal/native/install')
    def readers():return jobs.start('readers')
    @app.post('/api/terminal/native/{platform}/extract')
    def extract(platform:str,body:dict):return jobs.extract(platform,body)
    @app.post('/api/terminal/components/{name}/install')
    def install(name:str):return jobs.start(name)
    @app.get('/api/terminal/jobs/{jid}')
    def job(jid:str):return jobs.status(jid)
    @app.get('/api/terminal/service')
    def service():return service_status(root)
    @app.get('/api/agent-presets')
    def presets():
        from .agent_profiles import profile_catalog
        return profile_catalog()
    @app.get('/api/read-options')
    def read_options():
        from .read_options import READ_LIMITS
        return READ_LIMITS
    @app.get('/api/vision-options')
    def vision_options():
        from .vision import with_vision_mode
        try:with_vision_mode(dict(settings(),**model_settings(root)),'native')
        except ValueError as exc:return dict(default='ocr',native_available=False,note=str(exc))
        return dict(default='ocr',native_available=True,note='按需将选定图片发送给当前模型。')
    @app.on_event('shutdown')
    def shutdown():dialogues.close();jobs.close()
