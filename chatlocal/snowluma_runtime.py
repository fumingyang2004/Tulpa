"""Version-specific official admin API and owned Windows process operations.

Written against v1.14.22 public schemas; no SnowLuma source is copied or patched.
No discovery of passwords or attempt to control externally launched services.
"""
import ctypes
from ctypes import wintypes
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

import httpx

from .snowluma_secure import ManagedError, atomic_json, local_path, read_json


def process_identity(pid):
    if os.name != 'nt' or type(pid) is not int or pid <= 0:
        return None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)]*4
        created, exited, cpu, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(cpu), ctypes.byref(user)):
            return None
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        length = wintypes.DWORD(32768); buf = ctypes.create_unicode_buffer(length.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(length)):
            return None
        return dict(pid=pid, created=(created.dwHighDateTime << 32) | created.dwLowDateTime,
                    executable=buf.value)
    finally:
        kernel.CloseHandle(handle)


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def port_available(port):
    try:
        with socket.socket() as listener:
            if os.name == 'nt':
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False


def empty_config():
    return dict(networks=dict(httpServers=[], wsServers=[], httpClients=[], wsClients=[]),
                statusCommand=dict(enabled=False, swallow=False, cooldownSeconds=5, trigger='#sl'),
                historySync=dict(enabled=False), notifications=dict(channelIds=[]))


def onebot_config(ports, credentials):
    config = empty_config()
    def common(name, port, token):
        return dict(name=name, enabled=True, host='127.0.0.1', port=port, path='/',
                    accessToken=token, messageFormat='array', reportSelfMessage=True)
    config['networks']['httpServers'] = [dict(common('tulpa-http', ports['http'], credentials['http_token']), enableWebSocket=False)]
    config['networks']['wsServers'] = [dict(common('tulpa-events', ports['ws'], credentials['ws_token']), role='Event')]
    return config


class WindowsRuntime:
    def __init__(self):
        self.jobs = {}

    def _job(self, proc):
        """Close-on-owner-exit job: a crashed Tulpa cannot orphan its runtime."""
        class Limits(ctypes.Structure):
            _fields_=[('PerProcessUserTimeLimit',ctypes.c_longlong),('PerJobUserTimeLimit',ctypes.c_longlong),
                ('LimitFlags',wintypes.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),
                ('MaximumWorkingSetSize',ctypes.c_size_t),('ActiveProcessLimit',wintypes.DWORD),
                ('Affinity',ctypes.c_size_t),('PriorityClass',wintypes.DWORD),('SchedulingClass',wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_=[(name,ctypes.c_ulonglong) for name in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]
        class Extended(ctypes.Structure):
            _fields_=[('BasicLimitInformation',Limits),('IoInfo',IO),('ProcessMemoryLimit',ctypes.c_size_t),
                ('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];kernel.CreateJobObjectW.restype=wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        job=kernel.CreateJobObjectW(None,None)
        limits=Extended();limits.BasicLimitInformation.LimitFlags=0x2000
        if not job or not kernel.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(job,int(proc._handle)):
            if job:kernel.CloseHandle(job)
            proc.terminate();proc.wait(timeout=5)
            raise ManagedError('job_failed','无法建立本实例退出保护，已停止新子进程。')
        self.jobs[proc.pid]=job

    def identity(self, pid):
        return process_identity(pid)

    def hooks(self):
        """Inspect names only, never connect to or unload an external hook."""
        if os.name != 'nt':
            raise ManagedError('platform_unsupported', '托管运行仅支持 Windows x64。')
        try:
            return {int(m[1]) for name in os.listdir('\\\\.\\pipe\\')
                    if (m := re.fullmatch(r'mojo\.(\d+)\.control', name, re.I))}
        except OSError:
            raise ManagedError('hook_inventory_failed', '无法核对已有 QQ 接入管道，未启动新的 SnowLuma。') from None

    def spawn(self, directory, port, credentials, instance):
        if os.name != 'nt':
            raise ManagedError('platform_unsupported', '托管运行仅支持 Windows x64。')
        directory = local_path(directory)
        if self.hooks():
            raise ManagedError('external_hook_present', '发现已有 QQ Hook，未启动第二个 SnowLuma；请使用已有连接，或先自行退出原服务并重新打开 QQ。')
        if not port_available(port):
            raise ManagedError('port_conflict', '管理端口已被其他程序占用，未接管该服务。', retryable=True)
        config = directory/'config'
        config.mkdir(exist_ok=True)
        auth_path = config/'webui.json'
        if auth_path.exists():
            # Upstream regenerates malformed credentials on startup. Validate
            # first so a damaged restore can never become an implicit reset.
            auth = read_json(auth_path)
            try:
                if (set(auth)-{'passwordHash','passwordSalt','mustChangePassword','generatedAt','updatedAt','totp'}
                        or not re.fullmatch(r'[0-9a-fA-F]{128}',auth['passwordHash'])
                        or not re.fullmatch(r'[0-9a-fA-F]{32}',auth['passwordSalt'])
                        or auth['mustChangePassword'] is not False):
                    raise ValueError()
                for field in ('generatedAt','updatedAt'):datetime.fromisoformat(auth[field])
            except (KeyError,TypeError,ValueError):
                raise ManagedError('credential_state_invalid', '上游认证文件损坏或要求改密，未启动以避免上游自动重置；请恢复匹配备份。') from None
            if auth.get('totp') is not None:
                raise ManagedError('security_handoff', '托管实例存在额外安全设置，需要人工安全交接；未自动关闭。')
        runtime_path = config/'runtime.json'
        runtime = read_json(runtime_path)
        account_files = [local_path(path) for path in config.glob('onebot_*.json')]
        expected_name = 'onebot_'+credentials.get('bound_account','')+'.json'
        if any(path.name != expected_name for path in account_files):
            raise ManagedError('unexpected_config', '所选目录出现不属于当前绑定的 QQ 配置，未覆盖；请使用已有连接导入。')
        runtime.update(webuiHost='127.0.0.1', webuiPort=port, hookAutoLoad=False,
                       webuiTls=dict(enabled=False), trustProxy='', logMaxTotalMb=32, logRetainDays=1, logPerUin=False)
        atomic_json(runtime_path, runtime)
        if not (config/'onebot.json').exists():
            atomic_json(config/'onebot.json', empty_config())
        # An initialized owned account is never allowed to expose stale listeners
        # before management authentication and identity verification on restart.
        for path in account_files:
            if re.fullmatch(r'onebot_\d{5,10}\.json', path.name):
                atomic_json(path, dict(empty_config(), mode='snapshot'))
        # Official env API; never use DEV_MODE or accept agreements by env.
        allowed = {'systemroot','windir','path','pathext','temp','tmp','userprofile','appdata','localappdata','programdata','systemdrive','comspec'}
        env = {k:v for k,v in os.environ.items() if k.lower() in allowed}
        env.update(SNOWLUMA_HOOK_AUTOLOAD='0', SNOWLUMA_WEBUI_HOST='127.0.0.1',
                   SNOWLUMA_WEBUI_PORT=str(port), SNOWLUMA_WEBUI_TRUST_PROXY='',
                   TULPA_SNOWLUMA_INSTANCE=instance)
        if not (config/'webui.json').exists():
            env['SNOWLUMA_WEBUI_BOOTSTRAP_PASSWORD'] = credentials['admin_password']
        try:
            proc = subprocess.Popen([str(directory/'node.exe'), str(directory/'index.mjs')],
                cwd=directory, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError:
            raise ManagedError('spawn_failed', '官方运行包未能后台启动，请检查系统权限。') from None
        self._job(proc)
        identity = self.identity(proc.pid)
        if not identity:
            proc.terminate();proc.wait(timeout=5)
            self.release_job(proc.pid)
            raise ManagedError('process_unverifiable', '无法记录本次启动的进程身份，已停止本次子进程。')
        return dict(identity, instance=instance)

    def release_job(self, pid):
        job=self.jobs.pop(pid,None)
        if job:
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            kernel.CloseHandle(job)

    def owned(self, owner, directory, instance):
        if not owner or owner.get('instance') != instance:
            return False
        live = self.identity(owner.get('pid'))
        return bool(live and all(live.get(k) == owner.get(k) for k in ('pid','created','executable'))
                    and Path(live['executable']).resolve() == local_path(directory/'node.exe').resolve())

    def stop(self, owner, directory, instance):
        if not self.owned(owner, directory, instance):
            return False
        # Hold the verified process HANDLE across identity check and termination;
        # a PID reused between calls cannot target an unrelated process.
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)]*4
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1001, False, owner['pid'])
        if not handle:
            return False
        try:
            values = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(x) for x in values)):
                return False
            created = (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime
            return bool(created == owner['created'] and kernel.TerminateProcess(handle, 0))
        finally:
            kernel.CloseHandle(handle)
            self.release_job(owner['pid'])


class SnowLumaAPI:
    """Narrow management interface, only used for our own verified process."""
    def __init__(self, port, *, client_factory=httpx.Client):
        self.base = f'http://127.0.0.1:{port}'
        self.token = ''
        self.client_factory = client_factory

    def request(self, method, path, body=None):
        allowed = {'/api/login','/api/auth/state','/api/agreements','/api/agreements/record-consent',
                   '/api/processes','/api/qq-list','/api/status','/api/logs/level'}
        if path not in allowed and not re.fullmatch(r'/api/(?:processes/[1-9]\d{0,7}/(?:load|unload|probe-login)|config/[1-9]\d{4,9})', path):
            raise ManagedError('admin_action_rejected', '托管管理请求不在允许范围。')
        try:
            headers = {'Origin':self.base}
            if self.token:
                headers['Authorization'] = 'Bearer '+self.token
            with self.client_factory(timeout=httpx.Timeout(5, connect=2), trust_env=False, follow_redirects=False) as client:
                with client.stream(method, self.base+path, json=body, headers=headers) as response:
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 512*1024:
                            raise ManagedError('admin_protocol', '管理接口响应超出上限。')
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError()
            if response.status_code == 401:
                raise ManagedError('admin_auth_failed', '管理认证失败，未更换或重置密码；请恢复匹配的托管凭据。')
            if value.get('needsTotp'):
                raise ManagedError('security_handoff', '此实例要求额外安全验证；托管不会关闭或绕过双重认证。')
            if value.get('consentRequired') and response.status_code == 403:
                raise ManagedError('terms_changed', '上游要求重新确认协议。')
            if value.get('mustChangePassword') and path != '/api/login':
                raise ManagedError('initialization_incomplete', '上游处于改密交接状态；未自动重置未知凭据。')
            if response.status_code == 429:
                raise ManagedError('admin_rate_limited', '管理接口限流，请稍后重试。', retryable=True)
            if response.status_code >= 400 or value.get('success') is False:
                raise ManagedError('admin_rejected', '管理接口拒绝本次配置，请查看阶段诊断；未尝试其他账号或密码。')
            return value
        except ManagedError:
            raise
        except (httpx.HTTPError, OSError):
            raise ManagedError('admin_unreachable', '托管管理服务暂不可用。', retryable=True) from None
        except (ValueError, TypeError):
            raise ManagedError('admin_protocol', '管理响应与已审核版本不符，已停止。') from None

    def initialize(self, password, manifest, check):
        check()
        result = self.request('POST', '/api/login', dict(password=password))
        if not result.get('success') or not isinstance(result.get('token'), str) or not result['token']:
            raise ManagedError('admin_protocol', '管理登录没有返回有效会话。')
        self.token = result['token']
        if result.get('mustChangePassword'):
            raise ManagedError('initialization_incomplete', '托管初始化没有使用预期的官方随机密码流程，未自动改密。')
        check()
        agreements = self.request('GET', '/api/agreements')
        docs = {d.get('id'):d for d in agreements.get('documents', []) if isinstance(d, dict)}
        for expected in manifest['agreements']:
            text = docs.get(expected['id'], {}).get('text')
            if not isinstance(text, str) or hashlib.sha256(text.encode('utf-8')).hexdigest() != expected['sha256']:
                raise ManagedError('terms_changed', '上游实际协议与用户同意的内容不同，未代为同意。')
        if agreements.get('consentRequired'):
            check()
            result = self.request('POST', '/api/agreements/record-consent', dict(version=agreements.get('version')))
            if result.get('success') is not True:
                raise ManagedError('consent_failed', '官方协议确认未成功。')
        check()
        auth = self.request('GET', '/api/auth/state')
        if auth.get('mustChangePassword') is not False or self.request('GET', '/api/status').get('status') != 'running':
            raise ManagedError('admin_not_ready', '管理服务尚未完成初始化。')

    def processes(self):
        value = self.request('GET', '/api/processes').get('list')
        if not isinstance(value, list) or len(value) > 64:
            raise ManagedError('admin_protocol', 'QQ 进程列表格式不兼容。')
        return value

    def probe(self, pid):
        value = self.request('GET', f'/api/processes/{pid}/probe-login').get('info')
        uin = str(value.get('uin', '')) if isinstance(value, dict) else ''
        return uin if re.fullmatch(r'[1-9]\d{4,9}', uin) else ''

    def load(self, pid):
        return self.request('POST', f'/api/processes/{pid}/load', {})

    def unload(self, pid):
        return self.request('POST', f'/api/processes/{pid}/unload', {})

    def accounts(self):
        rows = self.request('GET', '/api/qq-list').get('list')
        if not isinstance(rows, list):
            raise ManagedError('admin_protocol', '管理账号列表格式不兼容。')
        return [str(r.get('uin','')) for r in rows if isinstance(r, dict)]

    def configure(self, account, ports, credentials):
        result = self.request('POST', '/api/config/'+account, onebot_config(ports, credentials))
        if not (result.get('saved') is True and result.get('applied') is True and result.get('online') is True):
            raise ManagedError('config_not_applied', 'OneBot 配置尚未完整应用，请检查端口或 QQ 登录后重试。', retryable=True)
