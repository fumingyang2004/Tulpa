"""Offline custom-path repair plus the installed Tulpa QQ batch importer.

Distribute with reader_patches.py, beside (not instead of) the user's installation.
No source files are moved, and no message, credential or grant is bundled.
"""
import argparse
from collections import deque
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reader_patches import patch_qq_configured_root, patch_qq_passive_scan

READER = 'tools/qq-reader/chatlog_keeper/qq_db.py'
KEY_SCANNER = 'tools/qq-reader/chatlog_keeper/_tulpa_passive_keys.py'
SETTING = 'data/qq-source-root.json'
LAYOUTS = ('nt_qq/nt_db/nt_msg.db', 'nt_db/nt_msg.db')
FORMAT = 'tulpa-qq-source-fix-v1'


def say(text):
    print(text, flush=True)


def atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path, data):
    atomic_bytes(path, json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))


def load_upgrade(root):
    path = root/'scripts/upgrade_installation.py'
    spec = importlib.util.spec_from_file_location('_qq_fix_upgrade', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_install(root):
    root = Path(root).absolute()
    if any(p.is_symlink() or p.is_junction() for p in (root, *root.parents)):
        raise ValueError('请把修复包放在普通本地 Tulpa 安装目录中，不通过目录链接运行。')
    root = root.resolve()
    for name in ('Tulpa.exe', 'runtime/python.exe', READER,
                 'scripts/upgrade_installation.py', 'scripts/list_client_accounts.py',
                 'scripts/export_qq.py', 'chatlocal/import_pipeline.py'):
        if not (root/name).is_file():
            raise ValueError('请将修复包完整解压到正在使用的 Tulpa.exe 同一层。')
    upgrade = load_upgrade(root)
    manifest, version, _ = upgrade.manifest(root)
    if version != (0, 5, 1):
        raise ValueError('此临时修复包适用于 Tulpa 0.5.1 完整版 / MCP 版。其他版本请联系开发者。')
    for name in (READER, KEY_SCANNER, SETTING, 'reports/private/qq-source-fix', '.tmp/upgrade.lock',
                 'data/chats.sqlite3', 'data/qq-snapshot-info.json', 'tools/reader-manifest.json'):
        upgrade.child(root, name)
    compile(patch_qq_passive_scan(patch_qq_configured_root((root/READER).read_bytes())), READER, 'exec')
    return root, upgrade, manifest


def accounts_at(root):
    result = {}
    with os.scandir(root) as entries:
        for number, entry in enumerate(entries):
            if number >= 1024:
                raise ValueError('指定目录的项目过多，请指定更接近 QQ 账号的聊天数据目录。')
            if not entry.name.isdecimal() or not entry.is_dir(follow_symlinks=False):
                continue
            account = Path(entry.path)
            if account.is_junction():
                continue
            for layout in LAYOUTS:
                path = account/layout
                if any(p.is_symlink() or p.is_junction() for p in (path, path.parent, path.parent.parent)):
                    continue
                if path.is_file() and path.stat().st_size > 0:
                    result[entry.name] = path
                    break
    return result


def find_sources(source):
    """Only the supplied folder and three child levels; metadata, not DB contents."""
    source = Path(source).absolute()
    if str(source).startswith(('\\\\', '//')) or source == Path(source.anchor):
        raise ValueError('请指定本机的 QQ 聊天数据文件夹，不能指定网络目录或整个盘。')
    if not source.is_dir():
        raise ValueError('QQ 聊天保存目录不存在。请检查修复窗口显示的路径；也可把正确文件夹拖到修复 CMD 上。')
    if any(p.is_symlink() or p.is_junction() for p in (source, *source.parents)):
        raise ValueError('QQ 数据目录使用了链接，请指定实际保存数据的文件夹。')
    pending = deque([(source, 0)])
    # Also accept dropping a specific account folder, without searching its siblings recursively.
    if source.name.isdecimal():
        rows = accounts_at(source.parent)
        if source.name in rows:
            return [(source.parent, rows)]
    found, checked, started = [], 0, time.monotonic()
    while pending:
        root, depth = pending.popleft()
        checked += 1
        if checked > 768 or time.monotonic()-started > 30:
            raise ValueError('定点查找超过范围，未修改数据。请把实际账号所在的文件夹拖到修复 CMD 上重试。')
        rows = accounts_at(root)
        if rows:
            found.append((root, rows))
            continue
        if depth < 3:
            with os.scandir(root) as entries:
                for number, entry in enumerate(entries):
                    if number >= 1024 or len(pending) >= 768:
                        raise ValueError('目录项目过多，未修改数据。请指定更接近 QQ 账号的文件夹。')
                    if entry.is_dir(follow_symlinks=False):
                        child = Path(entry.path)
                        if not child.is_junction():
                            pending.append((child, depth+1))
    if not found:
        raise ValueError('此目录及其有限子目录中仍未找到可读取的 nt_msg.db。没有移动或改动 QQ 数据；本次未开始导入。')
    return found


def existing_selection(root):
    """Account guard before Store initialization or any program/config mutation."""
    bound, selected = set(), None
    metadata = root/'data/qq-snapshot-info.json'
    if metadata.exists():
        value = json.loads(metadata.read_text('utf-8-sig'))
        bound.add(str(value['account']))
    path = root/'data/chats.sqlite3'
    if path.exists():
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=5)) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ('sync_state', 'live_state'):
                if table in tables:
                    row = db.execute(f'SELECT checkpoint FROM {table} WHERE platform=?', ('qq',)).fetchone()
                    if row and (account := json.loads(row[0]).get('account')):
                        bound.add(str(account))
            if 'client_settings' in tables:
                row = db.execute("SELECT value FROM client_settings WHERE name='import_scope'").fetchone()
                if row:
                    selected = json.loads(row[0]).get('qq', {}).get('account')
            if 'messages' in tables:
                # Preserve the single-source guard even if old snapshot metadata was deleted.
                for row in db.execute("SELECT DISTINCT substr(conversation_id,1,instr(conversation_id,':')-1) FROM messages WHERE platform='qq' AND instr(conversation_id,':')>0"):
                    if str(row[0]).isdecimal():
                        bound.add(str(row[0]))
    if len(bound) > 1:
        raise ValueError('此 Tulpa 已有多个 QQ 来源标识，未改动；需要先核对原有账号来源。')
    return next(iter(bound), None), selected


def select_source(roots, bound=None, selected=None, requested=None, ask=input):
    choices = sorted(((root, account) for root, rows in roots for account in rows), key=lambda r:(str(r[0]), r[1]))
    if bound and requested and requested != bound:
        raise ValueError('指定 QQ 账号与此 Tulpa 已有数据来源不一致，未导入其他账号。')
    wanted = bound or requested
    if wanted:
        choices = [row for row in choices if row[1] == wanted]
        if not choices:
            raise ValueError('指定目录中没有此 Tulpa 绑定的 QQ 账号。已有数据保留，不会自动改读其他账号。')
    elif selected:
        preferred = [row for row in choices if row[1] == selected]
        if preferred:
            choices = preferred
    if len(choices) == 1:
        return choices[0]
    say('检测到多个账号或数据位置，请选择本次登录并希望导入的 QQ：')
    for index, (root, account) in enumerate(choices, 1):
        say(f'  {index}. QQ {account}  —  {root}')
    value = ask('输入序号（不会自动选其他账号）：').strip()
    if not value.isdecimal() or not 1 <= int(value) <= len(choices):
        raise ValueError('没有选择有效账号，未改动。重新运行即可。')
    return choices[int(value)-1]


@contextmanager
def installation_lock(root, upgrade):
    if upgrade.running_in((root,)):
        raise ValueError('Tulpa 仍在运行。请右键托盘图标 → 彻底退出，再运行修复；QQ 保持登录。')
    lock = upgrade.child(root, '.tmp/upgrade.lock')
    lock.parent.mkdir(exist_ok=True)
    try:
        with lock.open('x', encoding='utf-8') as stream:
            json.dump(dict(kind=FORMAT, pid=os.getpid()), stream)
    except FileExistsError:
        raise ValueError('此目录正在升级或上次修复意外中断。请保留 .tmp/upgrade.lock 和 reports/private 中的记录，联系开发者。') from None
    try:
        if upgrade.running_in((root,)):
            raise ValueError('检测到 Tulpa 重新启动，请彻底退出后重试。')
        yield
    finally:
        lock.unlink(missing_ok=True)


def backup_database(root, folder):
    path = root/'data/chats.sqlite3'
    if not path.exists():
        return
    required = path.stat().st_size + 512*1024*1024
    if shutil.disk_usage(root).free < required:
        raise ValueError('可用空间不足以保留当前聊天数据库备份和 512 MiB 余量，未开始修复。')
    say('[2/5] 备份当前 Tulpa 聊天库（包括已有微信记录）…')
    last = [0.0]
    def progress(status, remaining, total):
        if time.monotonic()-last[0] >= 2 or remaining == 0:
            say(f'  备份进度：{100*(total-remaining)//max(1,total)}%')
            last[0] = time.monotonic()
    destination = folder/'userdata/chats.sqlite3'
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=5)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst, pages=2048, progress=progress)
            if dst.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('已有数据库的备份校验未通过，未修改读取器。')
    meta = root/'data/qq-snapshot-info.json'
    if meta.is_file():
        shutil.copy2(meta, folder/'userdata/qq-snapshot-info.json')


def install_patch(root, source, account, folder):
    helper = Path(__file__).resolve().parent/'qq_passive_keys.py'
    compile(helper.read_bytes(), KEY_SCANNER, 'exec')
    changes = {KEY_SCANNER: helper.read_bytes(),
               READER: patch_qq_passive_scan(patch_qq_configured_root((root/READER).read_bytes())),
               SETTING: json.dumps(dict(format='tulpa-qq-source-v1', path=str(source)), ensure_ascii=False, indent=2).encode('utf-8')}
    build = json.loads((root/'build-manifest.json').read_text('utf-8'))
    if isinstance(build.get('file_sha256'), dict):
        build['file_sha256'][READER] = hashlib.sha256(changes[READER]).hexdigest()
        build['file_sha256'][KEY_SCANNER] = hashlib.sha256(changes[KEY_SCANNER]).hexdigest()
    if KEY_SCANNER not in build['files']:
        build['files'].append(KEY_SCANNER)
    build['local_update'] = dict(kind=FORMAT, revision=2, description='Custom QQ source and bounded passive key scanning')
    changes['build-manifest.json'] = json.dumps(build, ensure_ascii=False, indent=2).encode('utf-8')
    reader_manifest = root/'tools/reader-manifest.json'
    if reader_manifest.is_file():
        rows = json.loads(reader_manifest.read_text('utf-8'))
        for row in rows:
            if row.get('file', '').replace('\\', '/') == READER:
                row['installed_sha256'] = hashlib.sha256(changes[READER]).hexdigest()
        rows = [row for row in rows if row.get('file', '').replace('\\', '/') != KEY_SCANNER]
        rows.append(dict(file=KEY_SCANNER, source='scripts/qq_passive_keys.py',
            installed_sha256=hashlib.sha256(changes[KEY_SCANNER]).hexdigest()))
        changes['tools/reader-manifest.json'] = json.dumps(rows, ensure_ascii=False, indent=2).encode('utf-8')
    original, written = {}, []
    for name in changes:
        path = root/name
        original[name] = path.read_bytes() if path.is_file() else None
        if original[name] is not None:
            backup = folder/'original'/name
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(original[name])
    atomic_json(folder/'patch.json', dict(format=FORMAT, state='applying', files=[
        dict(path=name, existed=original[name] is not None,
             before=hashlib.sha256(original[name]).hexdigest() if original[name] is not None else None,
             after=hashlib.sha256(data).hexdigest()) for name, data in changes.items()]))
    try:
        for name, data in changes.items():
            atomic_bytes(root/name, data)
            written.append(name)
        # This subprocess has no path override: proves persistence after restarting the EXE.
        env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
        env.pop('CHATLOG_QQ_DATA_ROOT', None)
        result = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(root/'scripts/list_client_accounts.py'), 'qq'],
            cwd=root, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or account not in json.loads(result.stdout):
            raise ValueError('保存路径后，安装版账号检测仍未通过；已恢复本次修改，没有开始导入。')
    except BaseException:
        for name in reversed(written):
            if original[name] is None:
                (root/name).unlink(missing_ok=True)
            else:
                atomic_bytes(root/name, original[name])
        raise
    atomic_json(folder/'applied.json', dict(format=FORMAT, verified_accounts=True))


def import_history(root, account, *, stickers=True):
    # Use this installation's exact normal exporter, batch commits, dedup and error logs.
    sys.path[:0] = [str(root), str(root/'scripts')]
    from chatlocal.config import ROOT
    if ROOT.resolve() != root:
        raise ValueError('加载了其他 Tulpa 的模块，已停止，未导入到错误安装目录。')
    from chatlocal.store import Store
    from chatlocal.client_accounts import bound_account
    from chatlocal.data_routes import read_clients
    store = Store()
    bound = bound_account(store, 'qq')
    if bound and bound != account:
        raise ValueError('导入前账号来源发生变化，已停止。')
    cancel = threading.Event()
    old_handler = signal.signal(signal.SIGINT, lambda *_: cancel.set())
    last = [None, 0.0]
    def progress(value):
        text = value.get('text', '') if isinstance(value, dict) else str(value)
        stage = value.get('stage') if isinstance(value, dict) else None
        if stage != last[0] or time.monotonic()-last[1] >= 2 or not isinstance(value, dict):
            say(text)
            last[:] = [stage, time.monotonic()]
    try:
        # Passing only QQ preserves WeChat enablement, scope, account and cursors.
        return read_clients(store, {'qq': dict(enabled=True, conversations=None, account=account)},
            dict(qq_days=30, qq_per_chat=500), {'qq': dict(start='', end='')},
            stickers, True, progress, all_messages={'qq': True}, cancelled=cancel)
    finally:
        signal.signal(signal.SIGINT, old_handler)


def repair(root, source, *, requested=None, stickers=True):
    root, upgrade, manifest = validate_install(root)
    # A shell override must not defeat the saved per-install setting during this run.
    os.environ.pop('CHATLOG_QQ_DATA_ROOT', None)
    with installation_lock(root, upgrade):
        say('[1/5] 定点查找 QQ 聊天数据库：'+str(source))
        sources = find_sources(source)
        bound, selected = existing_selection(root)
        source, account = select_source(sources, bound, selected, requested)
        say('本次导入 QQ '+account+'；源目录：'+str(source))
        say('读取此账号本机可用的全部日期和会话，内部仍分批处理。QQ 原数据不移动。')
        folder = upgrade.child(root, 'reports/private/qq-source-fix/'+time.strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:8])
        folder.mkdir(parents=True)
        backup_database(root, folder)
        say('[3/5] 保存自定义路径，并验证重启后仍能发现账号…')
        install_patch(root, source, account, folder)
        atomic_json(folder/'result.json', dict(format=FORMAT, state='importing', created_at=datetime.now(timezone.utc).isoformat()))
        say('[4/5] 开始导入到当前 Tulpa。窗口会持续显示阶段和新增条数；Ctrl+C 可停止并保留已入库批次。')
        try:
            receipt = import_history(root, account, stickers=stickers)
        except BaseException as exc:
            atomic_json(folder/'result.json', dict(format=FORMAT, state='interrupted', error_type=type(exc).__name__))
            raise
        summary = dict(format=FORMAT, state=receipt['status'], platforms=[{
            key: row[key] for key in ('platform', 'status', 'added', 'duplicate', 'skipped', 'job_id') if key in row
        } for row in receipt['platforms']])
        atomic_json(folder/'result.json', summary)
        say('[5/5] '+receipt['summary'])
        say('备份和运行记录：'+str(folder))
        say('QQ 路径已保存；今后照常双击 Tulpa.exe。实时读取的原有开关保持不变。')
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--account')
    parser.add_argument('--no-stickers', action='store_true')
    parser.add_argument('--no-launch', action='store_true')
    args = parser.parse_args()
    try:
        receipt = repair(args.root, args.source, requested=args.account, stickers=not args.no_stickers)
        if receipt['status'] == 'error':
            say('路径修复已生效，但读取未完成。请保持 QQ 登录后重试；本机日志位于 reports/private/data-qq-error.log。')
            return 2
        if not args.no_launch:
            subprocess.Popen([str(args.root.resolve()/'Tulpa.exe')], cwd=args.root.resolve(),
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return 0
    except KeyboardInterrupt:
        say('已停止。已有记录及已经提交的导入批次保留。')
        return 2
    except Exception as exc:
        say('本次未完成：'+(str(exc) if isinstance(exc, (ValueError, FileNotFoundError)) else type(exc).__name__))
        say('没有移动或删除 QQ 原数据。修复记录位于当前 Tulpa 的 reports/private/qq-source-fix。')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
