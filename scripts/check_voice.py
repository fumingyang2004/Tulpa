"""Core voice contracts in an isolated store; no client reads or ASR downloads."""
import json
import struct
import subprocess
import sys
import tempfile
import time
import threading
import wave
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'scripts'),str(ROOT/'tools/qq-reader')]
from chatlocal.store import Store
from chatlocal.voice import VoiceService,config,profile,public
from chatlocal.agent_tools import ChatTools
from chatlocal.retrieval import make_plan
from chatlocal.data_delete import preview,purge
from chatlocal.llm import validate_answer
from chatlocal.voice_process import ChildJob
from live_vfs import empty_wal_index
from chatlog_keeper.core._wal import _checksum_bytes

def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='voice-check-') as tmp:
        root=Path(tmp);store=Store(root/'messages.sqlite3');source=root/'input.json'
        rows=[dict(platform=p,conversation_id='c',conversation='测试会话',conversation_type='direct',sender='我',sender_id='me',
            timestamp='2026-09-23 10:00',source_id='voice-1',is_self=True,content='[语音消息]',
            voice=dict(kind='qq_ptt',filename='missing.amr') if p=='qq' else dict(kind='wechat_voice',account='fixture',chat='c',server_id='1')) for p in ('qq','wechat')]
        def put(items,**kw):source.write_text(json.dumps(items,ensure_ascii=False),'utf-8');return store.import_file(source,**kw)
        assert put(rows)['imported']==2 and put(rows)['duplicate']==2
        with store.connect() as db:
            assert db.execute('SELECT count(*) FROM voice_jobs').fetchone()[0]==0
            db.execute("UPDATE voice_settings SET strategy='new'")
        nextrow=dict(rows[0],source_id='voice-2')
        cp=dict(platform='qq',previous={},next=dict(account='fixture'),detail='fixture')
        with patch('chatlocal.voice.service',side_effect=AssertionError('Ingestion must not start ASR')):
            put([nextrow],sync=cp)
            put([nextrow],sync=dict(cp,previous=cp['next']))
            put([dict(rows[1],source_id='history')])
        with store.connect() as db:
            assert db.execute('SELECT count(*) FROM voice_jobs').fetchone()[0]==1
            assert db.execute('SELECT count(*) FROM voice_sources').fetchone()[0]==4
            db.execute('DELETE FROM voice_jobs')
        # Identical audio shares one backend/model-specific transcript across platforms.
        svc=VoiceService(store);audio=svc.root/'audio';audio.mkdir();wav=audio/('a'*64+'.wav')
        with wave.open(str(wav),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0'*32000)
        with patch('chatlocal.voice.profile',return_value='fixture-profile'):
            with store.connect() as db:
                db.execute('INSERT INTO voice_transcripts VALUES(?,?,?,?,?,?,?)',('a'*64,'fixture-profile','明天下午三點在實驗室提交 Router 報告','zh','whisper.cpp','fixture',time.time()))
                db.execute("UPDATE voice_sources SET audio_sha256=?,wav_path=?,duration_ms=1000 WHERE message_id IN (1,2)",('a'*64,str(wav.relative_to(ROOT))))
            with patch.object(svc,'_child',side_effect=AssertionError('Cached audio must not decode/transcribe')):
                svc._run(1,'transcribe');svc._run(2,'transcribe')
        assert public(store,1)['status']=='ready' and public(store,2)['playable']
        assert store.message(1)['content']=='[语音消息]'
        assert put(rows)['duplicate']==2
        plan=make_plan('实验室',start='2026-09-23',end='2026-09-23')
        tools=ChatTools(store,plan)
        found=tools.execute('search_messages',dict(query='实验室'))
        assert set(found['hit_ids'])=={1,2}
        assert all(m.get('voice',{}).get('transcript') for m in found['messages'] if m['id'] in (1,2))
        selected=make_plan('语音',conversations=[json.dumps(['qq','c'])],start='2026-09-23',end='2026-09-23')
        with patch('chatlocal.voice.service',side_effect=AssertionError('Out-of-scope tool must not start ASR')):
            assert 'error' in ChatTools(store,selected).execute('transcribe_voice',dict(message_id=2))
        tiny=ChatTools(store,plan,max_chars=1000)
        with patch('chatlocal.voice.service',side_effect=AssertionError('Budget gate must precede ASR')):
            assert 'error' in tiny.execute('transcribe_voice',dict(message_id=1))
        assert validate_answer({'claims':[{'text':'听到了事实','evidence_ids':[3]}]},[dict(store.message(3),voice={'transcript':None})])['rejected']==1
        # A restart resumes a previously interrupted durable job once.
        recovered=threading.Event()
        class Recover(VoiceService):
            def _run(self,mid,action):
                assert mid==3 and action=='transcribe'
                with self.store.connect() as db:db.execute("UPDATE voice_sources SET status='prepared' WHERE message_id=?",(mid,))
                recovered.set()
        with store.connect() as db:
            db.execute("INSERT INTO voice_jobs VALUES(3,'transcribe','running',?)",(time.time(),))
            db.execute("UPDATE voice_sources SET status='running' WHERE message_id=3")
        recovery=Recover(store);recovery.start();assert recovered.wait(3);recovery.close()
        with store.connect() as db:assert db.execute('SELECT count(*) FROM voice_jobs').fetchone()[0]==0
        # Shared audio survives one source deletion, and disappears with the last.
        for p in ('qq','wechat'):
            body=dict(platform=p,conversations=['c'],start='2026-09-23',end='2026-09-23')
            purge(store,body,preview(store,body)['fingerprint'])
            assert wav.exists()==(p=='qq')
        with store.connect() as db:assert db.execute('SELECT count(*) FROM voice_transcripts').fetchone()[0]==0
        assert put(rows,discovery=True)['imported']==0  # Discovery cannot undo deletion.
        # Verify kernel-enforced native-process cleanup (also applies to force-closing parent).
        process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            job=ChildJob(process);job.close();process.wait(timeout=3)  # Windows job close may report exit code 0.
        finally:
            if process.poll() is None:process.kill();process.wait()
        # Empty WAL compatibility accepts only verified empty headers.
        w=bytearray(32);struct.pack_into('>IIII',w,0,0x377f0682,3007000,4096,0);w[16:24]=b'12345678';struct.pack_into('>II',w,24,*_checksum_bytes(w[:24],big_endian=False))
        h=bytearray(48);struct.pack_into('<I',h,0,3007000);h[12]=1;h[32:40]=w[16:24];struct.pack_into('<II',h,40,*_checksum_bytes(h[:40],big_endian=False))
        wp=root/'wal';sp=root/'shm';wp.write_bytes(w);sp.write_bytes(h+h+b'\0'*4)
        assert empty_wal_index(wp,sp)
        h[16]=1;sp.write_bytes(h+h+b'\0'*4);assert not empty_wal_index(wp,sp)
    print('PASS: metadata-only import, idempotency, new-only queue, shared SHA/model cache, simplified Chinese search, hard scope/budget, voice citation guard, shared-cache deletion, native-process cleanup, strict empty-WAL compatibility. No cloud ASR.')

if __name__=='__main__':main()
