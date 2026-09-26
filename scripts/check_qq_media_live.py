"""Late QQ files + scoped manual refresh against isolated real SQLite/cache."""
import io
import json
import sys
import tempfile
import time
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PIL import Image
from chatlocal.store import Store
from chatlocal import media as cache
from chatlocal.media_refresh import refresh,prepare,selection
from chatlocal.data_delete import purge,preview
from reader_media import QQMedia
from live_media import QQMediaRetries
from refresh_media import recover


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='qq-media-check-') as tmp:
        folder=Path(tmp);now=time.time();today=datetime.fromtimestamp(now).strftime('%Y-%m-%d')
        parser=QQMedia(SimpleNamespace(),folder/'client/nt_db',load_stickers=False)
        target=parser.root/'Pic'/datetime.fromtimestamp(now).strftime('%Y-%m')/'Ori'/('a'*32+'.jpg')
        target.parent.mkdir(parents=True)
        image=io.BytesIO();Image.new('RGB',(20,12),'red').save(image,'PNG');png=image.getvalue()
        missing=cache.unavailable('image',md5='a'*32,filename='A'*32+'.jpg')
        omitted=cache.omitted_sticker('sticker',md5='b'*32,filename='face.gif')
        base=dict(platform='qq',conversation_id='test:group:10',conversation='测试群',conversation_type='group',
            sender='甲',sender_id='1',is_self=False,timestamp=int(now)*1000,source_id='100',content='正文',media=[omitted,missing])
        source=folder/'input.json';store=Store(folder/'messages.sqlite3')
        def write(messages):source.write_text(json.dumps(dict(reader='qq-live-database',messages=messages),ensure_ascii=False),'utf-8')
        cp=dict(account='test',cursors={'qq:10':[1,100]});transition=dict(platform='qq',previous={},next=cp,observed_at=now)
        with patch.object(cache,'DATA',folder/'cache'),patch('chatlocal.media_refresh.ingestion_lock',lambda **kw:nullcontext()),patch('chatlocal.media_refresh.message_commit_lock',nullcontext):
            write([base]);assert store.import_file(source,live=transition,load_stickers=False)['imported']==1
            retries=QQMediaRetries(store.path)
            assert retries.collect(parser,'test',None,now=now)[0]==[]
            target.write_bytes(b'incomplete')
            assert retries.collect(parser,'test',None,now=now+16)[0]==[]
            target.write_bytes(png)
            parser.index['a'*32]=[target.with_name('removed.jpg')]
            with patch.object(Path,'rglob',side_effect=AssertionError('No full rescan')):
                assert retries.collect(parser,'test',None,now=now+20)[0]==[]
                rows,_=retries.collect(parser,'test',None,now=now+48)
            assert len(rows)==1 and rows[0]['media'][1]['status']=='available'
            transition['previous']=cp;write(rows)
            result=store.import_file(source,live=transition,load_stickers=False)
            assert result['imported']==0 and result['duplicate']==1
            assert store.message(1)['content']=='正文\n[动画表情]'
            assert store.message(1)['media'][0]['source_index']==1
            with store.connect() as db:
                assert json.loads(db.execute("SELECT checkpoint FROM live_state WHERE platform='qq'").fetchone()[0])==cp
                db.execute('UPDATE messages SET media_json=?',(json.dumps([dict(missing,source_index=1)]),))
            assert QQMediaRetries(store.path).collect(parser,'wrong',None,now=now+60)[0]==[]
            assert QQMediaRetries(store.path).collect(parser,'test',['test:group:other'],now=now+60)[0]==[]
            assert QQMediaRetries(store.path).collect(parser,'test',[],now=now+60)[0]==[]
            assert QQMediaRetries(store.path).collect(parser,'test',None,now=now+90000)[0]==[]
            body=dict(platform='qq',conversations=['test:group:10'],start=today,end=today,load_stickers=False)
            assert prepare(store,dict(body,conversations=['other']))==[]
            assert prepare(store,dict(body,start='2000-01-01',end='2000-01-02'))==[]
            def runner(request,output):
                payload=json.loads(request.read_text('utf-8'))
                output.write_text(json.dumps(recover(payload,parser,'test')),'utf-8')
            result=refresh(store,body,runner=runner)
            assert result['updated']==1 and result['recovered']==1
            assert store.message(1)['media'][0]['source_index']==1
            assert refresh(store,body,runner=runner)['updated']==0
            thumbnail=io.BytesIO();Image.new('RGB',(5,3),'red').save(thumbnail,'PNG')
            small=cache.cache_image(thumbnail.getvalue(),md5='a'*32,filename='photo.jpg',thumbnail=True)
            with store.connect() as db:db.execute('UPDATE messages SET media_json=? WHERE id=1',(json.dumps([dict(small,source_index=1)]),))
            parser.cache={}
            assert refresh(store,body,runner=runner)['recovered']==1
            assert store.message(1)['media'][0]['width']==20
            # Manual switch restores omitted media from archived descriptors.
            animated=io.BytesIO();Image.new('RGB',(8,8),'blue').save(animated,'GIF')
            face=target.with_name('b'*32+'.gif');face.write_bytes(animated.getvalue())
            parser.load_stickers=True;parser.cache={}
            result=refresh(store,dict(body,load_stickers=True),runner=runner)
            assert result['updated']==1 and len(store.message(1)['media'])==2
            assert store.message(1)['content']=='正文'
            # New file originally called .jpg can still be an excluded GIF.
            parser.load_stickers=False;parser.cache={};target.write_bytes(animated.getvalue())
            assert parser.resolve('image','a'*32,'photo.jpg',base['timestamp'])['status']=='omitted'
            parser.cache={};target.write_bytes(png)
            with store.connect() as db:db.execute('UPDATE messages SET media_json=? WHERE id=1',(json.dumps([missing]),))
            def racing_delete(request,output):
                runner(request,output)
                purge(store,body,preview(store,body)['fingerprint'])
            assert refresh(store,body,runner=racing_delete)['updated']==0
            assert store.message(1) is None
    print('PASS: late/partial QQ attachments, stable indices, no full rescan, retry backoff/TTL/scope/account, idempotent manual date/conversation refresh, omitted sticker restoration, GIF opt-out, deletion race and checkpoint preservation. Synthetic only.')


if __name__=='__main__':main()
