"""Lifecycle of rebuildable QQ reader files; snapshots, keys and evidence stay put."""
import json
import os
from contextlib import contextmanager
from pathlib import Path

from .config import DATA, DB, ROOT

READER = DATA / 'qq-reader'
CACHE = READER / 'decrypted'
RECEIPT = 'export-receipt.json'
DATABASES = ('no_header.db', 'messages.db', 'profile_info_no_hdr.db',
             'profile_info_dec.db', 'group_info_no_hdr.db', 'group_info_dec.db')
REUSABLE = ('messages.db', 'profile_info_dec.db', 'group_info_dec.db')
CACHE_FILES = tuple(name + suffix for name in DATABASES
                    for suffix in ('', '-wal', '-shm', '-journal')) + tuple(name+'.pages.json' for name in REUSABLE) + ('export-cache.json', RECEIPT)


def cache_mode():
    from dotenv import dotenv_values
    value=os.environ.get('QQ_CACHE_MODE') or dotenv_values(ROOT/'.env').get('QQ_CACHE_MODE') or 'balanced'
    if value not in ('balanced','space'):
        raise ValueError('QQ_CACHE_MODE 必须为 balanced 或 space。')
    return value


class QQWorkspaceBusy(RuntimeError):
    pass


def checked_path(path):
    """Refuse junction/symlink redirection before any write or deletion."""
    path = Path(path).absolute()
    if not path.is_relative_to(ROOT) or path.resolve() != path:
        raise ValueError('QQ 缓存路径越界或经过链接，未执行操作。')
    return path


@contextmanager
def qq_workspace_lock(reader_dir=None):
    reader_dir = checked_path(reader_dir or READER)
    reader_dir.mkdir(parents=True, exist_ok=True)
    lock = checked_path(reader_dir / 'workspace.lock')
    with lock.open('a+b') as handle:
        if handle.seek(0, 2) == 0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise QQWorkspaceBusy('已有 QQ 读取或缓存清理正在运行，请稍后重试。') from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def release_qq_cache(store, source_hash):
    """Called only after an import commits. A receipt ties it to this cache generation."""
    if store.path.resolve() != DB.resolve():
        return None
    try:
        with qq_workspace_lock():
            return _release_after_import(store, source_hash, CACHE)
    except QQWorkspaceBusy:
        return dict(status='retained', reason='QQ 读取正在运行，缓存保留，稍后成功导入时重试。', released_bytes=0)
    except (OSError, ValueError):
        # A cleanup failure must not turn a committed import into a reported failure.
        return dict(status='retained', reason='缓存暂无法清理，已导入记录保留。', released_bytes=0)


def _release_after_import(store, source_hash, cache):
    cache = checked_path(cache)
    receipt_path = checked_path(cache / RECEIPT)
    if not receipt_path.exists():
        return None
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    if not isinstance(receipt, dict):
        raise ValueError('QQ 导出状态格式无效，读取缓存保留。')
    if receipt.get('source_hash') != source_hash:
        return dict(status='retained', reason='导入文件不是当前 QQ 导出的版本，读取缓存保留。', released_bytes=0)
    with store.connect() as db:
        if not db.execute('SELECT 1 FROM sources WHERE hash=?', (source_hash,)).fetchone():
            return dict(status='retained', reason='尚未成功导入，读取缓存保留。', released_bytes=0)
    # Validate every literal target before touching any file. Never recurse or glob-delete.
    retain = set(REUSABLE) | {name+'.pages.json' for name in REUSABLE} if receipt.get('cache_policy')=='balanced' else set()
    targets = [checked_path(cache / name) for name in CACHE_FILES if name not in retain]
    if any(p.exists() and not p.is_file() for p in targets):
        raise ValueError('QQ 缓存条目类型不符，未执行清理。')
    released = 0; removed = 0; blocked = []
    # Invalidate stale export-cache.json first. Keep the receipt if a file is locked,
    # so another successful import can retry. Decryption also checks messages.db.
    targets.sort(key=lambda p: (p.name != 'export-cache.json', p.name == RECEIPT))
    for path in targets:
        if path.name == RECEIPT and blocked:
            continue
        try:
            if not path.exists():
                continue
            size = path.stat().st_size
            path.unlink()
            released += size; removed += 1
        except OSError:
            blocked.append(path.name)
    return dict(status='partial' if blocked else 'released', released_bytes=released,
                retained_bytes=sum(checked_path(cache/name).stat().st_size for name in retain if checked_path(cache/name).is_file()),
                removed_files=removed, retained_files=blocked,
                reason='部分缓存仍被占用，下次成功导入时重试。' if blocked else
                       ('已释放 QQ 中间文件；保留一份解密缓存用于快速刷新。' if retain else
                       '已释放 QQ 中间文件和解密库；加密快照、密钥、消息及媒体保留。'))
