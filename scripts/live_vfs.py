"""Read-only, authenticated page view of client databases.

Only WAL/SHM are copied into our workspace. Main pages are read on demand.
The existing chatlog-keeper WAL validator supplies the commit boundary.
An optimistic source fence rejects concurrent main/WAL replacement or writes.
No SQLite connection, lock, journal or shared-memory mapping touches a client.
"""
import hashlib
import hmac
import os
import shutil
import struct
import sys
import tempfile
import uuid
from collections import OrderedDict
from pathlib import Path

import apsw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools/qq-reader'))
sys.path.insert(0, str(ROOT / 'tools/wechat-reader'))
_qq_worker = False


def configure_qq_worker():
    """Call once in the dedicated QQ worker, before opening any client views."""
    global _qq_worker
    if _qq_worker:
        return
    if sys.platform != 'win32' or apsw.sqlitelibversion() != '3.53.4' or apsw.connections():
        raise ValueError('qq_live_runtime_mismatch')
    dll = ROOT / '_internal/qq-live-lock.dll'
    if not dll.exists():
        dll = ROOT / 'build/qq-live/qq-live-lock.dll'
    if not dll.is_file():
        raise ValueError('qq_live_runtime_missing')
    try:
        with apsw.Connection(':memory:') as db:
            db.enable_load_extension(True)
            try:
                db.load_extension(str(dll), 'sqlite3_qqlivelock_init')
            finally:
                db.enable_load_extension(False)
    except apsw.Error:
        raise ValueError('qq_live_runtime_mismatch') from None
    finally:
        if 'db' in locals(): db.close()
    _qq_worker = True


class SourceBusy(OSError):
    pass


def empty_wal_index(wal,shm):
    """SQLite can publish a checksummed empty index with szPage=0.

    Accept only the observed fully empty state, never an unknown or partially
    written index. A nonempty index still uses the established WAL validator.
    Source fences and page HMAC verification remain mandatory.
    """
    from chatlog_keeper.core._wal import _checksum_bytes
    if not shm.is_file():return False
    with shm.open('rb') as f:h=f.read(100)
    with wal.open('rb') as f:w=f.read(32)
    if len(h)!=100 or len(w)!=32 or h[:48]!=h[48:96]:return False
    native='<' if sys.byteorder=='little' else '>'
    magic,version,size=struct.unpack_from('>III',w)
    if magic not in (0x377f0682,0x377f0683) or version!=3007000 or size!=4096:return False
    if struct.unpack_from(native+'I',h)[0]!=3007000 or h[4:8]!=b'\0'*4 or h[12]!=1:return False
    if h[13]!=int(magic==0x377f0683) or h[14:24]!=b'\0'*10 or h[96:100]!=b'\0'*4:return False
    if h[32:40]!=w[16:24]:return False
    return (struct.unpack_from(native+'II',h,40)==_checksum_bytes(h[:40],big_endian=sys.byteorder=='big') and
            struct.unpack_from('>II',w,24)==_checksum_bytes(w[:24],big_endian=magic==0x377f0683))


def stamp(path):
    try:
        s = Path(path).stat()
        return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns
    except FileNotFoundError:
        return None


def family_stamp(path):
    # Windows can defer mtime while writers keep handles open. Commit headers
    # and nBackfill (first 100 bytes) also fence checkpoints/WAL resets. Read
    # marks start later and intentionally do not trigger reads.
    try:
        with open(str(path)+'-shm','rb') as handle: marker=hashlib.sha256(handle.read(100)).digest()
    except FileNotFoundError:marker=None
    return stamp(path), stamp(str(path) + '-wal'), marker


class PageView:
    def __init__(self, path, key, platform):
        from chatlog_keeper.core._wal import inspect_wal
        if platform == 'wechat' and _qq_worker:
            raise ValueError('qq_live_runtime_mismatch')
        self.platform = platform
        self.path = Path(path)
        self.before = family_stamp(path)
        self.offset = 1024 if platform == 'qq' else 0
        self.main = self.path.open('rb')
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='live-pages-')
        self.wal = None
        self.pages = {}
        self.cache = OrderedDict()
        self.bytes_read = 0
        self.decrypted = 0
        try:
            self.main.seek(self.offset)
            first = self.main.read(4096)
            if platform == 'qq':
                import chatlog_keeper.qq_db as q
                key = key.encode('ascii') if isinstance(key, str) else key
                resolved = q._resolve_qq_cipher(key, first)
                if resolved is None:
                    raise ValueError('QQ page authentication failed')
                kdf, algo, reserve = resolved
                salt = first[:16]
                aes = hashlib.pbkdf2_hmac(kdf, key, salt, 4000, dklen=32)
                mac = hashlib.pbkdf2_hmac(kdf, aes, bytes(v ^ 0x3a for v in salt), 2, dklen=32)
                self.decode = lambda p, n: q._decrypt_qq_page(p, page_no=n, salt=salt,
                    aes_key=aes, mac_key=mac, hmac_algo=algo, reserve=reserve)
            else:
                from wechatauto import db as wx
                if len(key) == 48 and wx._verify_enc_key(key[:32], first):
                    key = key[:32]
                if not wx._verify_enc_key(key, first):
                    raise ValueError('WeChat page authentication failed')
                salt = key[32:] if len(key) == 48 else first[:16]
                mac = hashlib.pbkdf2_hmac('sha512', key[:32], bytes(v ^ 0x3a for v in salt), 2, dklen=32)
                def decode(page, number):
                    prefix = 16 if number == 1 else 0
                    tag = hmac.new(mac, page[prefix:4032] + struct.pack('<I', number), hashlib.sha512).digest()
                    if not hmac.compare_digest(tag, page[4032:4096]):
                        return None
                    return wx._decrypt_page(key, page, number)
                self.decode = decode
            self.size = self.before[0][2] - self.offset
            folder = Path(self.temp.name)
            source_wal = Path(str(path) + '-wal')
            source_shm = Path(str(path) + '-shm')
            if source_wal.exists():
                if source_wal.stat().st_size>64*1024*1024:
                    raise ValueError('wal_too_large')
                target = folder / 'view-wal'
                shutil.copyfile(source_wal, target)
                if source_shm.exists():
                    shutil.copyfile(source_shm, folder / 'view-shm')
                self.assert_fresh()
                empty=platform=='wechat' and empty_wal_index(target,folder/'view-shm')
                plan = None if empty else inspect_wal(target, shm_path=folder / 'view-shm', expected_page_size=4096)
                self.bytes_read += target.stat().st_size
                if plan and plan.frames_to_apply:
                    self.wal = target.open('rb')
                    for i in range(plan.frames_to_apply):
                        position = 32 + i * 4120
                        self.wal.seek(position)
                        number = struct.unpack('>I', self.wal.read(4))[0]
                        self.pages[number] = position + 24
                    self.size = plan.commit_size * 4096
            if platform == 'qq' and self.size >= 262144 * 4096:
                if not _qq_worker:
                    raise ValueError('qq_live_runtime_missing')
                # Only the exact wrapped Windows placeholder may bypass HMAC.
                # Never accept this page from WAL or arbitrary all-zero pages.
                self.main.seek(1024 + (262144 - 1) * 4096)
                lock_page = self.main.read(4096)
                self.bytes_read += len(lock_page)
                if 262144 in self.pages or not q._is_wrapped_windows_lock_placeholder(
                    lock_page, page_no=262144, source_wrapper_size=1024, platform_name=os.name):
                    raise ValueError('qq_live_lock_page_invalid')
            self.assert_fresh()
        except BaseException:
            self.close()
            raise

    def assert_fresh(self):
        if family_stamp(self.path) != self.before:
            raise SourceBusy('Client database changed during read')

    def page(self, number):
        if number in self.cache:
            self.cache.move_to_end(number)
            return self.cache[number]
        if number in self.pages:
            self.wal.seek(self.pages[number])
            encrypted = self.wal.read(4096)
        else:
            self.main.seek(self.offset + (number - 1) * 4096)
            encrypted = self.main.read(4096)
        self.bytes_read += len(encrypted)
        if self.platform == 'qq' and _qq_worker and number == 262144:
            if encrypted != bytes(4096) or number in self.pages:
                raise ValueError('qq_live_lock_page_invalid')
            plain = encrypted
        else:
            plain = self.decode(encrypted, number) if len(encrypted) == 4096 else None
        if plain is None:
            raise SourceBusy('Page authentication failed or source changed')
        if number == 1:
            plain = bytearray(plain)
            plain[18:20] = b'\x01\x01'  # Private immutable view has already applied WAL.
            plain[28:32] = struct.pack('>I', self.size // 4096)
            plain = bytes(plain)
        self.decrypted += 1
        self.cache[number] = plain
        if len(self.cache) > 2048:
            self.cache.popitem(last=False)
        return plain

    def xRead(self, amount, offset):
        result = bytearray()
        while amount:
            number, inside = divmod(offset, 4096)
            count = min(amount, 4096 - inside)
            result.extend(self.page(number + 1)[inside:inside + count])
            offset += count
            amount -= count
        return bytes(result)

    def xFileSize(self): return self.size
    def xLock(self, level): pass
    def xUnlock(self, level): pass
    def xCheckReservedLock(self): return False
    def xSectorSize(self): return 4096
    def xDeviceCharacteristics(self): return apsw.SQLITE_IOCAP_IMMUTABLE
    def xFileControl(self, op, pointer): return False
    def xClose(self): pass  # Managed by ReadView, after its final source fence.
    def xWrite(self, *args): raise apsw.ReadOnlyError('Client view is read-only')
    def xTruncate(self, *args): raise apsw.ReadOnlyError('Client view is read-only')

    def close(self):
        self.main.close()
        if self.wal: self.wal.close()
        self.temp.cleanup()


class ReadVFS(apsw.VFS):
    def __init__(self, view):
        self.view = view
        self.name = 'chatlocal-' + uuid.uuid4().hex
        super().__init__(self.name, '')

    def xOpen(self, name, flags):
        if not flags[0] & apsw.SQLITE_OPEN_MAIN_DB or flags[0] & apsw.SQLITE_OPEN_READWRITE:
            raise apsw.ReadOnlyError('Only read-only main database access is supported')
        flags[1] = apsw.SQLITE_OPEN_READONLY
        return self.view

    def xAccess(self, name, flags): return False
    def xDelete(self, *args): raise apsw.ReadOnlyError('Client view is read-only')


class ReadView:
    def __init__(self, path, key, platform):
        self.view = PageView(path, key, platform)
        try:
            self.vfs = ReadVFS(self.view)
            self.db = apsw.Connection(str(ROOT / '.tmp/live-virtual.db'),
                flags=apsw.SQLITE_OPEN_READONLY, vfs=self.vfs.name)
            self.db.execute('PRAGMA query_only=ON')
            self.db.execute('PRAGMA temp_store=MEMORY')
            self.db.execute('PRAGMA cache_size=-8192')
        except BaseException:
            self.view.close()
            raise

    def close(self):
        self.db.close()
        self.vfs.unregister()
        self.view.close()

    def __enter__(self): return self
    def __exit__(self, *args): self.close()
