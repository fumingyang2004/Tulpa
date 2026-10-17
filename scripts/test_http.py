"""Local management HTTP fixture shared by backend and optional TUI checks."""
from contextlib import contextmanager
import socket
import threading
import time


@contextmanager
def management_http(app):
    import uvicorn
    sock = socket.socket(); sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    # The fixture's TestClient owns startup/shutdown of the original MCP core.
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic()+5
        while not server.started and time.monotonic() < deadline: time.sleep(.02)
        if not server.started: raise AssertionError('Management HTTP did not start')
        yield f'http://127.0.0.1:{port}'
    finally:
        server.should_exit = True
        thread.join(5); sock.close()
        if thread.is_alive(): raise AssertionError('Management HTTP did not stop')
