"""Shared browser UI; management authentication is installed by the host app."""
from pathlib import Path
import sys
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from .config import ROOT


def install_web_ui(app, *, root=ROOT, store=None, legacy=True):
    root = Path(root)
    web = root / 'web' / 'desktop'
    @app.get('/')
    def index(request:Request):
        headers={
            'Cache-Control':'no-store',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            'Referrer-Policy':'no-referrer',
        }
        if request.query_params.get('desktop')=='1':
            page=(web/'index.html').read_text(encoding='utf-8').replace('<html lang="zh-CN">','<html lang="zh-CN" data-desktop="true">')
            if sys.platform == 'darwin':
                page=page.replace('<html lang="zh-CN" data-desktop="true">','<html lang="zh-CN" data-desktop="true" data-platform="macos">')
            return HTMLResponse(page,headers=headers)
        return FileResponse(web/'index.html',headers=headers)

    @app.get('/ui/{name}')
    def asset(name:str):
        if name not in ('app.css','app.js','chat.js','reply.js','group-admin.js','watch.js','data.js','live.js','artifacts.js','voice.js','collections.js','workspaces.js','desktop.css','desktop.js','snowluma.js','snowluma-macos.js','tulpa-logo.png','tulpa.js','tulpa.css','mcp.js','mcp.css'):
            raise HTTPException(404)
        path = web/name
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path,headers={'Cache-Control':'no-cache'})

    if not legacy:
        return app
    import gradio as gr
    from .ui import build_app
    from .appearance import THEME, CSS, JS
    # The native chat page uses the existing, tested Gradio queue callbacks.
    # Keep the old controls available as an advanced view, with the same data
    # and file-access boundaries. No second agent or persistence implementation.
    return gr.mount_gradio_app(app,build_app(store=store).queue(max_size=8),path='/legacy',
        server_name='127.0.0.1',server_port=7860,show_error=False,
        enable_monitoring=False,footer_links=[],ssr_mode=False,
        theme=THEME,css=CSS,js=JS,
        blocked_paths=[str(root/p) for p in ('.env','.git','.tmp','data','imports','tools','reports/private')],
        max_file_size='100mb')
