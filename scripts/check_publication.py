"""Check public source / release boundaries without printing private values.

This is a publication gate, not a claim that automated scanning finds all PII.
Review documentation and publish only the intended branch and release tag.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOTS = {'.git', '.venv', '.tmp', 'data', 'imports', 'exports', 'reports', 'release', 'build'}
SECRET = re.compile(r'(?:sk-[A-Za-z0-9]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')
PERSONAL_PATH = re.compile(r'[A-Za-z]:[\\/]Users[\\/](?!Public(?:[\\/]|\b)|Default(?:[\\/]|\b))[^\\/\s<>]+', re.I)
TEXT_EXT = {'.py', '.js', '.cjs', '.css', '.html', '.md', '.txt', '.json', '.toml', '.yml', '.yaml', '.cs', '.ps1', '.example', '.config'}


def allowed_path(name, *, archive=False):
    p = PurePosixPath(name)
    if not name or '\\' in name or ':' in name or p.is_absolute() or '..' in p.parts:
        return False
    if p.parts[0] in PRIVATE_ROOTS:
        return False
    if p.name == '.env' or (p.name.startswith('.env.') and p.name != '.env.example'):
        return False
    if p.suffix.lower() in {'.db', '.sqlite', '.sqlite3', '.log', '.dmp', '.bundle', '.pem', '.key', '.bak'}:
        return False
    if archive:
        if 'snowluma' in p.parts or any(part.lower().startswith('snowluma-') for part in p.parts):
            return False
        if p.name.endswith('.QA.exe') or p.name == 'direct_url.json':
            return False
        if p.parts[0] == '.cache' and (len(p.parts) < 3 or p.parts[1] not in {'rapidocr', 'voice-models'}):
            return False
    elif p.parts[0] in {'.cache', 'tools', 'node_modules'} or p.suffix.lower() in {'.zip', '.7z', '.exe', '.dll'}:
        return False
    return True


def check_text(name, data):
    if PurePosixPath(name).suffix not in TEXT_EXT and name not in {'LICENSE', '.gitignore'}:
        return []
    try:
        text = data.decode('utf-8-sig')
    except UnicodeError:
        return []
    result = []
    for label, pattern in [('credential', SECRET), ('personal path', PERSONAL_PATH)]:
        if pattern.search(text):
            result.append(f'{label}: {name}')
    return result


def source_check():
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode('utf-8').split('\0')
    errors = []
    count = 0
    for name in filter(None, names):
        file = ROOT / name
        if not file.is_file():  # Worktree deletions are allowed before staging.
            continue
        count += 1
        if not allowed_path(name):
            errors.append('private source path: ' + name)
        if file.is_symlink() or file.is_junction():
            errors.append('linked source: ' + name)
        errors.extend(check_text(name, file.read_bytes()))
    for name in ('README.md', 'LICENSE', 'THIRD_PARTY.md', 'SECURITY.md'):
        if not (ROOT / name).is_file():
            errors.append('missing: ' + name)
    # Document links to source files should work in the public repository.
    for doc in ROOT.glob('*.md'):
        for link in re.findall(r'\]\(([^)]+)\)', doc.read_text('utf-8')):
            if '://' in link or link.startswith(('#', 'mailto:')):
                continue
            target = link.split('#', 1)[0]
            if target and not (doc.parent / target).exists():
                errors.append(f'broken local link: {doc.name} -> {target}')
    if errors:
        raise RuntimeError('\n'.join(errors))
    return dict(source_files=count, source_scan='passed')


def archive_check(path):
    errors = []
    with zipfile.ZipFile(path) as z:
        names = [info.filename for info in z.infolist() if not info.is_dir()]
        if len(names) != len(set(names)) or any(not n.startswith('Tulpa/') for n in names):
            raise RuntimeError('Unexpected archive root or duplicate entries')
        manifest = json.loads(z.read('Tulpa/build-manifest.json'))
        expected = {'Tulpa/' + name for name in manifest['files']} | {'Tulpa/build-manifest.json'}
        if set(names) != expected:
            errors.append('archive does not match build allowlist')
        if manifest.get('product') != 'Tulpa' or manifest.get('working_tree_overlay'):
            errors.append('unexpected product / working-tree overlay')
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        if manifest.get('checkpoint') != head:
            errors.append('archive was not built from current source commit')
        for required in ('Tulpa.exe', 'Tulpa.Support.exe', 'LICENSE', 'THIRD_PARTY.md', 'web/tulpa-logo.png',
                         'tools/qq-reader/LICENSE', 'tools/wechat-reader/LICENSE'):
            if 'Tulpa/' + required not in expected:
                errors.append('missing package file: ' + required)
        for name in names:
            rel = name.removeprefix('Tulpa/')
            if not allowed_path(rel, archive=True):
                errors.append('private archive path: ' + rel)
            # Audit our files; third-party distributions retain their original
            # test fixtures, licenses and source URLs (not our personal data).
            if not rel.startswith(('runtime/', '_internal/', 'tools/')):
                if PurePosixPath(rel).suffix in TEXT_EXT or rel == 'LICENSE':
                    errors.extend(check_text(rel, z.read(name)))
        corrupt = z.testzip()
        if corrupt:
            errors.append('archive CRC failure: ' + corrupt)
    if errors:
        raise RuntimeError('\n'.join(errors))
    with path.open('rb') as stream:
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    return dict(archive=path.name, version=manifest['version'], commit=head, files=len(names),
                bytes=path.stat().st_size, sha256=sha, archive_scan='passed', crc='passed')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--archive', type=Path)
    args = p.parse_args()
    result = source_check()
    if args.archive:
        result.update(archive_check(args.archive))
    print(json.dumps(result, ensure_ascii=False, indent=2))
