"""Shared file policy for Linux source releases and ``tulpa package``.

Choose Linux documentation explicitly: recursively shipping doc/ reintroduces
upstream desktop installation guides, even after fixing the main README.
"""
import os
from pathlib import Path, PurePosixPath


QUICKSTART_NAMES = frozenset({'TESTING.txt', '看我.txt'})
ROOT_FILES = frozenset({
    '.env.example', '.gitattributes', '.gitignore', 'LICENSE', 'README.md', 'README.linux.md',
    *QUICKSTART_NAMES, 'setup-linux.sh', 'tulpa',
})
DOCUMENTS = frozenset({
    'MCP_CHAT_BRIDGE.md', 'LINUX_AGENT_SETUP.md', 'LINUX_CONTRIBUTING.md', 'LINUX_CONTRIBUTORS.md', 'DOCKER_ACCEPTANCE.md',
    'DOCKER_MANAGED_PLAN.md', 'LINUX_ACCEPTANCE.md', 'LINUX_TERMINAL.md',
    'SECURITY.md', 'TERMINAL_API.md', 'LINUX_THIRD_PARTY.md', 'LINUX_UPGRADE.md', 'SL_TOOLS.md',
    'assets/title.png',
})
REQUIREMENTS = frozenset({
    'linux.txt', 'linux-full.txt', 'linux-readers.txt', 'linux-ocr.txt',
    'linux-opencv.txt', 'linux-excludes.txt', 'linux-build.txt',
    'mcp.txt', 'tui.txt', 'artifacts.txt', 'voice.txt',
})
LICENSES = frozenset({
    'qq-bridge-MIT.txt', 'whisper-cpp-MIT.txt', 'whisper-model-MIT.txt',
    'PaddleOCR-Apache-2.0.txt',
})
# These helpers build/repair the upstream desktop release or require Gradio.
EXCLUDED_SCRIPTS = frozenset({
    'check_agent_ui.py', 'check_ui.py', 'check_development_readers.py',
    'check_reader_runtime.py', 'check_publication.py', 'check_upgrade.py',
    'check_qq_source_fix.py', 'fix_qq_source.py', 'package_qq_source_fix.py',
    'prepare_reader_runtime.py', 'upgrade_installation.py',
})
SOURCE_SUFFIXES = {
    'chatlocal': {'.py', '.md', '.json', '.txt', '.html', '.css', '.tcss', '.js'},
    'web': {'.html', '.css', '.js', '.png', '.svg'},
    'harness': {'.mjs'},
    'scripts': {'.py', '.mjs'},
}
PRIVATE_PARTS = frozenset({
    '__pycache__', 'node_modules', 'data', 'imports', 'exports', 'tools', 'reports',
})


def included(relative):
    """Whether a relative POSIX member name belongs in a Linux source package."""
    rel = PurePosixPath(relative)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts:
        return False
    if rel.as_posix() in ROOT_FILES:
        return True
    if any(part.startswith('.') or part in PRIVATE_PARTS for part in rel.parts):
        return False
    folder, *rest = rel.parts
    tail = '/'.join(rest)
    if folder == 'doc':
        return tail in DOCUMENTS
    if folder == 'requirements':
        return tail in REQUIREMENTS
    if folder == 'licenses':
        return tail in LICENSES
    # This shared diagnostic endpoint is also mounted by the Linux backend.
    if folder == 'desktop':
        return tail == 'diagnose.py'
    if folder == 'scripts' and (len(rest) != 1 or tail in EXCLUDED_SCRIPTS):
        return False
    return rel.suffix in SOURCE_SUFFIXES.get(folder, set())


def source_files(root):
    """Enumerate public source without Git, downloads, or following symlinks."""
    root = Path(root).resolve()
    chosen = []
    linux_readme = root / 'README.linux.md'
    prefer_linux_readme = linux_readme.is_file() and not linux_readme.is_symlink()
    for name in ROOT_FILES:
        if name == 'README.md' and prefer_linux_readme:
            continue
        path = root / name
        if path.is_file() and not path.is_symlink():
            chosen.append(path)
    for folder in (*SOURCE_SUFFIXES, 'doc', 'requirements', 'licenses', 'desktop'):
        base = root / folder
        if not base.is_dir() or base.is_symlink():
            continue
        for directory, dirs, files in os.walk(base, followlinks=False):
            current = Path(directory)
            dirs[:] = sorted(name for name in dirs if not name.startswith('.')
                             and name not in PRIVATE_PARTS and not (current / name).is_symlink())
            for name in files:
                path = current / name
                if included(path.relative_to(root).as_posix()) and path.is_file() and not path.is_symlink():
                    chosen.append(path)
    return sorted(chosen)


def release_path(relative):
    """Keep the shared repository README out of Linux packages and instances.

    Extracted packages have only README.md and can be repacked unchanged.
    """
    path = PurePosixPath(relative)
    return PurePosixPath('README.md') if path.as_posix() == 'README.linux.md' else path
