"""Isolated read-only native audio locator + bounded decoder. No network requests."""
import hashlib
import io
import json
import re
import struct
import sys
import wave
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tools/qq-reader'),str(ROOT/'tools/wechat-reader')]
from chatlocal.config import DATA,local_path

def locate_qq(locator,source,maximum):
    meta=json.loads((DATA/'qq-snapshot-info.json').read_text('utf-8'))
    if not source['conversation_id'].startswith(meta['account']+':'):raise ValueError('语音不属于当前 QQ 账号。')
    root=(Path(meta['source']).parent/'nt_data/Ptt').resolve()
    name=locator.get('filename','')
    if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,160}',name):raise ValueError('语音文件名无效。')
    for path in root.rglob(name):
        if not path.resolve().is_relative_to(root) or not path.is_file():continue
        if path.stat().st_size>maximum:raise ValueError('语音文件超过大小限制。')
        raw=path.read_bytes()
        if locator.get('md5') and hashlib.md5(raw).hexdigest()!=locator['md5']:continue
        return raw,str(path)
    return None,None

def locate_wechat(locator,source,maximum):
    from wechatauto.db import WeChatDB
    from live_reader import Reader
    meta=json.loads((DATA/'wechat-snapshot-info.json').read_text('utf-8'))
    server=int(source['source_id']) if source['source_id'].isdigit() else int(locator.get('server_id',0))
    if server<=0:raise ValueError('语音尚未取得服务器消息 ID，可稍后重试。')
    # Read authenticated pages from the live DB/WAL. Cached snapshots can be stale,
    # so failure is explicit rather than presenting an old cache as current.
    reader=Reader('wechat');reader.views=[]
    wx=WeChatDB(db_dir=str(Path(meta['source_db']).parent.parent),account=meta['account'],
        workdir=str(DATA/'live/wechat'),keys_file=str(DATA/'wechat-reader/keys.json'))
    if locator.get('account')!=wx.wxid or locator.get('chat')!=source['conversation_id']:
        raise ValueError('语音账号或会话不匹配。')
    try:
        for path in sorted(Path(meta['source_db']).glob('message/media_*.db')):
            rel=path.relative_to(Path(meta['source_db'])).as_posix()
            key=wx._keys.get(rel) or wx._keys.get(rel.replace('/','\\'))
            if not key:raise ValueError('微信语音分片缺少密钥，请更新本地读取密钥后重试。')
            db=reader.open(path,key)
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'VoiceInfo','Name2Id'}<=tables:continue
            row=db.execute('''SELECT length(v.voice_data),v.rowid FROM VoiceInfo v JOIN Name2Id n ON n.rowid=v.chat_name_id
              WHERE n.user_name=? AND v.svr_id=? ORDER BY v.create_time DESC LIMIT 1''',(source['conversation_id'],server)).fetchone()
            if row:
                if row[0]>maximum:raise ValueError('语音文件超过大小限制。')
                raw=db.execute('SELECT voice_data FROM VoiceInfo WHERE rowid=?',(row[1],)).fetchone()[0]
                for view in reader.views:view.view.assert_fresh()
                if raw:return bytes(raw),None
        for view in reader.views:view.view.assert_fresh()
        return None,None
    finally:
        for view in reader.views:view.close()

def decode(raw,maximum_seconds):
    if raw.startswith(b'\x02#!SILK_V3') or raw.startswith(b'#!SILK_V3'):
        start=10 if raw[0]==2 else 9;offset=start;frames=0
        # SILK packet lengths bound CPU/memory before the native decoder runs.
        while offset+2<=len(raw):
            size=struct.unpack_from('<h',raw,offset)[0];offset+=2
            if size<0:break
            if size==0 or offset+size>len(raw):raise ValueError('SILK 数据损坏，保留原消息。')
            frames+=1;offset+=size
            if frames*20>maximum_seconds*1000+1000:raise ValueError('语音超过当前时长限制。')
        import pysilk
        pcm=pysilk.decode(raw,sample_rate=16000)
        if len(pcm)>maximum_seconds*32000:raise ValueError('语音超过当前时长限制。')
        out=io.BytesIO()
        with wave.open(out,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(pcm)
        return 'silk',out.getvalue(),round(len(pcm)/32)
    if raw.startswith(b'RIFF'):
        with wave.open(io.BytesIO(raw)) as wav:
            duration=round(wav.getnframes()/wav.getframerate()*1000)
            if duration>maximum_seconds*1000:raise ValueError('语音超过当前时长限制。')
            if (wav.getnchannels(),wav.getsampwidth(),wav.getframerate())!=(1,2,16000):raise ValueError('当前仅支持 SILK 和 16kHz 单声道 WAV；此格式未转换。')
        return 'wav',raw,duration
    raise ValueError('当前原生音频格式暂不支持；未按扩展名猜测解码。')

def run(request):
    source=request['source'];locator=json.loads(source['locator_json'])
    raw,native=(locate_qq if source['platform']=='qq' else locate_wechat)(locator,source,request['max_bytes'])
    if raw is None:return {'status':'missing'}
    if len(raw)>request['max_bytes']:raise ValueError('语音文件超过大小限制。')
    sha=hashlib.sha256(raw).hexdigest();root=local_path(request['cache']);root.mkdir(parents=True,exist_ok=True)
    path=root/(sha+'.wav')
    if path.is_file():
        with wave.open(str(path)) as w:duration=round(w.getnframes()/w.getframerate()*1000)
        codec='silk' if b'SILK_V3' in raw[:12] else 'wav'
    else:
        codec,wav,duration=decode(raw,request['max_seconds'])
        temp=path.with_suffix('.part');temp.write_bytes(wav);temp.replace(path)
    return dict(codec=codec,sha256=sha,duration_ms=duration,native_path=native,wav_path=str(path.relative_to(ROOT)))

if __name__=='__main__':
    import contextlib
    try:
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            result=run(json.loads(local_path(sys.argv[1]).read_text('utf-8')))
    except ValueError as exc:
        # Only our messages in Chinese are user-facing; third-party parser errors
        # can contain source paths. Never forward them.
        reason=str(exc)
        result={'error':reason if reason.startswith(('语音','SILK 数据','当前','微信语音')) else '语音数据读取失败，可稍后重试。'}
    except Exception:result={'error':'本地语音源暂不可读或读取器依赖缺失，可稍后重试。'}
    local_path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False),'utf-8')
