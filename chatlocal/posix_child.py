"""Exec a local child with parent-death cleanup, without preexec_fn in threads."""
import argparse
import ctypes
import os
import signal
import subprocess
import sys
import time


def darwin_child(command, parent, memory):
    """macOS lacks PDEATHSIG and rejects RLIMIT_AS; supervise our own group.

    RSS is sampled every 250 ms, not a kernel allocation limit. The workers'
    input-size and timeout limits remain in force as on other platforms.
    """
    if os.getpgrp() != os.getpid():
        os.setsid()
    group = os.getpgrp()

    def stop(*_):
        os.killpg(group, signal.SIGKILL)

    if os.getppid() != parent:
        return 125
    child = os.fork()
    if child == 0:
        try:
            os.execvpe(command[0], command, os.environ)
        except OSError:
            os._exit(127)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while True:
        if os.getppid() != parent:
            stop()
        done, status = os.waitpid(child, os.WNOHANG)
        if done:
            code = os.waitstatus_to_exitcode(status)
            return code if code >= 0 else 128-code
        if memory:
            try:
                result = subprocess.run(['/bin/ps', '-axo', 'pgid=,rss='],
                                        capture_output=True, text=True, timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                stop()
            if result.returncode:
                stop()
            rss = sum(int(fields[1])*1024 for line in result.stdout.splitlines()
                      if len(fields := line.split()) == 2 and fields[0] == str(group))
            if rss > memory:
                stop()
        time.sleep(.25)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--parent', type=int, required=True)
    parser.add_argument('--memory', type=int, default=0)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        return 2
    if sys.platform == 'darwin':
        return darwin_child(command, args.parent, args.memory)
    if sys.platform.startswith('linux'):
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            return 125
        # The original parent might have died before prctl took effect.
        if os.getppid() != args.parent:
            return 125
    if args.memory:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (args.memory, args.memory))
    os.execvpe(command[0], command, os.environ)


if __name__ == '__main__':
    raise SystemExit(main())
