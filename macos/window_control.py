"""Verify a rendered Cocoa window or stop only this checkout's window process."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT/'.tmp/macos-window.json'


def current():
    try:
        value = json.loads(STATE.read_text())
        pid = value['pid']
        if type(pid) is not int or pid <= 1 or value.get('root') != str(ROOT):
            return None
        command = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'command='],
            capture_output=True, text=True, timeout=5).stdout.strip()
        if not command.endswith(' -u '+str(ROOT/'macos/launch.py')):
            return None
        return value
    except (OSError, ValueError, KeyError):
        return None


def main():
    action = sys.argv[1]
    if action == 'stop':
        value = current()
        if value:
            os.kill(value['pid'], signal.SIGTERM)
            deadline = time.monotonic()+10
            while current() and time.monotonic() < deadline:
                time.sleep(.1)
            if current():
                raise SystemExit('桌面窗口仍未退出，请手动关闭。后台保持运行。')
        return
    if action != 'verify':
        raise SystemExit('Expected stop or verify')
    deadline = time.monotonic()+90
    while time.monotonic() < deadline:
        value = current()
        if value and value.get('visible_windows', 0) and value.get('document', {}).get('text', 0) > 100:
            print(json.dumps(value, ensure_ascii=False))
            return
        time.sleep(.5)
    raise SystemExit('未确认 Cocoa 窗口已渲染，请检查 .tmp/macos-launch.log。')


if __name__ == '__main__':
    main()
