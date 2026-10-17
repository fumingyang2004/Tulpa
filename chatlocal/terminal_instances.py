"""A local hub for independent Tulpa installations, processes and QQ accounts.

Only public release files and Python dependencies are shared. In particular, a
new instance never inherits the hub's .env, grants, personas, history or SL vault.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import sys
import socket
import tempfile
from urllib.parse import urlparse

if sys.platform == 'darwin':
    from .macos_distribution import release_path, source_files
else:
    from .linux_distribution import release_path, source_files
from .platform_lock import file_lock
from .snowluma_secure import atomic_bytes
from .terminal_client import TerminalClient, TerminalError

ID = re.compile(r'[a-z][a-z0-9-]{0,31}\Z')
ENV_PREFIXES = ('TULPA_', 'REPLY_', 'ARTIFACT_', 'AGENT_', 'VISION_', 'VOICE_', 'CHATWEAVE_', 'CHATLOCAL_')
ENV_KEYS = {'API_BASE', 'API_KEY', 'MODEL', 'QQ_CACHE_MODE', 'PYTHONPATH', 'PYTHONHOME', 'CHATLOG_KEEPER_DATA_DIR'}


def private_environment_keys(root, environ):
    keys = set(ENV_KEYS)
    example = Path(root) / '.env.example'
    if example.is_file():
        keys.update(m.group(1) for m in re.finditer(r'^([A-Z][A-Z0-9_]*)=', example.read_text(), re.M))
    keys.update(k for k in environ if k.startswith(ENV_PREFIXES))
    return sorted(keys)


def child_environment(root):
    env = dict(os.environ)
    for key in private_environment_keys(root, env):
        env.pop(key, None)
    return env


def metadata(root):
    path = Path(root) / 'data/instance.json'
    if not path.exists():
        return None
    if path.is_symlink():
        raise ValueError('实例标识文件不能是符号链接。')
    value = json.loads(path.read_text())
    if (not isinstance(value, dict) or not ID.fullmatch(str(value.get('id', '')))
            or value['id'] == 'default' or not isinstance(value.get('hub_root'), str)
            or not Path(value['hub_root']).is_absolute()
            or not isinstance(value.get('name'), str)
            or not 1 <= len(value['name']) <= 60
            or any(ord(c) < 32 for c in value['name'])
            or any(type(value.get(k)) is not int or not 1024 <= value[k] <= 65535
                   for k in ('management_port', 'mcp_port'))
            or value['management_port'] == value['mcp_port']):
        raise ValueError('实例标识文件无效。')
    return value


def identity(root):
    child = metadata(root)
    if child:
        return dict(id=child['id'], name=child['name'])
    path = Path(root) / 'data/instances.json'
    if path.is_file():
        return dict(id='default', name=json.loads(path.read_text()).get('default_name', '默认实例'))
    return dict(id='default', name='默认实例')


def validate_mcp_port(root, port):
    """Managed children keep their reserved port; the hub cannot take one over."""
    child = metadata(root)
    if child:
        if port != child['mcp_port']:
            raise ValueError(f'此实例已分配 MCP 端口 {child["mcp_port"]}，请使用该端口。')
        return
    path = Path(root) / 'data/instances.json'
    if path.is_file():
        rows = json.loads(path.read_text()).get('items', [])
        if any(port in (r['management_port'], r['mcp_port']) for r in rows):
            raise ValueError('此端口已分配给另一个实例，请使用其他端口。')


class InstanceManager:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / 'data/instances.json'

    def hub_only(self):
        if metadata(self.root):
            raise ValueError('请从主实例的「实例管理」操作其他实例。')

    def read(self):
        self.hub_only()
        if self.path.is_symlink():
            raise ValueError('实例注册表不能是符号链接。')
        if not self.path.exists():
            return dict(version=1, default_name='默认实例', items=[])
        value = json.loads(self.path.read_text())
        if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('items'), list):
            raise ValueError('实例注册表无效，请检查 data/instances.json。')
        self.name(value.get('default_name'))
        ids, ports = set(), set()
        for item in value['items']:
            if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                    or not ID.fullmatch(item['id']) or item['id'] == 'default' or item['id'] in ids
                    or any(type(item.get(k)) is not int or not 1024 <= item[k] <= 65535
                           or item[k] in ports for k in ('management_port', 'mcp_port'))
                    or item['management_port'] == item['mcp_port']):
                raise ValueError('实例注册表中有重复或无效的 ID、端口。')
            self.name(item.get('name'))
            ids.add(item['id']); ports.update((item['management_port'], item['mcp_port']))
        return value

    def save(self, value):
        atomic_bytes(self.path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode())

    @contextmanager
    def locked(self):
        self.hub_only()
        with file_lock(self.root / '.tmp/instances.lock', blocking=True):
            yield self.read()

    def child_root(self, item):
        instance_id = item['id']
        if not ID.fullmatch(instance_id) or instance_id == 'default':
            raise ValueError('实例 ID 无效。')
        path = self.root / 'instances' / instance_id
        if path.parent.is_symlink() or path.is_symlink():
            raise ValueError('实例目录不能是符号链接。')
        info = metadata(path)
        if not info or info['id'] != instance_id or info['hub_root'] != str(self.root):
            raise ValueError('实例目录的归属不匹配，未操作。')
        return path

    def find(self, value, instance_id):
        for item in value['items']:
            if item['id'] == instance_id:
                return item
        raise ValueError('找不到此实例，请刷新列表。')

    def descriptor(self, item, *, default=False, overview=None):
        root = self.root if default else self.child_root(item)
        result = dict(item, root=str(root), default=default,
                      url=f'http://127.0.0.1:{item["management_port"]}',
                      token_file=str(root / 'data/terminal-admin.token'), running=False,
                      state='stopped', error='', mcp=None)
        try:
            if overview is None:
                from .terminal_launcher import health_ready, service_busy
                client = TerminalClient(result['url'], result['token_file'], timeout=1)
                try:
                    if not health_ready(client):
                        if service_busy(root):
                            raise TerminalError('后台正在启动或停止，请稍后刷新。')
                        return result
                    overview = client.get('/overview')
                finally:
                    client.close()
            if Path(overview.get('storage', '')).resolve() != root / 'data':
                raise TerminalError('此端口属于另一个 Tulpa 实例，未接入。')
            result.update(running=True, state='running', mcp=overview.get('mcp'))
        except TerminalError as exc:
            result.update(state='error', error=str(exc))
        return result

    def listing(self, hub_url, overview):
        value = self.read()
        default = dict(id='default', name=value['default_name'], management_port=urlparse(hub_url).port,
                       mcp_port=overview['mcp']['port'])
        items = [self.descriptor(default, default=True, overview=overview)]
        for item in value['items']:
            try:
                items.append(self.descriptor(item))
            except (ValueError, OSError):
                items.append(dict(item, default=False, running=False, state='error',
                                  error='实例目录或标识无效，请检查本机文件。'))
        return dict(items=items, current_id='default')

    @staticmethod
    def name(value):
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 60 or any(ord(c) < 32 for c in value):
            raise ValueError('实例名称须为 1–60 个字符，且不能含控制字符。')
        return value.strip()

    @staticmethod
    def port(value, used, start):
        if value is not None and (type(value) is not int or not 1024 <= value <= 65535):
            raise ValueError('端口须为 1024–65535 的整数。')
        for port in [value] if value is not None else range(start, 65536):
            if port in used:
                if value is not None:
                    raise ValueError('此端口已分配给另一个实例或服务。')
                continue
            try:
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1', port))
            except OSError:
                if value is not None:
                    raise ValueError('此端口已被占用，请选择其他端口。') from None
                continue
            return port
        raise ValueError('没有可用端口。')

    def copy_source(self, destination):
        """Only called for a new directory or under its stopped launch lock."""
        for source in source_files(self.root):
            relative = release_path(source.relative_to(self.root))
            target = destination / relative
            if any(p.is_symlink() for p in (target, *target.parents) if p != self.root):
                raise ValueError('实例源码路径不能是符号链接。')
            # Persona Markdown is user-editable configuration in the upstream
            # layout. Preserve each account's edits when syncing application code.
            if (relative.parent.as_posix() == 'chatlocal/prompts/mcp_chat' and target.is_file()
                    and target.suffix.lower() == '.md' and target.name.lower() not in ('behavior.md', 'readme.md')):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            # Atomic replacement also prevents a partially written Python module.
            atomic_bytes(target, source.read_bytes())
            target.chmod(0o755 if source.stat().st_mode & 0o111 else 0o644)

    def create(self, body, hub_url, overview):
        if set(body) - {'id', 'name', 'management_port', 'mcp_port'}:
            raise ValueError('实例创建参数无效。')
        instance_id = body.get('id')
        if not isinstance(instance_id, str) or not ID.fullmatch(instance_id) or instance_id == 'default':
            raise ValueError('ID 须以小写字母开头，最多 32 位，只含小写字母、数字和短横线；不能用 default。')
        name = self.name(body.get('name'))
        with self.locked() as value:
            if any(r['id'] == instance_id for r in value['items']):
                raise ValueError('此实例 ID 已存在。')
            base = self.root / 'instances'
            if base.is_symlink():
                raise ValueError('实例目录不能是符号链接。')
            base.mkdir(mode=0o700, exist_ok=True)
            destination = base / instance_id
            if destination.exists() or destination.is_symlink():
                raise ValueError('目标目录已存在，未覆盖。')
            used = {urlparse(hub_url).port, overview['mcp']['port']}
            used.update(p for r in value['items'] for p in (r['management_port'], r['mcp_port']))
            management = self.port(body.get('management_port'), used, 7862)
            mcp = self.port(body.get('mcp_port'), used | {management}, 18778)
            item = dict(id=instance_id, name=name, management_port=management, mcp_port=mcp)
            staging = Path(tempfile.mkdtemp(prefix='.creating-', dir=base))
            try:
                self.copy_source(staging)
                python_env = self.root / '.venv'
                if not (python_env / 'bin/python').is_file():
                    raise ValueError('请先在主实例运行 ./tulpa 准备运行环境。')
                (staging / '.venv').symlink_to(python_env.resolve(), target_is_directory=True)
                for folder in ('data', '.tmp', '.cache', 'imports'):
                    (staging / folder).mkdir(mode=0o700, exist_ok=True)
                atomic_bytes(staging / 'data/instance.json', (json.dumps(dict(item, hub_root=str(self.root), hub_url=hub_url), ensure_ascii=False) + '\n').encode())
                from .terminal_server import ensure_token
                from .store import Store
                from .mcp_access import MCPAccess
                ensure_token(staging / 'data/terminal-admin.token')
                MCPAccess(Store(staging / 'data/chats.sqlite3')).configure(False, mcp)
                staging.rename(destination)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
            value['items'].append(item)
            try:
                self.save(value)
            except BaseException:
                shutil.rmtree(destination)  # This call just created it; it has never run.
                raise
            return self.descriptor(item)

    def rename(self, instance_id, body, hub_url, overview):
        if set(body) != {'name'}:
            raise ValueError('修改实例仅接受 name。')
        name = self.name(body['name'])
        with self.locked() as value:
            if instance_id == 'default':
                value['default_name'] = name
            else:
                item = self.find(value, instance_id)
                root = self.child_root(item)
                child = metadata(root)
                child['name'] = name
                atomic_bytes(root / 'data/instance.json', (json.dumps(child, ensure_ascii=False) + '\n').encode())
                item['name'] = name
            self.save(value)
        return next(r for r in self.listing(hub_url, overview)['items'] if r['id'] == instance_id)

    def action(self, instance_id, action):
        if instance_id == 'default':
            raise ValueError('主实例正在提供管理界面；停止它请在主目录运行 ./tulpa stop。')
        from .terminal_launcher import ensure_backend, stop_backend
        with self.locked() as value:
            item = self.find(value, instance_id)
            root = self.child_root(item)
            selected = self.descriptor(item)
            if selected['state'] == 'error':
                raise ValueError(selected['error'])
            client = TerminalClient(selected['url'], selected['token_file'], timeout=3)
            try:
                if action == 'start':
                    ensure_backend(client.base_url, client.token_file, root=root, timeout=45,
                                   prepare=lambda: self.copy_source(root),
                                   environment=child_environment(self.root), managed_instance=True)
                elif action == 'stop':
                    stop_backend(client, root=root, timeout=35)
                else:
                    raise ValueError('未知实例操作。')
            finally:
                client.close()
            return self.descriptor(item)


def install_instance_routes(app, root, overview):
    from fastapi import Request
    manager = InstanceManager(root)

    @app.get('/api/terminal/instances')
    def instances(request: Request):
        return manager.listing(str(request.base_url).rstrip('/'), overview())

    @app.post('/api/terminal/instances')
    def create(body: dict, request: Request):
        return manager.create(body, str(request.base_url).rstrip('/'), overview())

    @app.put('/api/terminal/instances/{instance_id}')
    def rename(instance_id: str, body: dict, request: Request):
        return manager.rename(instance_id, body, str(request.base_url).rstrip('/'), overview())

    @app.post('/api/terminal/instances/{instance_id}/{action}')
    def action(instance_id: str, action: str, body: dict):
        if body:
            raise ValueError('实例启停不接受额外参数。')
        return manager.action(instance_id, action)
