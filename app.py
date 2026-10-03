from chatlocal.config import ROOT
from chatlocal.ui import build_app
from chatlocal.appearance import THEME, CSS, JS
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
import gradio as gr
import uvicorn


def create_app():
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    from chatlocal.chat_view import install_chat_routes
    install_chat_routes(app)
    from chatlocal.reply_routes import install_reply_routes
    install_reply_routes(app)
    from chatlocal.group_admin_routes import install_group_admin_routes
    install_group_admin_routes(app)
    from chatlocal.tulpa_routes import install_tulpa_routes
    install_tulpa_routes(app)
    from chatlocal.watch_routes import install_watch_routes
    install_watch_routes(app)
    from chatlocal.artifact_routes import install_artifact_routes
    install_artifact_routes(app)
    from chatlocal.voice_routes import install_voice_routes
    install_voice_routes(app)
    from chatlocal.investigation_routes import install_investigation_routes
    install_investigation_routes(app)
    from chatlocal.workspace_routes import install_workspace_routes
    install_workspace_routes(app)
    from chatlocal.desktop_routes import install_desktop_routes
    install_desktop_routes(app)
    from chatlocal.mcp_routes import install_mcp_routes
    install_mcp_routes(app)
    web=ROOT/'web'

    @app.get('/api/agent-presets')
    def agent_presets():
        from chatlocal.agent_profiles import profile_catalog
        return profile_catalog()

    @app.get('/api/read-options')
    def client_read_options():
        from chatlocal.read_options import READ_LIMITS
        return READ_LIMITS

    @app.get('/api/vision-options')
    def vision_options():
        from chatlocal.config import settings
        from chatlocal.vision import with_vision_mode
        try:with_vision_mode(settings(),'native')
        except ValueError as exc:return dict(default='ocr',native_available=False,note=str(exc))
        return dict(default='ocr',native_available=True,note='开启后仅在按需看图时，将指定图片和附近少量消息发送给当前 DeepSeek API；关闭使用本地 OCR。')

    @app.get('/')
    def index(request:Request):
        headers={
            'Cache-Control':'no-store',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            'Referrer-Policy':'no-referrer',
        }
        if request.query_params.get('desktop')=='1':
            page=(web/'index.html').read_text(encoding='utf-8').replace('<html lang="zh-CN">','<html lang="zh-CN" data-desktop="true">')
            return HTMLResponse(page,headers=headers)
        return FileResponse(web/'index.html',headers=headers)

    @app.get('/ui/{name}')
    def asset(name:str):
        if name not in ('app.css','app.js','chat.js','reply.js','group-admin.js','watch.js','data.js','live.js','artifacts.js','voice.js','collections.js','workspaces.js','desktop.css','desktop.js','tulpa-logo.png','tulpa.js','tulpa.css','mcp.js','mcp.css'):
            raise HTTPException(404)
        return FileResponse(web/name,headers={'Cache-Control':'no-cache'})

    # The native chat page uses the existing, tested Gradio queue callbacks.
    # Keep the old controls available as an advanced view, with the same data
    # and file-access boundaries. No second agent or persistence implementation.
    return gr.mount_gradio_app(app,build_app().queue(max_size=8),path='/legacy',
        server_name='127.0.0.1',server_port=7860,show_error=False,
        enable_monitoring=False,footer_links=[],ssr_mode=False,
        theme=THEME,css=CSS,js=JS,
        blocked_paths=[str(ROOT/p) for p in ('.env','.git','.tmp','data','imports','tools','reports/private')],
        max_file_size='100mb')

if __name__ == '__main__':
    uvicorn.run(create_app(),host='127.0.0.1',port=7860,access_log=False)
