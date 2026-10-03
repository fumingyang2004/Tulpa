"""Manual incremental refresh. A failed platform never rolls back another."""
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from .config import ROOT
from .sync_state import now


@contextmanager
def _file_lock(name):
    import msvcrt
    path=ROOT/'.tmp'/name
    with path.open('a+b') as handle:
        handle.seek(0);handle.write(b'0');handle.flush();handle.seek(0)
        try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise ValueError('正在读取或导入数据，请稍后重试。') from None
        try:yield
        finally:handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)


@contextmanager
def message_commit_lock():
    """Serialize media/archive mutation, message commits, and user deletion."""
    with _file_lock('message-commit.lock'):yield


@contextmanager
def ingestion_lock(*,commit=True):
    # Cold source reading can coexist with a short live commit. Existing
    # historical import/deletion callers keep both locks by default.
    with _file_lock('ingestion.lock'):
        if commit:
            with message_commit_lock():yield
        else:yield


def status(store):
    with store.connect() as db:
        return [dict(r) for r in db.execute('SELECT platform,last_sync_at,last_attempt_at,status,added,detail FROM sync_state ORDER BY platform')]


def sync_messages(store, platforms=None, *, load_stickers=False, progress=None, runner=None, cancelled=None):
    from .import_scope import get_scope
    scope=get_scope(store)
    platforms=[p for p,v in scope.items() if v['enabled']] if platforms is None else platforms
    if not isinstance(platforms,list) or not platforms or any(p not in ('qq','wechat') for p in platforms):raise ValueError('无效平台')
    if type(load_stickers) is not bool:raise ValueError('表情包设置无效')
    sys.path.insert(0,str(ROOT/'scripts')) if str(ROOT/'scripts') not in sys.path else None
    from incremental_common import bootstrap_time
    results=[]
    with ingestion_lock(commit=False):
        for platform in dict.fromkeys(platforms):
            started=time.perf_counter()
            timing={}
            batch_result={}
            if progress:progress(f"正在刷新{'QQ' if platform=='qq' else '微信'}新增消息…")
            with store.connect() as db:
                checkpoint=json.loads(db.execute('SELECT checkpoint FROM sync_state WHERE platform=?',(platform,)).fetchone()[0])
                db.execute("UPDATE sync_state SET status='running',last_attempt_at=?,detail='正在刷新' WHERE platform=?",(now(),platform))
            try:
                if cancelled and cancelled.is_set():raise ValueError('用户已停止刷新')
                if not scope[platform]['enabled']:raise ValueError('此平台未启用读取，请在聊天数据中修改读取范围。')
                from .client_accounts import resolve_account
                account=resolve_account(store,platform,scope[platform].get('account'))
                with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='sync-') as folder:
                    folder=Path(folder);request=folder/'request.json';output=folder/'delta.json'
                    inventory={}
                    if platform=='qq' and checkpoint.get('method')=='native-id-inventory-v1':
                        with store.connect() as db:
                            for name,data in db.execute("SELECT name,data FROM sync_inventory WHERE platform='qq'"):
                                if name not in ('c2c_msg_table','group_msg_table'):raise ValueError('QQ 身份目录表名无效')
                                item=folder/(name+'.previous.bin');item.write_bytes(data);inventory[name]=str(item)
                    bootstrap=not checkpoint or (platform=='qq' and checkpoint.get('method')!='native-id-inventory-v1')
                    request.write_text(json.dumps(dict(checkpoint=checkpoint,
                        conversations=scope[platform]['conversations'],
                        inventory=inventory,bootstrap_since=bootstrap_time(store,platform,full_only=platform=='qq') if bootstrap else 0)),encoding='utf-8')
                    command=[sys.executable,str(ROOT/'scripts'/f'export_{platform}.py'),'--refresh',
                        '--incremental-request',str(request),'--output',str(output),'--account',account]
                    if not load_stickers:command.append('--no-stickers')
                    batch_result={}
                    if runner is None:
                        from .import_pipeline import run_batches
                        read_started=time.perf_counter()
                        for attempt in range(3):
                            timing['attempts']=attempt+1
                            try:
                                batch_result=run_batches(store,platform,
                                    ['--incremental-request',str(request),'--account',account]+([] if load_stickers else ['--no-stickers']),output,
                                    progress=progress,cancelled=cancelled,automatic=True,
                                    key=dict(checkpoint=checkpoint,account=account,conversations=scope[platform]['conversations'],load_stickers=load_stickers),
                                    env={'CHATLOCAL_SYNC_METRICS':str(folder/'metrics.json')})
                                break
                            except ValueError as exc:
                                if attempt==2 or (cancelled and cancelled.is_set()) or '数据库在复制期间更新' not in str(exc):raise
                                if progress:progress(f'客户端正在写入数据库，正在重试快照（{attempt+2}/3）；已提交批次保留。')
                        timing['reader_seconds']=time.perf_counter()-read_started
                    else:
                        for attempt in range(3):
                            read_started=time.perf_counter()
                            result=runner(command,cwd=ROOT,env=dict(os.environ,PYTHONUTF8='1',CHATLOCAL_SYNC_METRICS=str(folder/'metrics.json')),capture_output=True,
                                text=True,encoding='utf-8',errors='replace',timeout=600,creationflags=subprocess.CREATE_NO_WINDOW)
                            timing['reader_seconds']=timing.get('reader_seconds',0)+time.perf_counter()-read_started
                            timing['attempts']=attempt+1
                            try:
                                if (folder/'metrics.json').exists():
                                    timing['reader']=json.loads((folder/'metrics.json').read_text(encoding='utf-8'))
                                    timing.setdefault('attempt_metrics',[]).append(timing['reader'])
                            except (OSError,ValueError):pass
                            busy='数据库在复制期间更新' in result.stderr or 'database remained active during' in result.stderr
                            if not result.returncode or not busy or attempt==2:break
                            if progress:progress(f'客户端正在写入数据库，正在重试快照（{attempt+2}/3）…')
                        if result.returncode:
                            log=ROOT/'reports/private'/f'sync-{platform}-error.log';log.parent.mkdir(parents=True,exist_ok=True)
                            log.write_text(result.stderr,encoding='utf-8')
                            # Reader-authored ValueErrors are actionable. Never send raw
                            # exception traces, paths or key diagnostics to the browser.
                            lines=[line.split('ValueError:',1)[1].strip() for line in result.stderr.splitlines() if line.startswith('ValueError:')]
                            raise ValueError(lines[-1][:240] if lines else '读取失败：请保持客户端登录；可能有新分片缺少密钥或数据库忙。诊断保存在本机。')
                    payload=json.loads(output.read_text(encoding='utf-8'))
                    next_checkpoint=payload['sync_checkpoint']
                    if not isinstance(next_checkpoint,dict) or not next_checkpoint.get('cursors'):raise ValueError('读取器未返回有效同步进度')
                    detail='消息数据库未变化' if payload.get('unchanged') else '新增扫描完成；文件与语音只登记，正文和转写按需读取'
                    if not checkpoint:detail+='；首次接续上次已导入快照（回叠5分钟），更早历史仍按补读范围获取'
                    transition=dict(platform=platform,previous=checkpoint,next=next_checkpoint,detail=detail)
                    if batch_result:transition['added']=batch_result['imported']
                    if platform=='qq' and not payload.get('unchanged'):
                        import hashlib
                        blobs={name:(folder/(name+'.next.bin')).read_bytes() for name in ('c2c_msg_table','group_msg_table')}
                        if any(hashlib.sha256(data).hexdigest()!=next_checkpoint['cursors'][name]['inventory_sha256'] for name,data in blobs.items()):raise ValueError('QQ 新身份目录校验失败')
                        transition['inventory']=blobs
                    if payload.get('unchanged'):
                        from .sync_state import commit_checkpoint
                        from .qq_labels import payload_labels
                        with store.connect() as db:
                            labels_updated=payload_labels(db,payload)
                            commit_checkpoint(db,transition,0)
                            if batch_result:
                                from .import_pipeline import complete_job
                                complete_job(db,batch_result['job_id'])
                        imported=dict(imported=0,duplicate=0,labels_updated=labels_updated)
                    else:
                        import_started=time.perf_counter()
                        # A live batch is short, but wait boundedly if it commits
                        # just as the cold export finishes. Never reread the DB.
                        for commit_attempt in range(30):
                            try:
                                with message_commit_lock():
                                    imported=store.import_file(output,load_stickers=load_stickers,sync=transition,complete_job=batch_result.get('job_id'))
                                break
                            except ValueError as exc:
                                if '正在读取或导入数据' not in str(exc) or commit_attempt==29:raise
                                time.sleep(0.1)
                        timing['import_seconds']=time.perf_counter()-import_started
                    results.append(dict(platform=platform,status='ok',added=imported['imported']+batch_result.get('imported',0),
                        duplicate=imported['duplicate']+batch_result.get('duplicate',0),detail=detail))
            except Exception as exc:
                detail=str(exc) if isinstance(exc,ValueError) else '刷新未完成；进度保留，请重试并检查本机读取器。'
                with store.connect() as db:
                    db.execute("UPDATE sync_state SET status='error',detail=? WHERE platform=?",(detail,platform))
                results.append(dict(platform=platform,status='error',added=getattr(exc,'result',batch_result).get('imported',0),detail=detail))
            timing['total_seconds']=round(time.perf_counter()-started,3)
            results[-1]['timing']=timing
            if progress:
                label='QQ' if platform=='qq' else '微信'
                progress(f"{label} {'新增 '+str(results[-1]['added'])+' 条' if results[-1]['status']=='ok' else '刷新失败'} · {timing['total_seconds']:.1f} 秒")
            report=ROOT/'reports/private'/f'sync-{platform}-latest.json'
            try:
                report.parent.mkdir(parents=True,exist_ok=True)
                report.write_text(json.dumps(dict(at=now(),**results[-1]),ensure_ascii=False,indent=2),encoding='utf-8')
            except OSError:pass  # Diagnostics must never change sync success.
    return results
