"""User-started model download with fixed source, size, digest and destination."""
import hashlib
import threading
import urllib.request
import uuid
from urllib.parse import urlparse
from fastapi import HTTPException, Request
from .config import ROOT

VOICE = dict(url='https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q5_1.bin',
             name='ggml-small-q5_1.bin', size=190085487,
             sha256='ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb')


class VoiceModelInstaller:
    def __init__(self, root=ROOT, *, asset=None, opener=None):
        self.root = root.resolve()
        self.asset = asset or VOICE
        self.opener = opener or urllib.request.urlopen
        self.path = self.root / '.cache/voice-models' / self.asset['name']
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.verified = None
        self.state = dict(status='missing', downloaded=0, total=self.asset['size'], message='尚未安装本地语音模型')

    def safe(self, path):
        if not path.resolve().is_relative_to(self.root) or path.is_symlink() or path.is_junction():
            raise ValueError('组件路径无效。')

    def status(self):
        with self.lock:
            if self.thread and self.thread.is_alive(): return dict(self.state)
            self.safe(self.path)
            if self.path.is_file():
                stat = self.path.stat(); stamp = (stat.st_size, stat.st_mtime_ns)
                if stamp != self.verified and stat.st_size == self.asset['size']:
                    with self.path.open('rb') as stream:
                        if hashlib.file_digest(stream, 'sha256').hexdigest() == self.asset['sha256']: self.verified = stamp
                if stamp == self.verified:
                    self.state.update(status='installed', downloaded=self.asset['size'], message='已安装，可离线转写')
                else: self.state.update(status='failed', message='模型不完整或校验失败，请重新安装')
            elif self.state['status'] == 'installed':
                self.state.update(status='missing', downloaded=0, message='尚未安装本地语音模型')
            return dict(self.state)

    def start(self):
        with self.lock:
            current = self.status()
            if current['status'] in ('installed', 'downloading', 'cancelling'): return current
            self.safe(self.path); self.path.parent.mkdir(parents=True, exist_ok=True)
            # A terminated process cannot finish its temporary download. Only
            # this fixed component's incomplete files are disposable here.
            for part in self.path.parent.glob(self.path.name + '.*.part'):
                self.safe(part); part.unlink()
            self.stop.clear()
            self.state.update(status='downloading', downloaded=0, message='正在下载，完成后校验 SHA256')
            self.thread = threading.Thread(target=self.download, daemon=True, name='voice-model-install')
            self.thread.start()
            return dict(self.state)

    def cancel(self):
        with self.lock:
            self.stop.set()
            if self.thread and self.thread.is_alive(): self.state.update(status='cancelling', message='正在取消下载')
            return dict(self.state)

    def download(self):
        temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.part')
        count = 0
        try:
            digest = hashlib.sha256(); self.safe(temporary)
            with self.opener(self.asset['url'], timeout=20) as src, temporary.open('xb') as dst:
                while not self.stop.is_set():
                    block = src.read(1024 * 1024)
                    if not block: break
                    count += len(block)
                    if count > self.asset['size']: raise ValueError('下载超过预期大小')
                    dst.write(block); digest.update(block)
                    with self.lock: self.state['downloaded'] = count
            if self.stop.is_set():
                with self.lock: self.state.update(status='cancelled', message='已取消，可重新安装')
                return
            if count != self.asset['size'] or digest.hexdigest() != self.asset['sha256']: raise ValueError('模型下载不完整或校验失败')
            self.safe(self.path); temporary.replace(self.path)
            stat = self.path.stat()
            with self.lock:
                self.verified = (stat.st_size, stat.st_mtime_ns)
                self.state.update(status='installed', message='已安装，可离线转写')
        except Exception as exc:
            with self.lock:
                self.state.update(status='failed', message='安装失败（'+type(exc).__name__+'），检查网络和可用空间后重试；原有模型未替换。')
        finally: temporary.unlink(missing_ok=True)


def install_component_routes(app, root=ROOT):
    installer = VoiceModelInstaller(root)
    def local(request, write=False):
        if request.client and request.client.host not in ('127.0.0.1', '::1', 'testclient'): raise HTTPException(403)
        if urlparse(str(request.base_url)).hostname not in ('127.0.0.1', 'localhost', '::1', 'testserver'): raise HTTPException(403)
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'): raise HTTPException(403)
        if write and (request.headers.get('x-chatweave-ui') != '1' or request.headers.get('content-type', '').split(';')[0] != 'application/json'): raise HTTPException(403)
    @app.get('/api/components/voice')
    def status(request: Request):
        local(request); return installer.status()
    @app.post('/api/components/voice')
    def install(request: Request, body: dict):
        local(request, True)
        if body not in ({'action':'install'}, {'action':'cancel'}): raise HTTPException(400, '组件操作无效')
        return installer.start() if body['action'] == 'install' else installer.cancel()
    @app.on_event('shutdown')
    def close(): installer.cancel()
    return installer
