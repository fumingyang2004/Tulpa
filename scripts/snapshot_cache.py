"""Reuse unchanged DB families; publish only upstream-verified private copies."""
import json
import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from incremental_common import fingerprint
from reader_metrics import count


def quarantine_unreadable_cache(target):
    """Retain an unusable generated copy and rebuild it from the client source.

    A denied file may also forbid a file rename, while its owning cache directory
    can still be renamed. Preserve that entire generated snapshot then rebuild
    once. No permission changes, client writes or message-store deletion.
    """
    from chatlocal.config import ROOT,local_path
    if target.is_symlink() or target.is_junction():raise ValueError('本地数据库副本包含链接，未自动修复。')
    target=local_path(target)
    relative=target.relative_to(ROOT)
    if len(relative.parts)<3 or not (relative.parts[0]=='.tmp' or relative.parts[:2] in (('data','qq-snapshot'),('data','wechat-reader-source'))):
        raise ValueError('数据库副本超出缓存范围，未自动修复。')
    recovery=local_path(tempfile.mkdtemp(dir=ROOT/'.tmp',prefix='snapshot-recovery-'))
    destination=local_path(recovery/target.name)
    if not destination.is_relative_to(recovery):raise ValueError('Invalid cache recovery destination')
    # Both resolved absolute directory targets are inside the application; only
    # the explicitly allowlisted derived snapshot root is moved, never DATA.
    try:os.replace(target,destination)
    except PermissionError:
        raise PermissionError('本地数据库副本暂时被占用或权限异常，无法重建。请完全关闭 Tulpa 后重新打开；未修改客户端数据，也未推进同步进度。') from None
    target.mkdir(parents=True,exist_ok=True)
    count('snapshot_unreadable_rebuilds')


def refresh_families(source, target, databases, *, _recovered=False):
    from chatlog_keeper.core._snapshot import snapshot_db_family, _family_signature
    from chatlocal.config import ROOT, local_path
    source, target = Path(source), Path(target)
    target=local_path(target)
    paths = [Path(p) for p in databases]
    def inventory():
        return fingerprint(source, [p.with_name(p.name+s) for p in paths for s in ('', '-wal')])
    before = inventory()
    manifest = target / '.snapshot-cache.json'
    try:
        saved = json.loads(manifest.read_text(encoding='utf-8'))
        if not isinstance(saved,dict):saved={}
    except (OSError, ValueError):
        saved = {}
    target.mkdir(parents=True, exist_ok=True)
    def save(value):
        pending=manifest.with_suffix('.tmp')
        pending.write_text(json.dumps(value),encoding='utf-8')
        os.replace(pending,manifest)
    def digest(path):
        with path.open('rb') as handle:return hashlib.file_digest(handle,'sha256').digest()
    updated = {}
    for path in paths:
        name = path.relative_to(source).as_posix()
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        family = {k:v for k,v in before.items() if k in (name, name+'-wal')}
        local_files = [destination.with_name(destination.name+s) for s in ('', '-wal', '-shm')]
        try:local = fingerprint(target, local_files)
        except PermissionError:
            # A failed previous read must not permanently poison future imports.
            # Existing messages/keys/cursors are separate and remain untouched.
            if _recovered:raise  # One bounded repair, never a rebuild loop.
            quarantine_unreadable_cache(target)
            return refresh_families(source,target,paths,_recovered=True)
        previous = saved.get(name, {})
        if (not isinstance(previous,dict) or not isinstance(previous.get('source'),dict)
                or not isinstance(previous.get('local'),dict)):
            previous={}
        # Adopt an existing verified-era snapshot only after comparing ALL main
        # bytes, not merely mtime. This avoids a large first-migration copy while
        # the live WAL keeps changing.
        same_main = (family.get(name) == previous.get('source',{}).get(name)
            and local.get(name) == previous.get('local',{}).get(name) and destination.is_file())
        if not previous and family.get(name) == local.get(name) and destination.is_file():
            same_main = digest(path) == digest(destination)
        if family == previous.get('source') and local == previous.get('local') and destination.is_file():
            count('snapshot_reused_databases')
            count('snapshot_reused_bytes', sum(v[0] for v in family.values()))
        elif same_main:
            # The encrypted main file is unchanged: only its WAL/SHM need a
            # stable copy. Fence the whole family, as the upstream reader does.
            with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='wal-snapshot-') as folder:
                stable = Path(folder)/path.name
                for attempt in range(3):
                    signature_before = _family_signature(path)
                    for suffix in ('-wal','-shm'):
                        src=path.with_name(path.name+suffix)
                        dest=stable.with_name(stable.name+suffix)
                        dest.unlink(missing_ok=True)
                        if src.exists():shutil.copy2(src,dest)
                    if signature_before == _family_signature(path):break
                else:raise ValueError('数据库在复制期间更新；请重试，未推进进度。')
                for suffix in ('-wal','-shm'):
                    src=stable.with_name(stable.name+suffix)
                    dest=destination.with_name(destination.name+suffix)
                    if src.exists():
                        count('snapshot_copied_bytes',src.stat().st_size)
                        os.replace(src,dest)
                    else:dest.unlink(missing_ok=True)
            count('snapshot_wal_only_databases')
            count('snapshot_reused_bytes',family[name][0])
            local=fingerprint(target,local_files)
        else:
            with snapshot_db_family(path) as stable:
                for suffix in ('', '-wal', '-shm'):
                    src = stable.with_name(stable.name+suffix)
                    dest = destination.with_name(destination.name+suffix)
                    if src.exists():
                        count('snapshot_copied_bytes', src.stat().st_size)
                        # Both paths are on the workspace volume. Moving the
                        # private stable copy avoids a second multi-GB copy.
                        os.replace(src, dest)
                    else:
                        dest.unlink(missing_ok=True)
            count('snapshot_copied_databases')
            local = fingerprint(target, local_files)
        updated[name] = dict(source=family, local=local)
        # A later family can be busy. Keep each successfully fenced copy for
        # retry, while the importer still requires the final global fence.
        save({**saved,**updated})
    if before != inventory():
        raise ValueError('数据库在复制期间更新；请重试，未推进进度。')
    # A removed source must not linger in the reader's shard enumeration.
    for path in target.rglob('*.db'):
        if path.relative_to(target).as_posix() not in updated:
            path=local_path(path)
            for suffix in ('', '-wal', '-shm'):
                path.with_name(path.name+suffix).unlink(missing_ok=True)
    save(updated)
    return before
