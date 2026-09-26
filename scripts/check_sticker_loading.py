"""Sticker opt-out: real media bytes, import round trip, tools/routes and UI wiring."""
import collections
import io
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'scripts'))
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
from chatlocal import media as media_module
from chatlocal.media import cache_image,STICKER_PLACEHOLDER
from chatlocal.store import Store
from chatlocal.agent_tools import ChatTools
from chatlocal.agent_sessions import Sessions
from chatlocal.chat_view import install_chat_routes
from chatlocal.retrieval import make_plan
from chatlocal.ui import build_app
from chatlocal.vision import VisionProvider
from reader_media import QQMedia,WeChatMedia


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='sticker-check-') as tmp:
        folder=Path(tmp)
        with patch.object(media_module,'DATA',folder):
            frames=[Image.new('RGB',(20,20),c) for c in ('red','blue')]
            gif=io.BytesIO();frames[0].save(gif,format='GIF',save_all=True,append_images=frames[1:],duration=100,loop=0)
            png=io.BytesIO();frames[0].save(png,format='PNG')
            with patch.object(Path,'write_bytes',side_effect=AssertionError('Skipped media must not be cached')):
                assert cache_image(gif.getvalue(),'image',load_stickers=False)['status']=='omitted'
            still=cache_image(png.getvalue())
            animation=cache_image(gif.getvalue(),'image')
            sticker=dict(still,kind='sticker')
            # Reader skips known WeChat emojis before any resource lookup/decryption.
            wx=WeChatMedia.__new__(WeChatMedia);wx.load_stickers=False
            assert wx.parse({'type_code':47,'content':'<msg><emoji md5="x"/></msg>'})[1][0]['status']=='omitted'
            # QQ disguised .jpg must be inspected by actual bytes, not extension.
            disguised=folder/'disguised.jpg';disguised.write_bytes(gif.getvalue())
            constants=['IMAGE_FILENAME','FILE_MD5_BIN','IMAGE_WIDTH','IMAGE_HEIGHT','FACE_MD5','FACE_HASH','TEXT_PRIMARY','OUTER_WRAPPER','REPLY_CONTAINER']
            q=SimpleNamespace(**{'_NTQQ_MSG_'+k:i for i,k in enumerate(constants)})
            q._proto_parse=lambda blob:{0:[('bytes',b'disguised.jpg')],2:[('varint',20)],3:[('varint',20)]}
            qq=QQMedia.__new__(QQMedia);qq.q=q;qq.load_stickers=False;qq.cache={}
            qq.index=collections.defaultdict(list,{'disguised.jpg':[disguised]})
            assert qq.parse(b'fixture',2)[1][0]['status']=='omitted'
            qq.cache={}
            with patch.object(Path,'read_bytes',side_effect=AssertionError('Known QQ sticker should skip bytes')):
                assert qq.parse(b'fixture',4096)[1][0]['status']=='omitted'

            base=dict(platform='qq',conversation_id='test',conversation='测试群',sender='甲',sender_id='a',timestamp='2026-09-21 09:00',is_self=False,content='')
            rows=[dict(base,source_id='mixed',content='保留正文',media=[sticker,still]),
                  dict(base,source_id='gif',media=[dict(animation,kind='image',animated=False,format='jpeg')]),
                  dict(base,platform='wechat',source_id='wx-sticker',media=[sticker]),
                  dict(base,source_id='missing-image',media=[dict(kind='image')]),
                  dict(base,source_id='literal',content='[动画表情]',media=[]),
                  dict(base,media=[animation])]
            source=folder/'full.json';source.write_text(json.dumps(rows,ensure_ascii=False),'utf-8')
            store=Store(folder/'test.sqlite3')
            assert store.import_file(source)['imported']==6
            old_paths={p:p.read_bytes() for p in (folder/'media').rglob('*') if p.is_file()}
            with patch.object(VisionProvider,'inspect',side_effect=AssertionError('Import must never call vision')):
                result=store.import_file(source,load_stickers=False)
            assert result['imported']==0 and result['sticker_placeholders']==4
            assert store.message(1)['content']=='保留正文\n'+STICKER_PLACEHOLDER
            assert len(store.message(1)['media'])==1 and store.message(1)['media'][0]['source_index']==1
            assert store.message(2)['content']==store.message(3)['content']==STICKER_PLACEHOLDER
            assert store.message(2)['media']==store.message(3)['media']==[]
            assert 'original_content' not in store.message(1)
            assert store.message(4)['media'][0]['status']=='unavailable'
            assert store.message(5)['content']==STICKER_PLACEHOLDER
            assert all(path.read_bytes()==data for path,data in old_paths.items())
            with store.connect() as db:
                assert len(db.execute("SELECT rowid FROM message_fts WHERE message_fts MATCH '动画'").fetchall())==5
            tools=ChatTools(store,make_plan('看看表情',platforms=['qq','wechat']))
            assert tools.execute('get_media',{'message_id':2})['media']==[]
            with patch.object(VisionProvider,'inspect',side_effect=AssertionError('Omitted media must not reach vision')):
                assert tools.execute('inspect_image',{'message_id':2}).get('error')
            app=FastAPI()
            with patch('chatlocal.chat_view.Store',return_value=store):install_chat_routes(app)
            client=TestClient(app)
            assert client.get('/media/1/0').status_code==404,'Old sticker URL must never show shifted ordinary image'
            assert client.get('/media/1/1').status_code==200
            page=client.get('/api/chat-context',params={'anchor_message_id':2}).json()
            row=next(m for m in page['messages'] if m['id']==2)
            assert row['content']==STICKER_PLACEHOLDER and row['media']==[]
            assert store.import_file(source,load_stickers=False)['imported']==0
            assert store.import_file(source,load_stickers=True)['imported']==0
            assert store.message(1)['content']=='保留正文' and len(store.message(1)['media'])==2
            assert store.message(2)['content']=='' and store.message(2)['media'][0]['kind']=='gif'
            assert client.get('/media/1/0').status_code==200
            assert sum(r['sticker_placeholders'] for r in store.stats())==0
            lost_source=folder/'lost-cache.json'
            lost_source.write_text(json.dumps([dict(rows[1],media=[{'kind':'image','status':'unavailable'}])]),'utf-8')
            assert store.import_file(lost_source,load_stickers=False)['sticker_placeholders']==1
            assert store.message(2)['content']==STICKER_PLACEHOLDER and not store.message(2)['media']
            store.import_file(source)
            conflict=folder/'bad.json';conflict.write_text(json.dumps([dict(rows[0],content='different')]),'utf-8')
            try:store.import_file(conflict,load_stickers=False)
            except ValueError:pass
            else:raise AssertionError('Source ID conflict guard was weakened')

            # A no-media export retains an explicit omission marker. Loading it
            # with the default option must not fabricate a missing attachment.
            omitted_source=folder/'omitted.json'
            omitted_source.write_text(json.dumps([dict(base,source_id='reader-skip',media=[{'kind':'sticker','status':'omitted'}])]),'utf-8')
            assert store.import_file(omitted_source)['sticker_placeholders']==1

            sessions=Sessions(folder/'sessions.sqlite3')
            with patch('chatlocal.ui.Sessions',return_value=sessions):ui=build_app(store)
            fns={fn.api_name:fn.fn for fn in ui.fns.values()}
            assert '请填写' in fns['import_local'](None,'自动识别',None,None)[0]
            out=fns['import_local_options'](str(source),'自动识别',None,None,False)
            assert json.loads(out[0])[0]['sticker_placeholders']==4
            assert '表情占位' in out[1]
            commands=[];actual_import=store.import_file
            def exported(path,**kwargs):return actual_import(source,**kwargs)
            with patch('chatlocal.ui.subprocess.run',side_effect=lambda command,**kw:commands.append(command) or SimpleNamespace(returncode=0)),patch.object(store,'import_file',side_effect=exported):
                updates=list(fns['read_latest_options'](False))
                assert len(commands)==2 and all('--no-stickers' in c for c in commands)
                assert '[动画表情]' in updates[-1][0]
                commands.clear();list(fns['read_latest']())
                assert len(commands)==2 and all('--no-stickers' not in c for c in commands)
            ui.close()
    print('PASS: no-copy sticker/GIF readers, ordinary images, placeholder+FTS, on/off dedup restore, source conflict checks, stable media URLs, tools/context, UI callbacks and legacy endpoints. No cloud API.')


if __name__=='__main__':main()
