"""Synthetic text/quote/file fixtures prove image opt-out performs no image IO."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts'),str(ROOT/'tools/wechat-reader'),str(ROOT/'tools/qq-reader')]

from PIL import Image
from chatlocal.live import LiveManager
from chatlocal.normalize import normalize
from chatlocal.read_preferences import wechat_images_enabled
from chatlocal.store import Store
from live_media import WeChatMediaRetries
from reader_media import WeChatMedia
import incremental_wechat
import live_reader
import live_wechat
import scoped_wechat


FAIL=AssertionError('Disabled image reader attempted attachment/key/decode IO')
CHAT='fixture@chatroom'
DIGEST='a'*32
QUOTE='<msg><appmsg><title>引用后的文字</title><refermsg><svrid>101</svrid><displayname>测试用户</displayname><content>原文字</content></refermsg></appmsg></msg>'
FILE='<msg><appmsg><type>6</type><title>fixture.txt</title><appattach><totallen>17</totallen><fileext>txt</fileext></appattach></appmsg></msg>'


def blocked_module():
    module=ModuleType('wechatauto.media')
    def forbidden(*args,**kwargs):raise FAIL
    module.MediaDownloader=forbidden
    return module


def check_no_io():
    # No reader image attributes may be consulted when disabled, either.
    class RestrictedReader:
        def __getattr__(self,name):raise FAIL
    with patch.dict(sys.modules,{'wechatauto.media':blocked_module()}), \
         patch.object(Path,'rglob',side_effect=FAIL),patch.object(Path,'glob',side_effect=FAIL), \
         patch.object(Path,'read_bytes',side_effect=FAIL),patch.object(Path,'read_text',side_effect=FAIL), \
         patch.object(Path,'stat',side_effect=FAIL),patch.object(Path,'is_file',side_effect=FAIL), \
         patch('reader_media.os.walk',side_effect=FAIL),patch('reader_media.cache_image',side_effect=FAIL), \
         patch.object(Image,'open',side_effect=FAIL),patch('live_media.sqlite3.connect',side_effect=FAIL):
        for live in (False,True):
            media=WeChatMedia(RestrictedReader(),{'source_db':'/synthetic/db_storage'},live=live,read_images=False)
            assert not hasattr(media,'decoder') and media.key is None and media.xor is None
            image=dict(type_code=3,content=f'<msg><img md5="{DIGEST}"/></msg>',chat=CHAT,create_time=1)
            _,items,_=media.parse(image)
            assert items[0]['kind']=='image' and items[0]['status']=='unavailable'
            assert items[0]['metadata']['md5']==DIGEST and items[0]['reason']=='图片读取已关闭'
            _,items,_=media.parse(dict(image,type_code=47,content=f'<msg><emoji md5="{DIGEST}"/></msg>'))
            assert items[0]['kind']=='sticker' and items[0]['status']=='unavailable'
            media.load_stickers=False
            assert media.parse(dict(image,type_code=47))[1][0]['status']=='omitted'
            assert media.parse(dict(type_code=1,content='正常文字'))==(None,[],None)
            assert media.parse(dict(type_code=49,content=FILE))==(None,[],None)
            text,items,reply=media.parse(dict(type_code=49,content=QUOTE))
            assert text=='引用后的文字' and items==[] and reply['content']=='原文字' and reply['source_id']=='101'
            assert media.current_paths(image,DIGEST)==[]
            assert media.live_image_key(Path('/synthetic/key-probe.dat')) is None
            media.advance_index()
            assert media.index_walk is None
            assert WeChatMediaRetries('/synthetic/never-open.sqlite3').collect(media,'fixture',None)==([],{})


class FixtureReader:
    account='fixture'
    wxid='fixture-self'
    def __init__(self,path):self.path=Path(path)
    def _message_dbs(self):return ['message/message_0.db']
    def _db_path(self,rel):return str(self.path)
    def _open(self,rel):
        db=sqlite3.connect(str(self.path));db.row_factory=sqlite3.Row;return db
    def _build_md5_index(self):return {hashlib.md5(CHAT.encode()).hexdigest():CHAT}
    def _nickname_index(self):return {CHAT:'测试群',self.wxid:'测试用户'}
    def _export_row(self,row,names):
        record=dict(row)
        record['type_code']=record.pop('local_type');record['content']=record.pop('message_content')
        return record


def fixture(folder):
    source=folder/'source/db_storage';(source/'message').mkdir(parents=True)
    path=source/'message/message_0.db';db=sqlite3.connect(str(path))
    table='Msg_'+hashlib.md5(CHAT.encode()).hexdigest()
    db.execute('CREATE TABLE Name2Id(user_name TEXT)')
    db.executemany('INSERT INTO Name2Id VALUES(?)',[('fixture-self',),(CHAT,)])
    db.execute(f'CREATE TABLE "{table}"(local_id INTEGER PRIMARY KEY,local_type INTEGER,server_id INTEGER,real_sender_id INTEGER,create_time INTEGER,message_content TEXT,packed_info_data BLOB,sort_seq INTEGER)')
    records=[(1,'正常文字'),(3,f'<msg><img md5="{DIGEST}"/></msg>'),(47,f'<msg><emoji md5="{DIGEST}"/></msg>'),(49,FILE),(49,QUOTE)]
    db.executemany(f'INSERT INTO "{table}" VALUES(?,?,?,?,?,?,?,?)',[(i,kind,100+i,1,1_700_000_000+i,text,None,i) for i,(kind,text) in enumerate(records,1)])
    db.commit();db.close()
    metadata=dict(account='fixture',source_db=str(source),snapshot_at='2026-01-01T00:00:00+00:00',fingerprint={})
    return FixtureReader(path),metadata


def assert_payload(payload):
    messages=payload['messages']
    assert len(messages)==5
    by_id={m['local_id']:m for m in messages}
    assert by_id[1]['content']=='正常文字'
    assert by_id[2]['media'][0]['status']=='unavailable'
    assert by_id[3]['media'][0]['status']=='unavailable'
    assert by_id[4]['content']==FILE and by_id[4]['media']==[]
    assert by_id[5]['text']=='引用后的文字' and by_id[5]['reply_to']['content']=='原文字'
    normalized={mid:normalize(raw,payload,'wechatauto') for mid,raw in by_id.items()}
    assert all(normalized.values())
    assert normalized[1]['content']=='正常文字'
    assert normalized[4]['content']=='[文件] fixture.txt'
    assert normalized[4]['files'][0]['filename']=='fixture.txt' and normalized[4]['files'][0]['size']==17
    assert normalized[5]['content']=='引用后的文字' and normalized[5]['reply_to']['content']=='原文字'


def check_cold_paths(folder,reader,metadata):
    output=folder/'cold.json';store=Store(folder/'cold-store.sqlite3')
    # Database/receipt IO is allowed; all attachment traversal, reads and image
    # decoding remain blocked. Both exporters must pass the opt-out through.
    with patch.dict(sys.modules,{'wechatauto.media':blocked_module()}), \
         patch.object(Path,'rglob',side_effect=FAIL),patch.object(Path,'read_bytes',side_effect=FAIL), \
         patch('reader_media.os.walk',side_effect=FAIL),patch('reader_media.cache_image',side_effect=FAIL), \
         patch.object(Image,'open',side_effect=FAIL), \
         patch.object(scoped_wechat,'checked_open',side_effect=lambda reader,rel:reader._open(rel)), \
         patch.object(incremental_wechat,'checked_open',side_effect=lambda reader,rel:reader._open(rel)), \
         patch.object(incremental_wechat,'Store',return_value=store):
        scoped_wechat.export_selected(reader,[dict(username=CHAT,name='测试群',message_count=5)],metadata,output,None,{},read_images=False)
        assert_payload(json.loads(output.read_text()))
        payload=incremental_wechat.export_delta(reader,metadata,dict(checkpoint={},bootstrap_since=0,conversations=None),True,read_images=False)
        assert_payload(payload)
        assert all(cursor['last']==5 for cursor in payload['sync_checkpoint']['cursors'].values())


def check_live_path(folder,reader,metadata):
    folder.mkdir();(folder/'wechat-snapshot-info.json').write_text(json.dumps(metadata))
    created=[]
    class EnabledFixtureMedia:
        read_images=True
        load_stickers=True
        def parse(self,message):return None,[],None
        def advance_index(self):pass
    def make_media(reader,metadata,load_stickers=True,*,live=False,read_images=True):
        created.append(read_images)
        if read_images:return EnabledFixtureMedia()
        return WeChatMedia(reader,metadata,load_stickers,live=live,read_images=False)
    worker=live_reader.Reader('wechat')
    fixture_open=reader._open
    worker.open=lambda path,key:fixture_open('message/message_0.db')
    reader._keys={'message/message_0.db':b'synthetic-database-key'}
    with patch.dict(sys.modules,{'wechatauto.media':blocked_module()}), \
         patch.object(live_reader,'DATA',folder),patch.object(live_wechat,'LiveWeChatDB',return_value=reader), \
         patch('reader_media.WeChatMedia',side_effect=make_media), \
         patch.object(Path,'rglob',side_effect=FAIL),patch.object(Path,'read_bytes',side_effect=FAIL), \
         patch('reader_media.os.walk',side_effect=FAIL),patch('reader_media.cache_image',side_effect=FAIL), \
         patch.object(Image,'open',side_effect=FAIL):
        worker.request=dict(checkpoint={},conversations=None,since=0,load_stickers=True,wechat_read_images=False)
        with patch.object(WeChatMediaRetries,'collect',side_effect=FAIL):
            payload,checkpoint=worker.wechat()
        assert_payload(payload)
        assert payload['media_replays']==0
        assert checkpoint['cursors'] and all(cursor['last']==5 for cursor in checkpoint['cursors'].values())
        first=worker.media
        # Toggle on/off without changing worker/account/sticker setting. Each
        # toggle rebuilds the media instance; unchanged flags reuse it.
        worker.request['checkpoint']=checkpoint;worker.request['wechat_read_images']=True
        with patch.object(WeChatMediaRetries,'collect',return_value=([],{})):
            worker.wechat();enabled=worker.media;worker.wechat()
        assert worker.media is enabled and worker.media is not first
        worker.request['wechat_read_images']=False
        with patch.object(WeChatMediaRetries,'collect',side_effect=FAIL):worker.wechat()
        assert worker.media is not first and worker.media is not enabled and not worker.media.read_images
        del worker.request['wechat_read_images']
        with patch.object(WeChatMediaRetries,'collect',return_value=([],{})):worker.wechat()
        assert created==[False,True,False,True]


def check_settings(folder):
    store=Store(folder/'settings.sqlite3');manager=LiveManager(store)
    assert manager.settings['wechat_read_images'] is True and wechat_images_enabled(store) is True
    manager.configure({'wechat_read_images':False})
    assert wechat_images_enabled(store) is False
    assert LiveManager(store).settings['wechat_read_images'] is False
    for value in (0,1,'false',None):
        try:manager.configure({'wechat_read_images':value})
        except ValueError:pass
        else:raise AssertionError('Non-bool image preference accepted')
    assert wechat_images_enabled(store) is False


def main():
    check_no_io()
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='wechat-no-images-') as tmp:
        folder=Path(tmp);reader,metadata=fixture(folder)
        check_settings(folder)
        check_cold_paths(folder,reader,metadata)
        # Cold exporters leave their independently closed connections behind;
        # recreate the fixture reader before testing the worker's _open swap.
        check_live_path(folder/'live',FixtureReader(reader.path),metadata)
    print('PASS: image opt-out skips downloader, key access, attachment IO/decode/retries; cold and live retain text, placeholders, files and quotes; live toggles rebuild media; persistent bool setting. Synthetic only.')


if __name__=='__main__':main()
