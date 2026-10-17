"""Cocoa client of the same authenticated backend used by the terminal UI."""
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
(ROOT/'.tmp').mkdir(exist_ok=True)
os.environ['TMPDIR'] = str(ROOT/'.tmp')


def main():
    from chatlocal.platform_lock import file_lock
    from chatlocal.snowluma_secure import atomic_json
    from chatlocal.terminal_client import TerminalClient
    from chatlocal.terminal_environment import prepare_environment
    from chatlocal.terminal_launcher import ensure_backend
    from macos.ui_bridge import DesktopUI

    os.umask(0o077)
    state = ROOT/'.tmp/macos-window.json'
    try:
        with file_lock(ROOT/'.tmp/macos-instance.lock'):
            atomic_json(state, dict(pid=os.getpid(), root=str(ROOT), visible_windows=0))
            import webview
            prepare_environment(ROOT)
            ensure_backend(root=ROOT, timeout=90)
            client = TerminalClient()
            ui = DesktopUI(ROOT, client)
            ticket = client.post('/desktop-ticket')
            window = webview.create_window('Tulpa', client.base_url+ticket['path'],
                width=1280, height=850, min_size=(900, 650),
                background_color='#1c1d20' if ui.get_appearance() == 'dark' else '#faf9f5')

            def loaded():
                ui.install(window)
                ui._apply_native_appearance(ui.get_appearance())
                from AppKit import NSApplication
                from PyObjCTools.AppHelper import callAfter
                # Verification records the actual DOM and a visible Cocoa window.
                # Do not record the temporary launch URL or any credentials.
                # Cocoa run_js returns the native WebKit result without the
                # eval() wrapper, so the page keeps its restrictive CSP.
                result = json.loads(window.run_js('JSON.stringify({title:document.title, ready:document.readyState, text:document.body.innerText.length, platform:document.documentElement.dataset.platform, theme:document.documentElement.dataset.theme, background:getComputedStyle(document.body).backgroundColor})'))
                def record_window():
                    visible = sum(bool(w.isVisible()) for w in NSApplication.sharedApplication().windows())
                    atomic_json(state, dict(pid=os.getpid(), root=str(ROOT), url=client.base_url,
                                            loaded_at=time.time(), visible_windows=visible, document=result))
                callAfter(record_window)
            window.events.loaded += loaded
            try:
                webview.start(gui='cocoa', private_mode=True,
                    icon=str(ROOT/'macos/assets/Tulpa.icns'))
            finally:
                state.unlink(missing_ok=True)
    except BlockingIOError:
        return


if __name__ == '__main__':
    main()
