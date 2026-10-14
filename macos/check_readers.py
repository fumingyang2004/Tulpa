"""Isolated Mac QQ crypto/VFS checks; no personal chats, keys or cloud calls."""
import hashlib
import os
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'scripts'), str(ROOT/'tools/qq-reader')]


def snapshot_discovery():
    from chatlog_keeper import qq_db as q
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as tmp:
        root = Path(tmp)
        for account, layout in [('opaque_snapshot', 'nt_qq/nt_db'), ('opaque_native', 'nt_db')]:
            database = root/account/layout/'nt_msg.db'
            database.parent.mkdir(parents=True)
            database.touch()
        (root/'unrelated').mkdir()
        assert set(q.find_qq_account_databases(root)) == {'opaque_snapshot', 'opaque_native'}
    print('PASS Mac opaque QQ accounts: native and private snapshot layouts, unrelated folders excluded')


def encrypted_view(folder):
    import apsw
    from check_live_runtime import encrypt, sqlite_pages
    from live_vfs import ReadView, configure_qq_worker, SourceBusy
    configure_qq_worker()
    pages = sqlite_pages(folder, 48)
    key = b'fixture-password'
    # Normal Mac SQLite skips page 262145, not the Windows shifted page 262144.
    relocated = {3:262143, 4:262144, 5:262146}
    struct.pack_into('>I', pages[2], 4044, relocated[3])
    struct.pack_into('>I', pages[1], 28, 262146)
    for old in relocated:
        next_page = struct.unpack_from('>I', pages[old])[0]
        struct.pack_into('>I', pages[old], 0, relocated.get(next_page, 0))
    source = folder/'encrypted.db'
    with source.open('wb') as stream:
        stream.truncate(1024+262146*4096)
        for old, page in pages.items():
            number = relocated.get(old, old)
            stream.seek(1024+(number-1)*4096)
            stream.write(encrypt(page, number, key, 'qq'))
    stamp = source.stat().st_mtime_ns
    with ReadView(source, key, 'qq') as view:
        assert list(view.db.execute('SELECT value FROM fixture')) == [('a'*15000,)]
        assert view.view.bytes_read < 65536
        try:view.db.execute("INSERT INTO fixture VALUES('bad')")
        except apsw.ReadOnlyError:pass
        else:raise AssertionError('source write allowed')
    assert source.stat().st_mtime_ns == stamp
    from chatlog_keeper.core._wal import _checksum_bytes
    changed = pages[4][:];changed[4:10] = b'update'
    encrypted = encrypt(changed, 262144, key, 'qq')
    header = struct.pack('>IIIIII', 0x377f0682, 3007000, 4096, 0, 12, 34)
    checksum = _checksum_bytes(header, big_endian=False)
    frame = struct.pack('>II', 262144, 262146)
    end = _checksum_bytes(frame+encrypted, checksum, big_endian=False)
    wal = Path(str(source)+'-wal')
    wal.write_bytes(header+struct.pack('>II', *checksum)+frame+header[16:24]+struct.pack('>II', *end)+encrypted)
    with ReadView(source, key, 'qq') as view:
        assert list(view.db.execute('SELECT value FROM fixture'))[0][0].count('update') == 1
        with source.open('r+b') as stream:
            stream.seek(1024+(262144-1)*4096);stream.write(b'\0'*4096)
        try:view.view.assert_fresh()
        except SourceBusy:pass
        else:raise AssertionError('source race accepted')
    wal.unlink()
    with ReadView(source, key, 'qq') as view:
        try:list(view.db.execute('SELECT value FROM fixture'))
        except SourceBusy:pass
        else:raise AssertionError('Mac zero-page authentication bypassed')
    print('PASS Mac encrypted >1 GiB QQ: overflow, WAL, read-only, source fence, zero-page rejection')


def errors(folder):
    import export_qq
    import chatlog_keeper.macos_key as keys
    args = SimpleNamespace(incremental_request=None, refresh=True, account='fixture',
        output=str(folder/'unused.json'))
    meta = dict(account='fixture', source=str(folder), fingerprint={})
    reader = SimpleNamespace(is_available=lambda:False)
    with patch.object(export_qq, 'snapshot', return_value=meta), \
            patch.object(export_qq, 'DATA', folder), \
            patch.object(export_qq.q, 'QQDBReader', return_value=reader), \
            patch.object(keys, 'last_error', return_value='process_access_denied'):
        try:export_qq.export(args)
        except ValueError as exc:
            assert '文稿文件夹授权' in str(exc) and 'macOS 已阻止' in str(exc)
        else:raise AssertionError('missing Mac permission diagnosis')
    print('PASS Mac key-denial diagnosis; no retry-login advice or message import')


def snapshots(folder):
    from snapshot_cache import refresh_families
    from reader_metrics import COUNTS
    source=folder/'client';target=folder/'snapshot'
    source.mkdir()
    client=source/'contact.db';client.write_bytes(b'fixture-contact'*1024)
    before=hashlib.sha256(client.read_bytes()).hexdigest()
    refresh_families(source,target,[client]);cached=target/client.name
    existing=set((ROOT/'.tmp').glob('snapshot-recovery-*'))
    try:
        cached.chmod(0)
        try:cached.read_bytes()
        except PermissionError:pass
        else:raise AssertionError('Test requires owner read denial')
        COUNTS.clear();refresh_families(source,target,[client])
        assert COUNTS['snapshot_unreadable_rebuilds']==1
        assert cached.read_bytes()==client.read_bytes()
        assert hashlib.sha256(client.read_bytes()).hexdigest()==before
        stamp=cached.stat().st_mtime_ns
        client.chmod(0)
        try:refresh_families(source,target,[client])
        except PermissionError:pass
        else:raise AssertionError('Source permission failure hidden')
        assert cached.stat().st_mtime_ns==stamp
    finally:
        client.chmod(0o600);cached.chmod(0o600)
        import shutil
        for recovery in set((ROOT/'.tmp').glob('snapshot-recovery-*'))-existing:
            for path in recovery.rglob('*'):
                if path.is_file():path.chmod(0o600)
            shutil.rmtree(recovery)
    print('PASS Mac snapshot permission recovery; source unchanged and source denial preserved')


if __name__ == '__main__':
    if sys.platform != 'darwin':raise SystemExit('Run on macOS')
    snapshot_discovery()
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp', prefix='mac-test-') as temp:
        folder = Path(temp)
        encrypted_view(folder)
        errors(folder)
        snapshots(folder)
