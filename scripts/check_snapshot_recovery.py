"""Windows ACL fixture: rebuild unreadable generated snapshots, never sources."""
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tools/qq-reader')]
from snapshot_cache import refresh_families
from reader_metrics import COUNTS


def main():
    # Only disposable fixture files receive this temporary deny-read ACE.
    sid=re.search(rb'S-1-[\d-]+',subprocess.check_output(['whoami','/user','/fo','csv','/nh'])).group().decode('ascii')
    recoveries=set();blocked=[]
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='snapshot-acl-check-') as folder:
        root=Path(folder);source=root/'client';target=root/'cache';source.mkdir()
        client=source/'contact.db';client.write_bytes(b'fixture-contact'*1024)
        before=hashlib.sha256(client.read_bytes()).hexdigest()
        refresh_families(source,target,[client]);cached=target/client.name
        existing=set((ROOT/'.tmp').glob('snapshot-recovery-*'))
        def deny(path):
            assert path.resolve().is_relative_to(root.resolve())
            subprocess.run(['icacls',str(path),'/deny','*'+sid+':(R)'],check=True,capture_output=True)
            blocked.append(path)
        try:
            deny(cached)
            try:cached.read_bytes()
            except PermissionError:pass
            else:raise AssertionError('Expected actual Win32 read denial')
            COUNTS.clear();refresh_families(source,target,[client])
            recoveries=set((ROOT/'.tmp').glob('snapshot-recovery-*'))-existing
            assert len(recoveries)==1 and COUNTS['snapshot_unreadable_rebuilds']==1
            assert cached.read_bytes()==client.read_bytes()
            assert hashlib.sha256(client.read_bytes()).hexdigest()==before
            COUNTS.clear();refresh_families(source,target,[client])
            assert COUNTS['snapshot_reused_databases']==1 and not COUNTS.get('snapshot_unreadable_rebuilds')
            # A source read denial is NOT treated as a disposable cache problem.
            stamp=cached.stat();deny(client)
            try:refresh_families(source,target,[client])
            except PermissionError:pass
            else:raise AssertionError('Source ACL failure hidden')
            assert cached.stat().st_mtime_ns==stamp.st_mtime_ns
        finally:
            recoveries|=set((ROOT/'.tmp').glob('snapshot-recovery-*'))-existing
            for recovery in recoveries:
                # Resolved exact test-owned recovery targets before cleanup.
                assert recovery.resolve().parent==(ROOT/'.tmp').resolve() and recovery.name.startswith('snapshot-recovery-')
                shutil.rmtree(recovery)
    print('PASS actual Windows deny-read fixture: old generated copy retained, fresh snapshot readable, source untouched, next refresh reuses cache, source denial fails without modifying cache. No real client files.')


if __name__=='__main__':main()
