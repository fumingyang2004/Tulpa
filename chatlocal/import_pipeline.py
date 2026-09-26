"""One visible import, bounded durable batches, short SQLite commits and diagnostics.

No source cursor is advanced until the producer seals the complete export. On a
reader failure, committed batches survive. Ready batches resume verbatim; an
unfinished source scan restarts safely using the existing native-ID deduplication.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from collections import deque
from pathlib import Path
from threading import Thread
from .config import ROOT, local_path
from .reader_batches import atomic_json, MAX_FILE_BYTES, RESERVE_BYTES
REPORTS=ROOT/'reports/private'


def initialize(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS import_jobs(
            id TEXT PRIMARY KEY, request_key TEXT NOT NULL, platform TEXT NOT NULL,
            status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS import_job_resume ON import_jobs(request_key,status);
        CREATE TABLE IF NOT EXISTS import_batches(token TEXT PRIMARY KEY, job TEXT NOT NULL, result TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS import_batch_job ON import_batches(job);
    ''')


STAGES = {'preparing':'准备读取器','snapshot':'制作本次快照','decrypt':'验证并解密数据',
          'strip_header':'准备数据库','inventory':'比较消息标识','reading':'读取消息',
          'waiting_for_commit':'等待当前批次入库','exported':'读取完成，核对入库结果','committing':'写入消息'}


class ImportInterrupted(ValueError):
    def __init__(self, message, result):
        super().__init__(message)
        self.result = result


def totals(store, job):
    result=dict(imported=0,duplicate=0,skipped=0,total=0,batches=0)
    with store.connect() as db:
        for row in db.execute('SELECT result FROM import_batches WHERE job=?',(job,)):
            data=json.loads(row[0])
            for key in ('imported','duplicate','skipped','total'):result[key]+=data.get(key,0)
            result['batches']+=1
    return result


def complete_job(db, job):
    """Commit completion with the final source receipt / sync checkpoint."""
    db.execute("UPDATE import_jobs SET status='completed',updated_at=? WHERE id=?",(time.time(),job))


def mark_complete(store, job):
    with store.connect() as db:complete_job(db,job)


def verify_complete(store, attempt):
    payload=json.loads((attempt/'complete.json').read_text('utf-8'))
    expected=payload.get('batch_count');rows=payload.get('exported_messages')
    if type(expected) is not int or expected<0 or type(rows) is not int or rows<0:
        raise ValueError('读取完成回执不完整；未推进同步进度')
    with store.connect() as db:
        receipts=db.execute('SELECT token,result FROM import_batches WHERE token LIKE ?',(attempt.name+':batch-%',)).fetchall()
    numbers=[int(r['token'].rsplit('-',1)[1]) for r in receipts]
    if sorted(numbers)!=list(range(1,expected+1)) or sum(json.loads(r['result'])['total'] for r in receipts)!=rows:
        raise ValueError('导入批次存在缺口；未推进同步进度，请保留诊断')
    return payload


def run_batches(store, platform, arguments, output, *, progress=None, cancelled=None, automatic=False, key=None, env=None):
    from .sync import message_commit_lock
    label='QQ' if platform=='qq' else '微信'
    identity=json.dumps([platform,key if key is not None else arguments,automatic],sort_keys=True,ensure_ascii=False)
    request_key=hashlib.sha256(identity.encode()).hexdigest()
    with store.connect() as db:
        old=db.execute("SELECT id FROM import_jobs WHERE request_key=? AND status!='completed' ORDER BY created_at DESC LIMIT 1",(request_key,)).fetchone()
        job=old[0] if old else uuid.uuid4().hex
        if not old:db.execute('INSERT INTO import_jobs VALUES(?,?,?,?,?,?)',(job,request_key,platform,'running',time.time(),time.time()))
        else:db.execute("UPDATE import_jobs SET status='running',updated_at=? WHERE id=?",(time.time(),job))
    folder=local_path(store.path.parent/'import-jobs'/job)
    folder.mkdir(parents=True,exist_ok=True)
    log=local_path(REPORTS/'imports'/f'{job}.jsonl')
    log.parent.mkdir(parents=True,exist_ok=True)
    started=time.monotonic();counts=totals(store,job);last_progress={};last_emit=0;process=None

    def diagnostic(event, **data):
        # Deliberate allowlist: no chat bodies, account/group IDs, keys, paths or SQL.
        allowed={'stage','batch','rows','bytes','seconds','returncode','code','error_type','frames','imported','duplicate','skipped','total','batches','attempt','scanned','exported','stages','counters'}
        payload=dict(at=datetime.now(timezone.utc).isoformat(),job=job,platform=platform,event=event,
            **{k:v for k,v in data.items() if k in allowed})
        try:
            with log.open('a',encoding='utf-8') as out:out.write(json.dumps(payload,ensure_ascii=False)+'\n')
        except OSError:pass

    def emit(stage='reading', force=False):
        nonlocal last_emit
        if not force and time.monotonic()-last_emit<1:return
        last_emit=time.monotonic()
        state=dict(type='import_progress',platform=platform,job_id=job,stage=stage,
            elapsed_seconds=round(time.monotonic()-started,1),**counts,
            scanned=last_progress.get('scanned'),exported=last_progress.get('exported'),
            expected=last_progress.get('total'),cancellable=True)
        state['text']=f"{label} · {STAGES.get(stage,'正在处理')} · 已新增 {counts['imported']:,} 条 / 已有 {counts['duplicate']:,} 条 / 跳过 {counts['skipped']:,} 条 · {counts['batches']} 批 · {state['elapsed_seconds']:.0f} 秒"
        if progress:progress(state)

    def drain(attempt):
        nonlocal counts
        for path in sorted(attempt.glob('batch-*.json')):
            if cancelled and cancelled.is_set():raise ValueError('已停止导入')
            if not re.fullmatch(r'batch-\d{8}\.json',path.name) or path.is_symlink() or path.resolve().parent!=attempt.resolve() or path.stat().st_size>MAX_FILE_BYTES:
                raise ValueError('批次文件校验失败')
            if shutil.disk_usage(folder).free<RESERVE_BYTES:raise ValueError('磁盘可用空间不足 512 MiB')
            token=attempt.name+':'+path.stem
            before=time.monotonic();emit('committing',True)
            for retry in range(100):
                try:
                    with message_commit_lock():
                        receipt=store.import_file(path,load_stickers='--no-stickers' not in arguments,
                            discovery=automatic,batch=dict(token=token,job=job,platform=platform))
                    break
                except ValueError as exc:
                    if '正在读取或导入数据' not in str(exc) or retry==99:raise
                    time.sleep(.1)
            diagnostic('batch_committed',batch=path.stem,rows=receipt['total'],bytes=path.stat().st_size,
                seconds=round(time.monotonic()-before,3),imported=receipt['imported'],duplicate=receipt['duplicate'],skipped=receipt['skipped'])
            path.unlink()  # Only after the message + receipt transaction committed.
            counts=totals(store,job)
            emit('committing',True)
            (attempt/'lease').touch()

    try:
        diagnostic('resume' if old else 'start',**counts)
        emit('preparing',True)
        if cancelled and cancelled.is_set():raise ValueError('已停止导入')
        # A sealed export resumes without touching the clients. An interrupted
        # producer's ready chunks are consumed, then its source scan is retried.
        for attempt in sorted(folder.glob('attempt-*')):
            (attempt/'stop').touch()
            drain(attempt)
            if (attempt/'complete.json').is_file():
                payload=verify_complete(store,attempt)
                atomic_json(output,payload)
                for item in attempt.glob('*.next.bin'):shutil.copyfile(item,output.parent/item.name)
                diagnostic('export_resumed',**counts)
                return dict(**counts,job_id=job,payload=payload,resumed=True)
        attempt=folder/('attempt-'+uuid.uuid4().hex);attempt.mkdir()
        (attempt/'lease').touch()
        command=[sys.executable,'-u',str(ROOT/'scripts'/f'export_{platform}.py'),'--refresh',
            '--output',str(attempt/'output.json'),'--batch-dir',str(attempt),*arguments]
        # Incremental inventories and requests live alongside this attempt so
        # a sealed export can resume after an application restart.
        if '--incremental-request' in command:
            pos=command.index('--incremental-request')+1
            shutil.copyfile(command[pos],attempt/'request.json');command[pos]=str(attempt/'request.json')
        stderr=deque(maxlen=160);stdout=deque(maxlen=30)
        process=subprocess.Popen(command,cwd=ROOT,env=dict(os.environ,PYTHONUTF8='1',CHATLOCAL_SYNC_METRICS=str(attempt/'metrics.json'),**(env or {})),
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        def collect(stream, tail):
            for line in stream:tail.append(line[:4096])
        threads=[Thread(target=collect,args=(process.stderr,stderr),daemon=True),Thread(target=collect,args=(process.stdout,stdout),daemon=True)]
        for thread in threads:thread.start()
        diagnostic('reader_started',attempt=attempt.name)
        last_stamp=None;active_at=time.monotonic();stage_name='preparing'
        while True:
            (attempt/'lease').touch()
            if cancelled and cancelled.is_set():raise ValueError('已停止导入')
            status=attempt/'progress.json'
            if status.exists():
                stamp=status.stat().st_mtime_ns
                if stamp!=last_stamp:
                    last_stamp=stamp;active_at=time.monotonic()
                    last_progress=json.loads(status.read_text('utf-8'))
                    new_stage=last_progress.get('stage','reading')
                    if new_stage!=stage_name:diagnostic('stage',stage=new_stage,seconds=round(time.monotonic()-started,3))
                    stage_name=new_stage
            drain(attempt)
            emit(stage_name)
            code=process.poll()
            if code is not None:break
            if time.monotonic()-active_at>600:raise ValueError('当前读取阶段连续 10 分钟无进度，已暂停')
            time.sleep(.15)
        for thread in threads:thread.join(timeout=2)
        drain(attempt)
        diagnostic('reader_exited',returncode=code,seconds=round(time.monotonic()-started,3),**counts)
        try:
            metrics_path=(env or {}).get('CHATLOCAL_SYNC_METRICS',attempt/'metrics.json')
            metrics=json.loads(Path(metrics_path).read_text('utf-8'))
            numeric=lambda values:{k:round(v,3) for k,v in values.items() if re.fullmatch(r'[a-z_]+',k) and type(v) in (int,float)}
            diagnostic('reader_metrics',stages=numeric(metrics.get('stages',{})),counters=numeric(metrics.get('counts',{})))
        except (OSError,ValueError,TypeError,AttributeError):pass
        if code or not (attempt/'complete.json').is_file():
            # Keep the established private error tail for support, plus a
            # structured stage/counter log for correlating the failure.
            error_log=REPORTS/f'data-{platform}-error.log'
            error_log.write_text(''.join(stderr),encoding='utf-8')
            if any('database remained active during' in s for s in stderr):
                raise ValueError('数据库在复制期间更新，请重试')
            lines=[s.split('ValueError:',1)[1].strip() for s in stderr if s.startswith('ValueError:')]
            raise ValueError(lines[-1][:240] if lines else '读取器未完成；请查看本机诊断日志')
        payload=verify_complete(store,attempt)
        atomic_json(output,payload)
        for item in attempt.glob('*.next.bin'):shutil.copyfile(item,output.parent/item.name)
        with store.connect() as db:db.execute("UPDATE import_jobs SET status='exported',updated_at=? WHERE id=?",(time.time(),job))
        diagnostic('export_completed',**counts)
        return dict(**counts,job_id=job,payload=payload)
    except Exception as exc:
        if 'attempt' in locals():(attempt/'stop').touch()
        if process and process.poll() is None:
            process.kill();process.wait(timeout=10)
        counts=totals(store,job)
        with store.connect() as db:db.execute("UPDATE import_jobs SET status='interrupted',updated_at=? WHERE id=?",(time.time(),job))
        diagnostic('interrupted',error_type=type(exc).__name__,code='cancelled' if cancelled and cancelled.is_set() else 'reader_or_commit_failure',
            frames=[dict(file=Path(f.filename).name,line=f.lineno,function=f.name) for f in traceback.extract_tb(exc.__traceback__)[-8:]],**counts)
        detail=str(exc) if isinstance(exc,ValueError) else '读取或写入失败，请检查本机诊断'
        raise ImportInterrupted(f"{detail}；已新增 {counts['imported']} 条保留，再次按相同设置读取可继续。诊断编号 {job[:8]}。",dict(**counts,job_id=job)) from exc
    finally:
        if process:
            if process.poll() is None:process.kill();process.wait(timeout=10)
            process.stdout.close();process.stderr.close()
