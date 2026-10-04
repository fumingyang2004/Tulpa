"""Offline, same-directory upgrade of a portable installation (stdlib only).

Run with the NEW package's Python, targeting the OLD installation. User state
never moves; IDs, absolute cache paths, MCP token hashes and live cursors survive.
Neither this script nor its output belongs to the MCP model tool surface.
"""
import argparse
from contextlib import ExitStack, closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = {'data', 'imports', 'reports', 'exports', '.tmp', '.git', '.env'}


class UpgradeError(ValueError):
    pass


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def child(root, name):
    """Check every component before touching a path, including Windows junctions."""
    p = PurePosixPath(name)
    if not name or '\\' in name or ':' in name or p.is_absolute() or '..' in p.parts:
        raise UpgradeError('安装清单包含不安全的路径。')
    path = root
    for part in p.parts:
        path = path / part
        if path.is_symlink() or path.is_junction():
            raise UpgradeError('安装目录包含链接或目录联接，请使用普通本地目录。')
    if not path.resolve().is_relative_to(root):
        raise UpgradeError('路径超出安装目录。')
    return path


def manifest(root, *, incoming=False):
    try:
        value = json.loads(child(root, 'build-manifest.json').read_text('utf-8'))
        version = tuple(int(x) for x in value['version'].split('.'))
        # Before 0.5.0 there was only the full edition and no edition field.
        if not incoming and 'edition' not in value and version < (0, 5, 0) and (root / 'app.py').is_file() and not (root / 'mcp-edition.json').exists():
            value['edition'] = 'full'
        if value['product'] != 'Tulpa' or len(version) != 3 or value.get('edition') not in ('full', 'mcp'):
            raise ValueError()
        files = value['files']
        if not isinstance(files, list) or len(set(files)) != len(files):
            raise ValueError()
        for name in files:
            path = child(root, name)
            p = PurePosixPath(name)
            if p.parts[0].lower() in PRIVATE or (p.name.lower().startswith('.env') and p.name.lower() != '.env.example'):
                raise ValueError()
            if incoming and not path.is_file():
                raise ValueError()
        if incoming:
            hashes = value['file_sha256']
            if set(hashes) != set(files) or value.get('working_tree_overlay') or value.get('local_update'):
                raise ValueError()
            for name in files:
                if digest(child(root, name)) != hashes[name]:
                    raise UpgradeError('新版文件校验失败，请重新完整解压官方 ZIP。')
        return value, version, set(files) | {'build-manifest.json'}
    except (OSError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, UpgradeError):
            raise
        raise UpgradeError('缺少或损坏的安装清单；请指定完整的 Tulpa 文件夹。') from None


def running_in(roots):
    """Read process inventory; never print command lines or credentials."""
    if os.name != 'nt':
        raise UpgradeError('升级工具仅支持 Windows。')
    try:
        command = '[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); Get-CimInstance Win32_Process | Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress'
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                capture_output=True, encoding='utf-8', errors='replace', timeout=30,
                                creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        rows = json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise UpgradeError('无法检查正在运行的程序，未执行升级。') from None
    matches = []
    for row in rows:
        if row['ProcessId'] == os.getpid():
            continue
        exe, command = str(row.get('ExecutablePath') or '').lower(), str(row.get('CommandLine') or '').lower()
        for root in roots:
            prefix = str(root).lower().rstrip('\\/') + '\\'
            # Only program paths, not the parent shell whose command invokes this upgrader.
            is_app = exe.startswith(prefix) or (Path(exe).name in ('python.exe', 'pythonw.exe') and
                       any(prefix + name in command for name in ('app.py', 'app_mcp.py', 'desktop\\serve.py', 'scripts\\live_reader.py')))
            if is_app:
                matches.append(row['ProcessId'])
                break
    return matches


def state_files(root):
    names = ['.env', 'data/chats.sqlite3', 'data/chats.sqlite3-wal',
             'data/mcp-access.sqlite3', 'data/mcp-access.sqlite3-wal', 'data/desktop-preferences.json',
             'data/qq-snapshot-info.json', 'data/wechat-snapshot-info.json']
    return {name: digest(child(root, name)) for name in names if child(root, name).is_file()
            and (not name.endswith('-wal') or child(root, name).stat().st_size > 0)}


def database_summary(root):
    result = {}
    for name, tables in [('chats.sqlite3', ('messages', 'sources', 'artifact_sources', 'workspaces', 'sessions')),
                         ('mcp-access.sqlite3', ('grants', 'operations', 'chat_sessions'))]:
        path = child(root, 'data/' + name)
        if not path.exists():
            continue
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise UpgradeError('本机数据库完整性检查未通过，未执行升级。')
            present = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            result[name] = {t: db.execute('SELECT count(*) FROM "' + t + '"').fetchone()[0] for t in tables if t in present}
    return result


def upgrade(source, target, *, apply=False, progress=print):
    for raw in (source, target):
        path = Path(raw).absolute()
        if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
            raise UpgradeError('请使用普通本地安装目录，不通过链接或目录联接升级。')
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise UpgradeError('新版和旧版必须在互不包含的独立目录。工具应从新解压的目录运行。')
    if running_in((source, target)):
        raise UpgradeError('请先从托盘“彻底退出”新旧 Tulpa，并停止对应开发服务，再重试。')
    progress('正在校验新版程序和旧版安装清单…', flush=True)
    new, nv, new_files = manifest(source, incoming=True)
    old, ov, old_files = manifest(target)
    if ov > nv:
        raise UpgradeError('不支持降级；请保留现有目录并联系维护者。')
    if old['edition'] != new['edition']:
        raise UpgradeError('请使用相同版本类型升级：完整版对应完整版，MCP 轻量版对应轻量版。')
    for name in new_files | old_files:
        child(target, name)
    changed = sorted(name for name in new_files if not child(target, name).is_file() or
                     digest(child(target, name)) != digest(child(source, name)))
    obsolete = sorted(old_files - new_files)
    before = database_summary(target)
    state_before = state_files(target)
    backup_size = sum(child(target, n).stat().st_size for n in changed + obsolete if child(target, n).is_file())
    backup_size += sum(child(target, n).stat().st_size for n in state_before)
    new_size = sum(child(source, n).stat().st_size for n in changed)
    required = backup_size + new_size + 64 * 1024 * 1024
    report = dict(from_version=old['version'], to_version=new['version'], edition=new['edition'],
                  changed_files=len(changed), retired_files=len(obsolete), required_free_bytes=required,
                  free_bytes=shutil.disk_usage(target).free, preserved=before, applied=False)
    if report['free_bytes'] < required:
        raise UpgradeError('磁盘空间不足，不能同时保留备份和新版程序。请清理其他文件后重试。')
    if not apply:
        return report
    if not changed and not obsolete:
        return dict(report, applied=True, already_current=True)
    lock = child(target, '.tmp/upgrade.lock')
    lock.parent.mkdir(exist_ok=True)
    try:
        lock.open('x', encoding='utf-8').close()
    except FileExistsError:
        raise UpgradeError('上一次升级仍在执行或被中断；请先检查 reports/private/upgrades 中的 journal.json 和备份。') from None
    backup = child(target, 'reports/private/upgrades/' + time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8])
    journal = dict(report, state='preparing', files=[])
    def save():
        (backup / 'journal.json').write_text(json.dumps(journal, ensure_ascii=False, indent=2), 'utf-8')
    safe_to_unlock = True
    try:
        if running_in((source, target)):
            raise UpgradeError('检测到程序重新启动，未执行升级。')
        backup.mkdir(parents=True)
        locks = ExitStack()
        with locks:
            # Refuse a late writer. Keep both databases frozen for the whole program update.
            for name in ('chats.sqlite3', 'mcp-access.sqlite3'):
                dbpath = child(target, 'data/' + name)
                if not dbpath.exists():
                    continue
                db = sqlite3.connect(dbpath.as_uri() + '?mode=rw', uri=True, timeout=0)
                locks.callback(db.close)
                db.execute('BEGIN IMMEDIATE')
                locks.callback(db.rollback)
            if state_files(target) != state_before:
                raise UpgradeError('升级检查后数据发生变化，请停止读取任务后重试。')
            # Raw DB + WAL remain a matching recovery set; locks prevent changes.
            for name in state_before:
                dst = child(backup, 'userdata/' + name)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(child(target, name), dst)
            for name in changed + obsolete:
                original = child(target, name)
                if original.is_file():
                    dst = child(backup, 'program/' + name)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, dst)
            journal['state'] = 'installing'
            save()
            total = len(changed) + len(obsolete)
            last_progress = 0
            for i, name in enumerate(changed + obsolete, 1):
                dst = child(target, name)
                # Journal before mutation, so abrupt power loss is recoverable too.
                journal['files'].append(name)
                save()
                if name in new_files:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(child(source, name), dst)
                    if digest(dst) != digest(child(source, name)):
                        raise UpgradeError('更新后的程序校验失败。')
                elif dst.is_file():
                    dst.unlink()  # Exact manifest path, already backed up; never recursive.
                if time.monotonic() - last_progress > 1 or i == total:
                    progress(f'更新程序 {i}/{total}', flush=True)
                    last_progress = time.monotonic()
            if state_files(target) != state_before:
                raise UpgradeError('个人数据校验不一致，停止升级。')
            journal.update(state='completed', applied=True, preserved_state_verified=True)
            save()
    except BaseException:
        # Restore only program files we touched, never overwrite current user data.
        try:
            for name in reversed(journal['files']):
                dst, saved = child(target, name), child(backup, 'program/' + name)
                if saved.is_file():
                    shutil.copy2(saved, dst)
                elif dst.is_file():
                    dst.unlink()
            journal['state'] = 'rolled_back'
            if backup.exists():
                save()
        except BaseException:
            safe_to_unlock = False
            journal['state'] = 'recovery_required'
            save()
        raise
    finally:
        if safe_to_unlock:
            lock.unlink(missing_ok=True)
    return dict(report, applied=True, preserved_state_verified=True, backup=str(backup))


def main():
    p = argparse.ArgumentParser(description='保留原目录全部个人数据，校验后更新程序；默认只检查。')
    p.add_argument('--target', required=True, help='原来使用的 Tulpa 文件夹（包含 Tulpa.exe）')
    p.add_argument('--apply', action='store_true', help='备份并执行升级；省略时只检查')
    args = p.parse_args()
    try:
        result = upgrade(ROOT, args.target, apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result['applied']:
            print('升级完成。请打开原目录的 Tulpa.exe；无需重新导入。MCP 客户端重新连接即可。')
    except (UpgradeError, OSError, sqlite3.Error) as exc:
        # OS errors may include account paths; keep diagnostics local and concise.
        print(str(exc) if isinstance(exc, UpgradeError) else '升级未完成（' + type(exc).__name__ + '）。请检查本机备份记录，勿删除原目录。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
