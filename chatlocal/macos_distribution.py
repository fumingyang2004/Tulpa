"""Public-source allowlist for Mac packages and independent instances."""
from pathlib import Path, PurePosixPath
from . import linux_distribution as base

ROOT_FILES = {'requirements.txt', 'setup-macos.sh', 'start-macos.sh', 'README.macos.md', 'package.json', 'package-lock.json'}
MAC_SUFFIXES = {'.py', '.md', '.json', '.png', '.icns', '.cjs', '.sh'}


def source_files(root):
    root = Path(root).resolve()
    exclude = {'README.linux.md', 'TESTING.txt', '看我.txt'}
    if (root/'README.macos.md').is_file():
        exclude.add('README.md')
    chosen = {p for p in base.source_files(root)
              if p.relative_to(root).as_posix() not in exclude}
    for name in ROOT_FILES | {'requirements/macos.txt', 'requirements/readers.txt', 'script/build_and_run.sh', 'doc/FRONTEND.md'}:
        path = root/name
        if path.is_file() and not any(p.is_symlink() for p in (path, *path.parents) if p != root):
            chosen.add(path)
    folder = root/'macos'
    if not folder.is_symlink():
        for path in folder.rglob('*'):
            rel = path.relative_to(folder)
            if (path.is_file() and path.suffix in MAC_SUFFIXES
                    and not any(p.startswith('.') or p in base.PRIVATE_PARTS for p in rel.parts)
                    and not any(p.is_symlink() for p in (path, *path.parents) if p != root)):
                chosen.add(path)
    tests = root/'tests/frontend'
    if not tests.is_symlink():
        for path in tests.glob('*'):
            if path.is_file() and not path.is_symlink() and path.suffix in {'.md', '.mjs', '.cjs'}:
                chosen.add(path)
    return sorted(chosen)


def release_path(relative):
    path = PurePosixPath(relative)
    return PurePosixPath('README.md') if path.as_posix() == 'README.macos.md' else path
