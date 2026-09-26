"""Block reuse around the installed WeChat reader; keep its WAL/check routines."""
import hashlib
import os
import sqlite3
import struct
from pathlib import Path
from page_cache import update_pages
from reader_metrics import count


def install(reader_class):
    from wechatauto import db as wx
    original = reader_class._open

    def cached_open(self, rel):
        src = Path(self._db_path(rel))
        dst = Path(self.workdir) / rel.replace(os.sep, '__')
        stamp = Path(str(dst)+'.stamp')
        wal = self._wal_path(rel)
        key = self._keys.get(rel)
        if not key:
            return original(self, rel)
        expected = (src.stat().st_mtime, src.stat().st_size,
                    os.path.getmtime(wal) if wal else 0.0, os.path.getsize(wal) if wal else 0)
        try:
            parts = stamp.read_text().split(',')
            if (int(parts[0]) == wx.STAMP_VERSION and dst.is_file()
                    and (float(parts[1]),int(parts[2]),float(parts[3]),int(parts[4])) == expected
                    and int(parts[5]) >= 0):
                # The upstream cache is already current; preserve its fast path.
                return original(self, rel)
        except (OSError, ValueError, IndexError):
            pass
        # Prevent the upstream fallback from accepting a partly updated cache.
        stamp.unlink(missing_ok=True)
        if len(key) == 48:
            with src.open('rb') as handle:
                if wx._verify_enc_key(key[:32], handle.read(wx.PAGE_SZ)):
                    key = key[:32]
        applied = 0
        def apply(target):
            nonlocal applied
            touched = set()
            if wal and os.path.getsize(wal) > self.WAL_HEADER_SZ:
                applied = self._merge_wal(str(target), wal, key, 0)
                # Conservative invalidation: all physical frame pages, even
                # stale ones ignored by the upstream merger. Never reuse a
                # previous WAL overlay as if it were the current main DB.
                with open(wal, 'rb') as handle:
                    handle.seek(self.WAL_HEADER_SZ)
                    while header := handle.read(24):
                        if len(header) != 24:break
                        page = struct.unpack('>I', header[:4])[0]
                        if page:touched.add(page)
                        handle.seek(wx.PAGE_SZ, 1)
                # The upstream merger also updates the page-count header.
                touched.add(1)
                count('wal_frames', applied)
            return touched
        def validate(target):
            if not self._check_merged(str(target)):
                raise ValueError('微信解密副本完整性检查失败')
        try:
            update_pages(src, dst, lambda page,n:wx._decrypt_page(key,page,n),
                identity=hashlib.sha256(b'wechat-cache-v1'+key).hexdigest(),
                apply_wal=apply, validate=validate)
            stamp.write_text('%d,%r,%d,%r,%d,%d' % (wx.STAMP_VERSION,*expected,applied))
        except (OSError, ValueError, sqlite3.DatabaseError):
            # Keep the proven full rebuild path. Its stale/WAL result is still
            # rejected by incremental_wechat.checked_open before cursor commit.
            dst.with_suffix(dst.suffix+'.pages.json').unlink(missing_ok=True)
            stamp.unlink(missing_ok=True)
            # Never offer a partly mutated working file as the upstream
            # reader's "previous good copy" if its full rebuild also fails.
            from chatlocal.config import local_path
            local_path(dst).unlink(missing_ok=True)
            count('full_rebuild_fallbacks')
            return original(self, rel)
        conn = sqlite3.connect(f'file:{dst}?mode=ro',uri=True)
        conn.row_factory = sqlite3.Row
        conn.text_factory = wx._sqlite_text_factory
        return conn
    reader_class._open = cached_open
