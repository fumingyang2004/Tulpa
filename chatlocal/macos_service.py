"""Per-checkout launchd agents; no shell interpolation or cross-instance control."""
import hashlib
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess

from .config import ROOT
from .snowluma_secure import atomic_bytes


def service_name(root=ROOT):
    return 'io.tulpa.backend.' + hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:16]


def paths(root):
    name = service_name(root) + '.plist'
    return (Path.home()/'Library/LaunchAgents'/name, Path(root)/'.tmp/launchd'/name)


def launchctl(*args, check=False):
    result = subprocess.run(['/bin/launchctl', *args], capture_output=True, text=True, timeout=20)
    if check and result.returncode:
        raise ValueError('launchd 操作失败，请在已登录的 macOS 桌面会话中重试，并查看 .tmp/terminal-service.log。')
    return result


def description(root=ROOT, port=7861):
    root = Path(root).resolve()
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('端口范围为 1024–65535。')
    return dict(Label=service_name(root), ProgramArguments=[str(root/'tulpa'), 'serve', '--port', str(port)],
                WorkingDirectory=str(root), RunAtLoad=True, KeepAlive={'SuccessfulExit': False},
                ThrottleInterval=5, ExitTimeOut=30, Umask=0o077,
                EnvironmentVariables=dict(PYTHONUNBUFFERED='1', TMPDIR=str(root/'.tmp'),
                    PATH=f'{root}/.runtime/container/bin:{Path.home()}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'),
                StandardOutPath=str(root/'.tmp/terminal-service.log'),
                StandardErrorPath=str(root/'.tmp/terminal-service.log'), TulpaManagedRoot=str(root))


def read_owned(path, root):
    try:
        if path.is_symlink():
            raise ValueError('launchd 配置是符号链接，未操作。')
        cfg = plistlib.loads(path.read_bytes())
        args = cfg.get('ProgramArguments', [])
        if (cfg.get('TulpaManagedRoot') != str(root) or cfg.get('Label') != service_name(root)
                or cfg.get('WorkingDirectory') != str(root) or len(args) != 4
                or args[:3] != [str(root/'tulpa'), 'serve', '--port']):
            raise ValueError('同名 launchd 服务不属于此 Tulpa 实例，未操作。')
        return cfg
    except (OSError, plistlib.InvalidFileException, TypeError):
        raise ValueError('无法读取本实例的 launchd 配置，未操作。') from None


def service_status(root=ROOT):
    root = Path(root).resolve()
    enabled, manual = paths(root)
    unit = enabled if enabled.exists() else manual
    installed = unit.exists()
    cfg = read_owned(unit, root) if installed else None
    if not shutil.which('launchctl'):
        return dict(available=False, installed=installed, active=False, unit=str(unit))
    result = launchctl('print', f'gui/{os.getuid()}/{service_name(root)}')
    fields = dict(re.findall(r'^\s*(path|program|state|pid) = (.+)$', result.stdout, re.M))
    ours = bool(cfg and fields.get('path') == str(unit) and fields.get('program') == str(root/'tulpa'))
    return dict(available=True, installed=installed, active=ours and fields.get('state') == 'running',
                loaded=result.returncode == 0, this_instance=ours, state=fields.get('state', 'stopped'),
                enabled=enabled.exists(), unit=str(unit), name=service_name(root),
                port=int(cfg['ProgramArguments'][3]) if cfg else None, pid=fields.get('pid'))


def service_action(action, *, root=ROOT, port=7861, start=False, enable=True):
    root = Path(root).resolve()
    current = service_status(root)
    if action == 'status':
        return current
    if not current['available']:
        raise ValueError('此命令需要 macOS launchd。')
    if current.get('loaded') and not current.get('this_instance'):
        raise ValueError('同名运行服务属于其他目录，未操作。')
    target = f'gui/{os.getuid()}/{service_name(root)}'
    enabled, manual = paths(root)
    for path in (enabled, manual):
        if path.exists() or path.is_symlink():
            read_owned(path, root)
    if action == 'install':
        if current.get('loaded') and not start:
            raise ValueError('服务已加载，请先停止，或使用 service install --start 更新。')
        cfg = description(root, port)
        if current.get('loaded'):
            launchctl('bootout', target, check=True)
        unit = enabled if enable else manual
        unit.parent.mkdir(parents=True, exist_ok=True)
        (root/'.tmp').mkdir(exist_ok=True)
        atomic_bytes(unit, plistlib.dumps(cfg))
        (manual if enable else enabled).unlink(missing_ok=True)
        if start:
            launchctl('bootstrap', f'gui/{os.getuid()}', str(unit), check=True)
    elif action in ('stop', 'uninstall'):
        if current.get('loaded'):
            launchctl('bootout', target, check=True)
        if action == 'uninstall':
            enabled.unlink(missing_ok=True)
            manual.unlink(missing_ok=True)
    elif action in ('start', 'restart'):
        if not current['installed']:
            raise ValueError('请先运行 tulpa service install。')
        if action == 'restart' and current.get('loaded'):
            launchctl('bootout', target, check=True)
        if action == 'restart' or not current.get('loaded'):
            launchctl('bootstrap', f'gui/{os.getuid()}', current['unit'], check=True)
        elif not current.get('active'):
            launchctl('kickstart', target, check=True)
    else:
        raise ValueError('未知服务操作。')
    return service_status(root)


def installed_service(root, port, token_file):
    root = Path(root).resolve()
    status = service_status(root)
    if not status.get('available') or not status.get('installed'):
        return False
    if status['port'] != port or Path(token_file) != root/'data/terminal-admin.token':
        raise ValueError('已有本实例的 launchd 服务，请使用它的端口和默认管理凭据。')
    return True
