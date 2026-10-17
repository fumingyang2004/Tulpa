"""Tulpa terminal application shell: header, sidebar, view switcher, workers."""
import asyncio
import copy
import threading

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import ContentSwitcher, Footer, OptionList, Static
from textual.widgets.option_list import Option

from .agent import AgentView
from .base import esc
from .chats import ChatsView
from .easter_egg import EasterEggModal, SequenceTracker, eligible
from .grants import GrantsView
from .instances import DEFAULT_ID, DEFAULT_NAME, InstancesView, display_name
from .managed import ComponentsView, ManagedView
from .materials import ArtifactsView, CollectionsView, KnowledgeView
from .memories import MemoriesView
from .native import NativeView
from .sessions import SessionsView
from .styles import CSS
from .views import EventsView, OverviewView, PersonasView, SearchView, SLView
from .watches import WatchesView
from .workspaces import WorkspacesView

try:
    from chatlocal.terminal_client import TerminalClient, TerminalError
except ImportError:  # Backend client not present yet; keep the UI importable for tests.
    TerminalClient = None

    class TerminalError(Exception):
        """Fallback safe user-facing error (the backend class replaces this)."""

DEFAULT_URL = 'http://127.0.0.1:7861'
VIEWS = (
    ('overview', '概览', OverviewView),
    ('instances', '实例管理', InstancesView),
    ('sl', 'SL 连接', SLView),
    ('managed', 'SL 托管', ManagedView),
    ('grants', 'MCP 授权', GrantsView),
    ('personas', '人设', PersonasView),
    ('sessions', '持续会话', SessionsView),
    ('search', '本地搜索', SearchView),
    ('events', '工具调用', EventsView),
    ('agent', 'Agent 对话', AgentView),
    ('chats', '聊天记录', ChatsView),
    ('native', '本机数据', NativeView),
    ('memories', '记忆', MemoriesView),
    ('watches', '关注卡', WatchesView),
    ('artifacts', '文件材料', ArtifactsView),
    ('collections', '证据集合', CollectionsView),
    ('knowledge', 'QQ 群资料', KnowledgeView),
    ('workspaces', '调查工作区', WorkspacesView),
    ('components', '组件与服务', ComponentsView),
)
# Single keys only: Textual cannot bind multi-digit sequences. 1–7 are the original
# pages; 8/9/0 reach the most used new ones; every page is in the scrolling sidebar.
# Explicit, not positional: SL 托管 sits next to SL 连接 in the sidebar without a key.
SHORTCUTS = {'1': 'overview', '2': 'sl', '3': 'grants', '4': 'personas', '5': 'sessions', '6': 'search',
             '7': 'events', '8': 'agent', '9': 'chats', '0': 'native'}
KEY_OF = {view: key for key, view in SHORTCUTS.items()}
KEY_OF['instances'] = 'F2'
# Talks only to the original hub backend, so it survives instance switches and outages.
HUB_VIEW = 'instances'
SLOW_TIMEOUT = 75  # Instance start may take up to 60 s on the hub.
# Global keys that must not act behind an open dialog (e.g. q/r in a read-only text box).
MODAL_BLOCKED = frozenset({'goto', 'reload', 'focus_sidebar', 'quit_app'})
STATE_LABEL = dict(unknown=('未检查', '#7f8a9a'), connecting=('连接中…', '#eab308'),
                   online=('已连接', '#22c55e'), offline=('离线', '#ef4444'))


def default_client_factory(base_url=None, token_file=None):
    def factory():
        if TerminalClient is None:
            raise TerminalError('未找到 chatlocal.terminal_client，请更新 Tulpa 后端。')
        return TerminalClient(base_url=base_url, token_file=token_file)
    return factory


def close_quietly(client):
    if client is not None and hasattr(client, 'close'):
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def safe_message(exc):
    """Only TerminalError text is user-facing by contract; anything else stays generic."""
    if isinstance(exc, TerminalError):
        return str(exc) or '请求失败。'
    return f'请求失败（{type(exc).__name__}）。'


def reached_server(exc):
    """An HTTP status on the error means the service answered, so it is not offline."""
    for name in ('status', 'status_code', 'code'):
        value = getattr(exc, name, None)
        if isinstance(value, int) and 100 <= value < 600:
            return True
    return False


class Header(Static):
    def show(self, state, base_url, version='', mcp=None, instance=''):
        label, color = STATE_LABEL[state]
        parts = ['[b #22d3ee]Tulpa[/]', f'[b #fbbf24]实例：{esc(instance)}[/]' if instance else '',
                 f'[#7f8a9a]{esc(version)}[/]' if version else '',
                 f'[{color}]● 后台{label}[/]', f'[#7f8a9a]{esc(base_url)}[/]']
        if state == 'online' and isinstance(mcp, dict):
            parts.append('[#a78bfa]MCP 运行中[/]' if mcp.get('running') else '[#7f8a9a]MCP 未运行[/]')
        self.update('  '.join(p for p in parts if p))


class OfflinePanel(Static):
    def show(self, message, base_url, instance=''):
        self.update(
            '[b #ef4444]无法连接 Tulpa 后台服务[/]\n\n'
            + (f'实例：{esc(instance)}\n' if instance else '') +
            f'地址：{esc(base_url)}\n'
            f'原因：{esc(message)}\n\n'
            '请在另一个终端运行 [b #22d3ee]./tulpa start[/] 启动后台，\n'
            '然后按 [b]r[/] 重试。关闭本界面不会停止后台服务。\n'
            '按 [b]F2[/] 打开实例管理，可切回默认实例或通过主后台启动所选实例。')


class TulpaApp(App):
    TITLE = 'Tulpa'
    CSS = CSS
    ENABLE_COMMAND_PALETTE = False  # Every action has a visible key; no English palette hint.
    BINDINGS = [
        Binding('q', 'quit_app', '退出'),
        Binding('r', 'reload', '刷新'),
        Binding('tab', 'focus_next', '切换焦点', show=False),
        Binding('shift+tab', 'focus_previous', show=False),
        Binding('escape', 'focus_sidebar', '菜单'),
        *[Binding(key, f'goto("{view}")', view, show=False) for key, view in SHORTCUTS.items()],
        Binding('f2', f'goto("{HUB_VIEW}")', '实例管理'),
    ]

    def check_action(self, action, parameters):
        if action in MODAL_BLOCKED and len(self.screen_stack) > 1:
            return False
        return True

    def action_quit_app(self):
        self.exit()

    def __init__(self, client=None, client_factory=None, base_url=None, instance_client_factory=None,
                 initial_instance=None, **kwargs):
        super().__init__(**kwargs)
        # The original backend is the hub: instance management always goes there,
        # also after switching. `_client` is the selected instance (the hub at start).
        self._hub = client
        self._client = client
        self._factory = client_factory or default_client_factory(base_url)
        self._instance_factory = instance_client_factory
        self._locks = {}  # One per client: the sync client is not assumed thread-safe.
        self._outstanding = {}  # id(client) -> requests submitted and not yet called back.
        self._retired = []  # Switched-away clients, closed once nothing is outstanding.
        self.generation = 0  # Bumped on every switch; older callbacks are discarded.
        self.instance = dict(id=DEFAULT_ID, name='')
        self._hub_stopped = False
        self._rebuilding = False  # True from a switch until the new pages are mounted.
        self._rebuild_lock = asyncio.Lock()
        self._initial_instance = initial_instance if isinstance(initial_instance, dict) else None
        self._base_url = base_url or getattr(client, 'base_url', None)
        self.conn_state = 'unknown'
        self.overview = None
        self.current = 'overview'
        self.pending_refresh = None  # Page whose data must be reloaded once the service answers.
        self.egg = SequenceTracker()
        self.egg_focus = None  # Widget focused while the sequence was being typed.

    @property
    def base_url(self):
        """The address actually used.

        A lazily created client resolves TULPA_URL and the default port itself, so
        the live client wins over whatever was passed in.
        """
        return getattr(self._client, 'base_url', None) or (
            self._base_url if self.instance_id == DEFAULT_ID else None) or DEFAULT_URL

    @property
    def instance_id(self):
        return str(self.instance.get('id') or DEFAULT_ID)

    @property
    def instance_name(self):
        return display_name(self.instance)

    def compose(self) -> ComposeResult:
        yield Header(id='header')
        with Horizontal(id='body'):
            yield OptionList(*[Option(f'{KEY_OF.get(key, " ")} {label}', id=key) for key, label, _ in VIEWS],
                             id='sidebar')
            yield OfflinePanel(id='offline')
            with ContentSwitcher(initial='overview', id='content'):
                for key, _, cls in VIEWS:
                    yield cls(id=key)
        yield Static('', id='status')
        yield Footer()

    def on_mount(self):
        self.query_one('#offline').display = False
        self.select_initial_instance()
        self.resolve_client()  # So the header shows the address the client really uses.
        self.query_one(Header).show('connecting', self.base_url, instance=self.instance_name)
        self.query_one('#sidebar', OptionList).highlighted = 0
        self.query_one('#sidebar').focus()
        self.page('overview').activate()

    def on_resize(self, event):
        self.set_class(event.size.width < 100, '-narrow')
        self.set_class(event.size.height < 32, '-short')

    async def on_event(self, event):
        # Seen here before the focused widget consumes it, so arrows still navigate.
        if isinstance(event, events.InputEvent) and not event.is_forwarded:
            if isinstance(event, events.Key) and eligible(self):
                if self.focused is not self.egg_focus:
                    self.egg.reset()  # Focus moved (e.g. a page focused itself): start over.
                    self.egg_focus = self.focused
                consume, complete = self.egg.feed(event.key)
                if complete:
                    self.push_screen(EasterEggModal())
                    return  # The completing `a` must not reach the page either.
                if consume:
                    event.stop()
                    event.prevent_default()
                    return
            elif isinstance(event, events.Key):
                self.egg.reset()  # A dialog or text field breaks the sequence.
        if isinstance(event, events.MouseDown):
            self.egg.reset()  # So does any click (also when it bubbles back up).
        await super().on_event(event)

    # ---- requests -------------------------------------------------------

    def hub_client(self):
        if self._hub is None:
            self._hub = self._factory()
        return self._hub

    def client(self):
        """The selected instance's client. Non-default clients are only set by switching."""
        if self._client is None:
            self._client = self.hub_client()
        return self._client

    def resolve_client(self):
        """Build the client early just to learn its address; failures surface per request."""
        try:
            self.client()
        except Exception:  # noqa: BLE001 - the first real request reports this properly
            pass

    def _submit(self, client, error, fn, group, exclusive, deliver):
        """Run fn(client) in a thread; deliver(ok, value) runs on the UI thread.

        The client is chosen here, at submission, never inside the worker, so a
        switch while the request waits for the lock cannot retarget it.
        """
        key = id(client)
        lock = self._locks.setdefault(key, threading.Lock()) if client is not None else None
        self._outstanding[key] = self._outstanding.get(key, 0) + 1

        def finish(success, value):
            self._outstanding[key] -= 1
            self._reap()
            deliver(success, value)

        def job():
            try:
                if client is None:
                    raise error
                with lock:
                    result = fn(client)
            except Exception as exc:  # noqa: BLE001 - every failure becomes a safe message
                self.call_from_thread(finish, False, exc)
            else:
                self.call_from_thread(finish, True, result)
        self.run_worker(job, thread=True, exclusive=exclusive, group=group)

    def _selected(self):
        try:
            return self.client(), None
        except Exception as exc:  # noqa: BLE001 - reported through the normal failure path
            return None, exc

    def request(self, fn, ok=None, failed=None):
        """Run fn(client) on the selected instance; stale (pre-switch) results are dropped."""
        if self._rebuilding:
            # Only pages of the instance being left exist now: none may reach the new client.
            return
        generation = self.generation
        client, error = self._selected()

        def deliver(success, value):
            if generation != self.generation:
                return  # Belongs to an instance the user switched away from.
            if success:
                self._succeeded(value, ok)
            else:
                self._failed(value, failed)
        self._submit(client, error, fn, 'api', False, deliver)

    def hub_request(self, fn, ok=None, failed=None, slow=False):
        """Instance management: always the original hub, never the selected instance.

        Its failures never change the selected instance's online/offline state.
        """
        try:
            client = self.hub_client()
            error = None
        except Exception as exc:  # noqa: BLE001
            client, error = None, exc
        if slow and client is not None and isinstance(getattr(client, 'timeout', None), (int, float)):
            # A private copy: a 60 s start neither holds the hub lock nor shortens other timeouts.
            client = copy.copy(client)
            client.timeout = max(client.timeout, SLOW_TIMEOUT)

        def deliver(success, value):
            if success:
                if ok:
                    ok(value)
            elif failed:
                failed(safe_message(value))
            else:
                self.notify(safe_message(value), severity='error', title='主后台请求失败')
        self._submit(client, error, fn, 'hub', False, deliver)

    def _succeeded(self, result, ok):
        if self.conn_state != 'online':
            self.set_connection('online')
        if ok:
            ok(result)

    def _failed(self, exc, failed):
        message = safe_message(exc)
        if not reached_server(exc):
            # TerminalError may not expose a status; a cheap probe decides offline vs. rejected.
            self.ping()
        if failed:
            failed(message)
        else:
            self.notify(message, severity='error', title='请求失败')

    def ping(self):
        generation = self.generation
        client, error = self._selected()

        def deliver(success, value):
            if generation != self.generation:
                return
            if success:
                self.overview = value if isinstance(value, dict) else {}
                self.set_connection('online')
            elif reached_server(value):
                self.set_connection('online')
            else:
                self.set_connection('offline', safe_message(value))
        self._submit(client, error, lambda c: c.get('/overview'), 'ping', True, deliver)

    def set_connection(self, state, message=''):
        previous = self.conn_state
        self.conn_state = state
        overview = self.overview if isinstance(self.overview, dict) else {}
        reported = overview.get('instance')
        if isinstance(reported, dict) and str(reported.get('id') or DEFAULT_ID) == self.instance_id \
                and reported.get('name'):
            self.instance['name'] = str(reported['name'])
        self.show_header()
        if state == 'offline':
            self.offline_message = message or '服务未响应。'
            self.query_one(OfflinePanel).show(self.offline_message, self.base_url, self.instance_name)
        self.layout_offline()
        if state == 'online' and not self._rebuilding:
            # A page that was requested while offline (or whose data is from before
            # the outage) is reloaded here, so one press of r after recovery really
            # refreshes the page and not just the header.
            key, self.pending_refresh = self.pending_refresh, None
            if key is not None and key == self.current:
                self.page(key).refresh_data()
            elif previous != 'online':
                self.page(self.current).activate()  # No-op if loaded or loading.

    def show_header(self):
        overview = self.overview if isinstance(self.overview, dict) else {}
        self.query_one(Header).show(self.conn_state, self.base_url, overview.get('version', ''),
                                    overview.get('mcp'), instance=self.instance_name)

    def layout_offline(self):
        """The offline panel replaces the pages, except 实例管理: it talks to the hub."""
        hide = self.conn_state == 'offline' and self.current != HUB_VIEW
        self.query_one(OfflinePanel).display = hide
        self.query_one('#content').display = not hide

    def update_overview(self, overview):
        self.overview = overview if isinstance(overview, dict) else {}
        self.set_connection('online')

    # ---- instances ------------------------------------------------------

    def instances_listed(self, descriptors):
        """Apply the selected instance's name and observed lifecycle, independently of hub health."""
        mine = descriptors.get(self.instance_id)
        if isinstance(mine, dict) and mine.get('name') and mine['name'] != self.instance.get('name'):
            self.instance['name'] = str(mine['name'])
            self.show_header()
        if self.instance_id == DEFAULT_ID or not isinstance(mine, dict) or type(mine.get('running')) is not bool:
            return
        stopped = not mine['running']
        if stopped == self._hub_stopped:
            return
        self._hub_stopped = stopped
        # A completed stop invalidates even requests already queued before it.
        # Fresh pages on restart cannot display an old grant/QR/status snapshot.
        self.generation += 1
        self.overview = None
        self.pending_refresh = None
        self._rebuilding = True
        self.set_connection('offline' if stopped else 'connecting',
                            mine.get('error') or '实例已停止，可在实例管理中重新启动。')
        self.run_worker(self._rebuild_views(self.generation, probe=not stopped), group='rebuild', exclusive=False)

    def make_instance_client(self, descriptor):
        if self._instance_factory is not None:
            return self._instance_factory(descriptor)
        hub = self.hub_client()
        if hasattr(hub, 'for_instance'):
            return hub.for_instance(descriptor)
        raise TerminalError('当前 Tulpa 客户端不支持多实例，请更新后端。')

    def switch_instance(self, descriptor):
        """Point every page except 实例管理 at another instance. Returns True on success."""
        if len(self.screen_stack) > 1:
            self.notify('请先关闭当前对话框再切换实例。', severity='warning', title='实例管理')
            return False
        iid = str(descriptor.get('id') or DEFAULT_ID)
        try:
            new = self.hub_client() if iid == DEFAULT_ID else self.make_instance_client(descriptor)
        except Exception as exc:  # noqa: BLE001
            self.notify(safe_message(exc), severity='error', title='无法切换实例')
            return False
        old = self._client
        if old is not None and old is not self._hub and old is not new:
            self._retired.append(old)
        self._client = new
        self._hub_stopped = False
        self.generation += 1  # Every callback and poll of the old instance is now stale.
        self.instance = dict(id=iid, name=str(descriptor.get('name') or ''))
        self.overview = None
        self.pending_refresh = None
        self.conn_state = 'connecting'
        self._rebuilding = True  # Synchronously: old controls and timers are dead from here on.
        self.show_header()
        self.layout_offline()
        self._reap()
        self.run_worker(self._rebuild_views(self.generation), group='rebuild', exclusive=False)
        return True

    async def _rebuild_views(self, generation, *, probe=True):
        """Fresh widgets per instance: no rows, forms, timers or polls survive a switch."""
        async with self._rebuild_lock:
            if generation != self.generation:
                return  # A later switch rebuilds anyway (and clears _rebuilding).
            try:
                content = self.query_one('#content', ContentSwitcher)
                await content.remove_children([w for w in content.children if w.id != HUB_VIEW])
                fresh = [cls(id=key) for key, _, cls in VIEWS if key != HUB_VIEW]
                for widget in fresh:
                    widget.display = False
                await content.mount_all(fresh)
            finally:
                if generation == self.generation:
                    self._rebuilding = False
            if generation != self.generation:
                return  # Switched again meanwhile: the later rebuild replaces these pages.
            content.current = None
            content.current = self.current
            self.layout_offline()
            if self.conn_state == 'online' and self.usable(self.current):
                self.page(self.current).activate()  # A ping answered while the pages were swapped.
        if generation == self.generation and probe:
            self.ping()  # Its answer activates the current page of the new instance.

    def select_initial_instance(self):
        """Opened from a child directory: select it before any page loads, keeping the hub."""
        descriptor, self._initial_instance = self._initial_instance, None
        if descriptor is None or str(descriptor.get('id') or DEFAULT_ID) == DEFAULT_ID:
            return
        try:
            self._client = self.make_instance_client(descriptor)  # Builds a client; no I/O.
        except Exception as exc:  # noqa: BLE001
            self.notify(safe_message(exc), severity='error', title='无法打开所选实例，已使用默认实例')
            return
        self.instance = dict(id=str(descriptor['id']), name=str(descriptor.get('name') or ''))

    def _reap(self):
        """Close retired clients only after their outstanding requests have returned."""
        for client in list(self._retired):
            if not self._outstanding.get(id(client)):
                self._retired.remove(client)
                self._outstanding.pop(id(client), None)
                self._locks.pop(id(client), None)
                close_quietly(client)

    # ---- navigation -----------------------------------------------------

    def page(self, key):
        return self.query_one(f'#{key}')

    def usable(self, key):
        """The hub page works whatever the selected instance does; others need it online."""
        if key == HUB_VIEW:
            return True
        return self.conn_state != 'offline' and not self._rebuilding and bool(self.query(f'#content > #{key}'))

    def on_option_list_option_highlighted(self, event):
        if event.option_list.id == 'sidebar' and event.option.id:
            self.show_view(event.option.id)

    def on_option_list_option_selected(self, event):
        if event.option_list.id == 'sidebar' and event.option.id:
            self.show_view(event.option.id)
            if self.usable(event.option.id):
                self.page(event.option.id).focus_main()

    def show_view(self, key):
        self.current = key
        if self._rebuilding and key != HUB_VIEW:
            return  # The rebuild shows self.current when the new widgets are mounted.
        self.query_one('#content', ContentSwitcher).current = key
        self.layout_offline()
        if self.usable(key):
            self.page(key).activate()

    def action_goto(self, key):
        sidebar = self.query_one('#sidebar', OptionList)
        sidebar.highlighted = [k for k, _, _ in VIEWS].index(key)
        self.show_view(key)
        if self.usable(key):
            self.page(key).focus_main()

    def action_focus_sidebar(self):
        self.query_one('#sidebar').focus()

    def action_reload(self):
        if self.current == HUB_VIEW:
            self.page(HUB_VIEW).refresh_data()
            return
        if self._rebuilding:
            return
        if self.conn_state == 'offline':
            # Remember what to reload: by the time the ping answers, the state is
            # 'connecting', so set_connection can no longer tell it came from offline.
            self.pending_refresh = self.current
            self.set_connection('connecting')
            self.ping()
            return
        self.page(self.current).refresh_data()

    def show_status(self, text):
        """Footer status line; never pass credentials here."""
        self.query_one('#status', Static).update(esc(text))

    def on_unmount(self):
        clients = [self._client, self._hub, *self._retired]
        for index, client in enumerate(clients):
            if client is not None and all(client is not c for c in clients[:index]):
                close_quietly(client)
