"""Verify development export source/runtime against an immutable release ZIP.

Checks code and the dedicated QQ runtime; does not load keys, export chats,
change source files or invoke a model. Native export tests are separate.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def check(archive):
    checked=[];runtime_files=[]
    with zipfile.ZipFile(archive) as release:
        manifests=[name for name in release.namelist() if name.count('/')==1 and name.endswith('/build-manifest.json')]
        assert len(manifests)==1, 'Expected a single product manifest'
        prefix=manifests[0].rsplit('/',1)[0]+'/'
        manifest=json.loads(release.read(manifests[0]))
        for rel in manifest['files']:
            if rel.endswith('.py') and rel.startswith(('chatlocal/','scripts/','tools/qq-reader/','tools/wechat-reader/')):
                assert (ROOT/rel).read_bytes()==release.read(prefix+rel),'Source differs: '+rel
                checked.append(rel)
            if rel.startswith('runtime/qq-sqlite/'):
                target=ROOT/'.venv/Scripts/qq-sqlite'/rel.removeprefix('runtime/qq-sqlite/')
                assert target.read_bytes()==release.read(prefix+rel),'QQ runtime differs: '+rel
                runtime_files.append(rel)
        assert checked and runtime_files
        installed=json.loads((ROOT/'tools/reader-manifest.json').read_text('utf-8'))
        assert installed==json.loads(release.read(prefix+'tools/reader-manifest.json'))
        for row in installed:
            assert hashlib.sha256((ROOT/row['file']).read_bytes()).hexdigest()==row['installed_sha256'],row['file']
    result=dict(release=manifest['version'],source_files=len(checked),qq_runtime_files=len(runtime_files),
        source_identical=True,qq_runtime_identical=True,reader_manifest_identical=True)
    print(json.dumps(result))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--release',type=Path,required=True)
    check(p.parse_args().release.resolve())
