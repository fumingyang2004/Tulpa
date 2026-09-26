"""Local event-triggered ingestion. Full refresh is an explicit separate job.

No model calls, client commands, message sending, or client filesystem writes.
Workers read/decrypt only requested pages. Stat changes wake a native-row query.
"""
import json
import hashlib
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import ROOT, DATA, DB
from .sync import message_commit_lock

PLATFORMS=('qq','wechat')
DEFAULT=dict(qq='database',wechat='database',load_stickers=False)
LIVE_SCOPE_KEY=json.dumps(dict(enabled=True,conversations=None),sort_keys=True)
DETAILS={
    'source_busy':'客户端正在写入，正在重试；未推进进度。',
    'key_unavailable':'密钥不可用；请登录客户端并手动刷新。',
    'new_shard_key_unavailable':'发现新分片但密钥不可用，请刷新消息。',
    'account_changed':'账号发生变化，请手动确认账号并补读历史。',
    'sequence_reset':'消息序列变化，请刷新消息并检查同步状态。',
    'shard_removed':'消息分片已移除，正在尝试恢复。',
    'conversation_unresolved':'新会话身份尚未解析，请刷新消息。',
    'schema_changed':'当前数据库结构不兼容；保留现有历史读取路径。',
    'reader_failed':'实时读取未完成，正在重连；也可手动刷新消息。',
    'decode_failed':'新增消息解码失败，未推进进度。',
    'worker_timeout':'实时读取超时，正在重启读取器。',
    'wal_too_large':'WAL 超过实时读取的 64 MB 预算，请手动刷新或等待已设置的自动刷新。',
    'large_qq_database':'QQ 实时读取器版本过旧，请完整覆盖最新补丁并重启。',
    'qq_live_runtime_missing':'QQ 实时兼容组件缺失，请完整覆盖最新补丁并重启。',
    'qq_live_runtime_mismatch':'QQ 实时兼容组件版本不匹配，请完整覆盖最新补丁并重启。',
    'qq_live_lock_page_invalid':'QQ 特殊锁页未通过校验；保留进度，请手动刷新并导出诊断。',
}
STAGES={'runtime':'启动读取器','cached_keys':'校验已缓存密钥','open_pages':'读取数据库页',
        'directory':'读取会话目录','messages':'查询新增消息','media_retry':'恢复近期媒体','source_fence':'校验读取一致性'}


def resume_checkpoint(platform,old,cold,account):
    """Keep live progress when a one-off import changes its own scope.

    Legacy WeChat readers also advanced cursors of excluded conversations.
    Do not mistake those for processed live history when removing the filter.
    Newly included chats use the existing bounded recent-tail bootstrap.
    """
    checkpoint=dict(old)
    if not checkpoint and platform=='wechat' and cold.get('account')==account:
        checkpoint=dict(cold)
    if platform=='wechat' and checkpoint.get('scope'):
        previous_scope=json.loads(checkpoint['scope'])
        wanted=previous_scope.get('conversations')
        if wanted is not None:
            suffixes={'Msg_'+hashlib.md5(cid.encode()).hexdigest() for cid in wanted}
            checkpoint['cursors']={key:value for key,value in checkpoint.get('cursors',{}).items()
                                   if key.rsplit('/',1)[-1] in suffixes}
    return checkpoint


def process_names():
    """Read Windows process names without spawning a shell every poll."""
    import ctypes
    from ctypes import wintypes as w
    class Entry(ctypes.Structure):
        _fields_=[('size',w.DWORD),('usage',w.DWORD),('pid',w.DWORD),('heap',ctypes.c_size_t),
            ('module',w.DWORD),('threads',w.DWORD),('parent',w.DWORD),('priority',w.LONG),
            ('flags',w.DWORD),('exe',w.WCHAR*260)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes=[w.DWORD,w.DWORD];kernel.CreateToolhelp32Snapshot.restype=w.HANDLE
    kernel.Process32FirstW.argtypes=[w.HANDLE,ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes=[w.HANDLE,ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes=[w.HANDLE]
    handle=kernel.CreateToolhelp32Snapshot(2,0)
    if handle in (None,ctypes.c_void_p(-1).value): raise OSError('Process inventory unavailable')
    try:
        entry=Entry();entry.size=ctypes.sizeof(entry);names=set()
        valid=kernel.Process32FirstW(handle,ctypes.byref(entry))
        while valid:
            names.add(entry.exe.lower());valid=kernel.Process32NextW(handle,ctypes.byref(entry))
        return names
    finally: kernel.CloseHandle(handle)


def wake_signature(platform):
    metadata=DATA/f'{platform}-snapshot-info.json'
    meta=json.loads(metadata.read_text(encoding='utf-8'))
    root=Path(meta.get('source',meta.get('source_db','')))
    paths=[root/'nt_msg.db'] if platform=='qq' else sorted((root/'message').glob('message_[0-9]*.db'))
    if not paths: raise FileNotFoundError('No source database')
    signature=[]
    for path in paths:
        for suffix in ('','-wal'):
            target=Path(str(path)+suffix)
            try:
                st=target.stat();signature.append((target.name,st.st_ino,st.st_size,st.st_mtime_ns))
            except FileNotFoundError: signature.append((target.name,None))
        try:
            with open(str(path)+'-shm','rb') as handle:
                signature.append((path.name+'-commit',hashlib.sha256(handle.read(100)).hexdigest()))
        except FileNotFoundError:signature.append((path.name+'-commit',None))
    return meta['account'],tuple(signature)


class Worker:
    def __init__(self, platform):
        self.last_stage='runtime'
        self.process=subprocess.Popen([sys.executable,str(ROOT/'scripts/live_reader.py'),platform],cwd=ROOT,
            env=dict(os.environ,PYTHONUTF8='1'),stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,text=True,encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW)
        self.output=queue.Queue(maxsize=2)
        def receive():
            try:
                for line in self.process.stdout: self.output.put(line)
            finally: self.output.put(None)
        threading.Thread(target=receive,daemon=True,name=f'live-{platform}-ipc').start()
    def read(self, request):
        self.last_stage='runtime'
        deadline=time.monotonic()+40
        self.process.stdin.write(json.dumps(request,ensure_ascii=False)+'\n');self.process.stdin.flush()
        while True:
            try: line=self.output.get(timeout=max(0,deadline-time.monotonic()))
            except queue.Empty: raise TimeoutError('worker_timeout') from None
            if line is None: raise RuntimeError('reader_failed')
            result=json.loads(line)
            if result.get('progress') in STAGES:
                self.last_stage=result['progress']
                continue
            return result
    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=3)
        for stream in (self.process.stdin,self.process.stdout):
            if stream:stream.close()


class LiveManager:
    def __init__(self, store):
        self.store=store
        self.stop_event=threading.Event()
        self.guard=threading.RLock()
        self.states={p:dict(platform=p,status='starting',detail='正在建立实时读取器',metrics={},failures=0) for p in PLATFORMS}
        self.workers={}
        self.threads=[]
        self.settings=self.load_settings()
        self.names=set();self.process_checked=0
        self.revision=0

    def load_settings(self):
        with self.store.connect() as db:
            row=db.execute("SELECT value FROM client_settings WHERE name='live_ingestion'").fetchone()
        saved=json.loads(row[0]) if row else {}
        return {k:saved.get(k,v) for k,v in DEFAULT.items()}

    def configure(self, body):
        value={k:body.get(k,self.settings[k]) for k in DEFAULT}
        if any(value[p] not in ('off','database') for p in PLATFORMS) or type(value['load_stickers']) is not bool:
            raise ValueError('实时模式或表情设置无效')
        with self.guard:
            with self.store.connect() as db:
                db.execute("INSERT OR REPLACE INTO client_settings VALUES('live_ingestion',?)",(json.dumps(value),))
            self.settings=value;self.revision+=1
        return value

    def update(self, p, **values):
        with self.guard:self.states[p].update(values)

    def snapshot(self):
        with self.guard:states=[dict(self.states[p],mode=self.settings[p]) for p in PLATFORMS]
        with self.store.connect() as db:
            persisted={r['platform']:dict(r) for r in db.execute('SELECT platform,last_received,last_commit,last_id,total_added FROM live_state')}
        for state in states:state.update(persisted[state['platform']])
        return dict(platforms=states,settings=dict(self.settings),server_time=time.time())

    def start(self):
        # Isolated stores used by offline checks must never start real readers.
        if self.store.path.resolve()!=DB.resolve():return
        for platform in PLATFORMS:
            thread=threading.Thread(target=self.run,args=(platform,),daemon=True,name='live-'+platform)
            thread.start();self.threads.append(thread)

    def close(self):
        self.stop_event.set()
        with self.guard:workers=list(self.workers.values())
        for worker in workers:worker.close()
        for thread in self.threads:thread.join(timeout=4)

    def client_running(self, p):
        with self.guard:
            if time.monotonic()-self.process_checked>3:
                self.names=process_names();self.process_checked=time.monotonic()
            return bool(self.names & ({'qq.exe'} if p=='qq' else {'weixin.exe','wechat.exe'}))

    def worker(self, p):
        with self.guard:
            if p not in self.workers:self.workers[p]=Worker(p)
            return self.workers[p]

    def reset_worker(self,p):
        with self.guard:worker=self.workers.pop(p,None)
        if worker:worker.close()

    def run(self,p):
        last=None;last_scan=0;failures=0;next_try=0;busy_count=0
        while not self.stop_event.wait(0.75):
            try:
                settings=dict(self.settings);revision=self.revision
                if settings[p]=='off':
                    self.update(p,status='off',detail='本工具的实时监听已关闭；与客户端是否运行无关')
                    self.reset_worker(p);last=None;continue
                if not self.client_running(p):
                    self.update(p,status='client_stopped',detail='客户端未运行；启动并登录后自动恢复')
                    self.reset_worker(p);last=None;continue
                if time.time()<next_try:continue
                try:signature=wake_signature(p)
                except (OSError,ValueError):
                    self.update(p,status='needs_baseline',detail='尚无有效本机来源，请先在聊天数据中读取一次历史。')
                    next_try=time.time()+10;continue
                wake=(signature,revision)
                # Periodic native-row check covers lost filesystem notifications.
                if wake==last and time.monotonic()-last_scan<15:continue
                # Exclusive data deletion/backfill still pauses live work.
                # Cold snapshot/decryption does not hold this shorter lock.
                with message_commit_lock():pass
                with self.store.connect() as db:
                    old=json.loads(db.execute('SELECT checkpoint FROM live_state WHERE platform=?',(p,)).fetchone()[0])
                    cold=json.loads(db.execute('SELECT checkpoint FROM sync_state WHERE platform=?',(p,)).fetchone()[0])
                checkpoint=resume_checkpoint(p,old,cold,signature[0])
                if checkpoint and checkpoint.get('account')!=signature[0]:raise ValueError('account_changed')
                self.update(p,status='receiving',detail='正在检查数据库新增消息')
                read_started=time.time()
                result=self.worker(p).read(dict(checkpoint=checkpoint,conversations=None,
                    since=int(time.time())-300,load_stickers=settings['load_stickers']))
                if not result['ok']:raise ValueError(result['error'])
                # Only a live settings/disable change cancels this batch.
                # One-off historical imports have an independent scope.
                if self.stop_event.is_set() or revision!=self.revision:continue
                transition=dict(platform=p,previous=old,next=dict(result['checkpoint'],scope=LIVE_SCOPE_KEY),observed_at=result['observed_at'])
                payload=result['payload'];count=len(payload['messages'])
                with message_commit_lock():
                    if count:
                        folder=DATA/'live/batches';folder.mkdir(parents=True,exist_ok=True)
                        path=folder/(p+'.json');path.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
                        try:imported=self.store.import_file(path,load_stickers=settings['load_stickers'],live=transition)
                        finally:path.unlink(missing_ok=True)
                        added=imported['imported']
                    else:
                        from .live_state import commit
                        with self.store.connect() as db:
                            from .qq_labels import payload_labels
                            payload_labels(db,payload)
                            commit(db,transition,0,0)
                        added=0
                metrics=dict(result['metrics'],added=added,elapsed_seconds=round(time.time()-read_started,4))
                self.update(p,status='listening',detail='等待部分消息取得服务器 ID；其余会话继续读取' if payload.get('pending_ack') else '数据库监听中；只读取新增消息',metrics=metrics,failures=0)
                self.log(p,'batch',metrics)
                last=None if payload.get('more') else wake
                last_scan=time.monotonic();failures=0;busy_count=0;next_try=time.time()+2 if payload.get('pending_ack') else 0
            except Exception as exc:
                if self.stop_event.is_set():break
                code=str(exc) if isinstance(exc,(ValueError,TimeoutError)) else 'reader_failed'
                busy=code=='source_busy' or '正在读取或导入数据' in code
                if busy:
                    busy_count+=1
                    self.update(p,status='retrying' if busy_count<8 else 'degraded',detail='等待稳定快照或其他导入完成；进度保留，稍后重试。')
                    next_try=time.time()+min(15,1.5*busy_count)
                else:
                    failures+=1
                    stage=getattr(self.workers.get(p),'last_stage','runtime')
                    detail=DETAILS.get(code,DETAILS['reader_failed'])
                    if code=='worker_timeout':detail+=' 停在：'+STAGES.get(stage,'读取数据')+'；进度未推进。'
                    self.update(p,status='degraded',detail=detail,failures=failures)
                    self.log(p,'failure',dict(code=code if code in DETAILS else 'reader_failed',stage=stage))
                    self.reset_worker(p)
                    next_try=time.time()+min(60,2**min(failures,5))

    def log(self, platform,event,metrics):
        # Aggregate diagnostics only. No message text, paths, IDs, or keys.
        folder=ROOT/'reports/private/live';folder.mkdir(parents=True,exist_ok=True)
        path=folder/(platform+'.jsonl')
        try:
            if path.exists() and path.stat().st_size>2*1024*1024:
                old=path.with_suffix('.previous.jsonl');old.unlink(missing_ok=True);path.replace(old)
            with path.open('a',encoding='utf-8') as handle:
                handle.write(json.dumps(dict(at=time.time(),event=event,**metrics))+'\n')
        except OSError:pass

def install_live_routes(app,store,same_origin):
    from fastapi import Request,HTTPException
    manager=LiveManager(store)
    @app.get('/api/live-status')
    def live_status(request:Request):same_origin(request);return manager.snapshot()
    @app.put('/api/live-settings')
    def live_settings(request:Request,body:dict):
        same_origin(request)
        try:return manager.configure(body)
        except ValueError as exc:raise HTTPException(400,str(exc)) from None
    @app.post('/api/live-visible')
    def visible(request:Request,body:dict):
        same_origin(request)
        upper=body.get('through_id')
        if type(upper)is not int or upper<0:raise HTTPException(400,'消息位置无效')
        with store.connect() as db:
            db.execute('UPDATE live_events SET ui_visible_at=? WHERE message_id<=? AND ui_visible_at IS NULL',(time.time(),upper))
        return dict(ok=True)
    return manager
