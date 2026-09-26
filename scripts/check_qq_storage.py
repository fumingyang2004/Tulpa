"""QQ cache lifecycle on tiny isolated files; no client reads or cloud requests."""
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from chatlocal.store import Store
from chatlocal import qq_storage as storage


def main():
    with tempfile.TemporaryDirectory(dir=storage.checked_path(ROOT/'.tmp'),prefix='qq-storage-check-') as folder:
        folder=storage.checked_path(folder)
        reader=folder/'qq-reader';cache=reader/'decrypted';cache.mkdir(parents=True)
        store=Store(folder/'chats.sqlite3')
        path=folder/'export.json'
        row=dict(platform='qq',conversation='合成群',conversation_id='q',sender='甲',
                 timestamp='2026-09-21 10:00',content='需要保留的文本',source_id='one',is_self=False)
        def export(content='需要保留的文本'):
            path.write_text(json.dumps(dict(reader='chatlog-keeper',messages=[dict(row,content=content)]),ensure_ascii=False),encoding='utf-8')
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            (cache/storage.RECEIPT).write_text(json.dumps(dict(source_hash=digest)),encoding='utf-8')
            return digest
        def working_files():
            for name in storage.CACHE_FILES:
                if name!=storage.RECEIPT:(cache/name).write_bytes(b'rebuildable')
        # These must survive every successful cleanup; no folder-wide removal.
        protected=[reader/'keys.json',cache/'unknown.db',cache/'info.json',folder/'snapshot.db',folder/'picture.gif']
        for p in protected:p.write_bytes(b'keep')
        with patch.multiple(storage,DB=store.path,READER=reader,CACHE=cache):
            working_files();digest=export()
            assert storage.release_qq_cache(store,digest)['status']=='retained'
            assert (cache/'messages.db').exists()  # No successful import yet.
            imported=store.import_file(path)
            assert imported['imported']==1 and imported['cache_cleanup']['status']=='released'
            assert not any((cache/name).exists() for name in storage.CACHE_FILES)
            assert all(p.read_bytes()==b'keep' for p in protected)
            assert store.message(1)['content']==row['content']
            # A conflict rolls back before the cleanup hook, preserving working files.
            working_files();export('conflicting text')
            try:store.import_file(path)
            except ValueError:pass
            else:raise AssertionError('Expected conflicting source ID to fail')
            assert (cache/'messages.db').exists() and (cache/storage.RECEIPT).exists()
            assert store.message(1)['content']==row['content']
            # Old imports cannot release a newer/failed export generation.
            assert storage.release_qq_cache(store,digest)['status']=='retained'
            export()
            with storage.qq_workspace_lock():
                code='from chatlocal.qq_storage import qq_workspace_lock,QQWorkspaceBusy\nimport sys\ntry:\n with qq_workspace_lock(sys.argv[1]): sys.exit(3)\nexcept QQWorkspaceBusy: sys.exit(0)'
                child=subprocess.run([sys.executable,'-c',code,str(reader)],cwd=ROOT,capture_output=True)
                assert child.returncode==0,child.stderr.decode('utf-8','replace')
                assert storage.release_qq_cache(store,digest)['status']=='retained'
                assert (cache/'messages.db').exists()
            # Locked file leaves a retry receipt, without failing a committed import.
            original_unlink=Path.unlink
            def locked_file(p,*args,**kwargs):
                if p==cache/'messages.db':raise PermissionError('synthetic lock')
                return original_unlink(p,*args,**kwargs)
            with patch.object(Path,'unlink',locked_file):
                result=store.import_file(path)
            assert result['duplicate']==1 and result['cache_cleanup']['status']=='partial'
            assert (cache/storage.RECEIPT).exists() and (cache/'messages.db').exists()
            result=store.import_file(path)
            assert result['cache_cleanup']['status']=='released'
            assert not (cache/'messages.db').exists()
            assert all(p.read_bytes()==b'keep' for p in protected)
            # New balanced exports keep only reusable plaintext/page manifests;
            # switching to space still releases that exact file allowlist.
            working_files();digest=export()
            (cache/storage.RECEIPT).write_text(json.dumps(dict(source_hash=digest,cache_policy='balanced')))
            result=store.import_file(path)
            assert (cache/'messages.db').exists() and (cache/'messages.db.pages.json').exists()
            assert not (cache/'no_header.db').exists()
            assert result['cache_cleanup']['retained_bytes']>0
            export();store.import_file(path)
            assert not (cache/'messages.db').exists() and not (cache/'messages.db.pages.json').exists()
            # Malformed receipt is a cleanup warning, not a false import failure.
            (cache/storage.RECEIPT).write_text('[]',encoding='utf-8')
            assert store.import_file(path)['cache_cleanup']['status']=='retained'
            try:storage.checked_path(ROOT.parent/'qq-cache-outside-project')
            except ValueError:pass
            else:raise AssertionError('Out-of-project cleanup path accepted')
        print('PASS: post-commit cleanup, failed import retention, generation mismatch, process lock, partial cleanup retry, malformed receipt, exact file allowlist and path boundary. No cloud API.')


if __name__=='__main__':main()
