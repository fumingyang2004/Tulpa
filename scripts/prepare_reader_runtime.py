"""Prepare development readers with the same pinned runtime as the desktop.

Only generated files under .venv/Scripts/qq-sqlite and build/ are written.
Does not replace the application's SQLite DLL or touch data/configuration.
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from desktop.build import HASHES
from desktop.fetch_runtime import ASSETS,CACHE


def verified_asset(name):
    path=CACHE/name
    CACHE.mkdir(parents=True,exist_ok=True)
    if not path.is_file():
        partial=path.with_suffix(path.suffix+'.part')
        with urllib.request.urlopen(ASSETS[name],timeout=60) as src,partial.open('wb') as dst:
            while block:=src.read(1024*1024):dst.write(block)
        with partial.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if actual!=HASHES[name]:raise RuntimeError('Runtime download checksum mismatch: '+name)
        partial.replace(path)
    with path.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
    if actual!=HASHES[name]:raise RuntimeError('Runtime cache checksum mismatch: '+name)
    return path


def prepare():
    if sys.platform!='win32' or struct.calcsize('P')!=8:raise RuntimeError('Windows x64 development Python required')
    executable=Path(sys.executable).resolve()
    expected=(ROOT/'.venv/Scripts/python.exe').resolve()
    if executable!=expected:raise RuntimeError('Run with this checkout .venv/Scripts/python.exe')
    target=executable.parent/'qq-sqlite'
    if not target.resolve().is_relative_to(ROOT/'.venv'):raise RuntimeError('Runtime target escaped checkout')
    target.mkdir(parents=True,exist_ok=True)
    def write(name,content):
        path=target/name
        if path.parent!=target or not path.resolve().is_relative_to(target.resolve()):raise RuntimeError('Unsafe runtime member')
        if path.is_file() and path.read_bytes()==content:return
        temporary=path.with_name(path.name+'.part');temporary.write_bytes(content);os.replace(temporary,path)
    # Use exactly the assets/versions owned by desktop/build.py, not a second
    # set of development pins or a copy of an already-used release folder.
    with zipfile.ZipFile(verified_asset('python-3.13.9-embed-amd64.zip')) as archive:
        for item in archive.infolist():
            if item.is_dir():continue
            if Path(item.filename).name!=item.filename:raise RuntimeError('Unexpected embedded runtime layout')
            if item.filename in ('sqlite3.dll','python313._pth'):continue
            write(item.filename,archive.read(item))
    with zipfile.ZipFile(verified_asset('sqlite-dll-win-x64-3530200.zip')) as archive:
        write('sqlite3.dll',archive.read('sqlite3.dll'))
    write('python313._pth','python313.zip\n.\n'.replace('\n',os.linesep).encode('utf-8'))
    helper=ROOT/'tools/qq-reader/chatlog_keeper/_qq_sqlite_helper.py'
    result=subprocess.run([str(target/'python.exe'),'-I','-u',str(helper),'--runtime-probe'],
        check=True,capture_output=True,text=True,timeout=25)
    value=json.loads(result.stdout)
    if not value.get('ok') or value.get('sqlite_version')!='3.53.2' or value.get('pending_byte')!=0x3ffffc00:
        raise RuntimeError('Dedicated QQ runtime self-check failed: '+str(value.get('error','unexpected_runtime')))
    verified_asset('sqlite-amalgamation-3530400.zip')
    from desktop.build_live_lock import build
    build()
    # The live DLL modifies APSW only in this dedicated QQ check process.
    probe=subprocess.run([str(executable),'-c',
        'import sys;sys.path.insert(0,"scripts");from live_vfs import configure_qq_worker;configure_qq_worker()'],
        cwd=ROOT,check=True,capture_output=True,text=True,timeout=20)
    print(json.dumps(dict(qq_history=value,qq_live='ok',application_sqlite_unchanged=True)))


if __name__=='__main__':prepare()
