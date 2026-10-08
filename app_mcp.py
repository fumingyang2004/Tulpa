"""Light desktop: native data UI and MCP, without Gradio or an Agent runtime."""
import re
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from chatlocal.config import ROOT


def create_app():
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    from chatlocal.chat_view import install_chat_routes
    from chatlocal.watch_routes import install_watch_routes
    from chatlocal.artifact_routes import install_artifact_routes
    from chatlocal.voice_routes import install_voice_routes
    from chatlocal.desktop_routes import install_desktop_routes
    from chatlocal.mcp_routes import install_mcp_routes
    from chatlocal.optional_components import install_component_routes
    install_chat_routes(app)
    install_watch_routes(app,data_only=True)
    install_artifact_routes(app)
    install_voice_routes(app)
    install_desktop_routes(app)
    install_mcp_routes(app)
    install_component_routes(app)
    web=ROOT/'web'

    @app.get('/api/read-options')
    def options():
        from chatlocal.read_options import READ_LIMITS
        return READ_LIMITS

    @app.get('/')
    def index(request:Request):
        # Reuse the existing import, real-time and source-browser forms.
        source=(web/'index.html').read_text(encoding='utf-8')
        fragments=[re.search(r'<svg class="icon-defs"[\s\S]*?</svg>',source).group()]
        for name in ('data-dialog','chat-dialog','image-dialog'):
            fragments.append(re.search(r'<dialog id="'+name+r'"[\s\S]*?</dialog>',source).group())
        page=(web/'mcp-home.html').read_text(encoding='utf-8').replace('<!-- shared-forms -->','\n'.join(fragments))
        return HTMLResponse(page,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"})

    @app.get('/ui/{name}')
    def asset(name:str):
        allowed={'app.css','desktop.css','snowluma.js','mcp.css','mcp.js','mcp-home.js','mcp-home.css','chat.js','data.js','live.js','voice.js','artifacts.js','tulpa-logo.png'}
        if name not in allowed:raise HTTPException(404)
        return FileResponse(web/name,headers={'Cache-Control':'no-cache'})
    return app


if __name__=='__main__':
    import uvicorn
    uvicorn.run(create_app(),host='127.0.0.1',port=7861,access_log=False)
