"""Upgrade real SQLite/WAL fixtures; no personal installation or credentials."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import upgrade_installation as u


def package(root, version, *, extra=False):
    root.mkdir()
    (root / 'chatlocal').mkdir()
    (root / 'Tulpa.exe').write_bytes(b'program-' + version.encode())
    (root / 'chatlocal/version.py').write_text(version)
    (root / 'LICENSE').write_text('license')
    if extra:
        (root / 'chatlocal/retired.py').write_text('old only')
    names = ['Tulpa.exe', 'chatlocal/version.py', 'LICENSE'] + (['chatlocal/retired.py'] if extra else [])
    data = dict(product='Tulpa', version=version, edition='mcp', files=names,
                file_sha256={n: u.digest(root / n) for n in names})
    (root / 'build-manifest.json').write_text(json.dumps(data))


def fails(fn):
    try:
        fn()
    except (u.UpgradeError, OSError, sqlite3.Error):
        return
    raise AssertionError('Expected rejection')


def main():
    with tempfile.TemporaryDirectory(prefix='tulpa-upgrade-') as folder:
        base = Path(folder)
        new, old = base / '新版本', base / '现有版本'
        package(new, '0.5.1')
        package(old, '0.5.0', extra=True)
        for directory in ('data/media', 'data/live', 'imports', 'reports/private', '.cache/voice-models'):
            (old / directory).mkdir(parents=True, exist_ok=True)
            (old / directory / 'keep.bin').write_bytes(b'user-owned')
        (old / '.env').write_text('API_KEY=local-fixture\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:3001\n')
        db = sqlite3.connect(old / 'data/chats.sqlite3')
        db.executescript("PRAGMA journal_mode=WAL; CREATE TABLE messages(id INTEGER PRIMARY KEY, content TEXT); INSERT INTO messages VALUES(7,'kept'); CREATE TABLE local_change_meta(epoch TEXT); INSERT INTO local_change_meta VALUES('stable'); CREATE TABLE live_state(checkpoint TEXT); INSERT INTO live_state VALUES('cursor-99');")
        db.commit()
        # Keep a WAL reader open; the migration must preserve the main DB + WAL pair.
        mcp = sqlite3.connect(old / 'data/mcp-access.sqlite3')
        mcp.executescript("CREATE TABLE grants(token_hash TEXT, scope TEXT); INSERT INTO grants VALUES('same-token-hash','unchanged-permissions'); CREATE TABLE settings(port INTEGER, enabled INTEGER); INSERT INTO settings VALUES(18777,1);")
        mcp.close()
        state = u.state_files(old)
        quiet = lambda *args, **kwargs: None
        with patch.object(u, 'running_in', return_value=[]):
            plan = u.upgrade(new, old, progress=quiet)
            assert not plan['applied'] and plan['preserved']['chats.sqlite3']['messages'] == 1
            assert not (old / '.tmp').exists(), 'Check-only created state'
            with patch.object(u, 'running_in', return_value=[123]):
                fails(lambda: u.upgrade(new, old, apply=True, progress=quiet))
            db.execute('BEGIN IMMEDIATE')
            fails(lambda: u.upgrade(new, old, apply=True, progress=quiet))
            db.rollback()
            assert not (old / '.tmp/upgrade.lock').exists()
            # Mid-update IO failure restores already overwritten program files.
            original_copy = u.shutil.copy2
            tripped = False
            def flaky(src, dst, *a, **kw):
                nonlocal tripped
                if Path(src) == new / 'chatlocal/version.py' and not tripped:
                    tripped = True
                    raise OSError('fixture write failure')
                return original_copy(src, dst, *a, **kw)
            with patch.object(u.shutil, 'copy2', side_effect=flaky):
                fails(lambda: u.upgrade(new, old, apply=True, progress=quiet))
            assert (old / 'Tulpa.exe').read_bytes() == b'program-0.5.0'
            assert (old / 'chatlocal/retired.py').is_file()
            assert u.state_files(old) == state
            result = u.upgrade(new, old, apply=True, progress=quiet)
            assert result['applied'] and result['preserved_state_verified']
            assert u.state_files(old) == state
            assert not (old / 'chatlocal/retired.py').exists()
            assert (Path(result['backup']) / 'program/chatlocal/retired.py').is_file()
            assert (Path(result['backup']) / 'userdata/data/chats.sqlite3-wal').exists()
            assert (old / 'Tulpa.exe').read_bytes() == b'program-0.5.1'
            assert u.upgrade(new, old, apply=True, progress=quiet)['already_current']
            for directory in ('data/media', 'data/live', 'imports', 'reports/private', '.cache/voice-models'):
                assert (old / directory / 'keep.bin').read_bytes() == b'user-owned'
            fails(lambda: u.upgrade(new, new, progress=quiet))
            data = json.loads((new / 'build-manifest.json').read_text())
            for field, value in [('edition', 'full'), ('version', '0.4.0'), ('files', data['files'] + ['data/chats.sqlite3'])]:
                (new / 'build-manifest.json').write_text(json.dumps(data | {field: value}))
                fails(lambda: u.upgrade(new, old, progress=quiet))
            (new / 'build-manifest.json').write_text(json.dumps(data))
            (new / 'Tulpa.exe').write_bytes(b'corrupted download')
            fails(lambda: u.upgrade(new, old, progress=quiet))
            for unsafe in ('../escape', 'C:/escape', 'data/../../escape', 'data\\bad'):
                fails(lambda: u.child(old, unsafe))
        db.close()
        # A clean shutdown leaves no WAL; taking the write lock may create an
        # empty WAL and must not be mistaken for changed user data.
        (new / 'Tulpa.exe').write_bytes(b'program-0.5.1')
        (old / 'Tulpa.exe').write_bytes(b'old program')
        with patch.object(u, 'running_in', return_value=[]):
            assert u.upgrade(new, old, apply=True, progress=quiet)['preserved_state_verified']
        legacy = base / 'legacy'
        package(legacy, '0.4.0')
        legacy_data = json.loads((legacy / 'build-manifest.json').read_text())
        legacy_data.pop('edition')
        (legacy / 'build-manifest.json').write_text(json.dumps(legacy_data))
        (legacy / 'app.py').write_text('# old full edition')
        assert u.manifest(legacy)[0]['edition'] == 'full'
        # Real Windows inventory recognizes a Python app from this installation.
        script = old / 'app.py'
        script.write_text('import time; time.sleep(30)')
        proc = subprocess.Popen([sys.executable, str(script)])
        try:
            assert proc.pid in u.running_in((old,)), 'Failed to recognize running source app'
        finally:
            proc.terminate()
            proc.wait(timeout=5)
    print('PASS upgrade: dry run, WAL/epoch/token/cursor preservation, user files untouched, backup, failed-copy rollback, writer lock, real process detection, same-version no-op, editions/downgrades/hashes/path checks.')


if __name__ == '__main__':
    main()
