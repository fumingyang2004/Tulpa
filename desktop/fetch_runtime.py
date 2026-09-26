"""Download pinned official Windows runtime components into the ignored cache."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
CACHE=ROOT/'.cache/desktop-downloads'
ASSETS={
    'FFmpeg-b08d7969c5-source.tar.gz':'https://codeload.github.com/FFmpeg/FFmpeg/tar.gz/b08d7969c5',
    'sqlite-amalgamation-3530400.zip':'https://www.sqlite.org/2026/sqlite-amalgamation-3530400.zip',
    'sqlite-dll-win-x64-3530200.zip':'https://www.sqlite.org/2026/sqlite-dll-win-x64-3530200.zip',
    'python-3.13.9-embed-amd64.zip':'https://www.python.org/ftp/python/3.13.9/python-3.13.9-embed-amd64.zip',
    'webview2-sdk-1.0.4191.47.zip':'https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/1.0.4191.47/microsoft.web.webview2.1.0.4191.47.nupkg',
    'webview2-153.0.4234.48-x64.cab':'https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/files/08cd33ee-d109-49b8-9301-9f0bea43c575/Microsoft.WebView2.FixedVersionRuntime.153.0.4234.48.x64.cab',
}

def fetch(item):
    name,url=item;path=CACHE/name
    if not path.exists():
        partial=path.with_suffix(path.suffix+'.part')
        with urllib.request.urlopen(url,timeout=60) as src,partial.open('wb') as dst:
            while block:=src.read(1024*1024):dst.write(block)
        partial.replace(path)
    digest=hashlib.file_digest(path.open('rb'),'sha256').hexdigest()
    print(f'{name}: {path.stat().st_size:,} bytes; SHA256 {digest}',flush=True)
    return dict(name=name,url=url,bytes=path.stat().st_size,sha256=digest)

if __name__=='__main__':
    CACHE.mkdir(parents=True,exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(fetch,ASSETS.items()))
    (CACHE/'download-manifest.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
