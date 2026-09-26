"""Real image bytes + isolated live/import transactions; no client/cloud access."""
import collections
import hashlib
import io
import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PIL import Image
from chatlocal import media as cache
from chatlocal.store import Store
from chatlocal.data_delete import preview,purge
from reader_media import WeChatMedia
from live_media import WeChatMediaRetries


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='wx-media-check-') as tmp:
        folder=Path(tmp);now=time.time()
        media=WeChatMedia.__new__(WeChatMedia)
        media.live=False
        media.root=folder/'client';media.load_stickers=False;media.index=collections.defaultdict(list);media.cache={}
        media.key='synthetic';media.xor=0
        media.decoder=SimpleNamespace(decrypt_image=lambda p,**kw:Path(p).read_bytes())
        chat='test@chatroom';digest='a'*32
        raw=dict(local_id=1,server_id=123,type_code=3,create_time=int(now),content='',
            sender_username='self',sender_name='本人',is_self=True,chat=chat,md5=digest,
            source_db='message/message_0.db',source_table='Msg_'+hashlib.md5(chat.encode()).hexdigest())
        head=dict(reader='wechatauto-replica',wxid='self',identity_method='per-shard',chats=[dict(username=chat,name='测试群')])
        img=io.BytesIO();Image.new('RGB',(20,12),'red').save(img,'PNG')
        png=img.getvalue()
        date=datetime.fromtimestamp(now).strftime('%Y-%m')
        target=media.root/'msg/attach'/hashlib.md5(chat.encode()).hexdigest()/date/'Img'/(digest+'_h.dat')
        target.parent.mkdir(parents=True)
        store=Store(folder/'messages.sqlite3');source=folder/'live.json'
        cp=dict(account='fixture',cursors={'fixture':dict(last=1,signature='x')})
        transition=dict(platform='wechat',previous={},next=cp,observed_at=now)
        with patch.object(cache,'DATA',folder/'cache'):
            _,missing,_=media.parse(raw)
            assert missing[0]['status']=='unavailable'
            source.write_text(json.dumps(dict(head,messages=[dict(raw,text='',media=missing)])),'utf-8')
            assert store.import_file(source,load_stickers=False,live=transition)['imported']==1
            retries=WeChatMediaRetries(store.path)
            assert retries.collect(media,'self',None,now=now)[0]==[]
            target.write_bytes(png) # Attachment arrives AFTER cursor was committed.
            with patch.object(Path,'rglob',side_effect=AssertionError('Do not scan all attachment directories')):
                assert retries.collect(media,'self',None,now=now+1)[0]==[], 'Retry cadence'
                rows,chats=retries.collect(media,'self',None,now=now+16)
            assert len(rows)==1 and rows[0]['media'][0]['status']=='available'
            assert rows[0]['media'][0]['width']==20
            source.write_text(json.dumps(dict(head,messages=rows)),'utf-8')
            transition['previous']=cp
            result=store.import_file(source,load_stickers=False,live=transition)
            assert result['imported']==0 and result['duplicate']==1
            assert store.message(1)['media'][0]['status']=='available'
            assert retries.collect(media,'self',None,now=now+40)[0]==[]
            with store.connect() as db:
                assert db.execute('SELECT count(*) FROM messages').fetchone()[0]==1
                assert json.loads(db.execute("SELECT checkpoint FROM live_state WHERE platform='wechat'").fetchone()[0])==cp
                # Simulate another incomplete attachment record for isolation tests.
                db.execute('UPDATE messages SET media_json=? WHERE id=1',(json.dumps(missing),))
            assert WeChatMediaRetries(store.path).collect(media,'other-account',None,now=now+40)[0]==[]
            assert WeChatMediaRetries(store.path).collect(media,'self',['other@chatroom'],now=now+40)[0]==[]
            assert WeChatMediaRetries(store.path).collect(media,'self',[],now=now+40)[0]==[]
            # In-flight media recovery cannot restore a user's deleted message.
            rows,_=WeChatMediaRetries(store.path).collect(media,'self',None,now=now+40)
            assert len(rows)==1
            today=datetime.fromtimestamp(now).strftime('%Y-%m-%d')
            scope=dict(platform='wechat',conversations=[chat],start=today,end=today)
            purge(store,scope,preview(store,scope)['fingerprint'])
            source.write_text(json.dumps(dict(head,messages=rows)),'utf-8')
            assert store.import_file(source,load_stickers=False,live=transition)['imported']==0
            assert store.message(1) is None
            # Corrupt/in-progress files and stale index entries must not poison
            # the parser, nor prevent a later complete file being retried.
            media.cache={};media.index[digest]=[target.with_name('removed.dat')]
            target.write_bytes(b'incomplete')
            assert media.parse(raw)[1][0]['status']=='unavailable'
            target.write_bytes(png)
            assert media.parse(raw)[1][0]['status']=='available'
            animation=io.BytesIO();a=Image.new('RGB',(10,10),'red');b=Image.new('RGB',(10,10),'blue')
            a.save(animation,'GIF',save_all=True,append_images=[b],duration=100,loop=0)
            media.cache={};target.write_bytes(animation.getvalue())
            assert media.parse(raw)[1][0]['status']=='omitted', 'GIF stays excluded with stickers off'
            with patch.object(media,'current_paths',side_effect=AssertionError('Known stickers must skip lookup')):
                assert media.parse(dict(raw,type_code=47))[1][0]['status']=='omitted'
    print('PASS: post-start attachment discovery, delayed/partial file recovery, no full rescan, retry cadence, scope/account isolation, same-ID enrichment, unchanged cursor, deletion protection and sticker opt-out. Synthetic only.')


if __name__=='__main__':main()
