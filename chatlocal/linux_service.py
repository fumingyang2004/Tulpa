"""Per-user systemd installation and a portable source distribution."""
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

from .config import ROOT
from .snowluma_secure import atomic_bytes


def _systemctl(*args,check=False):
    result=subprocess.run(['systemctl','--user',*args],capture_output=True,text=True,timeout=20)
    if check and result.returncode:raise ValueError('用户 systemd 操作未完成，请检查 systemctl --user status tulpa。')
    return result


def service_name(root=ROOT):
    if sys.platform == 'darwin':
        from .macos_service import service_name as action_impl
        return action_impl(root)
    from .terminal_instances import metadata
    root=Path(root).resolve()
    instance=metadata(root)
    if not instance:return 'tulpa.service'
    digest=hashlib.sha256(str(root).encode()).hexdigest()[:12]
    return f'tulpa-{instance["id"]}-{digest}.service'


def service_status(root=ROOT):
    if sys.platform == 'darwin':
        from .macos_service import service_status as action_impl
        return action_impl(root)
    if not shutil.which('systemctl'):return dict(available=False,installed=False,active=False,detail='未安装 systemd，可直接运行 tulpa serve。')
    name=service_name(root)
    unit=Path(os.environ.get('XDG_CONFIG_HOME',Path.home()/'.config'))/'systemd/user'/name
    try:
        result=_systemctl('show',name,'--property=ActiveState,SubState,UnitFileState,FragmentPath,WorkingDirectory')
        fields=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
        ours=fields.get('WorkingDirectory')==str(Path(root).resolve())
        return dict(available=bool(fields),installed=unit.is_file(),active=ours and fields.get('ActiveState')=='active',
            state=fields.get('ActiveState','unavailable'),substate=fields.get('SubState',''),enabled=fields.get('UnitFileState')=='enabled',
            this_instance=ours,unit=str(unit),name=name)
    except (OSError,subprocess.TimeoutExpired):return dict(available=False,installed=unit.is_file(),active=False,detail='当前会话无法连接用户 systemd。')


def _quote(value):
    value=str(value)
    if any(c in value for c in ('\n','\r','\0')):raise ValueError('服务路径无效。')
    return '"'+value.replace('\\','\\\\').replace('"','\\"').replace('%','%%').replace('$','$$')+'"'


def unit_text(root=ROOT,port=7861):
    root=Path(root).resolve()
    if not 1024<=port<=65535:raise ValueError('端口范围为 1024–65535。')
    directory=str(root)
    if any(c in directory for c in ('\n','\r','\0')) or directory.endswith((' ','\t','\\')):
        raise ValueError('服务目录含 systemd 无法表示的结尾空白或换行，请更换路径。')
    # WorkingDirectory takes the entire value literally; unlike ExecStart it
    # does not unquote shell-like strings. Spaces are valid without quotes.
    directory=directory.replace('%','%%')
    from .terminal_instances import metadata, private_environment_keys
    isolated = ('UnsetEnvironment='+' '.join(private_environment_keys(root,os.environ))+'\n') if metadata(root) else ''
    return ('# Managed by Tulpa Linux CLI\n[Unit]\nDescription=Tulpa local assistant\nAfter=network.target\n\n'
        '[Service]\nType=simple\nWorkingDirectory='+directory+'\nExecStart='+_quote(root/'tulpa')+f' serve --port {port}\n'
        'Restart=on-failure\nRestartSec=3\nTimeoutStopSec=30\nUMask=0077\n'
        'Environment=PYTHONUNBUFFERED=1\n'+isolated+'\n[Install]\nWantedBy=default.target\n')


def service_action(action,*,root=ROOT,port=7861,start=False,enable=True):
    if sys.platform == 'darwin':
        from .macos_service import service_action as action_impl
        return action_impl(action, root=root, port=port, start=start, enable=enable)
    if os.name=='nt' or not shutil.which('systemctl'):raise ValueError('此命令需要 Linux 用户 systemd。')
    name=service_name(root)
    unit=Path(os.environ.get('XDG_CONFIG_HOME',Path.home()/'.config'))/'systemd/user'/name
    root=Path(root).resolve()
    if action=='status':return service_status(root)
    if unit.exists():
        text=unit.read_text()
        if not text.startswith('# Managed by Tulpa Linux CLI\n') or _quote(root/'tulpa') not in text:
            raise ValueError('同名服务不属于此 Tulpa 实例，未覆盖或停止。')
    current=service_status(root)
    if current.get('state')=='active' and not current.get('this_instance'):
        raise ValueError('同名运行服务属于另一目录，未操作。')
    if action=='install':
        unit.parent.mkdir(parents=True,exist_ok=True)
        atomic_bytes(unit,unit_text(root,port).encode())
        if start and current.get('active'):_systemctl('stop',name,check=True)
        _systemctl('daemon-reload',check=True)
        if enable:_systemctl('enable',name,check=True)
        if start:_systemctl('start',name,check=True)
    elif action=='uninstall':
        if not unit.exists():return dict(ok=True,installed=False)
        _systemctl('disable','--now',name,check=True);unit.unlink();_systemctl('daemon-reload',check=True)
    elif action in ('start','stop','restart'):
        if not unit.exists() and not current.get('this_instance'):raise ValueError('请先运行 tulpa service install。')
        _systemctl(action,name,check=True)
    else:raise ValueError('未知服务操作。')
    return service_status(root)


def desktop_install(root=ROOT):
    if sys.platform == 'darwin':
        from macos.build_app import build
        return dict(path=str(build(Path.home()/'Applications/Tulpa.app', root=root)))
    root=Path(root).resolve()
    from .terminal_instances import metadata
    instance=metadata(root)
    filename=service_name(root).removesuffix('.service')+'.desktop' if instance else 'tulpa-terminal.desktop'
    path=Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))/'applications'/filename
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and 'X-Tulpa-Managed=true' not in path.read_text():raise ValueError('已有同名桌面入口，未覆盖。')
    executable=str(root/'tulpa').replace('\\','\\\\').replace('"','\\"').replace('`','\\`').replace('$','\\$').replace('%','%%')
    label='Tulpa'+(' · '+instance['name'] if instance else '')
    text='[Desktop Entry]\nType=Application\nName='+label+'\nComment=Local chat assistant\nTerminal=true\nExec="'+executable+'"\nCategories=Utility;\nX-Tulpa-Managed=true\n'
    atomic_bytes(path,text.encode())
    return dict(path=str(path))


def source_archive(output,root=ROOT):
    """Explicit public source allowlist; never package accounts, tokens or caches."""
    if sys.platform == 'darwin':
        from .macos_distribution import release_path, source_files
    else:
        from .linux_distribution import release_path, source_files
    root=Path(root).resolve();output=Path(output).expanduser().absolute()
    chosen=source_files(root)
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as stream,tarfile.open(fileobj=stream,mode='w:gz') as archive:
            for path in chosen:
                member=archive.gettarinfo(str(path),arcname='Tulpa/'+release_path(path.relative_to(root)).as_posix())
                member.uid=member.gid=0;member.uname=member.gname=''
                member.mode=0o755 if path.stat().st_mode&0o111 else 0o644
                with path.open('rb') as source:archive.addfile(member,source)
    except BaseException:
        output.unlink(missing_ok=True);raise
    return dict(path=str(output),files=len(chosen),bytes=output.stat().st_size)
