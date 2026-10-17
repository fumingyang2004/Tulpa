"""Real macOS child cleanup and memory enforcement, without application data."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chatlocal.platform_process import owned_popen, terminate_group


def alive(pid):
    result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'state='],
                            capture_output=True, text=True, timeout=5)
    return result.returncode == 0 and not result.stdout.strip().startswith('Z')


@unittest.skipUnless(sys.platform == 'darwin', 'requires real macOS')
class ProcessTests(unittest.TestCase):
    def test_exit_code_and_explicit_stop(self):
        process = owned_popen([sys.executable, '-c', 'raise SystemExit(7)'])
        self.assertEqual(process.wait(timeout=10), 7)
        process = owned_popen([sys.executable, '-c',
            'import os,time;print(os.getpid(),flush=True);time.sleep(60)'], stdout=subprocess.PIPE, text=True)
        try:
            child = int(process.stdout.readline())
            terminate_group(process)
            deadline = time.monotonic()+5
            while alive(child) and time.monotonic() < deadline:
                time.sleep(.1)
            self.assertFalse(alive(child))
        finally:
            terminate_group(process)
            process.stdout.close()

    def test_original_parent_death_cleans_worker(self):
        driver = '''import os,sys,json
sys.path.insert(0, sys.argv[1])
from chatlocal.platform_process import owned_popen
import subprocess
p=owned_popen([sys.executable,'-c','import os,time;print(os.getpid(),flush=True);time.sleep(60)'],stdout=subprocess.PIPE,text=True)
print(json.dumps([p.pid,int(p.stdout.readline())]),flush=True)
os._exit(0)
'''
        result = subprocess.run([sys.executable, '-c', driver, str(ROOT)], capture_output=True,
                                text=True, timeout=15, check=True)
        pids = json.loads(result.stdout)
        deadline = time.monotonic()+8
        while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(.1)
        remaining = [pid for pid in pids if alive(pid)]
        if remaining:
            os.killpg(pids[0], signal.SIGKILL)
        self.assertFalse(remaining)

    def test_memory_limit_stops_worker(self):
        process = owned_popen([sys.executable, '-c',
            'import time;data=bytearray(300*1024*1024);time.sleep(60)'], memory_bytes=100*1024*1024)
        try:
            self.assertNotEqual(process.wait(timeout=15), 0)
        finally:
            terminate_group(process)


if __name__ == '__main__':
    unittest.main()
