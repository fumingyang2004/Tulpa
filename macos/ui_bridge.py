"""Small Cocoa-only UI actions; no arbitrary URLs, commands or file access."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
import re
import subprocess
import threading
from urllib.parse import urlsplit

from chatlocal.snowluma_secure import atomic_json, read_json
from chatlocal.terminal_client import TerminalError

APPEARANCES = ('system', 'light', 'dark')


class LoginError(ValueError):
    """A credential-free explanation that the login panel can display."""


class DesktopUI:
    def __init__(self, root, client):
        self._preferences = Path(root) / 'data' / 'macos-appearance.json'
        self._client = client
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='tulpa-ui')
        self._pending = threading.BoundedSemaphore(4)
        self._handler = None

    def _trusted(self, address):
        url = urlsplit(str(address))
        base = urlsplit(self._client.base_url)
        return (url.scheme, url.netloc, url.path) == (base.scheme, base.netloc, '/')

    def install(self, window):
        """Use a narrow WebKit message handler; the generic bridge needs eval()."""
        from AppKit import NSObject
        from WebKit import WKWebView
        from PyObjCTools.AppHelper import callAfter
        if not self._trusted(window.get_current_url()):
            return
        completed = threading.Event()
        errors = []
        ui = self

        def attach():
            try:
                def find(view):
                    if isinstance(view, WKWebView):
                        return view
                    for child in view.subviews():
                        found = find(child)
                        if found is not None:
                            return found
                webview = find(window.native.contentView())
                if webview is None:
                    raise RuntimeError('未找到 Tulpa 的 WebKit 窗口。')
                if ui._handler is None:
                    class TulpaUIMessageHandler(NSObject):
                        def userContentController_didReceiveScriptMessage_(self, controller, message):
                            frame = message.frameInfo()
                            if not frame.isMainFrame() or not ui._trusted(frame.request().URL()):
                                return
                            body = message.body()
                            if not isinstance(body, str) or len(body) > 512:
                                return
                            try:
                                request = json.loads(body)
                                rid, action, value = request['id'], request['action'], request.get('value')
                                if not isinstance(rid, str) or not re.fullmatch(r'[a-f0-9-]{36}', rid):
                                    return
                                if action not in ('get_appearance', 'set_appearance', 'open_qq_login'):
                                    return
                                if action == 'set_appearance' and value not in APPEARANCES:
                                    return
                            except (ValueError, TypeError, KeyError):
                                return
                            if not ui._pending.acquire(blocking=False):
                                return

                            def run():
                                try:
                                    result = getattr(ui, action)(value) if action == 'set_appearance' else getattr(ui, action)()
                                    reply = dict(id=rid, value=result)
                                except LoginError as exc:
                                    reply = dict(id=rid, error=str(exc))
                                except Exception:
                                    reply = dict(id=rid, error='操作未完成，请稍后重试。')
                                finally:
                                    ui._pending.release()

                                def respond():
                                    if ui._trusted(webview.URL()):
                                        script = 'window.TulpaDesktopUI?.receive('+json.dumps(reply)+')'
                                        webview.evaluateJavaScript_completionHandler_(script, None)
                                callAfter(respond)
                            ui._executor.submit(run)
                    ui._handler = TulpaUIMessageHandler.alloc().init()
                    webview.configuration().userContentController().addScriptMessageHandler_name_(ui._handler, 'tulpaUI')
            except Exception as exc:
                errors.append(exc)
            finally:
                completed.set()
        callAfter(attach)
        if not completed.wait(10):
            raise RuntimeError('窗口接口初始化超时。')
        if errors:
            raise errors[0]
        window.run_js('''(() => {
          const pending = new Map();
          window.TulpaDesktopUI = {
            appearance: APPEARANCE_VALUE,
            call(action, value) {
              return new Promise((resolve, reject) => {
                const id = crypto.randomUUID();
                const timeout = action === 'open_qq_login' ? 90000 : 45000;
                const timer = setTimeout(() => {pending.delete(id);reject(Error('操作超时。'));}, timeout);
                pending.set(id, {resolve, reject, timer});
                window.webkit.messageHandlers.tulpaUI.postMessage(JSON.stringify({id, action, value}));
              });
            },
            receive(reply) {
              const task = pending.get(reply.id);if (!task) return;
              pending.delete(reply.id);clearTimeout(task.timer);
              if (reply.error) task.reject(Error(reply.error));else task.resolve(reply.value);
            }
          };
          window.dispatchEvent(new Event('tulpa-native-ready'));
        })()'''.replace('APPEARANCE_VALUE', json.dumps(self.get_appearance())))

    def get_appearance(self):
        value = read_json(self._preferences).get('appearance', 'system')
        return value if value in APPEARANCES else 'system'

    def set_appearance(self, appearance):
        if appearance not in APPEARANCES:
            raise ValueError('外观设置无效。')
        atomic_json(self._preferences, {'appearance': appearance})
        self._apply_native_appearance(appearance)
        return appearance

    def _apply_native_appearance(self, appearance):
        from AppKit import NSAppearance, NSApplication, NSAppearanceNameAqua, NSAppearanceNameDarkAqua
        from PyObjCTools.AppHelper import callAfter

        def apply():
            name = {'light': NSAppearanceNameAqua, 'dark': NSAppearanceNameDarkAqua}.get(appearance)
            NSApplication.sharedApplication().setAppearance_(NSAppearance.appearanceNamed_(name) if name else None)
        callAfter(apply)

    def open_qq_login(self):
        # Resolve the owned instance through its authenticated management API.
        # JavaScript cannot supply a command or a destination URL.
        # Preparing X/VNC can outlast the short timeout used by regular settings.
        # Override this request only; concurrent UI calls keep their own timeout.
        try:
            value = self._client.api_post('/api/desktop/snowluma/managed/docker-login', {}, timeout=60)
        except TerminalError as exc:
            raise LoginError(str(exc)) from None
        if not value.get('ok'):
            raise LoginError(value.get('message') or '暂时无法打开 QQ 登录页面。')
        try:
            url = urlsplit(value['novnc_url'])
            valid = (url.scheme == 'http' and url.hostname == '127.0.0.1' and url.port
                     and not url.username and not url.password and not url.fragment)
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise LoginError('登录地址无效。')
        try:
            subprocess.run(['/usr/bin/open', value['novnc_url']], check=True, timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            # Keep the usable login data when Launch Services cannot open a
            # browser. The user can retry or copy the URL without preparing QQ again.
            return dict(value, browser_opened=False,
                        browser_error='系统浏览器未能自动打开，请重试或复制登录地址到浏览器中打开。')
        return dict(value, browser_opened=True)
