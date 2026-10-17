"""Check the declared terminal dependencies without importing optional packages."""
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def required_packages(root=ROOT):
    packages, visited = {}, set()

    def read(path):
        path = path.resolve()
        if path in visited:
            return
        visited.add(path)
        for raw in path.read_text().splitlines():
            line = raw.split('#', 1)[0].strip()
            if not line:
                continue
            if line.startswith('-r '):
                read(path.parent / line[3:].strip())
                continue
            name, separator, pinned = line.partition('==')
            if not separator or not name or not pinned:
                raise ValueError('终端依赖清单格式无效，请检查 requirements。')
            packages[name] = pinned

    for name in (('macos.txt',) if sys.platform == 'darwin' else ('linux.txt', 'tui.txt')):
        read(Path(root) / 'requirements' / name)
    return packages


def missing_packages(root=ROOT):
    missing = []
    for name, pinned in required_packages(root).items():
        try:
            installed = version(name)
        except PackageNotFoundError:
            installed = None
        if installed != pinned:
            missing.append(name)
    return missing


def prepare_environment(root=ROOT):
    if sys.version_info[:2] != (3, 13):
        raise ValueError('终端版需要 Python 3.13；请使用 uv 或 Python 3.13 创建项目 .venv。')
    if not missing_packages(root):
        return
    print('正在准备 Tulpa 运行环境，首次安装需要联网……', file=sys.stderr, flush=True)
    script = 'setup-macos.sh' if sys.platform == 'darwin' else 'setup-linux.sh'
    result = subprocess.run(['sh', str(Path(root) / script)], cwd=root,
                            stdout=sys.stderr, stderr=sys.stderr)
    if result.returncode or missing_packages(root):
        raise ValueError('依赖安装未完成，请检查上方错误后重新运行 ./tulpa。')
