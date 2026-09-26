"""Local timing/counters only: never write keys, paths or message bodies."""
import json
import os
import time
from contextlib import contextmanager
from functools import wraps

START = time.perf_counter()
STAGES = {}
COUNTS = {}


@contextmanager
def measure(name):
    start = time.perf_counter()
    from chatlocal.reader_batches import stage
    stage(name)
    try:
        yield
    finally:
        STAGES[name] = STAGES.get(name, 0) + time.perf_counter() - start


def timed(name):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            with measure(name):
                return fn(*args, **kwargs)
        return wrapped
    return decorate


def count(name, amount=1):
    COUNTS[name] = COUNTS.get(name, 0) + amount


def report():
    if target := os.environ.get('CHATLOCAL_SYNC_METRICS'):
        from chatlocal.config import local_path
        try:
            local_path(target).write_text(json.dumps(dict(
                seconds=round(time.perf_counter()-START, 3),
                stages={k: round(v, 3) for k, v in STAGES.items()}, counts=COUNTS)), encoding='utf-8')
        except OSError:pass
