"""Local, derived voice evidence. No ASR or audio I/O in ingestion transactions."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from functools import lru_cache

from .config import ROOT,local_path

NOTE='[语音转写] 本地机器转写，可能误识别人名、数字和专有名词；原始语音未改写。'

def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS voice_sources(
        message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
        locator_json TEXT NOT NULL, availability TEXT NOT NULL DEFAULT 'UNKNOWN',
        codec TEXT, audio_sha256 TEXT, duration_ms INTEGER, native_path TEXT,
        wav_path TEXT, profile TEXT, transcript TEXT, language TEXT,
        backend TEXT, model TEXT, status TEXT NOT NULL DEFAULT 'unprepared',
        error TEXT, updated_at REAL);
      CREATE TABLE IF NOT EXISTS voice_transcripts(
        audio_sha256 TEXT NOT NULL, profile TEXT NOT NULL, transcript TEXT NOT NULL,
        language TEXT, backend TEXT, model TEXT, created_at REAL,
        PRIMARY KEY(audio_sha256,profile));
      CREATE TABLE IF NOT EXISTS voice_jobs(
        message_id INTEGER PRIMARY KEY REFERENCES voice_sources(message_id) ON DELETE CASCADE,
        action TEXT NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS voice_settings(id INTEGER PRIMARY KEY CHECK(id=1),strategy TEXT NOT NULL);
      INSERT OR IGNORE INTO voice_settings VALUES(1,'lazy');
    ''')

def register(db,mid,descriptor,*,is_new=False,automatic=False):
    from .voice_metadata import clean
    value=clean(descriptor)
    if not value:return
    db.execute('INSERT OR IGNORE INTO voice_sources(message_id,locator_json,duration_ms) VALUES(?,?,?)',
               (mid,json.dumps(value,ensure_ascii=False),value.get('duration_ms')))
    db.execute('UPDATE voice_sources SET locator_json=? WHERE message_id=?',(json.dumps(value,ensure_ascii=False),mid))
    if is_new and automatic and db.execute('SELECT strategy FROM voice_settings WHERE id=1').fetchone()[0]=='new':
        db.execute("INSERT OR IGNORE INTO voice_jobs VALUES(?,'transcribe','queued',?)",(mid,time.time()))
        db.execute("UPDATE voice_sources SET status='queued' WHERE message_id=? AND status='unprepared'",(mid,))

def public(store,mid):
    with store.connect() as db:row=db.execute('SELECT * FROM voice_sources WHERE message_id=?',(mid,)).fetchone()
    if not row:return None
    item={k:row[k] for k in ('message_id','availability','codec','duration_ms','status','transcript','language','backend','model','error')}
    item['note']=NOTE
    path=local_path(row['wav_path']) if row['wav_path'] else None
    item['playable']=bool(path and path.is_relative_to(store.path.parent/'voice') and path.is_file())
    if item['playable']:item['audio_url']=f'/api/voice/{mid}/audio'
    return item

def evidence(store,mid,limit=4000):
    value=public(store,mid)
    if value:
        value.pop('audio_url',None);value.pop('playable',None)
        if value.get('transcript'):
            text=value['transcript'];value['transcript']=text[:limit];value['truncated']=len(text)>limit
    return value

def reindex(db,mid):
    from .store import tokens
    row=db.execute('SELECT m.content,v.transcript FROM messages m LEFT JOIN voice_sources v ON v.message_id=m.id WHERE m.id=?',(mid,)).fetchone()
    if row:
        text=row[1] or ''
        if text:
            try:
                from opencc import OpenCC
                text+=' '+OpenCC('t2s').convert(text)
            except ImportError:pass  # Optional ASR dependency; text ingestion still works.
        db.execute('DELETE FROM message_fts WHERE rowid=?',(mid,))
        db.execute('INSERT INTO message_fts(rowid,terms) VALUES(?,?)',(mid,tokens(row[0]+' '+text)))

@lru_cache(maxsize=8)
def model_hash(path,size,mtime):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def config():
    from dotenv import dotenv_values
    values=dotenv_values(ROOT/'.env')
    def val(k,d):return os.environ.get(k,values.get(k) or d)
    def number(k,d,lo,hi):
        n=int(val(k,str(d)))
        if not lo<=n<=hi:raise ValueError('语音配置超出允许范围：'+k)
        return n
    language=val('VOICE_LANGUAGE','zh')
    if language not in ('zh','auto','en'):raise ValueError('VOICE_LANGUAGE 必须为 zh、auto 或 en')
    return dict(language=language,model=local_path(val('VOICE_MODEL_PATH','.cache/voice-models/ggml-small-q5_1.bin')),
        executable=local_path(val('VOICE_EXECUTABLE','tools/whispercpp/Release/whisper-cli.exe')),
        threads=number('VOICE_THREADS',4,1,8),timeout=number('VOICE_TIMEOUT_SECONDS',180,15,600),
        max_seconds=number('VOICE_MAX_SECONDS',180,1,600),max_bytes=number('VOICE_MAX_BYTES',16*1024*1024,1024,64*1024*1024))

def profile(cfg):
    p=cfg['model'];s=p.stat()
    exe=cfg['executable'];e=exe.stat()
    return 'whisper.cpp:'+model_hash(str(exe),e.st_size,e.st_mtime_ns)+':decode16k-v1:'+cfg['language']+':'+model_hash(str(p),s.st_size,s.st_mtime_ns)

class VoiceService:
    def __init__(self,store):
        self.store=store;self.root=store.path.parent/'voice';self.root.mkdir(exist_ok=True)
        self.stop=threading.Event();self.wake=threading.Event();self.thread=None
    def start(self):
        if self.thread and self.thread.is_alive():return
        self.thread=threading.Thread(target=self._loop,daemon=True,name='local-voice-worker');self.thread.start()
    def close(self):
        self.stop.set();self.wake.set()
        if self.thread:self.thread.join(timeout=3)
    def _child(self,args,timeout):
        from .voice_process import ChildJob
        process=subprocess.Popen(args,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        job=None
        deadline=time.monotonic()+timeout
        try:
            job=ChildJob(process)
            while True:
                if self.stop.is_set():raise VoiceInterrupted()
                if time.monotonic()>deadline:raise VoiceError('本地语音处理超时，可调整限制后重试。')
                try:
                    process.communicate(timeout=.5)
                    return process.returncode
                except subprocess.TimeoutExpired:pass
        finally:
            if process.poll() is None:process.kill();process.communicate()
            if job:job.close()
    def enqueue(self,mid,action='transcribe',retry=False):
        if action not in ('transcribe','prepare'):raise ValueError('未知语音操作')
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM voice_sources WHERE message_id=?',(mid,)).fetchone()
            if not row:raise ValueError('这条消息没有原生语音')
            if row['status']=='ready' and action=='transcribe' and not retry:
                try:
                    if row['profile']==profile(config()):return public(self.store,mid)
                except (OSError,ValueError):return public(self.store,mid)
            if db.execute("SELECT 1 FROM voice_jobs WHERE message_id=? AND state IN ('running','queued')",(mid,)).fetchone():return public(self.store,mid)
            db.execute('INSERT OR REPLACE INTO voice_jobs VALUES(?,?,?,?)',(mid,action,'queued',time.time()))
            db.execute("UPDATE voice_sources SET status='queued',error=NULL WHERE message_id=?",(mid,))
        self.start();self.wake.set();return public(self.store,mid)
    def wait(self,mid,timeout=210,cancel=None):
        self.enqueue(mid)
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            if cancel is not None and cancel.is_set():break
            result=public(self.store,mid)
            if result is None or result['status'] not in ('queued','running'):return result
            if self.stop.wait(.25):break
        return dict(message_id=mid,status='queued',note='本地转写仍在排队或执行，稍后可再次调用同一工具。')
    def _loop(self):
        # Cross-process ownership also protects CLI/Agent calls from double ASR.
        import msvcrt
        path=self.root/'worker.lock'
        path.touch(exist_ok=True);lock=path.open('r+b')
        try:
            while True:
                try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1);break
                except OSError:
                    if self.stop.wait(2):return
            with self.store.connect() as db:
                db.execute("UPDATE voice_jobs SET state='queued' WHERE state='running'")
                db.execute("UPDATE voice_sources SET status='queued' WHERE message_id IN (SELECT message_id FROM voice_jobs WHERE state='queued')")
            while not self.stop.is_set():
                with self.store.connect() as db:
                    job=db.execute("SELECT * FROM voice_jobs WHERE state='queued' ORDER BY created_at LIMIT 1").fetchone()
                    if job:
                        db.execute("UPDATE voice_jobs SET state='running' WHERE message_id=?",(job['message_id'],))
                        db.execute("UPDATE voice_sources SET status='running',error=NULL WHERE message_id=?",(job['message_id'],))
                if job:
                    interrupted=False
                    try:self._run(job['message_id'],job['action'])
                    except VoiceInterrupted:
                        interrupted=True
                        with self.store.connect() as db:
                            db.execute("UPDATE voice_jobs SET state='queued' WHERE message_id=?",(job['message_id'],))
                            db.execute("UPDATE voice_sources SET status='queued' WHERE message_id=?",(job['message_id'],))
                    except Exception as exc:
                        # Never persist raw paths, native keys or subprocess output in an error.
                        reason=str(exc) if isinstance(exc,VoiceError) else '本地语音处理失败（'+type(exc).__name__+'），可重试；原消息保留。'
                        with self.store.connect() as db:
                            db.execute("UPDATE voice_sources SET status='failed',error=?,updated_at=? WHERE message_id=?",(reason,time.time(),job['message_id']))
                    finally:
                        if not interrupted:
                            with self.store.connect() as db:db.execute('DELETE FROM voice_jobs WHERE message_id=?',(job['message_id'],))
                            cleanup(self.store)
                else:self.wake.wait(2);self.wake.clear()
        finally:lock.close()
    def _run(self,mid,action):
        with self.store.connect() as db:
            row=db.execute('SELECT v.*,m.platform,m.conversation_id,m.source_id FROM voice_sources v JOIN messages m ON m.id=v.message_id WHERE message_id=?',(mid,)).fetchone()
        if not row:return
        row=dict(row);cfg=config();wav=local_path(row['wav_path']) if row['wav_path'] else None
        if not wav or not wav.is_file():
            with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='voice-source-') as temp:
                request=Path(temp)/'request.json';output=Path(temp)/'result.json'
                request.write_text(json.dumps(dict(source=row,cache=str(self.root/'audio'),max_bytes=cfg['max_bytes'],max_seconds=cfg['max_seconds'])),'utf-8')
                code=self._child([sys.executable,str(ROOT/'scripts/voice_source.py'),str(request),str(output)],45)
                if code or not output.is_file():raise VoiceError('本地语音读取器失败，请检查客户端或解码依赖后重试。')
                result=json.loads(output.read_text('utf-8'))
            if result.get('status')=='missing':
                with self.store.connect() as db:db.execute("UPDATE voice_sources SET status='missing',availability='MISSING',error=?,updated_at=? WHERE message_id=?",('原生语音未在本机找到；未下载网络音频。',time.time(),mid))
                return
            if result.get('error'):raise VoiceError(result['error'])
            with self.store.connect() as db:
                db.execute("UPDATE voice_sources SET availability='AVAILABLE',codec=?,audio_sha256=?,duration_ms=?,native_path=?,wav_path=?,updated_at=? WHERE message_id=?",
                    (result['codec'],result['sha256'],result['duration_ms'],result.get('native_path'),result['wav_path'],time.time(),mid))
            row.update(audio_sha256=result['sha256'],duration_ms=result['duration_ms']);wav=local_path(result['wav_path'])
        if row['duration_ms']>cfg['max_seconds']*1000:raise VoiceError('语音超过当前时长限制，未转写。')
        if action=='prepare':
            with self.store.connect() as db:db.execute("UPDATE voice_sources SET status=CASE WHEN transcript IS NULL THEN 'prepared' ELSE 'ready' END,error=NULL WHERE message_id=?",(mid,))
            return
        if not cfg['model'].is_file() or not cfg['executable'].is_file():raise VoiceError('本地 ASR 尚未安装；运行 scripts/setup_voice.py 并安装 requirements-voice.txt。')
        fingerprint=profile(cfg)
        with self.store.connect() as db:cached=db.execute('SELECT * FROM voice_transcripts WHERE audio_sha256=? AND profile=?',(row['audio_sha256'],fingerprint)).fetchone()
        if cached:text,language=cached['transcript'],cached['language']
        else:
            with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='voice-asr-') as temp:
                output=Path(temp)/'transcript'
                code=self._child([str(cfg['executable']),'-m',str(cfg['model']),'-f',str(wav),'-l',cfg['language'],
                    '-t',str(cfg['threads']),'-oj','-of',str(output),'-ng','-np'],cfg['timeout'])
                result=output.with_suffix('.json')
                if code or not result.is_file():raise VoiceError('本地 ASR 执行失败，请检查模型和运行程序。')
                data=json.loads(result.read_text('utf-8'));text=''.join(s.get('text','') for s in data.get('transcription',[])).strip()
                language=data.get('result',{}).get('language','auto')
                if not text:raise VoiceError('未识别出可用语音文字；请试听原音频。')
                if len(text)>24000:raise VoiceError('转写文本超出单条限制。')
        with self.store.connect() as db:
            # A concurrent user deletion must never resurrect a source or its index.
            if not db.execute('SELECT 1 FROM voice_sources WHERE message_id=?',(mid,)).fetchone():return
            db.execute('INSERT OR IGNORE INTO voice_transcripts VALUES(?,?,?,?,?,?,?)',
                (row['audio_sha256'],fingerprint,text,language,'whisper.cpp',cfg['model'].name,time.time()))
            db.execute("UPDATE voice_sources SET profile=?,transcript=?,language=?,backend='whisper.cpp',model=?,status='ready',error=NULL,updated_at=? WHERE message_id=?",
                (fingerprint,text,language,cfg['model'].name,time.time(),mid))
            reindex(db,mid)

class VoiceError(Exception):pass
class VoiceInterrupted(Exception):pass

_services={}
_guard=threading.Lock()
def service(store):
    with _guard:
        key=str(store.path)
        if key not in _services:_services[key]=VoiceService(store)
        return _services[key]

def cleanup(store):
    """Prune only unreferenced derived audio; shared hashes remain reusable."""
    with store.connect() as db:
        db.execute('DELETE FROM voice_transcripts WHERE audio_sha256 NOT IN (SELECT audio_sha256 FROM voice_sources WHERE audio_sha256 IS NOT NULL)')
        keep={r[0] for r in db.execute('SELECT wav_path FROM voice_sources WHERE wav_path IS NOT NULL')}
        busy=db.execute('SELECT 1 FROM voice_jobs LIMIT 1').fetchone()
    if busy:return 0
    saved=0
    for path in (store.path.parent/'voice/audio').glob('*.wav'):
        if str(path.relative_to(ROOT)) not in keep:
            try:size=path.stat().st_size;path.unlink();saved+=size
            except OSError:pass  # Playback may temporarily hold the file on Windows.
    return saved
