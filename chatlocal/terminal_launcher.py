"""Start or reuse this installation's backend before opening its terminal UI."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from urllib.parse import urlparse

from .config import ROOT
from .platform_lock import file_lock
from .terminal_client import TerminalClient, TerminalError, read_token


def health_ready(client):
    import httpx
    try:
        with httpx.Client(timeout=1, trust_env=False, follow_redirects=False) as http:
            response = http.get(client.base_url + '/health')
    except httpx.ConnectError:
        return False
    except httpx.HTTPError:
        raise TerminalError('管理端口没有返回有效的 Tulpa 响应，请检查端口占用或服务状态。') from None
    try:
        health = response.json()
    except ValueError:
        health = None
    if (response.status_code != 200 or not isinstance(health, dict)
            or health.get('product') != 'Tulpa' or health.get('management') != 'terminal-v1'):
        raise TerminalError('管理端口已被其他服务占用，请用 --url 指定另一个本机端口。')
    return True


def ready(client, root=None):
    if not health_ready(client):
        return False
    # A public health response is insufficient: verify the selected private token.
    overview = client.get('/overview')
    if root is not None and Path(overview.get('storage', '')).resolve() != Path(root) / 'data':
        raise TerminalError('该后台属于另一个 Tulpa 目录，未接入。')
    return True


@contextmanager
def launch_lock(root, deadline):
    lock = file_lock(Path(root) / '.tmp/terminal-launch.lock')
    while True:
        try:
            lock.__enter__()
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TerminalError('另一个 Tulpa 正在启动，请稍后重新打开。') from None
            time.sleep(.1)
            lock = file_lock(Path(root) / '.tmp/terminal-launch.lock')
    try:
        yield
    finally:
        lock.__exit__(None, None, None)


def service_busy(root):
    try:
        with file_lock(Path(root) / '.tmp/terminal-service.lock'):
            return False
    except BlockingIOError:
        return True


def installed_service(root, port, token_file):
    """Reuse an owned systemd unit; never install or replace a service implicitly."""
    if sys.platform == 'darwin':
        from .macos_service import installed_service as mac_installed
        return mac_installed(root, port, token_file)
    from .linux_service import service_status
    status = service_status(root)
    unit = Path(status['unit']) if status.get('unit') else None
    if not status.get('available') or not unit or not unit.is_file():
        return False
    from .linux_service import _quote
    text = unit.read_text()
    if not text.startswith('# Managed by Tulpa Linux CLI\n') or _quote(Path(root) / 'tulpa') not in text:
        return False
    match = re.search(r'(?m)^ExecStart=.* serve --port (\d+)\s*$', text)
    if not match or int(match[1]) != port or token_file != Path(root) / 'data/terminal-admin.token':
        raise TerminalError('已有本实例的用户服务，请使用它的端口和默认管理凭据打开界面。')
    return True


def wait_ready(client, deadline, process=None, root=None):
    while True:
        if ready(client, root):
            return
        if process is not None and process.poll() is not None:
            raise TerminalError('Tulpa 后台启动失败，请查看本目录 .tmp/terminal-service.log。')
        if time.monotonic() >= deadline:
            raise TerminalError('后台尚未就绪；请检查服务日志和 --url 端口，本目录可能已在其他端口运行。')
        time.sleep(.15)


def ensure_backend(base_url=None, token_file=None, *, root=ROOT, timeout=30, notify=None,
                   prepare=None, environment=None, managed_instance=False):
    root = Path(root).resolve()
    client = TerminalClient(base_url, token_file or os.environ.get('TULPA_TOKEN_FILE')
                            or root / 'data/terminal-admin.token', timeout=2)
    deadline = time.monotonic() + timeout
    if ready(client, root):
        return dict(running=True, started=False, url=client.base_url)
    parsed = urlparse(client.base_url)
    if parsed.hostname not in ('127.0.0.1', 'localhost') or not 1024 <= parsed.port <= 65535:
        raise TerminalError('自动启动请使用 http://127.0.0.1:1024–65535 范围内的管理地址。')
    with launch_lock(root, deadline):
        if ready(client, root):
            return dict(running=True, started=False, url=client.base_url)
        if service_busy(root):
            wait_ready(client, deadline, root=root)
            return dict(running=True, started=False, url=client.base_url)
        selected_token = client.token_file.absolute()
        if selected_token.exists() or selected_token.is_symlink():
            read_token(selected_token)
        if notify:
            notify('正在启动 Tulpa 后台……')
        if prepare:
            prepare()
        if managed_instance:
            from .linux_service import service_action, service_status
            status = service_status(root)
            if status.get('available') and not installed_service(root, parsed.port, selected_token):
                # Independent units keep child accounts alive when the hub restarts.
                # Starting an instance does not opt it into login autostart.
                service_action('install', root=root, port=parsed.port, enable=False)
        if installed_service(root, parsed.port, selected_token):
            from .linux_service import service_action
            service_action('start', root=root)
            wait_ready(client, deadline, root=root)
            return dict(running=True, started=True, mode='launchd' if sys.platform == 'darwin' else 'systemd', url=client.base_url)
        log_path = root / '.tmp/terminal-service.log'
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        process = None
        try:
            with os.fdopen(fd, 'ab') as output:
                os.fchmod(output.fileno(), 0o600)
                env = dict(os.environ if environment is None else environment, PYTHONUNBUFFERED='1')
                process = subprocess.Popen([sys.executable, '-m', 'chatlocal.terminal_cli',
                    '--token-file', str(selected_token), 'serve', '--port', str(parsed.port)],
                    cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    start_new_session=True, close_fds=True, umask=0o077)
            wait_ready(client, deadline, process, root=root)
        except BaseException:
            if process is not None and process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(5)
            raise
        return dict(running=True, started=True, mode='background', url=client.base_url)


def stop_backend(client, *, root=ROOT, timeout=30):
    root = Path(root).resolve()
    deadline = time.monotonic() + timeout
    with launch_lock(root, deadline):
        if not ready(client):
            if service_busy(root):
                raise TerminalError('本目录后台仍在运行，请指定其实际 --url 端口后再停止。')
            return dict(stopped=True, already_stopped=True)
        overview = client.get('/overview')
        if Path(overview.get('storage', '')).resolve() != root / 'data':
            raise TerminalError('该后台属于另一个 Tulpa 目录，未停止。')
        from .linux_service import service_status, service_action
        status = service_status(root)
        if status.get('active') and status.get('this_instance'):
            service_action('stop', root=root)
        else:
            client.post('/shutdown')
        # Ownership/authentication were verified before requesting shutdown.
        # Do not issue a second authenticated request after the health probe:
        # the listener can close between those requests during a normal stop.
        while service_busy(root) or health_ready(client):
            if time.monotonic() >= deadline:
                raise TerminalError('已请求停止，但后台尚未退出，请检查当前任务和服务日志。')
            time.sleep(.15)
    return dict(stopped=True)
