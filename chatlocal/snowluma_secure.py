"""Small local primitives for the owned SnowLuma runtime. No secret diagnostics."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time
from contextlib import contextmanager


class ManagedError(ValueError):
    def __init__(self, code, message, *, retryable=False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


def sha256(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def local_path(path):
    path = Path(path).absolute()
    if str(path).startswith(('\\\\', '//')):
        raise ManagedError('unsafe_path', '托管目录必须位于本机磁盘。')
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ManagedError('unsafe_path', '托管路径包含链接，已停止以避免写入其他目录。')
    return path


def shared_reader(path):
    """Allow an atomic rename while a Windows status reader holds its handle."""
    if os.name != 'nt':
        return path.open('rb')
    from ctypes import wintypes
    import msvcrt
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,
                                wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    handle=kernel.CreateFileW(str(path),0x80000000,7,None,3,0x80,None)
    if handle==ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
    except Exception:
        kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle(handle)
        raise
    return os.fdopen(fd,'rb')


def read_json(path, default=None):
    path = local_path(path)
    try:
        for attempt in range(11):
            try:
                with shared_reader(path) as f:
                    raw = f.read(1024 * 1024 + 1)
                break
            except OSError as exc:
                if os.name != 'nt' or exc.winerror not in (5, 32, 33) or attempt == 10:
                    raise
                time.sleep(.02)
        if len(raw) > 1024 * 1024:
            raise ValueError()
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except FileNotFoundError:
        return {} if default is None else default
    except (OSError, ValueError):
        raise ManagedError('state_damaged', '托管状态无法读取；请恢复备份，原文件未重置。') from None


def atomic_bytes(path, raw):
    path = local_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp-' + secrets.token_hex(8))
    try:
        with temp.open('xb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        # Windows readers briefly hold handles without FILE_SHARE_DELETE. A
        # concurrent status poll must not kill the lifecycle owner.
        for attempt in range(11):
            try:
                os.replace(temp, path)
                break
            except OSError as exc:
                if os.name != 'nt' or exc.winerror not in (5, 32, 33) or attempt == 10:
                    raise
                time.sleep(.02)
    finally:
        temp.unlink(missing_ok=True)


def atomic_json(path, value):
    atomic_bytes(path, json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'))


def private_directory(path):
    path = local_path(path)
    path.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        path.chmod(0o700)
        return
    # No shell interpolation, passwords, usernames or tokens in argv.
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
        '[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value'],
        capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    sid = result.stdout.strip()
    if result.returncode or not sid.startswith('S-1-5-') or not all(c in 'S-0123456789' for c in sid):
        raise ManagedError('acl_failed', '无法确认当前 Windows 用户，未保存托管凭据。')
    result = subprocess.run(['icacls.exe', str(path), '/inheritance:r', '/grant:r',
        '*'+sid+':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F'], capture_output=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise ManagedError('acl_failed', '无法限制托管目录权限，未继续部署。')


class SecretVault:
    """DPAPI CurrentUser, UI forbidden. Upstream plaintext lives under inherited ACL."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / 'credentials.dpapi'

    @staticmethod
    def crypt(raw, decrypt=False):
        if os.name != 'nt':
            raise ManagedError('platform_unsupported', '托管凭据需要 Windows 当前用户 DPAPI。')
        from ctypes import wintypes
        class Blob(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
        buffer = ctypes.create_string_buffer(raw)
        src = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        dst = Blob()
        api = ctypes.WinDLL('crypt32', use_last_error=True)
        fn = api.CryptUnprotectData if decrypt else api.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        fn.restype = wintypes.BOOL
        if not fn(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst)):
            raise ManagedError('credential_unavailable', '当前 Windows 用户无法解密托管凭据；不要重置密码，请恢复原用户备份。')
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        try:
            return ctypes.string_at(dst.data, dst.size)
        finally:
            kernel.LocalFree(dst.data)

    def read(self):
        local_path(self.path)
        if not self.path.exists():
            return {}
        try:
            if self.path.stat().st_size > 65536:
                raise ValueError()
            value = json.loads(self.crypt(self.path.read_bytes(), True))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except ManagedError:
            raise
        except (ValueError, OSError):
            raise ManagedError('credential_unavailable', '托管凭据无法读取，未生成替代密码。') from None

    def write(self, value):
        private_directory(self.directory)
        atomic_bytes(self.path, self.crypt(json.dumps(value).encode()))


_locks_guard = threading.Lock()
_locks = {}


@contextmanager
def file_lock(path, *, blocking=True):
    """OS lock; a dead owner never leaves an authoritative stale PID lock."""
    path = local_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _locks_guard:
        mutex = _locks.setdefault(str(path).casefold(), threading.Lock())
    if not mutex.acquire(blocking=blocking):
        raise ManagedError('busy', '另一个窗口正在操作此托管实例。', retryable=True)
    stream = None
    acquired = False
    try:
        try:
            stream = path.open('a+b')
            if not path.stat().st_size:
                stream.write(b'\0'); stream.flush()
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                # LK_LOCK is bounded by the CRT; no indefinite handler hangs.
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            acquired = True
        except OSError:
            raise ManagedError('busy', '托管文件正在使用或不可写，请稍后重试。', retryable=True) from None
        yield
    finally:
        if stream:
            if acquired:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_UN)
            stream.close()
        mutex.release()
