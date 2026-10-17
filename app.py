from chatlocal.config import ROOT
from chatlocal.ui import build_app
from chatlocal.appearance import THEME, CSS, JS
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
import gradio as gr
import uvicorn
import sys


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

    from chatlocal.web_frontend import install_web_ui
    return install_web_ui(app)

if __name__ == '__main__':
    uvicorn.run(create_app(),host='127.0.0.1',port=7860,access_log=False)
