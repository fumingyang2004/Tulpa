"""User-authorized configuration of a selected local SnowLuma installation.

No package download, component copying, or Agent-facing management API.
"""
import atexit
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time

from .snowluma_secure import (ManagedError, SecretVault, atomic_json, file_lock,
                             local_path, private_directory, read_json)
from .snowluma_local import LocalInstallation, fingerprint
from .snowluma_runtime import SnowLumaAPI, WindowsRuntime, free_port, port_available


PHASES = dict(idle='请选择 SnowLuma 文件夹', detecting='核对本地运行文件',
    starting='后台启动', initializing='首次初始化', choosing='选择 QQ',
    waiting_qq='等待打开 QQ', waiting_login='等待 QQ 登录', configuring='配置 OneBot',
    verifying='验证连接', ready='QQ 已就绪', reconnecting='连接恢复中',
    cancelled='已取消', revoked='已撤销授权', stopped='托管服务已停止', error='接入未完成')


def load_manifest():
    return read_json(Path(__file__).with_name('snowluma_manifest.json'))


def consent_key(manifest):
    value = {k:manifest[k] for k in ('version','adapter','agreements','scope')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ManagedSnowLuma:
    def __init__(self, root, *, manifest=None, runtime=None, api_factory=SnowLumaAPI,
                 installer_factory=LocalInstallation, vault_factory=SecretVault,
                 on_invalidated=lambda:None, probe=None, poll_seconds=3,
                 login_timeout=180, event_timeout=20, auto_select_single=True,
                 check_account=lambda account:None):
        self.root = local_path(root)
        self.directory = self.root/'data/snowluma-managed'
        self.state_path = self.directory/'state.json'
        self.manifest = manifest if manifest is not None else load_manifest()
        self.runtime = runtime or WindowsRuntime()
        self.api_factory, self.installer_factory = api_factory, installer_factory
        self.installations = installer_factory(self.manifest, self.directory)
        self.previews = {}
        self.auto_select_single, self.check_account = auto_select_single, check_account
        self.vault = vault_factory(self.directory)
        self.on_invalidated, self.probe = on_invalidated, probe
        self.poll_seconds = max(.02, poll_seconds)
        self.login_timeout = max(.1, min(180, login_timeout))
        self.event_timeout = max(.1, min(20, event_timeout))
        self.halt = threading.Event()
        self.thread = None
        self.mutex = threading.RLock()
        self.api = None
        self.heartbeat = None

    def state(self):
        return read_json(self.state_path, dict(schema=1, phase='idle', enabled=False, generation=0))

    def _require_permission(self):
        if self.manifest.get('mode') != 'user_selected_local_directory':
            raise ManagedError('unsupported_mode', '当前仅支持配置用户明确选择的本地 SnowLuma 文件夹。')

    def inspect(self, folder):
        self._require_permission()
        try:receipt = self.installations.inspect(folder)
        except ManagedError:raise
        except (OSError,ValueError,TypeError,RuntimeError):
            raise ManagedError('folder_unreadable', '文件夹无法完整读取或正在变化，请核对权限和解压结果后重新载入。') from None
        with self.mutex:
            now = time.monotonic()
            self.previews = {k:v for k,v in self.previews.items() if now-v[0]<600}
            if len(self.previews) >= 8:
                self.previews.pop(next(iter(self.previews)))
            key = secrets.token_urlsafe(24)
            consent = fingerprint([consent_key(self.manifest), receipt['fingerprint']])
            self.previews[key] = (now, receipt, consent)
        return dict(selection_id=key, consent_fingerprint=consent,
                    **{k:receipt[k] for k in ('folder','version','mode','message')})

    def _consented(self, state):
        return (state.get('consent', {}).get('fingerprint') == consent_key(self.manifest)
                and state.get('consent', {}).get('scope') == self.manifest['scope']
                and bool(state.get('installation'))
                and state.get('consent', {}).get('source') == state['installation'].get('fingerprint'))

    def _write(self, state):
        state['updated_at'] = time.time()
        atomic_json(self.state_path, state)

    def _set(self, generation, phase=None, **fields):
        with file_lock(self.directory/'state.lock'):
            state = self.state()
            if state.get('generation') != generation or not state.get('enabled'):
                raise ManagedError('cancelled', '任务已取消或授权已变化。')
            if phase and state.get('phase') != phase:
                history = state.get('history', [])[-23:]
                history.append(dict(phase=phase, at=time.time()))
                fields.update(phase=phase, history=history)
            state.update(fields)
            self._write(state)
            return state

    def status(self):
        try:
            state = self.state()
        except ManagedError as exc:
            state = dict(phase='error', code=exc.code, message=str(exc), enabled=False)
        phase = state.get('phase', 'idle')
        public = {k:state[k] for k in ('enabled','generation','account','message','code','retryable',
            'progress','ports','verified_at','updated_at','history','choices','selection_required') if k in state}
        owner = state.get('owner') or {}
        selected = state.get('selected') or {}
        public.update(phase=phase, phase_label=PHASES.get(phase,'状态未知'),
            version=self.manifest['version'], real_pc_verified=self.manifest['compatibility']['real_pc_verified'],
            available=self.manifest.get('mode') == 'user_selected_local_directory',
            consent_current=self._consented(state), has_consent=bool(state.get('consent')), consent_fingerprint=consent_key(self.manifest),
            consent_at=state.get('consent',{}).get('at'), agreements=self.manifest['agreements'],
            scope=self.manifest['scope'], service_pid=owner.get('pid'), qq_pid=selected.get('pid'),
            storage=str(self.directory), folder=state.get('installation',{}).get('folder',''), qq_ready=phase=='ready',
            authorization_note='',
            security_note='Tulpa 凭据由 Windows 当前用户 DPAPI 保护。所选 SnowLuma 的配置目录会保存它运行所需的明文 Token，并限制为当前用户和系统访问。')
        if public.get('qq_ready') and (not self.thread or not self.thread.is_alive()):
            public.update(qq_ready=False, phase='reconnecting', phase_label=PHASES['reconnecting'])
        return public

    def begin(self, body):
        if set(body) != {'accepted','fingerprint','selection_id'} or body['accepted'] is not True:
            raise ManagedError('consent_required', '请先检测本地文件夹，再主动勾选协议与自动配置授权。')
        with self.mutex:
            preview = self.previews.get(body.get('selection_id')) if isinstance(body.get('selection_id'),str) else None
        if not preview or time.monotonic()-preview[0]>=600:
            raise ManagedError('selection_expired', '文件夹检查已过期，请重新检测后确认。')
        _, receipt, agreed = preview
        if body['fingerprint'] != agreed:
            raise ManagedError('consent_required', '文件夹或协议已变化，请重新检测并主动同意。')
        if receipt['mode'] != 'local':
            raise ManagedError('existing_installation', '请导入这份 SnowLuma 的已有连接；不会重置其管理密码。')
        self._require_permission()
        self.installations.verify(receipt)
        private_directory(self.directory)
        with file_lock(self.directory/'state.lock'):
            state = self.state()
            if state.get('installation') and state['installation']['folder'] != receipt['folder']:
                raise ManagedError('installation_bound', '本连接已绑定另一文件夹。请保留该绑定，使用另一份 Tulpa 接入其他 SnowLuma。')
            if state.get('enabled') and self._consented(state):
                duplicate = True
            else:
                duplicate = False
                state.update(schema=1, mode='managed', instance=state.get('instance') or secrets.token_hex(16),
                    generation=state.get('generation',0)+1, enabled=True, phase='detecting', code='', message='',
                    installation=receipt,
                    consent=dict(fingerprint=consent_key(self.manifest), source=receipt['fingerprint'], at=time.time(), scope=self.manifest['scope']),
                    version=self.manifest['version'], retryable=False, choices=[], selection_required=False)
                self._write(state)
        if not duplicate or state.get('phase') not in ('error','cancelled','revoked'):
            self.launch()
        return dict(self.status(), already_started=duplicate)

    def retry(self):
        self._require_permission()
        with file_lock(self.directory/'state.lock'):
            state = self.state()
            if not self._consented(state) or state.get('phase') == 'revoked':
                raise ManagedError('consent_required', '需要重新主动同意，不能靠重试恢复已撤销授权。')
            if self.thread and self.thread.is_alive() and state.get('enabled'):
                return self.status()
            state.update(enabled=True, generation=state.get('generation',0)+1, phase='detecting', code='', message='')
            self._write(state)
        self.launch()
        return self.status()

    def select(self, choice_id):
        self._require_permission()
        with file_lock(self.directory/'state.lock'):
            state = self.state()
            if not state.get('enabled') or not self._consented(state) or state.get('phase') != 'choosing':
                raise ManagedError('selection_expired', '当前不是选择 QQ 阶段，请刷新状态。')
            choice = next((c for c in state.get('choices',[]) if c['id']==choice_id), None)
            if not choice or not choice.get('identity'):
                raise ManagedError('selection_expired', '所选进程不在当前列表，请刷新后重选。')
            if self.runtime.identity(choice['pid']) != choice['identity']:
                raise ManagedError('process_changed', 'QQ 进程已变化，请重新选择。')
            if state.get('account') and choice.get('account') and state['account'] != choice['account']:
                raise ManagedError('account_mismatch', '该实例已绑定另一个 QQ 账号，不能自动换号。')
            state.update(selected=choice['identity'], selected_at=time.time(), selection_required=False)
            self._write(state)
        return self.status()

    def cancel(self, *, revoke=False):
        # Change generation first. In-flight operations/results cannot republish
        # credentials or ready status after this point.
        if not self.state_path.exists():
            return self.status()
        with file_lock(self.directory/'state.lock'):
            state = self.state()
            state.update(enabled=False, generation=state.get('generation',0)+1,
                         phase='revoked' if revoke else 'cancelled', choices=[], selection_required=False)
            if revoke:
                state.pop('consent', None)
            self._write(state)
        self.halt.set()
        self.on_invalidated()
        # Worker cleanup only stops the exact owned SnowLuma, never QQ/external.
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(5)
        return self.status()

    def use_external(self):
        if not self.state_path.exists():
            return
        with file_lock(self.directory/'state.lock'):
            state=self.state()
            if state.get('enabled'):
                raise ManagedError('managed_active','请先撤销托管授权。')
            state.update(mode='external',generation=state.get('generation',0)+1)
            self._write(state)

    def launch(self):
        with self.mutex:
            if self.thread and self.thread.is_alive():
                return
            self.halt.clear()
            self.thread = threading.Thread(target=self.run, name='tulpa-snowluma-managed', daemon=True)
            self.thread.start()

    def resume(self):
        try:state = self.state()
        except ManagedError:return  # Corrupt QQ setup must not disable historical MCP.
        if state.get('enabled') and self._consented(state):
            self.launch()

    def close(self):
        self.halt.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(6)

    def _check(self, generation):
        state = self.state()
        if self.halt.is_set() or not state.get('enabled') or state.get('generation') != generation:
            raise ManagedError('cancelled', '托管操作已取消。')
        self._require_permission()
        if not self._consented(state):
            raise ManagedError('terms_changed', '协议或配置授权范围已变化，请重新同意。')
        return state

    def _pause(self, generation, seconds):
        if self.halt.wait(seconds):
            raise ManagedError('cancelled', '托管操作已取消。')
        return self._check(generation)

    def run(self):
        generation = self.state().get('generation')
        try:
            with file_lock(self.directory/'owner.lock', blocking=False):
                self._work(generation)
        except ManagedError as exc:
            if exc.code == 'busy':
                return  # Another window owns this root; never stop its process.
            if exc.code != 'cancelled':
                try:
                    self._set(generation, 'error', code=exc.code, message=str(exc), retryable=exc.retryable)
                except ManagedError:
                    pass
            self.on_invalidated()
        except Exception:
            try:
                self._set(generation, 'error', code='internal_error', message='托管接入异常，已停止；请重试或恢复备份。', retryable=True)
            except ManagedError:
                pass
            self.on_invalidated()

    def _work(self, generation):
        check = lambda: self._check(generation)
        state = check()
        self._set(generation, 'detecting')
        install = local_path(state['installation']['folder'])
        if not (state.get('owner') and self.runtime.owned(state['owner'],install,state['instance'])) and self.runtime.hooks():
            raise ManagedError('external_hook_present', '已有 QQ 接入服务正在工作，请导入已有连接；未启动第二份服务或改写配置。')
        with self.installations.acquire(state['installation'], state['instance'], check) as install:
            self._run_installation(generation, install)

    def _run_installation(self, generation, install):
        check = lambda: self._check(generation)
        state = check()
        existing_owner = state.get('owner')
        if not (existing_owner and self.runtime.owned(existing_owner, install, state['instance'])):
            if self.runtime.hooks():
                raise ManagedError('external_hook_present', '发现已有 QQ 接入服务，未启动第二份。可选择其文件夹导入已有连接；若要换用新服务，请先自行退出原服务并重开 QQ。')
        credentials = self.vault.read()
        if not credentials:
            if state.get('initialized') or (install/'config/webui.json').exists():
                raise ManagedError('credential_unavailable', '已初始化服务的托管凭据缺失，未重置密码。')
            credentials = dict(admin_password=secrets.token_urlsafe(36), http_token=secrets.token_urlsafe(32),
                               ws_token=secrets.token_urlsafe(32))
            check(); self.vault.write(credentials)
        check()
        owner = state.get('owner')
        receiver = None
        selected = None
        load_requested = False
        try:
            if owner and self.runtime.owned(owner, install, state['instance']):
                ports = state['ports']
            else:
                ports = dict(admin=free_port(), http=free_port(), ws=free_port())
                while len(set(ports.values())) != 3:
                    ports = dict(admin=free_port(), http=free_port(), ws=free_port())
                self._set(generation, 'starting', ports=ports)
                check()
                owner = self.runtime.spawn(install, ports['admin'], dict(credentials,bound_account=state.get('account','')), state['instance'])
                self._set(generation, owner=owner)
            self.api = self.api_factory(ports['admin'])
            self._set(generation, 'initializing')
            for attempt in range(8):
                check()
                try:
                    self.api.initialize(credentials['admin_password'], self.manifest, check)
                    break
                except ManagedError as exc:
                    if exc.code != 'admin_unreachable' or attempt == 7:
                        raise
                    self._pause(generation, min(2, .3*(attempt+1)))
            self._set(generation, initialized=True)
            selected = state.get('selected')
            # Existing process identity can resume only if PID, creation time,
            # executable and originally verified account still match.
            if selected and self.runtime.identity(selected['pid']) != selected:
                selected = None
                self._set(generation, selected=None)
            while not selected:
                check()
                if self.runtime.hooks():
                    raise ManagedError('external_hook_present', '选择 QQ 期间出现外部 Hook，已停止本托管服务；未操作外部实例。')
                choices = []
                for proc in self.api.processes():
                    pid = proc.get('pid')
                    identity = self.runtime.identity(pid)
                    if not identity or Path(identity['executable']).name.lower() != 'qq.exe':
                        continue
                    account = self.api.probe(pid)
                    # No nickname guesses. Empty native path can still be tied
                    # to a verified OS process image/creation time.
                    choice_id = hashlib.sha256(json.dumps([generation,identity],sort_keys=True).encode()).hexdigest()[:24]
                    choices.append(dict(id=choice_id, pid=pid, identity=identity, path=identity['executable'],
                                        account=account, account_label=account or '账号待验证'))
                self._set(generation, 'choosing' if choices else 'waiting_qq', choices=choices,
                          selection_required=bool(choices), message='请选择要绑定的 QQ。' if choices else '请打开桌面 QQ 并完成必要扫码，Tulpa 将自动继续。')
                if self.auto_select_single and len(choices)==1:
                    self.select(choices[0]['id'])
                    selected = self.state().get('selected')
                    continue
                state = self._pause(generation, self.poll_seconds)
                selected = state.get('selected')
            check()
            if self.runtime.identity(selected['pid']) != selected:
                raise ManagedError('process_changed', '所选 QQ 进程已退出或被复用，请重选。', retryable=True)
            before = self.api.probe(selected['pid'])
            if before:self.check_account(before)
            if self.runtime.hooks():
                raise ManagedError('external_hook_present', '加载前检测到已有 QQ Hook，未接管。')
            expected = state.get('account', '')
            if expected and before and before != expected:
                raise ManagedError('account_mismatch', 'QQ 登录账号与固定绑定不同，未加载该账号。')
            check(); load_requested=True; self.api.load(selected['pid'])
            self._set(generation, 'waiting_login', message='等待所选 QQ 返回已登录账号；无需设置端口或 Token。')
            deadline = time.monotonic()+self.login_timeout
            account = ''
            while not account:
                check()
                if self.runtime.identity(selected['pid']) != selected:
                    raise ManagedError('process_changed', 'QQ 进程已变化，已停止接入。', retryable=True)
                current = self.api.probe(selected['pid'])
                if expected and current and current != expected:
                    raise ManagedError('account_mismatch', '所选 QQ 已切换账号，相关工具已停止。')
                accounts = self.api.accounts()
                if current and current in accounts:
                    if any(a != current for a in accounts):
                        raise ManagedError('unexpected_account', '托管实例出现未选择的账号，已停止。')
                    account = current
                    self.check_account(account)
                    break
                if time.monotonic() >= deadline:
                    raise ManagedError('login_timeout', 'QQ 登录尚未完成，请完成扫码后重试。', retryable=True)
                self._pause(generation, self.poll_seconds)
            self._set(generation, 'configuring', account=account, choices=[], selection_required=False)
            check()
            if not port_available(ports['http']) or not port_available(ports['ws']):
                # Only our listeners may be replaced. Fresh ports avoid probing
                # an unrelated service that acquired the previous reservation.
                ports.update(http=free_port(), ws=free_port())
                while len(set(ports.values())) != 3:
                    ports.update(http=free_port(), ws=free_port())
                self._set(generation, ports=ports)
            self.api.configure(account, ports, credentials)
            check()
            from .onebot import Client
            from .onebot_events import EventReceiver
            http = dict(url=f'http://127.0.0.1:{ports["http"]}',token=credentials['http_token'])
            ws = dict(url=f'ws://127.0.0.1:{ports["ws"]}',token=credentials['ws_token'])
            self._set(generation, 'verifying')
            if self.probe:
                receiver = self.probe(http, ws, account, check)
            else:
                receiver = EventReceiver(lambda event:None, lambda phase:None,
                    config_factory=lambda:dict(ws), client_factory=lambda **kw:Client(http,**kw), metadata_only=True)
                receiver.start()
            self.heartbeat = receiver
            deadline = time.monotonic()+self.event_timeout
            while receiver.status()['state'] != 'connected':
                check()
                if receiver.status()['state'] in ('account_mismatch','auth_error','handshake_error'):
                    raise ManagedError('event_identity_failed', '实时事件账号或认证不匹配，未启用 QQ 工具。')
                if time.monotonic() >= deadline:
                    raise ManagedError('event_unverified', '尚未收到同账号的真实生命周期或心跳事件，不能判定就绪。', retryable=True)
                self._pause(generation, .1)
            self._verify_identity(state['instance'], owner, install, selected, account)
            self._set(generation, 'ready', verified_at=time.time(), message='管理认证、QQ 账号与实时事件已核对一致。', code='')
            disconnect_since = 0
            while True:
                self._pause(generation, self.poll_seconds)
                self._verify_identity(state['instance'], owner, install, selected, account)
                status = receiver.status()
                if status['state'] != 'connected' or status.get('account') != account:
                    if not disconnect_since:
                        disconnect_since = time.monotonic()
                        self._set(generation, 'reconnecting', message='事件连接中断，QQ 工具暂停；正在按原账号恢复。')
                        self.on_invalidated()
                    if status['state'] in ('account_mismatch','auth_error') or time.monotonic()-disconnect_since > 45:
                        raise ManagedError('event_disconnected', '实时事件未恢复，请重试；不会切换账号或 Token。', retryable=True)
                else:
                    disconnect_since = 0
                    self._set(generation, 'ready', verified_at=time.time(), code='', message='QQ 托管连接正常。')
        finally:
            if receiver:
                receiver.stop()
            self.heartbeat = None
            # The OS owner lock is still held here. Never touch another window's
            # process or a reused external instance, and never terminate QQ.
            if owner and self.runtime.owned(owner, install, state['instance']):
                if load_requested and selected and self.runtime.identity(selected['pid'])==selected:
                    try:self.api.unload(selected['pid'])
                    except Exception:pass  # No speculative retry or QQ termination.
                self.runtime.stop(owner, install, state['instance'])
            current = self.state()
            if current.get('generation') == generation and self.halt.is_set() and current.get('enabled'):
                self._set(generation, 'stopped', verified_at=0)

    def _verify_identity(self, instance, owner, install, selected, account):
        self.check_account(account)
        if not self.runtime.owned(owner, install, instance):
            raise ManagedError('service_changed', '托管服务进程已变化，相关 QQ 工具已停止。', retryable=True)
        if self.runtime.identity(selected['pid']) != selected:
            raise ManagedError('process_changed', 'QQ 进程已重启，需要重新确认目标进程。', retryable=True)
        if self.runtime.hooks() - {selected['pid']}:
            raise ManagedError('external_hook_present', '出现其他 QQ Hook，已暂停本托管服务；未操作其他账号。')
        if self.api.probe(selected['pid']) != account or self.api.accounts() != [account]:
            raise ManagedError('account_mismatch', 'QQ 登录账号与原绑定不同，已停止相关工具。')
        if self.api.request('GET','/api/auth/state').get('mustChangePassword') is not False:
            raise ManagedError('admin_not_ready', '管理认证状态已变化，相关工具已停止。')

    def connection(self, kind):
        state = self.state()
        # Once explicitly selected, a stopped/revoked managed mode must never
        # fall back to a previous .env account behind the user's back.
        if not state.get('instance') or state.get('mode')=='external':
            return None
        empty = dict(url='',token='')
        if (not state.get('enabled') or not self._consented(state)
                or state.get('phase') != 'ready' or time.time()-state.get('verified_at',0)>12
                or not self.thread or not self.thread.is_alive()):
            return empty
        credentials = self.vault.read()
        return dict(url=('http' if kind=='http' else 'ws')+f'://127.0.0.1:{state["ports"][kind]}',
            token=credentials[kind+'_token'], _managed=dict(root=str(self.root), generation=state['generation'],
            instance=state['instance'], account=state['account']))

    def guard(self, marker, *, fresh=True):
        state = self._check(marker['generation'])
        if state.get('phase') != 'ready' or state.get('instance') != marker['instance'] or state.get('account') != marker['account']:
            raise ManagedError('managed_not_ready', 'QQ 托管绑定已失效，未执行操作。')
        if time.time()-state.get('verified_at',0)>12:
            raise ManagedError('managed_not_ready', 'QQ 托管身份验证已过期。')
        if fresh:
            self._verify_identity(state['instance'], state['owner'], local_path(state['installation']['folder']), state['selected'], state['account'])
        if not self.heartbeat or self.heartbeat.status().get('state') != 'connected':
            raise ManagedError('event_disconnected', 'QQ 事件通道未就绪，未执行操作。')


_registry, _registry_lock = {}, threading.RLock()


def manager(root):
    key = str(Path(root).resolve()).casefold()
    with _registry_lock:
        if key not in _registry:
            _registry[key] = ManagedSnowLuma(root)
        return _registry[key]


def connection(root, kind='http'):
    # No access or new folders for untouched existing/manual installations.
    if not (Path(root)/'data/snowluma-managed/state.json').exists():
        return None
    try:
        return manager(root).connection(kind)
    except ManagedError:
        return dict(url='',token='')  # Fail closed; do not silently switch account.


def guard(config, *, fresh=True):
    marker = config.get('_managed')
    if marker:
        manager(marker['root']).guard(marker,fresh=fresh)


def shutdown_all():
    for value in list(_registry.values()):
        value.close()


atexit.register(shutdown_all)
