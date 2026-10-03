"""Local product configuration. Keys are write-only; no secret enters responses."""
import os
import json
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values,set_key
from fastapi import HTTPException,Request
from fastapi.responses import Response
from .config import ROOT

from .version import VERSION
_settings_lock=threading.Lock()
_fields=('API_BASE','API_KEY','MODEL','REPLY_ONEBOT_URL','REPLY_ONEBOT_TOKEN')


def read_product_settings(root=ROOT):
    values=dotenv_values(root/'.env')
    def value(key):return os.environ.get(key,values.get(key) or '').strip()
    return dict(api_base=value('API_BASE'),model=value('MODEL'),has_api_key=bool(value('API_KEY')),
        configured=all(value(k) for k in ('API_BASE','API_KEY','MODEL')),
        sender_url=value('REPLY_ONEBOT_URL'),has_sender_token=bool(value('REPLY_ONEBOT_TOKEN')),
        environment_overrides=[k for k in _fields if k in os.environ])


def save_product_settings(body,root=ROOT):
    if not isinstance(body,dict) or set(body)-{'api_base','model','api_key','sender_url','sender_token'}:
        raise ValueError('模型配置字段无效。')
    update={}
    for key,env in [('api_base','API_BASE'),('model','MODEL'),('api_key','API_KEY'),('sender_url','REPLY_ONEBOT_URL'),('sender_token','REPLY_ONEBOT_TOKEN')]:
        if key not in body:continue
        value=body[key]
        if not isinstance(value,str) or len(value)>4096 or any(ord(c)<32 for c in value):raise ValueError('配置中不能包含换行或控制字符。')
        value=value.strip()
        if key in ('api_key','sender_token') and not value:continue  # Blank means keep existing, never expose a mask as the key.
        if key in ('api_base','sender_url') and value:
            parsed=urlparse(value)
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('请输入完整 HTTP(S) 接口地址，不要在地址中填写密钥。')
            if key=='sender_url' and parsed.hostname not in ('127.0.0.1','localhost','::1'):
                raise ValueError('QQ 发送接口须为本机 localhost 地址。')
            value=value.rstrip('/')
        if key in ('api_base','model') and not value:raise ValueError('请填写 API 地址和模型名称。')
        if key=='model' and len(value)>200:raise ValueError('模型名称过长。')
        update[env]=value
    if not update:raise ValueError('没有需要保存的设置。')
    with _settings_lock:
        path=root/'.env'
        existing=dotenv_values(path)
        if set(update) & {'API_BASE','API_KEY','MODEL'} and not update.get('API_KEY',os.environ.get('API_KEY',existing.get('API_KEY') or '')):
            raise ValueError('请填写 API Key。')
        (root/'.tmp').mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix='settings-',suffix='.env',dir=root/'.tmp',delete=False) as f:temp=Path(f.name)
        try:
            temp.write_text(path.read_text(encoding='utf-8') if path.exists() else '',encoding='utf-8')
            for key,value in update.items():set_key(str(temp),key,value,quote_mode='always')
            os.replace(temp,path)
            # A deliberate UI save also replaces inherited values in this process
            # so subsequent turns really use the values the user just entered.
            for key,value in update.items():
                if key in os.environ:os.environ[key]=value
        finally:temp.unlink(missing_ok=True)
    return read_product_settings(root)


def install_desktop_routes(app,root=ROOT):
    def local(request,write=False):
        if request.client and request.client.host not in ('127.0.0.1','::1','testclient'):raise HTTPException(403)
        if urlparse(str(request.base_url)).hostname not in ('127.0.0.1','localhost','::1','testserver'):raise HTTPException(403)
        origin=request.headers.get('origin')
        if origin and origin!=str(request.base_url).rstrip('/'):raise HTTPException(403,'只允许本机同源操作。')
        if write and (request.headers.get('x-chatweave-ui')!='1' or request.headers.get('content-type','').split(';')[0]!='application/json'):
            raise HTTPException(403,'请通过模型设置保存。')

    @app.get('/api/desktop/status')
    def status(request:Request):
        local(request)
        from .activity import _guard,_states
        with _guard:busy=any(v['readers'] or v['deleting'] for v in _states.values())
        return dict(app='ChatWeave',product='Tulpa',version=VERSION,settings=read_product_settings(root),
                    storage=str(root),busy=busy,owned=bool(os.environ.get('CHATWEAVE_SESSION_TOKEN')))

    # The desktop service uses a new localhost port on each launch. Persist
    # this display choice beside its data, rather than relying on origin storage.
    preferences=root/'data'/'ui-preferences.json'
    @app.get('/api/ui-preferences')
    def read_preferences(request:Request):
        local(request)
        try:
            value=json.loads(preferences.read_text(encoding='utf-8')).get('show_reasoning',True)
        except (OSError,ValueError,AttributeError):value=True
        return dict(show_reasoning=value if isinstance(value,bool) else True)

    @app.put('/api/ui-preferences')
    def save_preferences(request:Request,body:dict):
        local(request,True)
        if set(body)!={'show_reasoning'} or not isinstance(body['show_reasoning'],bool):
            raise HTTPException(400,'显示设置无效。')
        with _settings_lock:
            preferences.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix='ui-prefs-',suffix='.json',dir=preferences.parent,delete=False) as f:
                temp=Path(f.name)
            try:
                temp.write_text(json.dumps(body),encoding='utf-8')
                os.replace(temp,preferences)
            finally:temp.unlink(missing_ok=True)
        return body

    @app.put('/api/desktop/settings')
    def save(request:Request,body:dict):
        local(request,True)
        try:return save_product_settings(body,root)
        except ValueError as exc:raise HTTPException(400,str(exc)) from None

    # Independent of model credentials, and shared with the native tray shell.
    desktop_preferences=root/'data'/'desktop-preferences.json'
    def preferences_value():
        result=dict(mode='',background=False)
        try:
            saved=json.loads(desktop_preferences.read_text(encoding='utf-8'))
            if saved.get('mode') in ('chat','mcp','both'):result['mode']=saved['mode']
            if type(saved.get('background')) is bool:result['background']=saved['background']
        except (OSError,ValueError,AttributeError):pass
        return result

    @app.get('/api/desktop/preferences')
    def desktop_prefs(request:Request):
        local(request)
        return preferences_value()

    @app.put('/api/desktop/preferences')
    def save_desktop_prefs(request:Request,body:dict):
        local(request,True)
        if not body or set(body)-{'mode','background'} or ('mode' in body and body['mode'] not in ('chat','mcp','both')) or ('background' in body and type(body['background']) is not bool):
            raise HTTPException(400,'启动设置无效。')
        with _settings_lock:
            result=preferences_value();result.update(body)
            desktop_preferences.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix='desktop-prefs-',suffix='.json',dir=desktop_preferences.parent,delete=False) as f:temp=Path(f.name)
            try:
                temp.write_text(json.dumps(result),encoding='utf-8');os.replace(temp,desktop_preferences)
            finally:temp.unlink(missing_ok=True)
        return result

    @app.post('/api/desktop/onebot/test')
    def test_onebot(request:Request,body:dict):
        local(request,True)
        if body:raise HTTPException(400,'检测使用已保存的 OneBot 配置。')
        from .onebot import Client,configuration,OneBotError,invalidate_availability
        config=configuration(root)
        invalidate_availability()
        if not config['url']:return dict(ok=False,message='尚未配置 OneBot；本地聊天查询仍可使用。')
        try:
            account=Client(config,timeout=3).login()
            # Invalidate cached availability after saving/testing a new endpoint.
            service=getattr(app.state,'tulpa_mcp',None)
            if service:
                with service.tools.qq_lock:service.tools.qq_at=0
            return dict(ok=True,account=account,message='已连接 QQ '+account+'。MCP 可复用此配置；请在授权连接中开启 OneBot 读取。')
        except OneBotError as exc:return dict(ok=False,message=str(exc))

    @app.get('/api/desktop/diagnostics')
    def diagnostics(request:Request):
        local(request)
        import json
        from desktop.diagnose import collect
        return Response(json.dumps(collect(root),ensure_ascii=False,indent=2),media_type='application/json',
            headers={'Content-Disposition':'attachment; filename="Tulpa-diagnostics.json"','Cache-Control':'no-store'})
