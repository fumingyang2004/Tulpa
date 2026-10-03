"""Owned localhost service for Tulpa.exe; never starts an external sender."""
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));os.chdir(ROOT)


def main():
    import uvicorn
    from fastapi import Request,HTTPException
    from app import create_app
    token=os.environ.get('CHATWEAVE_SESSION_TOKEN','')
    if len(token)<32:raise SystemExit('Desktop session token missing')
    ready=(ROOT/'.tmp'/sys.argv[1]).resolve()
    if ready.parent!=(ROOT/'.tmp').resolve():raise SystemExit('Invalid ready path')
    listener=socket.socket();listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    application=create_app()
    # Source links must work before anyone opens the MCP settings dialog.
    application.state.tulpa_mcp.source_base=f'http://127.0.0.1:{port}'
    server=uvicorn.Server(uvicorn.Config(application,host='127.0.0.1',port=port,access_log=False,log_level='warning',timeout_graceful_shutdown=5))
    @application.post('/api/desktop/quit')
    def quit(request:Request):
        if not secrets.compare_digest(request.headers.get('authorization',''),'Bearer '+token):raise HTTPException(403)
        server.should_exit=True
        return dict(stopping=True)
    def notify():
        while not server.started:
            if server.should_exit:return
            time.sleep(.1)
        ready.write_text(json.dumps(dict(url=f'http://127.0.0.1:{port}',pid=os.getpid())),encoding='utf-8')
    threading.Thread(target=notify,daemon=True).start()
    try:server.run(sockets=[listener])
    finally:ready.unlink(missing_ok=True);listener.close()


if __name__=='__main__':main()
