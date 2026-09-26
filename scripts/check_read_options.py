"""Client-read limits through actual UI callbacks; no client scan or cloud API."""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from chatlocal.read_options import read_options,READ_LIMITS
from chatlocal.store import Store
from chatlocal.agent_sessions import Sessions
from chatlocal.ui import build_app


def main():
    assert read_options(120.0,3000,2000)==dict(qq_days=120,qq_per_chat=3000,wechat_per_chat=2000)
    for bad in (0,3651,True,None,1.5,'invalid'):
        try:read_options(bad,500,500)
        except ValueError:pass
        else:raise AssertionError('Invalid day limit accepted')
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='read-options-') as tmp:
        folder=Path(tmp);store=Store(folder/'chats.sqlite3');sessions=Sessions(folder/'sessions.sqlite3')
        exports={}
        for platform in ('qq','wechat'):
            file=folder/(platform+'-real.json')
            payload=dict(messages=[dict(platform=platform,conversation='测试',conversation_id=platform,sender='甲',
                timestamp='2026-06-23 13:38',content='较早记录',source_id='1',is_self=False)])
            if platform=='qq':payload.update(reader='chatlog-keeper',days=30,per_chat=500,media_window=500)
            file.write_text(json.dumps(payload,ensure_ascii=False),'utf-8');exports[platform]=file
        with patch('chatlocal.qq_storage.release_qq_cache',return_value=None):store.import_file(exports['qq'])
        assert store.latest_qq_coverage()==dict(days=30,per_chat=500,media_window=500)
        # Editing an export that has not been imported must not change status.
        qq=json.loads(exports['qq'].read_text('utf-8'));qq.update(days=120,per_chat=3000,media_window=3000)
        exports['qq'].write_text(json.dumps(qq,ensure_ascii=False),'utf-8')
        assert store.latest_qq_coverage()['days']==30
        with patch('chatlocal.ui.Sessions',return_value=sessions):app=build_app(store)
        fns={fn.api_name:fn.fn for fn in app.fns.values()}
        handler=next(fn for fn in app.fns.values() if fn.api_name=='read_with_limits')
        assert handler.inputs[1].preprocess(1.5)==1.5,'UI must not round invalid day settings silently'
        commands=[];imports=[];actual_import=store.import_file
        def exported(path,**kwargs):
            imports.append(kwargs)
            platform=Path(path).name.split('-')[0]
            return actual_import(exports[platform],**kwargs)
        with patch('chatlocal.ui.subprocess.run',side_effect=lambda command,**kwargs:commands.append(command) or SimpleNamespace(returncode=0)),patch.object(store,'import_file',side_effect=exported),patch('chatlocal.qq_storage.release_qq_cache',return_value=None):
            out=list(fns['read_with_limits'](False,120,3000,2000))
            assert len(commands)==2
            assert commands[0][2:]==['--refresh','--days','120','--per-chat','3000','--no-stickers']
            assert commands[1][2:]==['--refresh','--limit','2000','--no-stickers']
            assert all(k['load_stickers'] is False for k in imports)
            assert '最近 120 天' in out[0][0] and '2000 条原始消息' in out[-1][0]
            assert '回溯 120 天' in out[-1][1] and '最多 3000 条文本' in out[-1][1]
            assert store.latest_qq_coverage()['days']==120
            assert store.stats()[0]['messages']==1
            commands.clear()
            invalid=list(fns['read_with_limits'](True,120,0,500))
            assert 'QQ 每会话条数' in invalid[0][0] and not commands
            list(fns['read_latest_options'](True))
            assert commands[0][2:]==['--refresh','--days','30','--per-chat','500']
            assert commands[1][2:]==['--refresh','--limit','500']
            commands.clear();list(fns['read_latest']())
            assert commands[0][2:]==['--refresh','--days','30','--per-chat','500']
        with patch('chatlocal.ui.subprocess.run',return_value=SimpleNamespace(returncode=1,stderr='ValueError: 导出超过 100 MB')),patch.object(store,'import_file',side_effect=AssertionError('Failed export must not import an old file')):
            failed=list(fns['read_with_limits'](True,120,10000,10000))
            assert '超过单文件 100 MB' in failed[-1][0]
        assert set(READ_LIMITS)=={'qq_days','qq_per_chat','wechat_per_chat'}
        app.close()
    print('PASS: custom UI read limits -> QQ/WeChat subprocess arguments -> import; preflight validation, last successful archive metadata, sticker setting and old endpoints preserved. Synthetic exports; no actual client read or cloud API.')


if __name__=='__main__':main()
