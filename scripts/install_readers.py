"""Fetch only pinned upstream reader modules and licenses into tools/."""
import concurrent.futures
import hashlib
import json
import sys
from pathlib import Path
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from chatlocal.config import local_path
from reader_patches import patch_qq_proxy,patch_wechat_db,patch_qq_helper,patch_qq_configured_root,patch_qq_passive_scan

QQ_COMMIT='b55675779e50edec913fab9d891e4185c8f7c9ac'
WX_COMMIT='492a8fb70b95865613d6d8d9740323233dbfa197'
CORE=['__init__.py','_macos.py','_paths.py','_path_resolver.py','_private_temp.py',
      '_secrets.py','_snapshot.py','_wal.py','_windows_process_memory.py','trace_sink.py']
QQ_FILES=['LICENSE','chatlog_keeper/__init__.py','chatlog_keeper/qq_db.py',
          'chatlog_keeper/stream_protocol.py','chatlog_keeper/_qq_sqlite_proxy.py',
          'chatlog_keeper/_qq_sqlite_helper.py']+['chatlog_keeper/core/'+p for p in CORE]


def download(job):
    repo,commit,relative,directory=job
    url=f'https://raw.githubusercontent.com/{repo}/{commit}/{relative}'
    with urlopen(url,timeout=45) as response:
        data=response.read()
    source_hash=hashlib.sha256(data).hexdigest()
    if relative=='chatlog_keeper/_qq_sqlite_proxy.py':data=patch_qq_proxy(data)
    if relative=='chatlog_keeper/_qq_sqlite_helper.py':data=patch_qq_helper(data)
    if relative=='wechatauto/db.py':data=patch_wechat_db(data)
    if relative=='chatlog_keeper/qq_db.py':
        original=b'return " ".join(out)[:1000]'
        if data.count(original)!=2:
            raise ValueError('Upstream QQ reader changed; review text truncation patch')
        data=data.replace(original,b'return " ".join(out)')
        data=patch_qq_configured_root(data)
        data=patch_qq_passive_scan(data)
    target=local_path(ROOT/'tools'/directory/relative)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(data)
    return dict(file=str(target.relative_to(ROOT)),url=url,source_sha256=source_hash,
                installed_sha256=hashlib.sha256(data).hexdigest())


def main():
    jobs=[('labazhou2024/chatlog-keeper',QQ_COMMIT,p,'qq-reader') for p in QQ_FILES]
    jobs += [('fanyuantaier/wechatauto-replica',WX_COMMIT,p,'wechat-reader') for p in ['LICENSE','wechatauto/db.py','wechatauto/media.py']]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        manifest=list(pool.map(download,jobs))
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
