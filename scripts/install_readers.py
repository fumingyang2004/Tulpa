"""Fetch only pinned upstream reader modules and licenses into tools/."""
import concurrent.futures
import hashlib
import json
import sys
import os
import tarfile
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from reader_patches import patch_qq_proxy,patch_wechat_db,patch_qq_helper,patch_qq_configured_root,patch_qq_passive_scan,patch_wechat_media

QQ_COMMIT='b55675779e50edec913fab9d891e4185c8f7c9ac'
WX_COMMIT='492a8fb70b95865613d6d8d9740323233dbfa197'
LINUX_COMMIT='2225294fd0761e10ce15540f21e7dbb2ac304c3c'
LINUX_SHA256='7beed277d9bcb9d6a3715e5634a5f8ccda69af8c546030a098d170cddef48f31'
CORE=['__init__.py','_macos.py','_paths.py','_path_resolver.py','_private_temp.py',
      '_secrets.py','_snapshot.py','_wal.py','_windows_process_memory.py','trace_sink.py']
QQ_FILES=['LICENSE','chatlog_keeper/__init__.py','chatlog_keeper/qq_db.py',
          'chatlog_keeper/stream_protocol.py','chatlog_keeper/_qq_sqlite_proxy.py',
          'chatlog_keeper/_qq_sqlite_helper.py','chatlog_keeper/macos_key.py',
          'chatlog_keeper/scripts/macos_memory_scan.c']+['chatlog_keeper/core/'+p for p in CORE]


def install_target(directory,relative):
    # The installer owns this checkout's tools/, not the app's import root.
    # Resolve both sides identically (Windows runners may expose a junction).
    root=ROOT.resolve()
    base=(root/'tools'/directory).resolve()
    target=(base/relative).resolve()
    if directory not in ('qq-reader','wechat-reader') or not base.is_relative_to(root/'tools') or not target.is_relative_to(base):
        raise ValueError('Reader installation path escaped tools directory')
    return target


def download(job):
    repo,commit,relative,directory=job
    target=install_target(directory,relative)
    url=f'https://raw.githubusercontent.com/{repo}/{commit}/{relative}'
    for attempt in range(3):
        try:
            with urlopen(url,timeout=45) as response:
                data=response.read()
            break
        except Exception:
            if attempt == 2:raise
            time.sleep(1)
    source_hash=hashlib.sha256(data).hexdigest()
    if relative=='chatlog_keeper/_qq_sqlite_proxy.py':data=patch_qq_proxy(data)
    if relative=='chatlog_keeper/_qq_sqlite_helper.py':data=patch_qq_helper(data)
    if relative=='wechatauto/media.py':data=patch_wechat_media(data)
    if relative=='wechatauto/db.py':
        data=patch_wechat_db(data)
        if sys.platform.startswith('linux'):
            data+=b'\nfrom chatlocal.linux_readers import install_wechat as _tulpa_linux\n_tulpa_linux(globals())\n'
    if relative=='wechatauto/media.py' and sys.platform.startswith('linux'):
        data+=b'\nfrom chatlocal.linux_readers import install_media as _tulpa_linux\n_tulpa_linux(globals())\n'
    if relative=='chatlog_keeper/qq_db.py':
        original=b'return " ".join(out)[:1000]'
        if data.count(original)!=2:
            raise ValueError('Upstream QQ reader changed; review text truncation patch')
        data=data.replace(original,b'return " ".join(out)')
        data=patch_qq_configured_root(data)
        data=patch_qq_passive_scan(data)
        # macOS accounts are opaque names. Tulpa's private snapshots retain
        # the Windows-compatible nt_qq/nt_db layout, which must be discoverable
        # alongside the Mac client's native nt_db layout.
        original=b'and (item / "nt_db" / "nt_msg.db").is_file()'
        if data.count(original)!=1:
            raise ValueError('Upstream QQ discovery changed; review Mac snapshot patch')
        data=data.replace(original,b'and ((item / "nt_db" / "nt_msg.db").is_file() or (item / "nt_qq" / "nt_db" / "nt_msg.db").is_file())')
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(data)
    return dict(file=str(target.relative_to(ROOT.resolve())),url=url,source_sha256=source_hash,
                installed_sha256=hashlib.sha256(data).hexdigest())


def install_linux():
    """Pinned MIT Linux readers including their guarded opt-in key helpers."""
    url='https://codeload.github.com/labazhou2024/chatlog-keeper/tar.gz/'+LINUX_COMMIT
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='reader-install-') as temp:
        archive=Path(temp)/'reader.tar.gz'
        with urlopen(url,timeout=90) as src,archive.open('wb') as out:
            size=0
            while block:=src.read(1024*1024):
                size+=len(block)
                if size>20*1024*1024:raise ValueError('Reader archive too large')
                out.write(block)
        if hashlib.sha256(archive.read_bytes()).hexdigest()!=LINUX_SHA256:raise ValueError('Reader archive digest mismatch')
        prepared=[]
        with tarfile.open(archive) as tf:
            prefix='chatlog-keeper-'+LINUX_COMMIT+'/'
            for member in tf.getmembers():
                rel=member.name.removeprefix(prefix)
                if member.name==rel or not member.isfile():continue
                if rel!='LICENSE' and not (rel.startswith('chatlog_keeper/') and Path(rel).suffix in ('.py','.c')):continue
                if '..' in Path(rel).parts:raise ValueError('Unsafe reader archive')
                raw=tf.extractfile(member).read();data=raw
                if rel=='chatlog_keeper/qq_db.py':
                    data=data.replace(b'return " ".join(out)[:1000]',b'return " ".join(out)')
                    data+=b'\nfrom chatlocal.linux_readers import install_qq as _tulpa_linux\n_tulpa_linux(globals())\n'
                if rel=='chatlog_keeper/_qq_sqlite_helper.py':data=patch_qq_helper(data)
                prepared.append((rel,raw,data))
        manifest=[]
        for rel,raw,data in prepared:
            target=install_target('qq-reader',rel);target.parent.mkdir(parents=True,exist_ok=True)
            staged=target.with_suffix(target.suffix+'.new');staged.write_bytes(data);staged.replace(target)
            manifest.append(dict(file=str(target.relative_to(ROOT.resolve())),url=url,commit=LINUX_COMMIT,
                source_sha256=hashlib.sha256(raw).hexdigest(),installed_sha256=hashlib.sha256(data).hexdigest()))
        return manifest


def main():
    linux=sys.platform.startswith('linux')
    (ROOT/'.tmp').mkdir(exist_ok=True)
    manifest=install_linux() if linux else []
    jobs=[] if linux else [('labazhou2024/chatlog-keeper',QQ_COMMIT,p,'qq-reader') for p in QQ_FILES]
    jobs += [('fanyuantaier/wechatauto-replica',WX_COMMIT,p,'wechat-reader') for p in ['LICENSE','wechatauto/db.py','wechatauto/media.py']]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        manifest+=list(pool.map(download,jobs))
    helper=ROOT/'scripts/qq_passive_keys.py'
    target=ROOT/'tools/qq-reader/chatlog_keeper/_tulpa_passive_keys.py'
    target.write_bytes(helper.read_bytes())
    manifest.append(dict(file=str(target.relative_to(ROOT)),source='scripts/qq_passive_keys.py',
        installed_sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
    package=ROOT/'tools'/'wechat-reader'/'wechatauto'
    (package/'__init__.py').write_text('# Only the upstream read-only database module is loaded.\n',encoding='utf-8')
    (package/'logger.py').write_text("import logging\nwxlog = logging.getLogger('wechat-reader')\nwxlog.addHandler(logging.NullHandler())\nwxlog.propagate = False\n",encoding='utf-8')
    (ROOT/'tools'/'reader-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(f'Installed {len(manifest)} pinned reader/license files; manifest: tools/reader-manifest.json')


if __name__=='__main__':
    main()
