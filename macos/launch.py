"""macOS Cocoa shell. Owns a loopback service and stops it on window close."""
import fcntl
import os
from pathlib import Path
import socket
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))


def main():
    import uvicorn
    import webview
    from app import create_app

    (ROOT / '.tmp').mkdir(exist_ok=True)
    lock = (ROOT / '.tmp/macos-instance.lock').open('a+')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return
    application = create_app()
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    application.state.tulpa_mcp.source_base = f'http://127.0.0.1:{port}'
    server = uvicorn.Server(uvicorn.Config(application, host='127.0.0.1', port=port,
        access_log=False, log_level='warning', timeout_graceful_shutdown=5))
    worker = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    worker.start()
    state = ROOT / '.tmp/macos-service.json'
    try:
        deadline = time.monotonic() + 60
        while not server.started:
            if not worker.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError('Tulpa local service did not start')
            time.sleep(.1)
        import json
        state.write_text(json.dumps({'url': f'http://127.0.0.1:{port}', 'pid': os.getpid()}))
        webview.create_window('Tulpa — Mac', f'http://127.0.0.1:{port}/?desktop=1',
            width=1280, height=850, min_size=(900, 650))
        webview.start(gui='cocoa', private_mode=False,
            storage_path=str(ROOT / 'data/macos-webview'),
            icon=str(ROOT / 'macos/assets/Tulpa.icns'))
    finally:
        server.should_exit = True
        worker.join(timeout=8)
        listener.close()
        state.unlink(missing_ok=True)
        lock.close()


if __name__ == '__main__':
    main()
