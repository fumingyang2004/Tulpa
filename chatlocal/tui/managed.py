"""Managed SnowLuma setup, existing SnowLuma import, optional components and the Linux service.

Managed setup follows the native consent flow: inspect a user-chosen folder,
show every agreement and scope item, and only `start` with accepted=true plus
the exact consent fingerprint and selection id the inspection returned. QQ
binding is an explicit choice among verified processes. Tokens never appear.
"""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TextArea

from . import model
from .base import View, esc
from .forms import ChoiceModal, Field, FormModal, required
from .jobs import follow
from .modals import ConfirmModal, Dialog, TextModal

M = '/api/desktop/snowluma/managed'
SCOPE = dict(configure_user_selected_directory_in_place='就地配置你选择的 SnowLuma 文件夹',
             backup_existing_config_before_write='写入前备份已有配置',
             loopback_admin_http_ws='只在本机回环地址开启管理、HTTP 与事件接口',
             manage_random_credentials='生成并保存随机连接凭据',
             bind_one_selected_qq_process='只绑定你选择的一个 QQ 进程',
             resume_same_verified_account='之后只自动恢复同一已验证账号')
COMPONENTS = (('readers', 'QQ/微信本机读取器'), ('voice', '本地语音转写'), ('ocr', '图片 OCR'), ('office', 'Office 文档解析'))
DOCKER_SCOPE = dict(pull_official_pinned_image='下载官方发布的固定版本 SnowLuma 运行环境',
                    manage_owned_container='只管理由 Tulpa 创建的那一个运行环境',
                    persist_qq_data='保留 QQ 登录数据，重启后尝试恢复原账号',
                    loopback_admin_http_ws_novnc='管理、连接与扫码登录页面只在本机回环地址开放',
                    manage_random_credentials='生成并保存随机连接凭据与登录密码',
                    bind_one_verified_qq_account='只绑定一个已验证的 QQ 账号',
                    resume_same_verified_account='之后只自动恢复同一已验证账号')
BUSY = ('detecting', 'pulling', 'starting', 'initializing', 'configuring', 'verifying', 'reconnecting', 'waiting_qq',
        'waiting_login', 'choosing')
WATCH = BUSY + ('ready',)  # Ready keeps a slower poll so a dropped connection still shows up.
SECRET_KEYS = frozenset({'password', 'passwd', 'pwd', 'token', 'secret'})


def safe_url(url):
    """Drop userinfo and any credential-like query item, so a URL never carries the password."""
    url = str(url or '').strip()
    if not url:
        return ''
    try:
        parts = urlsplit(url)
        host = parts.hostname or ''
        if ':' in host:
            host = f'[{host}]'
        netloc = host + (f':{parts.port}' if parts.port else '')
        query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                           if k.lower() not in SECRET_KEYS])
        fragment = '' if any(k in parts.fragment.lower() for k in SECRET_KEYS) else parts.fragment
        return urlunsplit((parts.scheme, netloc, parts.path, query, fragment))
    except ValueError:
        return ''


def url_port(url):
    try:
        return urlsplit(url).port
    except ValueError:
        return None


def is_docker(status):
    return (status or {}).get('backend') == 'docker'


def docker_plan(s):
    """What the Docker panel's buttons may do for this status; nothing here acts.

    backend defaults to 'local' even when nothing is bound, so only a real local
    installation (a folder) blocks Docker, matching the backend's backend_bound rule.
    """
    s = s if isinstance(s, dict) else {}
    docker, enabled = is_docker(s), bool(s.get('enabled'))
    plan = dict(label='启用 Docker 托管', action=None, danger=False,
                login=bool(docker and enabled and s.get('login_available')),
                logs=bool(docker and s.get('container_id')))
    if 'docker' not in (s.get('supported_backends') or ()):
        plan['note'] = '当前平台不支持 Docker 托管；可按 e 导入已有 SnowLuma 连接。'
    elif not docker and s.get('folder'):
        plan['note'] = '此 Tulpa 已绑定本机文件夹托管；Docker 托管请使用独立的 Tulpa 实例。'
    elif docker and enabled:
        plan.update(label='停止 Docker 托管', action='cancel', danger=True,
                    note=('托管出错，可按 t 重试；' if s.get('phase') == 'error' else '') + '停止后保留 QQ 登录数据和授权。'
                    + ('' if plan['login'] or s.get('phase') == 'ready' else '扫码入口准备好后即可扫码登录。'))
    elif docker and s.get('consent_current') and s.get('phase') != 'revoked':
        plan.update(action='retry', note='授权仍有效，将直接重新启动运行环境，通常无需再次扫码。')
    else:
        plan.update(action='docker_inspect', note='先检测 Docker 环境；阅读并勾选同意协议后才会启动。')
    return plan


def docker_state(s):
    """One status line: whether the container is managed, and separately whether QQ is logged in."""
    s = s if isinstance(s, dict) else {}
    if not is_docker(s):
        return '运行环境 [#7f8a9a]未启用[/]'
    phase = s.get('phase') or ''
    if not s.get('enabled'):
        run = '[#7f8a9a]已撤销授权[/]' if phase == 'revoked' else \
            '[#7f8a9a]已停止[/]' + ('（授权保留）' if s.get('consent_current') else '')
        return f'运行环境 {run}'
    color = '#22c55e' if phase == 'ready' else '#ef4444' if phase == 'error' else '#eab308'
    run = f'[{color}]已启用 · {esc(s.get("phase_label") or phase or "—")}[/]'
    if phase == 'ready':
        qq = '[#22c55e]已登录[/]' + (f' {esc(s["account"])}' if s.get('account') else '')
    elif phase == 'waiting_login':
        qq = '[#eab308]等待扫码登录[/]'
    else:
        qq = '[#7f8a9a]未就绪[/]'
    return f'运行环境 {run}  ·  QQ {qq}'


def folder_form(title, lead):
    return FormModal(title, [Field('folder', 'SnowLuma 文件夹', placeholder='/home/you/SnowLuma（已解压的绝对路径）')],
                     lambda v: required(v['folder'], '文件夹路径', 2048), '检测', lead=lead)


def flag(value, yes='是', no='否'):
    return f'[#22c55e]{yes}[/]' if value else f'[#7f8a9a]{no}[/]'


class LoginModal(ModalScreen):
    """Scan-to-login entry for the Docker backend.

    The password is held only by this screen's read-only box and dropped on close;
    it is never copied into the status card, the state line or a notification.
    """

    BINDINGS = [Binding('escape', 'close', '关闭')]

    def __init__(self, result):
        super().__init__()
        result = result if isinstance(result, dict) else {}
        self.novnc = safe_url(result.get('novnc_url'))
        self.webui = safe_url(result.get('webui_url'))
        self._password = str(result.get('password') or '')

    def compose(self) -> ComposeResult:
        ports = sorted({p for p in (url_port(self.novnc), url_port(self.webui)) if p})
        forward = ' '.join(f'-L {p}:127.0.0.1:{p}' for p in ports)
        with Dialog(classes='dialog -secret'):
            yield Static('扫码登录 QQ', classes='dialog-title')
            with VerticalScroll(classes='dialog-flex'):
                yield Static('在浏览器打开登录页面，输入下方密码后用手机 QQ 扫码。登录完成后本页会自动更新状态。', classes='hint')
                yield Static('登录页面', classes='card-title')
                yield Static(esc(self.novnc or '—'), classes='secret-box', id='login-url')
                if self.webui:
                    yield Static('SnowLuma 管理页面', classes='card-title')
                    yield Static(esc(self.webui), classes='secret-box', id='login-webui')
                yield Static('登录页面密码', classes='card-title')
                yield TextArea(self._password or '（未取得密码，请关闭后重试）', read_only=True, id='login-password', soft_wrap=True)
                yield Static('[b #eab308]密码只在此处显示，关闭后不会保留。[/] 请勿分享给他人。', classes='warn')
                if forward:
                    yield Static('如果 Tulpa 运行在远程服务器上，页面只在该服务器本机开放。'
                                 '请在你自己的电脑上运行下面的命令后，再用本机浏览器打开上述地址：', classes='hint')
                    yield Static(esc(f'ssh -N {forward} 用户名@服务器地址'), classes='secret-box', id='login-ssh')
                yield Static('在文本框中可用鼠标或 ctrl+a / ctrl+c 复制（取决于终端）。', classes='hint')
            with Horizontal(classes='buttons'):
                yield Button('关闭 (Esc)', id='close', variant='primary')

    def on_mount(self):
        self.query_one('#login-password').styles.height = 3
        self.query_one('#login-password').focus()

    def forget(self):
        self._password = ''
        for box in self.query('#login-password'):
            box.load_text('')

    def on_unmount(self):
        self._password = ''  # Widgets may already be gone; the box itself dies with the screen.

    def on_button_pressed(self, event):
        event.stop()
        self.action_close()

    def action_close(self):
        self.forget()
        self.dismiss(None)


class ManagedView(View):
    title = 'SL 托管'
    subtitle = '由 Tulpa 托管 SnowLuma（本机文件夹或 Docker），或导入已有 SnowLuma 连接'
    BINDINGS = [Binding('i', 'inspect', '检测文件夹'), Binding('d', 'docker_inspect', 'Docker 托管'),
                Binding('b', 'login', '扫码登录'), Binding('l', 'logs', '最近日志'),
                Binding('c', 'choose', '选择 QQ'), Binding('t', 'retry', '重试/启动'),
                Binding('x', 'cancel', '停止托管'), Binding('R', 'revoke', '撤销授权'), Binding('e', 'existing', '导入已有连接'),
                Binding('o', 'test', '检测连接')]

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        # Outside the scrolling body, so the Docker buttons never scroll away or sit below the status card.
        with Vertical(id='mg-docker'):
            yield Static('[b #22d3ee]SL Docker 托管[/]', id='mg-docker-title')
            yield Static('运行环境 [#7f8a9a]读取中…[/]', id='mg-docker-state')
            with Horizontal(id='mg-docker-buttons'):
                yield Button('启用 Docker 托管', id='mg-docker-toggle', variant='primary', disabled=True)
                yield Button('扫码登录', id='mg-docker-login', disabled=True)
                yield Button('查看日志', id='mg-docker-logs', disabled=True)
            yield Static('', classes='hint', id='mg-docker-note')
        with VerticalScroll(classes='page-flex', id='mg-body'):
            yield Static('', classes='card', id='mg-status')
            yield Static('', classes='detail', id='mg-result')
        yield Static('i 本机文件夹托管 · d Docker 托管 · b 扫码登录 · l 最近日志 · c 选择 QQ · t 重试 · x 停止 · '
                     'R 撤销授权 · e 导入已有连接 · o 检测已保存连接', classes='hint')

    def on_mount(self):
        self.status = {}
        self.acting = False
        self.focus_pending = False
        self.poll_interval = None
        self.query_one('#mg-result').display = False

    def focus_main(self):
        toggle = self.query_one('#mg-docker-toggle', Button)
        if not toggle.disabled:
            toggle.focus()
            return
        # Not loaded yet: keep page keys working, and move to the main button once it is usable.
        self.focus_pending = 'mg-docker-toggle'
        self.query_one('#mg-body', VerticalScroll).focus()

    def refresh_data(self):
        self.start_load(lambda c: c.api_get(M), self.show, '正在读取托管状态…')

    # ---- Docker panel -----------------------------------------------------

    def pending(self):
        """A load or action is in flight; the status on screen may be stale, so nothing should act on it."""
        return bool(self.busy or getattr(self, 'acting', False))

    def set_state(self, kind, text=''):
        super().set_state(kind, text)
        self.sync_docker()  # Every load/action transition passes through here.

    def act(self, fn, ok=None, text='正在处理…', error='操作失败'):
        """As View.act, but the panel buttons stay disabled until the request settles."""
        self.acting = True
        self.set_state('loading', text)

        def done(result):
            self.acting = False
            self.set_state('ok')
            if ok:
                ok(result)

        def failed(message):
            self.acting = False
            self.set_state('error', f'{error}：{message}')
        self.app.request(fn, done, failed)

    def sync_docker(self):
        if not self.is_mounted or not hasattr(self, 'status'):
            return
        s, plan = self.status, docker_plan(self.status)
        hold = self.pending() or not self.loaded
        toggle = self.query_one('#mg-docker-toggle', Button)
        toggle.label = plan['label']
        toggle.variant = 'error' if plan['danger'] else 'primary'
        enabled = {'mg-docker-toggle': not (hold or not plan['action']),
                   'mg-docker-login': not (hold or not plan['login']),
                   'mg-docker-logs': not (hold or not plan['logs'])}
        self.park_focus(enabled)
        for bid, ok in enabled.items():
            self.query_one(f'#{bid}', Button).disabled = not ok
        self.query_one('#mg-docker-state', Static).update(
            docker_state(s) if self.loaded else '运行环境 [#7f8a9a]读取中…[/]')
        self.query_one('#mg-docker-note', Static).update(esc(plan.get('note') or '') if self.loaded else '')
        self.restore_focus(enabled)

    def park_focus(self, enabled):
        """Disabling the focused button blurs it to nothing, and page keys (d/t/R…) then go nowhere.

        Move focus to the page body first and remember which button to give it back to.
        """
        focused = self.screen.focused
        if focused is not None and focused.id in enabled and not enabled[focused.id] and focused.parent \
                and focused.parent.id == 'mg-docker-buttons':
            self.focus_pending = focused.id
            # Synchronous, not focus(): the disabled watcher blurs right away, before a deferred focus would run.
            self.screen.set_focus(self.query_one('#mg-body', VerticalScroll), scroll_visible=False)

    def restore_focus(self, enabled):
        """Give focus back only if the user is still here, no dialog is open and focus was not moved elsewhere."""
        if not self.focus_pending:
            return
        if self.app.screen is not self.screen:
            return  # A dialog is open; decide once it has closed and the panel next refreshes.
        here = self.display and getattr(self.app, 'current', self.id) == self.id
        if not here or self.screen.focused not in (None, self, self.query_one('#mg-body')):
            self.focus_pending = False  # The user went to the sidebar, another page or another control.
            return
        target = self.focus_pending if self.focus_pending in enabled else 'mg-docker-toggle'
        if not enabled.get(target):
            target = 'mg-docker-toggle'
        if not enabled.get(target):
            return  # Still busy or unusable; focus stays in the page body so page keys keep working.
        self.focus_pending = False
        self.screen.set_focus(self.query_one(f'#{target}', Button))

    def on_button_pressed(self, event):
        handlers = {'mg-docker-toggle': self.docker_toggle, 'mg-docker-login': self.action_login,
                    'mg-docker-logs': self.action_logs}
        handler = handlers.get(event.button.id)
        if handler:
            event.stop()
            handler()

    def docker_toggle(self):
        action = docker_plan(self.status)['action']
        if action and not self.pending():
            getattr(self, f'action_{action}')()

    def guard(self):
        """Key presses during a load/action would act on stale status; refuse them like the disabled buttons."""
        if self.pending():
            self.app.notify('正在处理，请稍候再操作。', title='SL 托管')
            return True
        return False

    def show(self, status):
        self.status = status if isinstance(status, dict) else {}
        self.sync_docker()
        s = self.status
        docker = is_docker(s)
        rows = [('方式', 'Docker 运行环境' if docker else ('本机文件夹' if s.get('backend') == 'local' or s.get('folder') else '—')),
                ('阶段', f'[b]{esc(s.get("phase_label") or s.get("phase") or "—")}[/]'),
                ('版本', esc(s.get('version') or '—') + ('' if s.get('real_pc_verified') else '  [#eab308]尚未在真机完整验证[/]'))]
        if docker:
            container = s.get('container_name') or '—'
            if s.get('container_id'):
                container += f' ({str(s["container_id"])[:12]})'
            login = safe_url(s.get('novnc_url'))
            rows += [('镜像', esc(s.get('image') or '—')), ('容器', esc(container)),
                     ('账号', esc(s.get('account') or '—')),
                     ('QQ 就绪', flag(s.get('qq_ready'), '就绪', '未就绪')), ('授权', flag(s.get('consent_current'), '有效', '未授权')),
                     ('登录入口', (esc(login) + '  [#7f8a9a]按 b 打开（含密码）[/]') if login else '—')]
        else:
            rows += [('文件夹', esc(s.get('folder') or '—')), ('账号', esc(s.get('account') or '—')),
                     ('QQ 就绪', flag(s.get('qq_ready'), '就绪', '未就绪')), ('授权', flag(s.get('consent_current'), '有效', '未授权')),
                     ('托管进程', esc(s.get('service_pid') or '—')), ('QQ 进程', esc(s.get('qq_pid') or '—'))]
        if s.get('message'):
            rows.append(('说明', esc(s['message'])))
        if s.get('code'):
            rows.append(('代码', esc(s['code'])))
        if docker and s.get('phase') == 'waiting_login':
            rows.append(('操作', '[#eab308]请按 b 打开扫码登录[/]'))
        elif s.get('selection_required'):
            rows.append(('操作', '[#eab308]请按 c 选择要绑定的 QQ[/]'))
        if s.get('security_note'):
            rows.append(('安全', f'[#7f8a9a]{esc(s["security_note"])}[/]'))
        self.query_one('#mg-status', Static).update(
            '[b #a78bfa]托管 SnowLuma[/]\n' + '\n'.join(f'[#7f8a9a]{esc(k)}[/]  {v}' for k, v in rows))
        if not s.get('available', True):
            if 'docker' in (s.get('supported_backends') or ()):
                self.set_state('info', '本机文件夹托管在此平台不可用；可按 d 使用 Docker 托管，或按 e 导入已有连接。')
            else:
                self.set_state('info', '当前平台不提供托管模式；可按 e 导入已有 SnowLuma 连接。')
        self.watch_status(s)

    def watch_status(self, s):
        """Keep one status loop: fast while working, slower once ready so a disconnect still shows."""
        if not (s.get('enabled') and s.get('phase') in WATCH):
            return
        interval = 3 if s.get('phase') in BUSY else 10
        # Restarting on every tick would fire the next request immediately; only restart on change.
        if not self.polling('managed') or interval != self.poll_interval:
            self.poll_interval = interval
            self.poll('managed', lambda c: c.api_get(M), self.tick, interval=interval)

    def tick(self, status):
        self.show(status)
        status = status if isinstance(status, dict) else {}
        return bool(status.get('enabled')) and status.get('phase') in WATCH

    def managed_action(self, action, body, label, after=None):
        """POST one managed action. `after(result)` may return a (kind, text) note kept across the refresh."""
        def ok(result):
            result = result if isinstance(result, dict) else {}
            if result.get('ok') is False:
                self.set_state('error', f'{label}未完成：{result.get("message") or result.get("code") or "未知原因"}')
                return
            note = after(result) if after else ('info', f'{label}完成。')
            if note:
                self.reload_with(*note)
            else:
                self.refresh_data()
        self.act(lambda c: c.api_post(f'{M}/{action}', body), ok, f'正在{label}…', f'{label}失败')

    def action_inspect(self):
        if self.guard():
            return
        def chosen(folder):
            if folder:
                self.managed_action('inspect', dict(folder=folder), '检测文件夹', self.consent)
        self.app.push_screen(folder_form('检测 SnowLuma 文件夹', '只读取所选文件夹，检测不会修改任何文件或启动程序。'), chosen)

    def consent(self, preview):
        if preview.get('mode') != 'local':
            self.show_result(f'{preview.get("message") or ""}\n此文件夹已有自己的配置；请按 e 导入已有连接，不会重置它的管理密码。')
            return 'info', '这是已有 SnowLuma 安装，请使用导入流程。'
        head = [f'文件夹：{preview.get("folder")}', f'SnowLuma 版本：{preview.get("version")}', str(preview.get('message') or '')]
        self.ask_consent(preview, head, self.status.get('agreements') or [], self.status.get('scope') or [], SCOPE,
                         '协议或文件夹变化后需要重新同意；可随时停止或撤销授权。', 'start',
                         ('info', '已开始托管；请按提示打开并登录 QQ，需要时按 c 选择。'))

    def ask_consent(self, preview, head, agreements, scope, labels, tail, action, started):
        """Show this preview's agreements and scope; the checkbox starts unticked and is never pre-accepted."""
        lines = [*head, '', '需要你阅读并同意以下协议：']
        lines += [f'· {a.get("title")}' + (f' {a.get("version")}' if a.get('version') else '') + f'\n  {a.get("url")}'
                  f'\n  SHA256 {a.get("sha256")}' for a in agreements if isinstance(a, dict)]
        lines += ['', '授权 Tulpa 执行以下操作：'] + [f'· {labels.get(item, item)}' for item in scope]
        lines += ['', tail]
        body = dict(accepted=True, fingerprint=preview.get('consent_fingerprint'), selection_id=preview.get('selection_id'))

        def validate(v):
            if not v['accepted']:
                raise ValueError('请先勾选同意协议与上述授权范围。')
            return body

        def done(result):
            if result:
                self.managed_action(action, result, '开始托管', lambda r: started)
        self.app.push_screen(FormModal('同意协议并授权托管', [
            Field('terms', '\n'.join(lines), 'note'),
            Field('accepted', '我已阅读并同意上述协议，授权 Tulpa 执行上述操作', 'check', False),
        ], validate, '同意并开始'), done)

    # ---- Docker backend ---------------------------------------------------

    def action_docker_inspect(self):
        if self.guard():
            return
        self.managed_action('docker-inspect', {}, '检测 Docker', self.docker_consent)

    def docker_consent(self, preview):
        if preview.get('mode') != 'docker':
            return 'error', '检测结果不是 Docker 托管，请刷新后重试。'
        head = ['方式：Docker 运行环境（支持 Linux / macOS）', f'SnowLuma 版本：{preview.get("version") or "—"}',
                f'运行环境：{preview.get("image") or "—"}' + (f'（{preview["platform"]}）' if preview.get('platform') else ''),
                str(preview.get('message') or ''), '首次启动需要下载运行环境，可能需要几分钟并占用一定磁盘空间。']
        self.ask_consent(preview, head, preview.get('agreements') or [], preview.get('scope') or [], DOCKER_SCOPE,
                         '协议或运行环境版本变化后需要重新同意；可随时停止或撤销授权，QQ 登录数据会保留。', 'docker-start',
                         ('info', '已开始 Docker 托管；准备完成后按 b 扫码登录 QQ。'))

    def action_login(self):
        if self.guard():
            return
        if not is_docker(self.status):
            self.set_state('info', '扫码登录只用于 Docker 托管；本机文件夹托管请直接在 QQ 中登录。')
            return

        def ok(result):
            result = result if isinstance(result, dict) else {}
            if result.get('ok') is False:
                self.set_state('error', f'打开扫码登录未完成：{result.get("message") or result.get("code") or "未知原因"}')
                return
            if not safe_url(result.get('novnc_url')):
                self.set_state('error', '登录页面尚未就绪，请稍后再试。')
                return
            self.app.push_screen(LoginModal(result))  # The result dict is not kept by the view.
        self.act(lambda c: c.api_post(f'{M}/docker-login', {}), ok, '正在打开扫码登录…', '打开扫码登录失败')

    def action_logs(self):
        if self.guard():
            return
        if not is_docker(self.status):
            self.set_state('info', '最近日志只用于 Docker 托管。')
            return

        def ok(result):
            result = result if isinstance(result, dict) else {}
            if result.get('ok') is False:
                self.set_state('error', f'读取日志未完成：{result.get("message") or result.get("code") or "未知原因"}')
                return
            lines = result.get('lines') if isinstance(result.get('lines'), list) else []
            text = '\n'.join(str(line) for line in lines) or '（暂无日志）'
            name = result.get('container_name') or self.status.get('container_name') or ''
            self.app.push_screen(TextModal('最近日志' + (f'：{name}' if name else ''), text,
                                           '[#7f8a9a]只显示最近的日志，敏感信息已隐藏；不会保存到文件。[/]'))
        self.act(lambda c: c.api_post(f'{M}/docker-logs', {}), ok, '正在读取最近日志…', '读取日志失败')

    def action_choose(self):
        if self.guard():
            return
        if is_docker(self.status):
            self.set_state('info', 'Docker 托管只绑定扫码登录的那一个 QQ 账号，无需选择。')
            return
        choices = self.status.get('choices') or []
        if self.status.get('phase') != 'choosing' or not choices:
            self.set_state('info', '当前不是选择 QQ 阶段。')
            return
        options = [(f'PID {c.get("pid")} · {c.get("account_label") or c.get("account") or "账号待验证"} · '
                    f'{model.one_line(c.get("path"), 50)}', c.get('id')) for c in choices]

        def picked(cid):
            if cid:
                self.managed_action('select', dict(choice_id=cid), '绑定 QQ')
        self.app.push_screen(ChoiceModal('选择要绑定的 QQ', options, '只绑定所选进程；已绑定其他账号时不会自动换号。'), picked)

    def action_retry(self):
        if self.guard():
            return
        if not self.status.get('consent_current'):
            self.set_state('info', '尚未授权托管：按 d 检测 Docker 或按 i 检测文件夹，并同意协议。'
                           if is_docker(self.status) or 'docker' in (self.status.get('supported_backends') or ())
                           else '尚未授权托管：按 i 检测文件夹并同意协议。')
            return
        self.managed_action('retry', {}, '重试托管')

    def action_cancel(self):
        if self.guard():
            return
        def confirmed(yes):
            if yes:
                self.stop_poll('managed')
                self.managed_action('cancel', {}, '停止托管')
        if is_docker(self.status):
            body = ('停止 Tulpa 管理的 Docker 运行环境，其中的 QQ 和 SnowLuma 都会关闭；持续群聊会话将停止。\n'
                    'QQ 登录数据和授权都会保留，可按 t 重新启动，通常无需再次扫码。')
        else:
            body = ('停止 Tulpa 启动的 SnowLuma；持续群聊会话将停止。\n'
                    '只停止 Tulpa 托管的进程，不会关闭 QQ 或外部实例。授权保留，可按 t 重新启动。')
        self.app.push_screen(ConfirmModal('停止托管', body, '停止', danger=True), confirmed)

    def action_revoke(self):
        if self.guard():
            return
        def confirmed(yes):
            if yes:
                self.stop_poll('managed')
                self.managed_action('revoke', {}, '撤销授权')
        if is_docker(self.status):
            body = ('停止 Docker 运行环境（其中的 QQ 和 SnowLuma 会关闭），并撤销协议同意与授权。\n'
                    'QQ 登录数据仍会保留；之后需要按 d 重新检测并主动同意才能再次托管。')
        else:
            body = '停止托管并撤销协议同意与授权。\n之后需要重新检测文件夹并主动同意才能再次托管。'
        self.app.push_screen(ConfirmModal('撤销托管授权', body, '撤销', danger=True), confirmed)

    def action_test(self):
        if self.guard():
            return
        def ok(result):
            result = result if isinstance(result, dict) else {}
            self.show_result(f'{"通过" if result.get("ok") else "未通过"}：{result.get("message") or ""}'
                             + (f'\n账号 {result["account"]}' if result.get('account') else ''))
        self.act(lambda c: c.api_post('/api/desktop/onebot/test', {}), ok, '正在检测已保存的连接…', '检测失败')

    def show_result(self, text):
        box = self.query_one('#mg-result', Static)
        box.update(esc(text))
        box.display = True

    # ---- existing SnowLuma import -----------------------------------------

    def action_existing(self):
        if self.guard():
            return
        def chosen(folder):
            if not folder:
                return

            def ok(result):
                if result.get('ok') is False:
                    self.set_state('error', f'识别失败：{result.get("message") or result.get("code")}')
                    return
                self.pick_account(result)
            self.act(lambda c: c.api_post('/api/desktop/snowluma/inspect', dict(folder=folder)), ok, '正在识别配置…', '识别失败')
        self.app.push_screen(folder_form('导入已有 SnowLuma 连接', '只读取 config 中的账号与节点；不会修改 SnowLuma 配置。'), chosen)

    def pick_account(self, preview):
        accounts = preview.get('accounts') or []
        usable = [a for a in accounts if a.get('code') == 'ok']
        if not usable:
            self.show_result('\n'.join(f'{a.get("account")}：{a.get("message")}' for a in accounts) or preview.get('message') or '')
            self.set_state('error', '没有可导入的账号节点。')
            return

        def node_label(n):
            return f'{n.get("name")} · {n.get("url") or "—"}' + (' · 有凭据' if n.get('token_present') else '') + \
                ('' if n.get('usable') else f' · 不可用：{n.get("reason") or ""}')

        def picked(account):
            if not account:
                return
            a = next(x for x in usable if x.get('account') == account)
            http = [(node_label(n), n.get('id')) for n in a['nodes'].get('http') or [] if n.get('usable')]
            ws = [('不导入事件节点（持续群聊不可用）', '')] + [(node_label(n), n.get('id')) for n in a['nodes'].get('ws') or [] if n.get('usable')]

            def validate(v):
                if not v['http_id']:
                    raise ValueError('请选择 HTTP 节点。')
                return dict(selection_id=preview.get('selection_id'), account=account, http_id=v['http_id'], ws_id=v['ws_id'] or '')

            def done(body):
                if body:
                    self.import_existing(body)
            self.app.push_screen(FormModal(f'选择节点：{account}', [
                Field('http_id', 'HTTP 节点', 'select', http[0][1] if len(http) == 1 else None, http),
                Field('ws_id', '事件节点', 'select', ws[1][1] if len(ws) == 2 else '', ws),
                Field('note', str(a.get('message') or ''), 'note'),
            ], validate, '检测并导入', lead='导入前会用所选节点验证账号；验证失败不会保存，原连接保持不变。'), done)
        self.app.push_screen(ChoiceModal('选择 QQ 账号', [(f'{a.get("account")} · {a.get("message")}', a.get('account'))
                                                         for a in usable], f'SnowLuma {preview.get("version")} · {preview.get("folder")}'),
                             picked)

    def import_existing(self, body):
        def ok(result):
            result = result if isinstance(result, dict) else {}
            self.show_result(str(result.get('message') or ''))
            if result.get('saved'):
                self.set_state('info', '已导入并保存连接。')
                self.app.show_status('SnowLuma 连接已导入')
            else:
                self.set_state('error', '未保存：' + str(result.get('message') or result.get('code') or ''))
        self.act(lambda c: c.api_post('/api/desktop/snowluma/import', body), ok, '正在验证并导入…', '导入失败')


class ComponentsView(View):
    title = '组件与服务'
    subtitle = '可选运行组件的真实安装状态；安装只在你选择后进行'
    BINDINGS = [Binding('I', 'install', '安装组件'), Binding('v', 'voice_model', '语音模型'),
                Binding('x', 'cancel_voice', '取消模型下载')]

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        with VerticalScroll(classes='page-flex'):
            with Horizontal(classes='cards'):
                yield Static('', classes='card', id='cp-main')
                yield Static('', classes='card', id='cp-voice')
            yield Static('', classes='card', id='cp-service')
            yield Static('', classes='detail', id='cp-job')
        yield Static('I 安装所选组件 · v 下载语音模型（固定来源与 SHA256 校验）· x 取消模型下载 · 服务安装请使用 ./tulpa 命令行',
                     classes='hint')

    def on_mount(self):
        self.components, self.voice = {}, {}
        self.query_one('#cp-job').display = False

    def refresh_data(self):
        def fetch(c):
            return c.get('/components'), c.get('/service'), c.api_get('/api/components/voice')
        self.start_load(fetch, self.show, '正在检查组件…')

    def show(self, result):
        comps, service, voice = result
        self.components = comps if isinstance(comps, dict) else {}
        self.voice = voice if isinstance(voice, dict) else {}
        c = self.components
        rows = [f'Agent 运行环境  {flag((c.get("agent") or {}).get("installed"), "已安装", "未安装")}']
        for key, label in COMPONENTS:
            if key != 'voice':
                rows.append(f'{label}  {flag((c.get(key) or {}).get("installed"), "已安装", "未安装")}')
        readers = c.get('readers') or {}
        if readers:
            rows.append(f'[#7f8a9a]架构 {esc(readers.get("architecture") or "—")} · 主动取密钥 '
                        f'{"支持" if readers.get("active_key_supported") else "不支持"}[/]')
            if readers.get('detail'):
                rows.append(f'[#7f8a9a]{esc(model.one_line(readers["detail"], 120))}[/]')
        self.query_one('#cp-main', Static).update('[b #a78bfa]组件[/]\n' + '\n'.join(rows))
        v = c.get('voice') or {}
        m = v.get('model') if isinstance(v.get('model'), dict) else self.voice
        progress = f'{model.size_text(m.get("downloaded"))} / {model.size_text(m.get("total"))}' if m.get('total') else ''
        self.query_one('#cp-voice', Static).update('[b #a78bfa]本地语音[/]\n' + '\n'.join([
            f'运行库  {flag(v.get("installed"), "已安装", "未安装")}', f'可执行文件  {flag(v.get("executable"), "就绪", "缺失")}',
            f'编译工具  {flag(v.get("build_tools"), "可用", "缺失")}',
            f'模型  {esc(m.get("status") or "—")} {esc(progress)}', f'[#7f8a9a]{esc(m.get("message") or "")}[/]']))
        s = service if isinstance(service, dict) else {}
        lines = [f'可用 {flag(s.get("available"))} · 已安装 {flag(s.get("installed"))} · 运行 {flag(s.get("active"), "运行中", "未运行")}'
                 + (f' · 开机启动 {flag(s.get("enabled"))}' if 'enabled' in s else '')]
        if s.get('state') or s.get('substate'):
            lines.append(f'状态 {esc(s.get("state") or "")} {esc(s.get("substate") or "")}')
        if s.get('detail'):
            lines.append(f'[#7f8a9a]{esc(model.one_line(s["detail"], 140))}[/]')
        lines.append('[#7f8a9a]用户级服务由 ./tulpa 命令行安装和管理；本界面只显示状态。[/]')
        self.query_one('#cp-service', Static).update('[b #a78bfa]本机后台服务[/]\n' + '\n'.join(lines))
        if m.get('status') in ('downloading', 'cancelling'):
            self.poll('voice', lambda cl: cl.api_get('/api/components/voice'), self.voice_tick, interval=2)

    def voice_tick(self, state):
        state = state if isinstance(state, dict) else {}
        self.voice = state
        box = self.query_one('#cp-job', Static)
        box.update(f'[b #a78bfa]语音模型[/] {esc(state.get("status"))} '
                   f'{esc(model.size_text(state.get("downloaded")))} / {esc(model.size_text(state.get("total")))}\n'
                   f'{esc(state.get("message") or "")}')
        box.display = True
        if state.get('status') in ('downloading', 'cancelling'):
            return True
        self.reload_with('info' if state.get('status') == 'installed' else 'error', state.get('message') or '')
        return False

    def action_install(self):
        missing = [(f'{label}' + ('（已安装，将核对并补齐）' if (self.components.get(key) or {}).get('installed') else ''), key)
                   for key, label in COMPONENTS]

        def picked(name):
            if not name:
                return
            label = dict(COMPONENTS)[name]

            def confirmed(yes):
                if yes:
                    self.act(lambda c: c.post(f'/components/{name}/install', {}),
                             lambda r: follow(self, (r or {}).get('job_id'), kind='component', box=self.query_one('#cp-job'),
                                              label=f'安装{label}', done=lambda job: self.refresh_data()),
                             f'正在启动{label}安装…', '安装失败')
            self.app.push_screen(ConfirmModal(f'安装{label}', f'只安装{label}缺少的运行库和依赖（版本已固定）。\n'
                                              '可能需要联网下载并占用磁盘空间；不会安装其他组件。', '安装'), confirmed)
        self.app.push_screen(ChoiceModal('选择要安装的组件', missing), picked)

    def action_voice_model(self):
        if self.voice.get('status') == 'installed':
            self.set_state('info', '语音模型已安装并通过校验。')
            return
        total = model.size_text(self.voice.get('total'))

        def confirmed(yes):
            if yes:
                self.act(lambda c: c.api_post('/api/components/voice', dict(action='install')),
                         lambda r: self.poll('voice', lambda cl: cl.api_get('/api/components/voice'), self.voice_tick, 2),
                         '正在开始下载…', '下载失败')
        self.app.push_screen(ConfirmModal('下载本地语音模型', f'从固定来源下载 whisper.cpp 模型（约 {total}），完成后校验 SHA256。\n'
                                          '校验失败不会替换已有模型。', '下载'), confirmed)

    def action_cancel_voice(self):
        if self.voice.get('status') != 'downloading':
            self.set_state('info', '没有正在进行的模型下载。')
            return
        self.act(lambda c: c.api_post('/api/components/voice', dict(action='cancel')),
                 lambda r: self.set_state('info', '已请求取消下载。'), '正在取消…', '取消失败')
