"""Standalone, read-only QQ account-discovery support probe (stdlib only).

Use the affected installation's Python and reader, not this checkout's reader.
Only directory/file metadata and specific data-directory settings are inspected.
Never instantiate QQDecryptor, open message databases, extract keys or send QQ.
Workers are bounded so an inaccessible/mapped drive still yields a report.
"""
import argparse
import contextlib
import ctypes
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import traceback

FORMAT = 'tulpa-qq-account-diagnostic-v1'
SOURCE_FILES = ('scripts/list_client_accounts.py', 'chatlocal/client_accounts.py',
                'tools/qq-reader/chatlog_keeper/qq_db.py',
                'tools/qq-reader/chatlog_keeper/core/_paths.py')
LAYOUTS = ('nt_qq/nt_db/nt_msg.db', 'nt_db/nt_msg.db', 'nt_qq/global/nt_db/nt_msg.db')
PUBLIC_PARTS = {'documents', '文档', 'onedrive', 'tencent files', 'tencent', 'qq',
                'nt_qq', 'nt_db', 'global', 'appdata', 'roaming', 'local', 'users',
                'runtime', 'scripts', 'chatlocal', 'tools', 'qq-reader', 'chatlog_keeper',
                'core', 'python.exe', 'qq.exe', 'nt_msg.db', 'nt_msg.db-wal',
                'msg3.0.db', 'msg2.0.db', 'userdatainfo.ini', 'userdata.ini'}


def error_info(exc):
    # Deliberately omit str(exc), source lines, locals, arbitrary paths and logs.
    result = dict(type=type(exc).__name__)
    for key in ('errno', 'winerror'):
        value = getattr(exc, key, None)
        if type(value) is int:
            result[key] = value
    if isinstance(exc, ModuleNotFoundError) and re.fullmatch(r'[a-zA-Z_][\w.]{0,100}', exc.name or ''):
        result['module'] = exc.name
    frames = []
    for frame in traceback.extract_tb(exc.__traceback__):
        path = frame.filename.replace('\\', '/')
        known = next((name for name in (*SOURCE_FILES, 'scripts/diagnose_qq_accounts.py') if path.endswith('/'+name)), None)
        if known:
            frames.append(dict(file=known, line=frame.lineno))
    if frames:
        result['frames'] = frames[-8:]
    return result


class Redactor:
    def __init__(self, root):
        self.root = Path(root)
        self.home = Path.home()
        self.accounts, self.parts = {}, {}

    def account(self, value):
        return self.accounts.setdefault(str(value), '<账号'+str(len(self.accounts)+1)+'>')

    def path(self, value):
        if not value:
            return None
        path = Path(value)
        prefix, parts = '', path.parts
        for base, label in ((self.root, '<TULPA>'), (self.home, '<USERPROFILE>')):
            try:
                parts = path.relative_to(base).parts
                prefix = label
                break
            except ValueError:
                pass
        masked = []
        for part in parts:
            if part.casefold() in PUBLIC_PARTS:
                masked.append(part)
            elif re.fullmatch(r'[A-Za-z]:[\\/]?', part) or part in ('/', '\\'):
                masked.append(part.rstrip('\\/'))
            elif part.isdecimal():
                masked.append(self.account(part))
            else:
                masked.append(self.parts.setdefault(part.casefold(), '<目录'+str(len(self.parts)+1)+'>'))
        return '/'.join(([prefix] if prefix else [])+masked)


def file_state(path):
    try:
        info = path.stat()
        return dict(state='file' if path.is_file() else 'not_file', bytes=info.st_size)
    except FileNotFoundError:
        return dict(state='missing')
    except OSError as exc:
        return dict(state='error', error=error_info(exc))


def name_kind(name):
    if name.isdecimal():return 'numeric_account'
    if name.casefold().startswith('nt_qq_'):return 'nt_qq_prefixed'
    if name.casefold() in ('nt_qq', 'global', 'all users'):return name.casefold()
    return 'non_numeric'


def inspect_directory(path, limit=120):
    """Bounded known layouts only; no recursive search and no database reads."""
    result = dict(state='missing', children=[], databases=[], truncated=False)
    try:
        path.stat()
        if not path.is_dir():
            result['state'] = 'not_directory'
            return result
        result['state'] = 'directory'
        # Detect an override that points at an account folder instead of its parent.
        for layout in LAYOUTS:
            state = file_state(path/layout)
            if state['state'] != 'missing':
                result['databases'].append(dict(owner=str(path), layout=layout, depth=0, **state))
        with os.scandir(path) as entries:
            for index, entry in enumerate(entries):
                if index >= limit:
                    result['truncated'] = True
                    break
                try:
                    if not entry.is_dir(follow_symlinks=False):continue
                    child = Path(entry.path)
                    row = dict(path=str(child), name_kind=name_kind(entry.name), layouts=[])
                    for layout in LAYOUTS:
                        state = file_state(child/layout)
                        if state['state'] != 'missing':
                            row['layouts'].append(dict(layout=layout, **state))
                            result['databases'].append(dict(owner=str(child), layout=layout, depth=1, **state))
                    row['legacy_db'] = any((child/name).is_file() for name in ('Msg3.0.db','Msg2.0.db'))
                    result['children'].append(row)
                except OSError as exc:
                    result['children'].append(dict(path=entry.path, error=error_info(exc)))
    except FileNotFoundError:
        result['state'] = 'missing'
    except OSError as exc:
        result.update(state='error', error=error_info(exc))
    return result


def windows_paths():
    home = Path.home()
    docs = []
    if os.name == 'nt':
        buffer = ctypes.create_unicode_buffer(32768)
        status = ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer)
        if status == 0 and buffer.value:docs.append(('windows_documents', Path(buffer.value)))
    docs += [('home_documents',home/'Documents'), ('home_onedrive_documents',home/'OneDrive/Documents'),
             ('home_onedrive_chinese',home/'OneDrive/文档')]
    roots = []
    override = os.environ.get('CHATLOG_QQ_DATA_ROOT','').strip()
    if override:roots.append(dict(source='environment_override',path=override,stock=True))
    roots += [dict(source=source,path=str(path/'Tencent Files'),stock=True) for source,path in docs]
    for key in ('OneDrive','OneDriveConsumer','OneDriveCommercial'):
        if os.environ.get(key):
            roots += [dict(source=key+'_documents',path=str(Path(os.environ[key])/name/'Tencent Files'),stock=False)
                      for name in ('Documents','文档')]
    drives = []
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
        bits = kernel.GetLogicalDrives()
        for index in range(26):
            if bits & (1<<index):
                drive = chr(65+index)+':\\'
                kind = kernel.GetDriveTypeW(drive)
                drives.append(dict(drive=drive, kind=kind))
                if kind in (2,3,6):
                    roots += [dict(source='drive_root',path=str(Path(drive)/'Tencent Files'),stock=True),
                              dict(source='drive_documents',path=str(Path(drive)/'Documents/Tencent Files'),stock=True)]
    return dict(roots=roots, drives=drives, override_set=bool(override))


def qq_processes():
    if os.name != 'nt':return dict(state='unsupported_os', processes=[])
    command = r'''[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$ErrorActionPreference='Stop'
$rows=@(Get-CimInstance Win32_Process -Filter "Name='QQ.exe' OR Name='QQNT.exe'" | ForEach-Object {
  $version=$null
  if($_.ExecutablePath){try{$version=[System.Diagnostics.FileVersionInfo]::GetVersionInfo($_.ExecutablePath).FileVersion}catch{}}
  $dataPath=$null
  if($_.CommandLine -match '--user-data-dir(?:=|\s+)(?:"([^"]+)"|(\S+))'){
    $dataPath=if($Matches[1]){$Matches[1]}else{$Matches[2]}
  }
  [pscustomobject]@{name=$_.Name;version=$version;exe=$_.ExecutablePath;user_data=$dataPath}
})
ConvertTo-Json -InputObject $rows -Compress'''
    result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],
        capture_output=True, timeout=12, encoding='utf-8', errors='replace', creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:return dict(state='query_failed',processes=[])
    return dict(state='ok',processes=json.loads(result.stdout))


def config_hints(paths):
    """Read only small known directory-settings files, not .env or login files."""
    result = []
    for path in paths[:20]:
        path = Path(path)
        state = file_state(path)
        row = dict(path=str(path), **state, hints=[])
        if state['state'] == 'file' and state['bytes'] <= 65536:
            try:
                raw = path.read_bytes()
                for encoding in ('utf-8-sig','utf-16','gb18030'):
                    try:text = raw.decode(encoding);break
                    except UnicodeError:continue
                else:text = ''
                for line in text.splitlines():
                    match = re.fullmatch(r'\s*(UserDataSavePath|UserDataPath|UserDataDir|DataPath)\s*=\s*(.*?)\s*',line,re.I)
                    if match:
                        value = os.path.expandvars(match[2].strip('"'))
                        if Path(value).is_absolute() and not value.startswith(('\\\\','//')):
                            row['hints'].append(value)
            except OSError as exc:row['error']=error_info(exc)
        result.append(row)
    return result


def installed_reader(root):
    phases = []
    selected, accounts = [], []
    phase = 'import_reader'
    started = time.monotonic()
    def progress(value):print(json.dumps(dict(progress=value)),flush=True)
    progress(phase)
    try:
        sys.path[:0] = [str(root),str(root/'scripts'),str(root/'tools/qq-reader')]
        logging.disable(logging.CRITICAL)
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            from chatlog_keeper import qq_db as qq
            if Path(qq.__file__).resolve() != (root/'tools/qq-reader/chatlog_keeper/qq_db.py').resolve():
                phase='reader_module_outside_installation'
                raise RuntimeError('Wrong reader installation')
            spec = importlib.util.spec_from_file_location('tulpa_account_probe_target',root/'scripts/list_client_accounts.py')
            module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        phases.append(dict(stage=phase,state='ok',seconds=round(time.monotonic()-started,3)))
        # Run the exact installed function with trace wrappers, preserving its
        # arguments/return values. No reader instance or encrypted DB is opened.
        def traced(name, function):
            def run(*args, **kwargs):
                nonlocal phase
                phase = name;progress(phase);at=time.monotonic()
                value = function(*args, **kwargs)
                phases.append(dict(stage=name,state='ok',seconds=round(time.monotonic()-at,3)))
                if name=='find_data_root':selected.append(str(value) if value else None)
                if name=='list_account_databases':accounts.extend(value.keys())
                return value
            return run
        qq.find_qq_data_root = traced('find_data_root',qq.find_qq_data_root)
        qq.find_qq_account_databases = traced('list_account_databases',qq.find_qq_account_databases)
        values = module.discover('qq')
        return dict(state='ok',phase=phase,phases=phases,selected_root=selected[-1] if selected else None,accounts=list(values))
    except Exception as exc:
        return dict(state='error',phase=phase,phases=phases,selected_root=selected[-1] if selected else None,error=error_info(exc))


def worker(stage, root, payload):
    if stage=='paths':return windows_paths()
    if stage=='processes':return qq_processes()
    if stage=='configs':return dict(files=config_hints(payload['paths']))
    if stage=='directory':return inspect_directory(Path(payload['path']))
    if stage=='reader':return installed_reader(root)
    raise ValueError('Unknown diagnostic stage')


def run_worker(stage, root, payload=None, timeout=12):
    started = time.monotonic()
    try:
        result = subprocess.run([sys.executable,'-X','utf8','-B',str(Path(__file__).resolve()),
            '--worker',stage,'--root',str(root)],input=json.dumps(payload or {}),cwd=root,
            env=dict(os.environ,PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1'),capture_output=True,
            encoding='utf-8',errors='replace',timeout=timeout,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        records = []
        for line in result.stdout.splitlines():
            try:records.append(json.loads(line))
            except ValueError:pass
        output = next((row['result'] for row in reversed(records) if isinstance(row,dict) and 'result' in row),None)
        if output is None:return dict(state='worker_failed',exit_code=result.returncode)
        return output
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout or b''
        if isinstance(raw,bytes):raw=raw.decode('utf-8','replace')
        phases = [name for name in ('import_reader','find_data_root','list_account_databases') if '"'+name+'"' in raw]
        return dict(state='timeout',phase=phases[-1] if phases else stage,seconds=round(time.monotonic()-started,2))
    except OSError as exc:
        return dict(state='worker_failed',error=error_info(exc))


def conclusions(report):
    notes = []
    reader = report['installed_detection']
    if reader['state']=='ok':
        if reader['account_count']:
            notes.append(dict(code='accounts_found_now',text='本次安装版检测找到了账号。若界面仍为空，需要核对是否运行了另一份 Tulpa，或界面检测是否超时。'))
        else:
            notes.append(dict(code='account_detection_empty',text='已复现：安装版账号检测返回空列表；问题发生在找目录/识别账号阶段，尚未进入数据库解密。'))
    elif reader['state']=='timeout':
        notes.append(dict(code='account_detection_timeout',text='安装版检测超过 25 秒；查看最后阶段，可能是目录访问或依赖加载卡住，不能视为没有账号。'))
    else:
        notes.append(dict(code='account_detection_error',text='安装版账号检测出错；报告保留阶段、异常类型和代码位置，没有导出异常中的私人路径。'))
    for candidate in report['directories']:
        for db in candidate.get('databases',[]):
            if db['state']!='file' or not db.get('bytes'):continue
            if db['depth']==0:
                notes.append(dict(code='account_folder_as_root',candidate=candidate['path'],text='这个候选目录本身就是账号目录；现有检测要求再向上一层作为数据根目录。'))
            elif db['owner_kind']!='numeric_account':
                notes.append(dict(code='non_numeric_account_directory',candidate=candidate['path'],text='发现含 nt_msg.db 的非纯数字目录；现有 Windows 检测可能会过滤掉它，需要结合完整目录结果确认。'))
            elif db['layout'] not in LAYOUTS[:2]:
                notes.append(dict(code='unrecognized_database_layout',candidate=candidate['path'],text='找到数据库元数据，但目录层级不在当前读取器识别的两种布局中。'))
            elif candidate['path']!=reader.get('selected_root'):
                notes.append(dict(code='database_outside_selected_root',candidate=candidate['path'],text='在安装版未选中的候选目录发现账号数据库；可能存在自定义位置或多个数据根目录。'))
    if any(item.get('state') in ('error','timeout','worker_failed','worker_error','budget_exhausted') for item in report['directories']):
        notes.append(dict(code='directory_probe_incomplete',text='部分目录无法访问或检测超时；未把这些目录判断为空。'))
    if not any(item.get('databases') for item in report['directories']):
        notes.append(dict(code='no_database_in_probed_locations',text='检查过的位置未发现已知 QQ 数据库布局。这不是整盘搜索，不能据此断言电脑没有聊天数据；下一步可提供 QQ 设置中显示的文件存储位置再定点检查。'))
    return notes


def collect(root, extra=None, announce=lambda text:None):
    root = Path(root).resolve()
    redactor = Redactor(root)
    report = dict(format=FORMAT,created_at=datetime.now(timezone.utc).isoformat(),
        privacy='No chats, database contents, keys, API tokens, raw account numbers, usernames, command lines or full private paths. Local reports only; no network upload.',
        system=dict(os=platform.system(),release=platform.release(),version=platform.version(),architecture=platform.machine()),
        runtime=dict(python=platform.python_version(),bits=ctypes.sizeof(ctypes.c_void_p)*8,packaged=(root/'runtime/python.exe').is_file()),
        installation=dict(path=redactor.path(root)),components={})
    if os.name=='nt':report['system']['elevated']=bool(ctypes.windll.shell32.IsUserAnAdmin())
    try:
        manifest=json.loads((root/'build-manifest.json').read_text('utf-8-sig'))
        report['installation']['version']=manifest.get('version') if re.fullmatch(r'[0-9A-Za-z.\-]{1,40}',str(manifest.get('version',''))) else 'unknown'
        report['installation']['edition']=manifest.get('edition') if manifest.get('edition') in ('full','mcp') else 'unknown'
    except (OSError,ValueError):report['installation']['version']='manifest_unavailable'
    for rel in SOURCE_FILES:
        file=root/rel;state=file_state(file)
        if state['state']=='file':
            try:state['sha256']=hashlib.sha256(file.read_bytes()).hexdigest()
            except OSError as exc:state['error']=error_info(exc)
        report['components'][rel]=state
    announce('[1/5] 检查 Windows 路径和 QQ 程序版本…')
    paths=run_worker('paths',root);processes=run_worker('processes',root,timeout=16)
    report['path_probe']={key:value for key,value in paths.items() if key!='roots'}
    process_rows=[];hints=[]
    config_paths=[]
    for name in ('APPDATA','LOCALAPPDATA'):
        if os.environ.get(name):config_paths.append(str(Path(os.environ[name])/'Tencent/QQ/UserDataInfo.ini'))
    for row in processes.get('processes',[]):
        version=re.search(r'\d+(?:\.\d+){1,4}',str(row.get('version','')))
        entry=dict(name=row['name'],version=version[0] if version else None,exe_queryable=bool(row.get('exe')),
                   exe=redactor.path(row.get('exe')),user_data=redactor.path(row.get('user_data')))
        if entry not in process_rows:process_rows.append(entry)
        if row.get('user_data'):hints.append(dict(source='qq_process_user_data',path=row['user_data'],stock=False))
        if row.get('exe'):config_paths.append(str(Path(row['exe']).parent/'UserDataInfo.ini'))
    report['qq_processes']=dict(state=processes.get('state','unknown'),items=process_rows,
        **{key:processes[key] for key in ('error','phase','seconds') if key in processes})
    announce('[2/5] 运行这份 Tulpa 自带的 QQ 账号检测（最多 25 秒）…')
    reader=run_worker('reader',root,timeout=25)
    chosen=reader.pop('selected_root',None)
    found=reader.pop('accounts',[])
    report['installed_detection']=dict(reader,selected_root=redactor.path(chosen),account_count=len(found),accounts=[redactor.account(a) for a in found])
    announce('[3/5] 检查 QQ 数据目录设置线索…')
    configs=run_worker('configs',root,dict(paths=list(dict.fromkeys(config_paths))),timeout=10)
    report['config_probe']={key:value for key,value in configs.items() if key!='files'}
    report['config_hints']=[]
    for row in configs.get('files',[]):
        values=row.pop('hints',[])
        report['config_hints'].append(dict(row,path=redactor.path(row['path']),hints=[redactor.path(p) for p in values]))
        for value in values:
            hints += [dict(source='qq_data_setting',path=value,stock=False),dict(source='qq_data_setting_child',path=str(Path(value)/'Tencent Files'),stock=False)]
    candidates=[];seen=set()
    inputs=([dict(source='manual',path=str(Path(extra).resolve()),stock=False)] if extra else [])
    inputs+=([dict(source='installed_reader_selected',path=chosen,stock=True)] if chosen else [])
    inputs+=paths.get('roots',[])+hints
    for item in inputs:
        key=os.path.normcase(os.path.abspath(item['path']))
        if key in seen:continue
        seen.add(key);candidates.append(item)
    report['directories']=[]
    deadline=time.monotonic()+60
    announce('[4/5] 分别核对候选目录和数据库文件元数据（不读消息）…')
    for item in candidates[:40]:
        path=item['path']
        if time.monotonic()>deadline:
            result=dict(state='budget_exhausted')
        elif path.startswith(('\\\\','//')):
            result=dict(state='network_path_not_probed')
        else:
            result=run_worker('directory',root,dict(path=path),timeout=min(5,max(1,deadline-time.monotonic())))
        for child in result.get('children',[]):child['path']=redactor.path(child['path'])
        for db in result.get('databases',[]):
            db['owner_kind']=name_kind(Path(db['owner']).name)
            db['owner']=redactor.path(db['owner'])
        report['directories'].append(dict(path=redactor.path(path),source=item['source'],stock_candidate=item['stock'],**result))
    report['candidate_limit_reached']=len(candidates)>40
    report['conclusions']=conclusions(report)
    announce('[5/5] 生成脱敏报告…')
    return report


def text_report(report):
    lines=['Tulpa · QQ 账号识别诊断', '', '这份报告只记录目录结构、版本、阶段和错误代码，不含聊天正文或密钥。',
           '账号和私人目录已经替换为 <账号N> / <目录N>，同一占位符表示同一项。', '',
           'Tulpa：'+str(report['installation'].get('version'))+' / '+str(report['installation'].get('edition','unknown')),
           'QQ：'+(', '.join(sorted({p['version'] for p in report['qq_processes']['items'] if p['version']})) or '未取得版本'),
           '安装版检测：'+report['installed_detection']['state']+'；账号数 '+str(report['installed_detection']['account_count']), '', '初步结论：']
    for row in report['conclusions']:lines.append('- ['+row['code']+'] '+row['text'])
    lines += ['', '逐个目录的检测结果：']
    for item in report['directories']:
        lines.append('- '+str(item['path'])+' ['+item['source']+'] '+item['state'])
        for db in item.get('databases',[]):
            lines.append('  '+str(db['owner'])+'/'+db['layout']+'：'+db['state']+'，'+str(db.get('bytes','?'))+' 字节；目录类型 '+db['owner_kind'])
        if item.get('error'):lines.append('  '+json.dumps(item['error'],ensure_ascii=False))
    lines += ['', '完整分阶段数据见同名 JSON；请将 TXT 和 JSON 一起交给开发者。',
              '未扫描整盘、未读取数据库内容、未解密、未改动 QQ / Tulpa 配置、未发送任何消息。']
    return '\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path)
    parser.add_argument('--data-dir',type=Path)
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--open',action='store_true')
    parser.add_argument('--worker',choices=['paths','processes','configs','directory','reader'])
    args=parser.parse_args()
    root=args.root or Path(__file__).resolve().parents[1]
    if args.worker:
        try:result=worker(args.worker,root.resolve(),json.loads(sys.stdin.read() or '{}'))
        except Exception as exc:result=dict(state='worker_error',error=error_info(exc))
        print(json.dumps(dict(result=result),ensure_ascii=True),flush=True)
        return
    report=collect(root,args.data_dir,announce=lambda line:print(line,flush=True))
    output=args.output_dir or root/'reports/private/qq-account-diagnostics'
    output.mkdir(parents=True,exist_ok=True)
    name='QQ账号诊断-'+datetime.now().strftime('%Y%m%d-%H%M%S')
    (output/(name+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    (output/(name+'.txt')).write_text(text_report(report),'utf-8-sig')
    print('诊断完成。请把以下目录中的同名 TXT 和 JSON 发给开发者：\n'+str(output.resolve()),flush=True)
    if args.open and os.name=='nt':os.startfile(str(output.resolve()))


if __name__=='__main__':main()
