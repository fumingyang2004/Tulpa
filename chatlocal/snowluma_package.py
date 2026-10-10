"""Pinned official package acquisition. Never executes code; no latest fallback."""
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import time
from urllib.parse import urlparse
import zipfile

import httpx

from .snowluma_secure import ManagedError, atomic_json, local_path, read_json, sha256


OFFICIAL_HOSTS = frozenset(('github.com', 'release-assets.githubusercontent.com',
                          'objects.githubusercontent.com', 'github-releases.githubusercontent.com'))


def check_url(url):
    p = urlparse(url)
    if (p.scheme != 'https' or p.hostname not in OFFICIAL_HOSTS or p.username or p.password
            or p.port not in (None, 443) or p.fragment):
        raise ManagedError('source_rejected', '下载地址不是已审核的官方 HTTPS 源，未下载。')


class PackageInstaller:
    def __init__(self, manifest, directory, *, client_factory=httpx.Client):
        self.manifest, self.directory, self.client_factory = manifest, local_path(directory), client_factory

    def download(self, check, progress):
        package = self.manifest['package']
        cache = local_path(self.directory / 'downloads')
        cache.mkdir(parents=True, exist_ok=True)
        final = cache / (package['sha256'] + '.zip')
        if final.exists():
            check()
            if final.stat().st_size != package['size'] or sha256(final) != package['sha256']:
                final.rename(local_path(final.with_name(final.name+'.rejected-'+secrets.token_hex(6))))
                raise ManagedError('cache_hash_mismatch', '下载缓存校验失败，已隔离且未执行；点击重试将重新下载。', retryable=True)
            progress('download_verified', package['size'], package['size'])
            return final
        partial = local_path(final.with_suffix('.partial'))
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > package['size']:
            partial.rename(local_path(partial.with_name(partial.name+'.rejected-'+secrets.token_hex(6))))
            raise ManagedError('cache_hash_mismatch', '残留下载大小不符，已隔离；可重试重新下载。', retryable=True)
        if offset == package['size']:
            check()
            if sha256(partial) != package['sha256']:
                partial.rename(local_path(partial.with_name(partial.name+'.rejected-'+secrets.token_hex(6))))
                raise ManagedError('package_hash_mismatch', '完整残留下载校验失败，已隔离且未执行；可重试重新下载。', retryable=True)
            os.replace(partial, final)
            return final
        deadline, url = time.monotonic() + 600, package['url']
        check_url(url)
        try:
            with self.client_factory(timeout=httpx.Timeout(8, connect=5, read=3), follow_redirects=False) as client:
                for _ in range(6):
                    check()
                    check_url(url)
                    with client.stream('GET', url, headers={'Range': f'bytes={offset}-',
                            'User-Agent':'Tulpa-managed-setup', 'Accept-Encoding':'identity'}) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            url = str(response.url.join(response.headers.get('location', '')))
                            continue
                        response.raise_for_status()
                        if response.status_code == 206:
                            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('content-range', ''))
                            if not match or int(match[1]) != offset or int(match[3]) != package['size']:
                                raise ManagedError('range_invalid', '下载续传范围不一致，未拼接文件。')
                        elif response.status_code == 200:
                            offset = 0  # Server ignored Range; replace only this owned partial.
                        else:
                            raise ManagedError('download_protocol', '下载服务器返回了不支持的状态。')
                        with partial.open('ab' if offset else 'wb') as f:
                            for chunk in response.iter_bytes(128 * 1024):
                                check()
                                if time.monotonic() > deadline:
                                    raise ManagedError('download_timeout', '下载超时，可安全续传重试。', retryable=True)
                                offset += len(chunk)
                                if offset > package['size']:
                                    raise ManagedError('package_size', '下载文件超过已审核大小，已停止。')
                                f.write(chunk)
                                progress('downloading', offset, package['size'])
                            f.flush(); os.fsync(f.fileno())
                        break
                else:
                    raise ManagedError('download_redirect', '官方地址重定向次数过多，未继续。')
        except httpx.HTTPError:
            raise ManagedError('download_network', '官方运行包下载未完成，请检查网络后重试；已保留可校验的续传文件。', retryable=True) from None
        check()
        if offset != package['size']:
            raise ManagedError('download_incomplete', '下载尚未完整，可重试续传。', retryable=True)
        if sha256(partial) != package['sha256']:
            partial.rename(local_path(partial.with_name(partial.name+'.rejected-'+secrets.token_hex(6))))
            raise ManagedError('package_hash_mismatch', '官方运行包 SHA256 不匹配，已隔离且未执行；可重试重新下载。', retryable=True)
        check()
        os.replace(partial, final)
        progress('download_verified', offset, offset)
        return final

    def extract(self, archive, destination, check):
        destination = local_path(destination)
        if destination.exists():
            raise ManagedError('destination_exists', '解压目标已存在，未覆盖现有运行包。')
        # A verified package is mandatory even when supplied from local cache.
        if sha256(archive) != self.manifest['package']['sha256']:
            raise ManagedError('package_hash_mismatch', '解压前校验失败，未写入运行目录。')
        destination.mkdir(parents=True)
        try:
            with zipfile.ZipFile(archive) as z:
                infos = z.infolist()
                if len(infos) > 30000 or sum(i.file_size for i in infos) > 1024*1024*1024:
                    raise ManagedError('archive_limits', '运行包解压大小或文件数超出限制。')
                seen = set()
                for info in infos:
                    name = info.orig_filename
                    parts = PurePosixPath(name).parts
                    if (not parts or name.startswith(('/', '\\')) or '\\' in name or ':' in name
                            or any(p in ('.', '..') or p.rstrip(' .') != p or any(ord(c)<32 for c in p)
                                   or re.fullmatch(r'(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?', p) for p in parts)
                            or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1
                            or name.rstrip('/').casefold() in seen):
                        raise ManagedError('archive_path', '运行包包含不安全或重复路径，未部署。')
                    seen.add(name.rstrip('/').casefold())
                    target = local_path(destination.joinpath(*parts))
                    if not target.is_relative_to(destination):
                        raise ManagedError('archive_path', '运行包路径越界，未部署。')
                for info in infos:
                    check()
                    target = local_path(destination.joinpath(*PurePosixPath(info.orig_filename).parts))
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as src, target.open('xb') as dst:
                        while chunk := src.read(128 * 1024):
                            check(); dst.write(chunk)
        except (zipfile.BadZipFile, OSError, RuntimeError):
            raise ManagedError('archive_damaged', '运行包解压失败，半成品不会启动；重试会使用新临时目录。') from None
        # Support either flat official packages or one clearly identified wrapper.
        candidates = [destination] + [p for p in destination.iterdir() if p.is_dir()]
        roots = [p for p in candidates if (p/'package.json').is_file() and (p/'index.mjs').is_file()]
        if len(roots) != 1:
            raise ManagedError('package_layout', '官方运行包布局不符合已审核版本，未执行。')
        root = roots[0]
        self.validate(root)
        check()
        return root

    def validate(self, root):
        root = local_path(root)
        package = read_json(root/'package.json')
        if package.get('name') != '@snowluma/runtime' or package.get('version') != self.manifest['version']:
            raise ManagedError('version_mismatch', '运行包实际版本与固定清单不同，未执行。')
        for name in self.manifest['package']['required']:
            if not local_path(root/name).is_file():
                raise ManagedError('package_incomplete', '官方完整包缺少必要运行文件，未执行。')
        for doc in self.manifest['agreements']:
            if sha256(root/doc['file']) != doc['sha256']:
                raise ManagedError('terms_changed', '运行包协议内容已变化，需要重新审核并征询同意。')
        with (root/'node.exe').open('rb') as stream:
            if stream.read(2) != b'MZ':
                raise ManagedError('runtime_invalid', '官方 Node.js 运行时格式不正确。')
        if not any(root.rglob('*.node')):
            raise ManagedError('package_incomplete', '运行包缺少原生组件。')

    def deploy(self, check, progress):
        install = self.directory / 'runtime'
        receipt = read_json(self.directory/'package-receipt.json')
        if install.exists():
            if receipt.get('package_sha256') != self.manifest['package']['sha256'] or not receipt.get('files'):
                raise ManagedError('ownership_unknown', '现有目录没有匹配的托管清单，未覆盖或运行。')
            for item in receipt['files']:
                check()
                rel = PurePosixPath(item['path'])
                if (rel.is_absolute() or '..' in rel.parts or ':' in item['path'] or '\\' in item['path']
                        or not local_path(install/rel).is_relative_to(local_path(install))):
                    raise ManagedError('state_damaged', '安装清单路径无效。')
                path = local_path(install/rel)
                if not path.is_file() or sha256(path) != item['sha256']:
                    raise ManagedError('installed_hash_mismatch', '已安装运行文件发生变化，未启动；可恢复已验证备份。')
            self.validate(install)
            return install
        archive = self.download(check, progress)
        stage = self.directory / ('stage-' + secrets.token_hex(8))
        progress('extracting', 0, 0)
        root = self.extract(archive, stage, check)
        files = [dict(path=p.relative_to(root).as_posix(), sha256=sha256(p))
                 for p in root.rglob('*') if p.is_file() and p.relative_to(root).parts[0] not in ('config','data','logs')]
        check()
        # Receipt is durable before the atomic directory rename, so interrupted
        # publication can resume without treating an unverified folder as runnable.
        atomic_json(self.directory/'package-receipt.json', dict(version=self.manifest['version'],
            package_sha256=self.manifest['package']['sha256'], source=self.manifest['package']['url'], files=files))
        check()
        root.rename(install)
        return install
