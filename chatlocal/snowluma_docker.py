"""Docker operations for one explicitly authorized, digest-pinned SL instance.

Docker is reached through a local Unix socket, never a user's remote context.
Commands use argv and bounded private output files; secrets never enter argv.
No operation removes a container or a volume. Upstream artifacts stay in Docker.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import time

from .snowluma_runtime import SnowLumaAPI, empty_config, onebot_config
from .snowluma_secure import ManagedError

SCOPE = ['pull_official_pinned_image', 'manage_owned_container', 'persist_qq_data',
         'loopback_admin_http_ws_novnc', 'manage_random_credentials',
         'bind_one_verified_qq_account', 'resume_same_verified_account']
PORTS = dict(admin=5099, http=3000, ws=3001, novnc=6081)
MOUNTS = {'data': '/app/data', 'qq-config': '/app/.config', 'qq-data': '/app/.local/share'}
LABEL = 'io.tulpa.snowluma'


def docker_manifest(manifest):
    return dict(manifest, mode='managed_docker', adapter='snowluma-docker-1.14.22', scope=list(SCOPE))


def release(manifest):
    arch = {'aarch64': 'arm64', 'arm64': 'arm64', 'x86_64': 'amd64', 'AMD64': 'amd64'}.get(platform.machine())
    cfg = manifest.get('docker', {})
    digest = cfg.get('platforms', {}).get(arch)
    if platform.system() not in ('Linux', 'Darwin') or not digest:
        raise ManagedError('docker_platform', 'Docker 托管支持 Linux / macOS 的 ARM64 和 x64；macOS 需要本机 Linux 虚拟机。')
    return dict(image=cfg['repository']+':'+cfg['tag'], image_ref=cfg['repository']+'@'+digest,
                platform='linux/'+arch, digest=digest, version=manifest['version'])


class DockerRuntime:
    def __init__(self, root, *, executable=None, endpoint=None):
        self.root = Path(root)
        self.executable = executable
        self.endpoint = endpoint
        self.root_id = hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()[:20]

    def _command(self):
        executable = self.executable or shutil.which('docker')
        if not executable and platform.system() == 'Darwin':
            for path in (self.root/'.runtime/container/bin/docker', Path.home()/'.local/bin/docker', Path('/usr/local/bin/docker'),
                         Path('/opt/homebrew/bin/docker'), Path('/Applications/Docker.app/Contents/Resources/bin/docker')):
                if path.is_file() and os.access(path, os.X_OK):
                    executable = str(path)
                    break
        if not executable:
            raise ManagedError('docker_missing', '未安装 Docker。请先安装并启动 Docker Engine，再检测。', retryable=True)
        endpoint = self.endpoint or os.environ.get('DOCKER_HOST')
        if not endpoint:
            user = Path(f'/run/user/{os.getuid()}/docker.sock')
            sockets = [user, Path('/var/run/docker.sock')]
            if platform.system() == 'Darwin':
                sockets = [Path.home()/'.colima/tulpa/docker.sock', Path.home()/'.colima/default/docker.sock',
                           Path.home()/'.docker/run/docker.sock', Path('/var/run/docker.sock')]
            endpoint = 'unix://'+str(next((p for p in sockets if p.exists()), sockets[0]))
        if not endpoint.startswith('unix:///') or any(c in endpoint for c in ('\n', '\r', '\0')):
            raise ManagedError('docker_remote', '托管只支持本机 Docker Unix socket；请移除远程 DOCKER_HOST 设置。')
        return [executable, '--host', endpoint]

    def call(self, args, *, timeout=15, check=lambda: None, data=None, missing=False, limit=2*1024*1024):
        check()
        # Temporary files avoid deadlock and unbounded communicate() buffers on pull/logs.
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err, tempfile.TemporaryFile() as inp:
            if data is not None:
                inp.write(data); inp.seek(0)
            try:
                proc = subprocess.Popen(self._command()+list(args), stdin=inp if data is not None else subprocess.DEVNULL,
                                        stdout=out, stderr=err, env=dict(os.environ, DOCKER_CLI_HINTS='false'))
            except OSError:
                raise ManagedError('docker_missing', 'Docker 命令无法执行，请检查安装。', retryable=True) from None
            deadline = time.monotonic()+timeout
            try:
                while proc.poll() is None:
                    check()
                    if time.monotonic() > deadline:
                        raise ManagedError('docker_timeout', 'Docker 操作超时，可查看容器状态后重试。', retryable=True)
                    if os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > limit:
                        raise ManagedError('docker_output_limit', 'Docker 返回内容超出限制。')
                    time.sleep(.05)
                check()
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try: proc.wait(2)
                    except subprocess.TimeoutExpired:
                        proc.kill(); proc.wait()
            out.seek(0); raw = out.read(limit+1)
            err.seek(0); error = err.read(min(limit, 65536)).decode('utf-8', 'replace')
            if len(raw) > limit:
                raise ManagedError('docker_output_limit', 'Docker 返回内容超出限制。')
            if proc.returncode:
                lower = error.lower()
                if missing and any(x in lower for x in ('no such object', 'no such container', 'no such image', 'no such volume', 'could not find the file', 'no such file or directory in container')):
                    return None
                if 'permission denied' in lower and ('docker.sock' in lower or 'connect' in lower):
                    raise ManagedError('docker_permission', '当前 Tulpa 用户无权连接 Docker。加入 Docker 用户组并重新登录或重启用户服务后重试。', retryable=True)
                if any(x in lower for x in ('cannot connect', 'connection refused', 'is the docker daemon running', 'failed to connect to the docker')):
                    raise ManagedError('docker_unavailable', 'Docker 引擎未运行或不可访问；macOS 请启动 Colima / Docker Desktop，Linux 请检查 docker.service。', retryable=True)
                if args and args[0] == 'pull':
                    raise ManagedError('docker_pull_failed', '官方固定版本镜像下载失败，请检查 Docker 的网络或代理后重试。', retryable=True)
                if any(x in lower for x in ('port is already allocated', 'address already in use')):
                    raise ManagedError('port_conflict', '容器端口已被占用；停止占用后重试，不会更改其他服务。', retryable=True)
                if any(x in lower for x in ('operation not permitted', 'cgroup', 'iptables', 'nftables')):
                    raise ManagedError('docker_host_limit', '宿主机的容器权限、cgroup 或网络条件不满足 Docker 运行要求，请检查 Docker 服务日志。', retryable=True)
                raise ManagedError('docker_failed', 'Docker 操作失败，请检查 Docker 服务或容器日志。', retryable=True)
            return raw

    def json(self, args, **kwargs):
        raw = self.call(args, **kwargs)
        if raw is None: return None
        try: return json.loads(raw)
        except (ValueError, TypeError):
            raise ManagedError('docker_protocol', 'Docker 返回了无法识别的数据。') from None

    def environment(self, manifest):
        item = release(manifest)
        info = self.json(['info', '--format', '{{json .}}'], timeout=5)
        arch = {'aarch64': 'arm64', 'x86_64': 'amd64'}.get(info.get('Architecture'), info.get('Architecture'))
        if info.get('OSType') != 'linux' or 'linux/'+str(arch) != item['platform']:
            raise ManagedError('docker_platform', 'Docker 引擎架构与本机不匹配，未使用模拟架构启动 QQ。')
        if not info.get('ID'):
            raise ManagedError('docker_protocol', 'Docker 引擎没有返回可核对的身份。')
        return dict(item, engine_id=info['ID'], engine_version=info.get('ServerVersion', ''))

    def name(self, instance):
        if not re.fullmatch(r'[0-9a-f]{32}', instance):
            raise ManagedError('state_damaged', '容器实例身份无效。')
        return f'tulpa-sl-{self.root_id}-{instance[:12]}'

    def labels(self, instance):
        return {LABEL: '1', LABEL+'.root': self.root_id, LABEL+'.instance': instance}

    def inspect(self, name):
        rows = self.json(['container', 'inspect', name], missing=True)
        if rows is None: return None
        if not isinstance(rows, list) or len(rows) != 1:
            raise ManagedError('docker_protocol', 'Docker 容器信息无效。')
        return rows[0]

    def ensure_image(self, selection, check):
        rows = self.json(['image', 'inspect', selection['image_ref']], missing=True, check=check)
        if not rows:
            self.call(['pull', '--quiet', '--platform', selection['platform'], selection['image_ref']],
                      timeout=1800, check=check, limit=8*1024*1024)
            rows = self.json(['image', 'inspect', selection['image_ref']], check=check)
        image = rows[0]
        if (str(image.get('Os'))+'/'+str(image.get('Architecture')) != selection['platform']
                or selection['image_ref'] not in image.get('RepoDigests', [])):
            raise ManagedError('docker_image_changed', '镜像架构或摘要与授权版本不符。')
        return image['Id']

    def volumes(self, instance, *, create=False, check=lambda: None):
        result = {}
        for suffix, mount in MOUNTS.items():
            name = self.name(instance)+'-'+suffix
            labels = dict(self.labels(instance), **{LABEL+'.volume': suffix})
            rows = self.json(['volume', 'inspect', name], missing=True, check=check)
            if rows is None and create:
                args = ['volume', 'create']
                for key, value in labels.items(): args += ['--label', key+'='+value]
                self.call(args+[name], check=check)
                rows = self.json(['volume', 'inspect', name], check=check)
            if not rows or any((rows[0].get('Labels') or {}).get(k) != v for k, v in labels.items()):
                raise ManagedError('docker_volume_changed', '持久卷缺失或属于其他实例，未接管或覆盖。')
            if rows[0].get('Driver') != 'local' or rows[0].get('Options'):
                raise ManagedError('docker_volume_changed', '持久卷驱动或挂载选项已变化，未继续使用。')
            result[mount] = name
        return result

    def validate(self, owner, selection, instance, ports, *, running=False):
        row = self.inspect(owner['id'])
        if not row:
            raise ManagedError('docker_container_missing', '已绑定容器被删除；数据卷保留，请恢复原容器后重试。', retryable=True)
        labels = row.get('Config', {}).get('Labels') or {}
        host = row.get('HostConfig', {})
        bindings = host.get('PortBindings') or {}
        mounts = {m.get('Destination'): m.get('Name') for m in row.get('Mounts', []) if m.get('Type') == 'volume'}
        expected = {str(PORTS[k])+'/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(v)}] for k,v in ports.items()}
        if (any(labels.get(k) != v for k,v in self.labels(instance).items())
                or row.get('Name', '').lstrip('/') != self.name(instance)
                or row.get('Image') != owner['image_id'] or row.get('Config', {}).get('Image') != selection['image_ref']
                or bindings != expected or host.get('NetworkMode') != 'bridge'
                or host.get('Privileged') or host.get('PidMode') or host.get('Binds')
                or mounts != {mount:self.name(instance)+'-'+key for key,mount in MOUNTS.items()}
                or len(row.get('Mounts', [])) != 3):
            raise ManagedError('docker_owner_changed', '容器归属、镜像、网络或挂载已变化，已停止托管访问。')
        if running and not row.get('State', {}).get('Running'):
            raise ManagedError('docker_stopped', '容器已停止，正在恢复原实例。', retryable=True)
        return row

    def read_file(self, container, path, *, missing=False):
        raw = self.call(['cp', container+':'+path, '-'], missing=missing, limit=1024*1024)
        if raw is None: return None
        try:
            with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
                members = archive.getmembers()
                if len(members) != 1 or not members[0].isfile() or members[0].size > 512*1024:
                    raise ValueError()
                return archive.extractfile(members[0]).read()
        except (tarfile.TarError, ValueError, OSError):
            raise ManagedError('docker_file_invalid', '容器文件格式无效，未继续初始化。') from None

    def verify_package(self, owner, manifest):
        try: package = json.loads(self.read_file(owner['id'], '/app/runtime/package.json'))
        except (ValueError, TypeError):
            raise ManagedError('docker_package_invalid', '镜像中的 SnowLuma 包信息无效。') from None
        if package.get('name') != '@snowluma/runtime' or package.get('version') != manifest['version']:
            raise ManagedError('unsupported_version', '容器实际 SnowLuma 版本与授权版本不符。')
        for agreement in manifest['agreements']:
            raw = self.read_file(owner['id'], '/app/runtime/'+agreement['file'])
            if hashlib.sha256(raw).hexdigest() != agreement['sha256']:
                raise ManagedError('terms_changed', '镜像中的实际协议与已展示协议不同，未启动。')

    def verify_credentials(self, owner, credentials, *, required):
        raw = self.read_file(owner['id'], '/app/data/config/webui.json', missing=True)
        if raw is None and not required: return
        try:
            value = json.loads(raw)
            salt = bytes.fromhex(value['passwordSalt'])
            expected = hashlib.scrypt(credentials['admin_password'].encode(), salt=salt, n=16384, r=8, p=1, dklen=64).hex()
            valid = (len(salt)==16 and value.get('passwordHash')==expected
                     and value.get('mustChangePassword') is False and not value.get('totp'))
        except (ValueError, TypeError, KeyError): valid = False
        if not valid:
            raise ManagedError('credential_state_invalid', '容器中的认证文件缺失、已变化或要求额外验证；未重置密码，请恢复匹配配置。')

    def seed_config(self, owner, check):
        """Disable upstream default adapters before first launch. Never overwrite an existing file."""
        if self.read_file(owner['id'], '/app/data/config/onebot.json', missing=True) is not None: return
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w') as archive:
            directory = tarfile.TarInfo('config'); directory.type=tarfile.DIRTYPE; directory.mode=0o700
            directory.uid=directory.gid=1000; archive.addfile(directory)
            data = json.dumps(empty_config()).encode()
            entry = tarfile.TarInfo('config/onebot.json'); entry.mode=0o600; entry.size=len(data); entry.uid=entry.gid=1000
            archive.addfile(entry, io.BytesIO(data))
        self.call(['cp', '-', owner['id']+':/app/data'], data=raw.getvalue(), check=check)

    def create(self, selection, instance, image_id, ports, credentials, check):
        name = self.name(instance)
        if self.inspect(name):
            raise ManagedError('docker_name_conflict', '同名容器已存在，未接管；请核对实例状态。')
        volumes = self.volumes(instance, create=True, check=check)
        args = ['create', '--name', name, '--platform', selection['platform'], '--restart', 'unless-stopped',
                '--network', 'bridge', '--shm-size', '1g', '--ulimit', 'nofile=65536:65536',
                '--cap-add', 'SYS_PTRACE', '--security-opt', 'seccomp=unconfined',
                '--log-driver', 'json-file', '--log-opt', 'max-size=10m', '--log-opt', 'max-file=3']
        for key, value in self.labels(instance).items(): args += ['--label', key+'='+value]
        for key, port in ports.items(): args += ['-p', f'127.0.0.1:{port}:{PORTS[key]}']
        # These are fresh data directories, not runtime assets. Skipping Docker's
        # image-directory copy also avoids copying SELinux xattrs into rootless volumes.
        for destination, volume in volumes.items(): args += ['--mount', f'type=volume,src={volume},dst={destination},volume-nocopy']
        env = dict(SNOWLUMA_WEBUI_HOST='0.0.0.0', SNOWLUMA_WEBUI_PORT='5099', SNOWLUMA_HOOK_AUTOLOAD='1',
                   SNOWLUMA_ONEBOT_HOST='0.0.0.0', SNOWLUMA_EXTRA_QQ_HOMES='', SNOWLUMA_LOG_LEVEL='warn',
                   SNOWLUMA_QQ_FLAGS='--disable-gpu --disable-software-rasterizer --disable-gpu-compositing',
                   SNOWLUMA_WEBUI_BOOTSTRAP_PASSWORD=credentials['admin_password'], VNC_PASSWD=credentials['vnc_password'])
        with tempfile.NamedTemporaryFile(mode='w', prefix='tulpa-docker-', encoding='utf-8') as stream:
            for key,value in env.items(): stream.write(key+'='+value+'\n')
            stream.flush()
            self.call(args+['--env-file', stream.name, selection['image_ref']], check=check)
        row = self.inspect(name)
        if not row or not re.fullmatch(r'[0-9a-f]{64}', row.get('Id','')):
            raise ManagedError('docker_protocol', 'Docker 未返回有效的容器身份。')
        owner = dict(id=row['Id'], image_id=image_id)
        self.validate(owner, selection, instance, ports)
        return owner

    def recover_created(self, selection, instance, ports, image_id):
        """Recover a create whose daemon result arrived after cancellation/crash."""
        row = self.inspect(self.name(instance))
        if row is None: return None
        owner = dict(id=row['Id'], image_id=image_id)
        self.validate(owner, selection, instance, ports)
        return owner

    def start(self, owner, selection, instance, ports, check):
        row = self.validate(owner, selection, instance, ports)
        if not row.get('State', {}).get('Running'):
            self.call(['start', owner['id']], timeout=60, check=check)

    def prepare_display(self, owner, selection, instance, ports, check):
        """Restore the private login workspace and a phone-compatible VNC view.

        Run only after X is ready, and again when the operator opens the login entry.
        Existing browser settings and desktop wheel events must not hide QQ.
        """
        self.validate(owner, selection, instance, ports, running=True)
        self.call(['exec', '-i', owner['id'], 'python3', '-'], check=check,
                  data=Path(__file__).with_name('snowluma_desktop.py').read_bytes())
        for setting in ('off', 'noblank', 'reset'):
            self.call(['exec', owner['id'], 'env', 'DISPLAY=:1', 'xset', 's', setting], check=check)
        # Keep password authentication; permit a reconnect or a second browser
        # to receive frames while an old tab still owns a VNC connection.
        for command in ('shared', 'refresh'):
            self.call(['exec', owner['id'], 'env', 'DISPLAY=:1', 'X11VNC_SYNC_TIMEOUT=8',
                       'x11vnc', '-sync', '-R', command], check=check, timeout=12)

    def stop(self, owner, selection, instance, ports):
        row=self.validate(owner, selection, instance, ports)
        if row.get('State',{}).get('Running'):
            self.call(['stop', '--time', '5', owner['id']], timeout=12)

    def logs(self, owner, selection, instance, ports, credentials):
        self.validate(owner, selection, instance, ports)
        # Docker writes container stderr to the CLI's stderr; combine with --timestamps
        # in a dedicated implementation, still never return unsanitized output.
        with tempfile.TemporaryFile() as output:
            try:
                result = subprocess.run(self._command()+['logs', '--tail', '120', '--timestamps', owner['id']],
                                        stdout=output, stderr=output, timeout=5, stdin=subprocess.DEVNULL)
            except (OSError, subprocess.TimeoutExpired):
                raise ManagedError('docker_logs_failed', '暂时无法读取容器日志。', retryable=True) from None
            if result.returncode:
                raise ManagedError('docker_logs_failed', '暂时无法读取容器日志。', retryable=True)
            output.seek(max(0, output.tell()-65536))
            text = output.read(65536).decode('utf-8', 'replace')
        text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
        for value in credentials.values():
            if isinstance(value,str) and value: text=text.replace(value, '[已隐藏]')
        return ['[含凭据的日志已隐藏]' if re.search(r'password|token|secret|authorization|cookie|密码|凭据', line, re.I)
                else ''.join(c for c in line if c.isprintable())[:1000] for line in text.splitlines()[-120:]]


class DockerSnowLumaAPI(SnowLumaAPI):
    def configure(self, account, ports, credentials):
        config = onebot_config(PORTS, credentials)
        for key in ('httpServers', 'wsServers'):
            for server in config['networks'][key]: server['host']='0.0.0.0'
        result = self.request('POST', '/api/config/'+account, config)
        if not all(result.get(k) is True for k in ('saved', 'applied', 'online')):
            raise ManagedError('config_not_applied', 'QQ 尚未在线或容器 OneBot 配置未生效，请完成扫码后重试。', retryable=True)
