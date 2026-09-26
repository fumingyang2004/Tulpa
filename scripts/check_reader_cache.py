"""Critical cache recovery/identity tests on isolated fixtures; no client/API."""
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tools/qq-reader'))
from chatlocal.config import ROOT
from page_cache import update_pages, PAGE, BLOCK
from snapshot_cache import refresh_families
from reader_metrics import COUNTS
from incremental_common import message_files_unchanged


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='reader-cache-check-') as folder:
        root=Path(folder);src=root/'source.db';dst=root/'plain.db'
        # Deliberately simple reversible encoding tests the cache algorithm;
        # real QQ cryptographic/output equivalence is verified separately.
        raw=b'A'*BLOCK+b'B'*BLOCK+b'C'*BLOCK
        src.write_bytes(raw)
        def decode(page,n):return bytes(v^0x55 for v in page)
        expected=lambda: bytes(v^0x55 for v in src.read_bytes())
        overlay={};calls=[0]
        def apply(target):
            with target.open('r+b') as output:
                for n,value in overlay.items():output.seek((n-1)*PAGE);output.write(value)
            return set(overlay)
        def check(target):
            value=bytearray(expected())
            for n,page in overlay.items():value[(n-1)*PAGE:n*PAGE]=page
            assert target.read_bytes()==bytes(value)
        def run(identity='a',validate=check):
            update_pages(src,dst,decode,identity=identity,apply_wal=apply,validate=validate)
        run();assert COUNTS['decrypted_pages']==48
        COUNTS.clear();run();assert COUNTS.get('decrypted_pages',0)==0 and COUNTS['reused_pages']==48
        # In-place database changes (checkpoint), including older data, survive.
        with src.open('r+b') as f:f.seek(BLOCK+19);f.write(b'new')
        COUNTS.clear();run();assert COUNTS['decrypted_pages']==16 and COUNTS['reused_pages']==32
        overlay[2]=b'W'*PAGE;run()
        overlay.clear();run()  # WAL reset: old overlay MUST disappear.
        # Even matching size/mtime cannot make damaged plaintext reusable.
        stamp=dst.stat()
        with dst.open('r+b') as f:f.seek(9);f.write(b'corrupt')
        os.utime(dst,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        run()
        # Failed validation leaves no manifest; recovery starts from the source.
        def fail(_):raise ValueError('synthetic interrupted rebuild')
        try:run(validate=fail)
        except ValueError:pass
        else:raise AssertionError('expected failure')
        assert not dst.with_suffix('.db.pages.json').exists()
        COUNTS.clear();run();assert COUNTS['decrypted_pages']==48
        COUNTS.clear();run(identity='different-key');assert COUNTS['decrypted_pages']==48
        # A malformed manifest is a cache miss, never a lost message.
        dst.with_suffix('.db.pages.json').write_text('[]')
        run()
        source=root/'live';target=root/'snapshot';source.mkdir()
        one=source/'one.db';two=source/'two.db'
        one.write_bytes(b'1'*8192);two.write_bytes(b'2'*8192)
        paths=[one,two]
        refresh_families(source,target,paths)
        COUNTS.clear();refresh_families(source,target,paths)
        assert COUNTS.get('snapshot_copied_bytes',0)==0 and COUNTS['snapshot_reused_databases']==2
        wal=source/'one.db-wal';wal.write_bytes(b'W'*100)
        COUNTS.clear();refresh_families(source,target,paths)
        assert COUNTS['snapshot_wal_only_databases']==1 and COUNTS['snapshot_copied_bytes']==100
        assert (target/wal.name).read_bytes()==wal.read_bytes()
        one.write_bytes(b'3'*8192)
        COUNTS.clear();refresh_families(source,target,paths)
        assert COUNTS['snapshot_copied_databases']==1 and COUNTS['snapshot_reused_databases']==1
        assert (target/one.name).read_bytes()==one.read_bytes()
        two.unlink();refresh_families(source,target,[one]);assert not (target/two.name).exists()
        old=dict(account='a',cursors={'x':1},fingerprint={'nt_msg.db':[100,1],'profile_info.db':[20,1]})
        new=dict(old,fingerprint={'nt_msg.db':[100,1],'profile_info.db':[20,2]})
        assert message_files_unchanged(old,new,'qq')
        assert not message_files_unchanged(old,dict(new,account='b'),'qq')
        assert not message_files_unchanged(old,dict(new,fingerprint={'nt_msg.db':[100,2]}),'qq')
        old=dict(account='a',cursors={'x':1},fingerprint={'message/message_0.db':[100,1]})
        new=dict(old,fingerprint={**old['fingerprint'],'message/message_1.db':[10,1]})
        assert not message_files_unchanged(old,new,'wechat')
    print('PASS: cold/warm block reuse, changed blocks, WAL reset, damaged plaintext, interrupted update, key change, malformed manifest, per-DB reuse, WAL-only copy and removed shards. Synthetic fixtures; no cloud.')


if __name__=='__main__':main()
