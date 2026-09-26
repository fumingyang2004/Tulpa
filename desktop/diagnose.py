"""Opt-in support report and temporary LAN read-only transport.

No .env, keys, process memory, message databases or raw log text is exported.
Transport serves one precomputed report, has no command/file APIs, and expires.
"""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime,timezone
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler,HTTPServer
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]


def client_processes():
    if os.name!='nt':return []
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    class Entry(ctypes.Structure):
        _fields_=[('dwSize',wintypes.DWORD),('cntUsage',wintypes.DWORD),('pid',wintypes.DWORD),
            ('heap',ctypes.c_size_t),('module',wintypes.DWORD),('threads',wintypes.DWORD),
            ('parent',wintypes.DWORD),('priority',wintypes.LONG),('flags',wintypes.DWORD),('exe',wintypes.WCHAR*260)]
    k.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD];k.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    k.Process32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Entry)];k.Process32NextW.argtypes=k.Process32FirstW.argtypes
    k.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];k.OpenProcess.restype=wintypes.HANDLE
    k.CloseHandle.argtypes=[wintypes.HANDLE]
    k.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    v=ctypes.WinDLL('version',use_last_error=True)
    v.GetFileVersionInfoSizeW.argtypes=[wintypes.LPCWSTR,ctypes.POINTER(wintypes.DWORD)]
    v.GetFileVersionInfoW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p]
    v.VerQueryValueW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR,ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(wintypes.UINT)]
    a=ctypes.WinDLL('advapi32',use_last_error=True)
    a.OpenProcessToken.argtypes=[wintypes.HANDLE,wintypes.DWORD,ctypes.POINTER(wintypes.HANDLE)]
    a.GetTokenInformation.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)]
    result=[];snapshot=k.CreateToolhelp32Snapshot(2,0)
    if snapshot in (None,ctypes.c_void_p(-1).value):return result
    try:
        entry=Entry();entry.dwSize=ctypes.sizeof(entry);more=k.Process32FirstW(snapshot,ctypes.byref(entry))
        while more:
            name=entry.exe.lower()
            if name in ('qq.exe','weixin.exe','wechat.exe'):
                row=dict(client='qq' if name=='qq.exe' else 'wechat',version=None,queryable=False,memory_read_allowed=False,elevated=None)
                process=k.OpenProcess(0x1000,False,entry.pid)
                if process:
                    try:
                        row['queryable']=True;path=ctypes.create_unicode_buffer(32768);size=wintypes.DWORD(len(path))
                        if k.QueryFullProcessImageNameW(process,0,path,ctypes.byref(size)):
                            unused=wintypes.DWORD();length=v.GetFileVersionInfoSizeW(path.value,ctypes.byref(unused))
                            if 0<length<1024**2:
                                data=ctypes.create_string_buffer(length)
                                if v.GetFileVersionInfoW(path.value,0,length,data):
                                    ptr=ctypes.c_void_p();count=wintypes.UINT()
                                    if v.VerQueryValueW(data,'\\',ctypes.byref(ptr),ctypes.byref(count)) and count.value>=52:
                                        words=ctypes.cast(ptr,ctypes.POINTER(wintypes.DWORD))
                                        row['version']='.'.join(str(x) for x in (words[2]>>16,words[2]&65535,words[3]>>16,words[3]&65535))
                        token=wintypes.HANDLE()
                        if a.OpenProcessToken(process,8,ctypes.byref(token)):
                            try:
                                elevated=wintypes.DWORD();size=wintypes.DWORD()
                                if a.GetTokenInformation(token,20,ctypes.byref(elevated),4,ctypes.byref(size)):row['elevated']=bool(elevated.value)
                            finally:k.CloseHandle(token)
                    finally:k.CloseHandle(process)
                handle=k.OpenProcess(0x10|0x400,False,entry.pid)
                if handle:row['memory_read_allowed']=True;k.CloseHandle(handle)
                if row not in result:result.append(row)
            more=k.Process32NextW(snapshot,ctypes.byref(entry))
    finally:k.CloseHandle(snapshot)
    return result


def failure_details(text):
    """Only known exception names, numeric OS codes and shipped code locations.

    Do not copy exception messages, arbitrary basenames or source lines: those
    can contain usernames, account IDs, queries, credentials and chat content.
    """
    names={'PermissionError','OSError','FileNotFoundError','FileExistsError','NotADirectoryError',
        'IsADirectoryError','RuntimeError','ValueError','TypeError','NameError','AttributeError',
        'KeyError','IndexError','ImportError','ModuleNotFoundError','UnicodeEncodeError','UnicodeDecodeError',
        'DatabaseError','OperationalError','IntegrityError','QQShiftedSQLiteError','TimeoutError',
        'TimeoutExpired','CalledProcessError','AssertionError','MemoryError','OverflowError'}
    exception_line=None;details={}
    for line in text.splitlines():
        match=re.match(r'^(?:[a-zA-Z_]\w*\.)*([a-zA-Z_]\w*):',line)
        if match and match[1] in names:
            details={'exception_type':match[1]};exception_line=line
    if exception_line:
        for field,pattern in (('winerror',r'\[WinError (\d+)\]'),('errno',r'\[Errno (\d+)\]')):
            match=re.search(pattern,exception_line)
            if match:details[field]=int(match[1])
    known=set()
    for directory in ('scripts','chatlocal','desktop','tools/qq-reader','tools/wechat-reader'):
        for code in (ROOT/directory).rglob('*.py'):
            known.add(code.relative_to(ROOT).as_posix())
    known.update(('app.py','subprocess.py','shutil.py','tempfile.py','threading.py',
                  'pathlib/_local.py','pathlib/_abc.py','ctypes/__init__.py'))
    suffixes=sorted(known,key=len,reverse=True);frames=[]
    for line in text.splitlines():
        match=re.match(r'^\s*File "([^"\r\n]+)", line (\d+)',line)
        if not match:continue
        value=match[1].replace('\\','/')
        source=next((p for p in suffixes if value==p or value.endswith('/'+p)),None)
        if source:frames.append(dict(file=source,line=int(match[2])))
    if frames:details['frames']=frames[-12:]
    return details


def failure_summary(path):
    if not path.is_file():return {'present':False}
    with path.open('rb') as stream:
        stream.seek(max(0,path.stat().st_size-128*1024));text=stream.read(128*1024).decode('utf-8',errors='replace')
    checks={
        'qq_sqlite_runtime_mismatch':('sqlite3_version_not_pinned','sqlite3_dll_outside_python_prefix'),
        'qq_isolated_reader_refused':('isolated QQ SQLite helper',),
        'qq_query_failed':('isolated QQ SQLite query failed',),
        'wechat_no_database_keys':('数据库无可用密钥','数据库密钥提取失败'),
        'client_not_running':('未检测到 Weixin.exe','未检测到 QQ'),
        'source_changing':('database remained active during',),
        'missing_module':('ModuleNotFoundError',),
    }
    codes=[code for code,patterns in checks.items() if any(p in text for p in patterns)]
    details=failure_details(text)
    if details.get('winerror') in (32,33):codes.append('file_in_use')
    elif details.get('winerror')==5 or details.get('errno')==13 or details.get('exception_type')=='PermissionError':codes.append('permission_denied')
    result=dict(present=True,record='last_failure_log_not_latest_attempt',codes=codes or ['unclassified'],at=datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat(),**details)
    match=re.search(r'旧版密钥兼容检查：进程=(\d+)，无法读取=(\d+)，扫描=([\d.]+) MiB，验证通过=(\d+)/(\d+)，达到上限=(True|False)',text)
    if match:result['legacy_scan']=dict(processes=int(match[1]),blocked=int(match[2]),scanned_mib=float(match[3]),verified=int(match[4]),databases=int(match[5]),limited=match[6]=='True')
    return result


def collect(root=ROOT):
    root=Path(root);created=datetime.now(timezone.utc).isoformat()
    report=dict(format='chatweave-support-v1',created_at=created,system=dict(os=platform.system(),release=platform.release(),version=platform.version(),architecture=platform.machine()),
        runtime=dict(python=platform.python_version(),sqlite=sqlite3.sqlite_version,packaged=(root/'runtime/python.exe').is_file()),
        privacy='No credentials, chats, account IDs, file paths, raw logs or memory contents included.')
    if os.name=='nt':report['system']['elevated']=bool(ctypes.windll.shell32.IsUserAnAdmin())
    try:
        with tempfile.TemporaryFile(dir=root) as stream:stream.write(b'probe');stream.flush()
        writable=True
    except OSError:writable=False
    report['storage']=dict(writable=writable,free_mib=shutil.disk_usage(root).free//1024**2,path_length=len(str(root)),unicode_path=not str(root).isascii(),network_path=str(root).startswith('\\\\'))
    try:report['clients']=client_processes()
    except Exception as exc:report['clients_error']=type(exc).__name__
    manifest=root/'build-manifest.json'
    if manifest.is_file():
        try:report['build_version']=json.loads(manifest.read_text(encoding='utf-8')).get('version','unknown')
        except (OSError,ValueError):report['build_version']='unreadable'
    # Hash a fixed code allowlist to distinguish unpatched/mixed installations.
    report['components']={}
    for name in ('chatlocal/wechat_keys.py','tools/wechat-reader/wechatauto/db.py','tools/qq-reader/chatlog_keeper/_qq_sqlite_proxy.py',
                 'scripts/export_qq.py','scripts/qq_message_types.py','tools/qq-reader/chatlog_keeper/_qq_sqlite_helper.py',
                 'scripts/live_reader.py','scripts/live_vfs.py','scripts/live_wechat.py','scripts/reader_media.py',
                 'scripts/qq_labels.py','chatlocal/qq_labels.py',
                 '_internal/qq-live-lock.dll'):
        path=root/name
        report['components'][name]=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    helper=root/'tools/qq-reader/chatlog_keeper/_qq_sqlite_helper.py'
    dedicated=root/'runtime/qq-sqlite/python.exe'
    executable=dedicated if dedicated.is_file() else Path(sys.executable)
    if helper.is_file():
        try:
            result=subprocess.run([str(executable),'-I','-u',str(helper),'--runtime-probe'],capture_output=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            probe=json.loads(result.stdout)
            report['qq_runtime']={k:probe[k] for k in ('ok','error','sqlite_version','isolated','pending_byte') if k in probe}
        except Exception as exc:report['qq_runtime']=dict(ok=False,error=type(exc).__name__)
    else:report['qq_runtime']=dict(ok=False,error='missing_helper')
    report['last_import']={name:failure_summary(root/'reports/private'/('data-'+name+'-error.log')) for name in ('qq','wechat')}
    report['live']={name:live_summary(root/'reports/private/live'/(name+'.jsonl')) for name in ('qq','wechat')}
    # A prior failure log can survive a later successful import. Export the
    # current receipt separately, without scope/IDs/details or any free text.
    receipt=root/'data/read-latest.json'
    if receipt.is_file():
        try:
            if receipt.stat().st_size>1024**2:raise ValueError('receipt too large')
            value=json.loads(receipt.read_text(encoding='utf-8'));latest={}
            for key in ('started_at','finished_at'):
                if isinstance(value.get(key),str):latest[key]=datetime.fromisoformat(value[key]).isoformat()
            if value.get('status') in ('ok','partial','error'):latest['status']=value['status']
            latest['platforms']=[]
            for item in value.get('platforms',[]):
                if item.get('platform') not in ('qq','wechat') or item.get('status') not in ('ok','partial','error'):continue
                row=dict(platform=item['platform'],status=item['status'])
                for key in ('added','duplicate','invalid_timestamps'):
                    if type(item.get(key)) is int and 0<=item[key]<=10**9:row[key]=item[key]
                latest['platforms'].append(row)
            report['latest_read']=latest
        except (OSError,ValueError,TypeError,AttributeError):report['latest_read']=dict(status='receipt_unavailable')
    return report


def live_summary(path):
    """Export only typed aggregates and enumerated codes; never raw JSONL."""
    codes={'source_busy','key_unavailable','new_shard_key_unavailable','account_changed','sequence_reset',
           'shard_removed','conversation_unresolved','schema_changed','reader_failed','decode_failed',
           'worker_timeout','wal_too_large','large_qq_database','qq_live_runtime_missing',
           'qq_live_runtime_mismatch','qq_live_lock_page_invalid'}
    stages={'runtime','cached_keys','open_pages','directory','messages','media_retry','source_fence'}
    if not path.is_file():return dict(present=False)
    rows=[]
    try:
        with path.open('rb') as stream:
            stream.seek(max(0,path.stat().st_size-16384));lines=stream.read(16384).splitlines()
        for line in lines:
            try:value=json.loads(line)
            except (ValueError,UnicodeError):continue
            if not isinstance(value,dict) or value.get('event') not in ('batch','failure'):continue
            row=dict(event=value['event'])
            for key in ('at','seconds','elapsed_seconds','bytes_read','pages_decrypted','databases','candidates','added'):
                if type(value.get(key)) in (int,float) and 0<=value[key]<=10**12:row[key]=value[key]
            if value.get('code') in codes:row['code']=value['code']
            if value.get('stage') in stages:row['stage']=value['stage']
            rows.append(row)
    except OSError:return dict(present=True,error='unreadable')
    return dict(present=True,recent=rows[-10:])


def serve(bind,ready):
    # One explicit local-use address, including campus/shared 100.64/10, never
    # all interfaces/public IPs. No permanent listener or automatic startup.
    address=ipaddress.ip_address(bind)
    if address.version!=4 or not any(address in ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','100.64.0.0/10')):raise ValueError('Private LAN IPv4 required')
    token=secrets.token_urlsafe(24)
    payload=json.dumps(collect(),ensure_ascii=False,indent=2).encode('utf-8')
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path!='/diagnostics' or self.headers.get('Origin') or not hmac.compare_digest(self.headers.get('Authorization','').encode('utf-8'),('Bearer '+token).encode('ascii')):
                self.send_error(403);return
            self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        def log_message(self,*args):pass
    class Server(HTTPServer):
        def get_request(self):
            sock,addr=super().get_request();sock.settimeout(2);return sock,addr
    server=Server((bind,0),Handler);server.timeout=1
    ready=Path(ready).resolve()
    if ready.parent!=(ROOT/'.tmp').resolve() or not re.fullmatch(r'support-[a-f0-9]+\.json',ready.name):raise ValueError('Invalid ready path')
    try:
        staged=ready.with_suffix('.part')
        staged.write_text(json.dumps(dict(url='http://'+bind+':'+str(server.server_port)+'/diagnostics',token=token)),encoding='utf-8')
        os.replace(staged,ready)
        deadline=time.monotonic()+600
        while time.monotonic()<deadline:server.handle_request()
    finally:server.server_close();ready.unlink(missing_ok=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--serve');parser.add_argument('--ready');parser.add_argument('--output');args=parser.parse_args()
    if args.serve:serve(args.serve,args.ready)
    elif args.output:Path(args.output).write_text(json.dumps(collect(),ensure_ascii=False,indent=2),encoding='utf-8')
    else:print(json.dumps(collect(),ensure_ascii=True,indent=2))
