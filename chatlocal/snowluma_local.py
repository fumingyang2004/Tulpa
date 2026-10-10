"""Configure an explicitly selected local runtime in place; no distribution.

Inspection never executes files or writes to the selected folder. Only a fresh
official-format installation, or one already owned by this Tulpa, is writable.
Existing independent installations use the read-only OneBot import workflow.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil

from .snowluma_secure import (ManagedError, atomic_json, file_lock, local_path,
                             private_directory, read_json, sha256)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class LocalInstallation:
    def __init__(self, manifest, directory):
        self.manifest, self.directory = manifest, directory

    def _path(self, folder):
        if (not isinstance(folder, str) or not folder.strip() or len(folder) > 2048
                or not Path(folder).is_absolute() or any(ord(c) < 32 for c in folder)):
            raise ManagedError('folder_required', '请选择已下载并解压的 SnowLuma 本机文件夹。')
        root = local_path(folder.strip())
        if not root.is_dir():
            raise ManagedError('folder_missing', '文件夹不存在，请先解压下载的 SnowLuma。')
        return root

    def inspect(self, folder):
        root = self._path(folder)
        package = read_json(root/'package.json')
        if package.get('name') != '@snowluma/runtime' or not (root/'index.mjs').is_file():
            raise ManagedError('not_snowluma', '请选择包含 index.mjs、package.json 的 SnowLuma 文件夹。')
        version = package.get('version')
        if version not in self.manifest['compatibility']['existing_import']:
            raise ManagedError('unsupported_version', '此 SnowLuma 版本尚未适配，请使用已支持版本或已有 OneBot 高级连接。')
        config = local_path(root/'config')
        marker = read_json(config/'tulpa-owner.json')
        own = marker.get('tulpa_root') == str(self.directory.parent.parent)
        if marker and not own:
            raise ManagedError('owned_elsewhere', '这个 SnowLuma 已由另一份 Tulpa 管理；请使用另一文件夹，避免互相改写。')
        # Do not read/reset an independent admin password or take over its process.
        external = not own and ((config/'webui.json').exists() or any(config.glob('onebot_*.json')))
        if not external and not own:
            global_config = read_json(config/'onebot.json')
            networks = global_config.get('networks', global_config)
            if not isinstance(networks,dict):
                raise ManagedError('existing_config', '已有配置格式异常，未自动修改。')
            external = any(networks.get(k) for k in ('httpServers','wsServers','httpClients','wsClients'))
        if external:
            return dict(folder=str(root), version=version, mode='existing',
                        fingerprint=fingerprint([str(root),version,'existing']),
                        message='检测到已有配置。确认后自动核对并导入现有连接，不改密码、不重启服务。')
        if version != self.manifest['version']:
            raise ManagedError('unsupported_version', f'自动初始化支持 SnowLuma {self.manifest["version"]} 完整版；这个旧版本可通过已有连接导入。')
        for name in self.manifest['required_files']:
            if not local_path(root/name).is_file():
                raise ManagedError('package_incomplete', '缺少运行文件，请下载并完整解压 Windows x64 完整版（内置 Node.js），无需另装运行环境。')
        for doc in self.manifest['agreements']:
            if sha256(root/doc['file']) != doc['sha256']:
                raise ManagedError('terms_changed', '所选包的协议内容与已适配版本不一致，尚未执行；请核对官方版本。')
        with (root/'node.exe').open('rb') as stream:
            if stream.read(2) != b'MZ':
                raise ManagedError('runtime_invalid', '所选 Node.js 不是预期的 Windows 运行文件。')
        files = {}
        total = 0
        for base, directories, names in os.walk(root, followlinks=False):
            parent = Path(base)
            # Only the runtime tree. User data/configuration is not fingerprinted
            # into the public consent value or treated as executable resources.
            if parent == root:
                directories[:] = [d for d in directories if d.lower() not in ('config','data','logs','.git')]
            for name in directories:
                local_path(parent/name)
            for name in names:
                path = local_path(parent/name)
                rel = path.relative_to(root).as_posix()
                size = path.stat().st_size
                total += size
                if len(files) >= 10000 or total > 768*1024*1024:
                    raise ManagedError('package_too_large', '文件夹内容超出运行包检查范围，请选择单独解压的官方 SnowLuma 文件夹。')
                files[rel] = sha256(path)
        if not any(name.endswith('.node') for name in files):
            raise ManagedError('package_incomplete', '运行包缺少原生组件，请重新完整解压。')
        value = dict(folder=str(root), version=version, mode='local', files=files)
        if len(json.dumps(value).encode())>512*1024:
            raise ManagedError('package_too_large', '运行包清单过大，请选择单独解压的官方文件夹。')
        value['fingerprint'] = fingerprint(value)
        value['message'] = '文件夹已就绪。同意后将在此目录自动初始化、配置并连接 QQ；不下载或复制 SnowLuma。'
        return value

    def verify(self, receipt):
        current = self.inspect(receipt.get('folder'))
        if current['mode'] != 'local' or current['fingerprint'] != receipt.get('fingerprint'):
            raise ManagedError('installation_changed', '所选运行文件或归属已变化，请重新检测文件夹并确认；未运行修改后的文件。')
        return self._path(current['folder'])

    @contextmanager
    def acquire(self, receipt, instance, check):
        root = self.verify(receipt)
        check()
        config = local_path(root/'config')
        config.mkdir(exist_ok=True)
        # One lock at the selected installation protects different Tulpa roots.
        with file_lock(config/'tulpa-connection.lock', blocking=False):
            check()
            marker_path = config/'tulpa-owner.json'
            marker = read_json(marker_path)
            owner = dict(tulpa_root=str(self.directory.parent.parent), instance=instance)
            if marker and any(marker.get(k) != v for k,v in owner.items()):
                raise ManagedError('owned_elsewhere', '文件夹已被另一连接占用，未修改配置。')
            if not marker:
                # Recheck after locking: another process could have initialized
                # it since inspection. No implicit admin-password reset.
                if self.inspect(str(root))['mode'] != 'local':
                    raise ManagedError('installation_changed', '文件夹已被其他服务初始化，请改用已有连接导入。')
                backup = self.directory/'local-config-backup'/instance
                private_directory(backup)
                hashes = {}
                for path in config.iterdir():
                    if path.name == 'tulpa-connection.lock':
                        continue
                    path = local_path(path)
                    if not path.is_file() or path.stat().st_size > 2*1024*1024:
                        raise ManagedError('existing_config', '配置目录含未知内容，未覆盖；请使用单独解压的运行包。')
                    shutil.copy2(path, backup/path.name)
                    hashes[path.name] = sha256(path)
                    if sha256(backup/path.name) != hashes[path.name]:
                        raise ManagedError('backup_failed', '配置备份校验失败，未自动配置。')
                atomic_json(backup/'backup-manifest.json', dict(source=str(config), files=hashes))
                check()
                private_directory(config)
                atomic_json(marker_path, dict(owner, version=self.manifest['version']))
            yield root
