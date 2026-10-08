"""QQ reply/mixed-content regression: real decoder, synthetic rows; no client/API."""
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tools/qq-reader')]
os.environ['CHATLOG_KEEPER_DATA_DIR']=str(ROOT/'data/qq-reader')
import chatlog_keeper.qq_db as q
from incremental_qq import delta_rows, METHOD
from qq_message_types import SUPPORTED_SQL, supported
from reader_media import QQMedia
from live_reader import Reader


def varint(n):
    out=bytearray()
    while n>127:out.append((n&127)|128);n>>=7
    out.append(n)
    return bytes(out)


def field(n,body):
    if isinstance(body,str):body=body.encode('utf-8')
    return varint(n*8+2)+varint(len(body))+body


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='qq-reply-check-') as temp:
        folder=Path(temp)
        text=lambda s:field(q._NTQQ_MSG_TEXT_PRIMARY,s)
        quote=b''.join(field(q._NTQQ_MSG_REPLY_CONTAINER,text(s)) for s in ('管理课程主页的同学忙完会发','@同学','  '))
        blob=field(q._NTQQ_MSG_OUTER_WRAPPER,quote+text('已更新'))
        parser=QQMedia(q,folder/'nt_db',load_stickers=False)
        body,media,reply=parser.parse(blob,33)
        assert body=='已更新' and media==[] and reply=={'content':'管理课程主页的同学忙完会发 @同学'}
        assert parser.parse(field(q._NTQQ_MSG_OUTER_WRAPPER,text('第一段')+text('第二段')),17)[0]=='第一段 第二段'
        assert parser.parse(text('普通文本'),1)[2] is None
        assert parser.parse(field(q._NTQQ_MSG_REPLY_CONTAINER,b''),33)[2]['content']=='[引用消息正文不可用]'
        db=sqlite3.connect(':memory:')
        db.execute('CREATE TABLE qq("40001" INTEGER PRIMARY KEY,"40003" INT,"40050" INT,"40090" TEXT,"40033" INT,"40800" BLOB,"40021" TEXT,"40020" TEXT,"40011" INT,"40012" INT,"40027" INT)')
        def put(uid,seq,ts,peer,kind,subtype,content):
            db.execute('INSERT INTO qq VALUES(?,?,?,?,?,?,?,?,?,?,?)',(uid,seq,ts,'uid',123,content,peer,'uid',kind,subtype,int(peer)))
        put(100,1,1000,'10',2,1,text('旧消息'))
        put(110,2,1100,'10',9,33,blob)
        put(120,0,900,'10',9,33,blob)  # Outside retained date range.
        put(130,1,1200,'20',9,33,blob) # Outside selected conversation.
        spec=SimpleNamespace(table='qq',conversation_column='40021',table_rank=1,conversation_type='group')
        reqpath=folder/'request.json'
        rows,cursor=delta_rows(db,spec,dict(checkpoint={},bootstrap_since=0),reqpath)
        assert {r[0] for r in rows}=={100,110,120,130}
        request=dict(checkpoint=dict(method=METHOD,account='test',cursors={'qq':cursor}),
            inventory={'qq':str(folder/'qq.next.bin')},conversations=['test:group:10'])
        put(50,3,1150,'10',9,33,blob) # New UID is smaller; still included once.
        rows,cursor=delta_rows(db,spec,request,reqpath)
        assert [r[0] for r in rows]==[50], 'New reply accepted even with smaller UID; preserve conversation scope'
        request['checkpoint'].update(cursors={'qq':cursor})
        rows,_=delta_rows(db,spec,request,reqpath)
        assert list(rows)==[], 'Repeated incremental read is idempotent; old skipped IDs require scoped historical reread'
        # Historical selection uses the same SQL predicate.
        assert {r[0] for r in db.execute(f'SELECT "40001" FROM qq WHERE {SUPPORTED_SQL}')}=={50,100,110,120,130}
        put(140,4,1400,'10',9,33,text('非引用系统事件'))
        # One native envelope contains both text and photos. The envelope must
        # survive the type filter even when animated stickers are disabled.
        picture=field(q._NTQQ_MSG_IMAGE_FILENAME,'photo.jpg')+field(q._NTQQ_MSG_FILE_MD5_BIN,b'p'*16)
        sticker=field(q._NTQQ_MSG_IMAGE_FILENAME,'face.gif')+field(q._NTQQ_MSG_FACE_MD5,b'f'*16)
        mixed=field(q._NTQQ_MSG_OUTER_WRAPPER,text('租房介绍')+picture)
        with_face=mixed+field(q._NTQQ_MSG_OUTER_WRAPPER,sticker)
        put(150,5,1500,'10',2,3,mixed)
        put(160,6,1600,'10',2,19,with_face)
        put(170,7,1700,'10',2,4096,field(q._NTQQ_MSG_OUTER_WRAPPER,sticker))
        put(180,8,1800,'10',9,99,text('不支持的系统事件'))
        # Same acceptance rule for historical SQL, incremental SQL and live Python.
        selected={r[0] for r in db.execute(f'SELECT "40001" FROM qq WHERE {SUPPORTED_SQL}')}
        assert selected=={r[0] for r in db.execute('SELECT "40001","40011","40012" FROM qq') if supported(r[1],r[2])}
        rows,cursor=delta_rows(db,spec,request,reqpath)
        assert {r[0] for r in rows}=={140,150,160,170}
        request['checkpoint']['cursors']={'qq':cursor}
        assert list(delta_rows(db,spec,request,reqpath)[0])==[]
        (folder/'qq-snapshot-info.json').write_text(json.dumps(dict(account='test',source=str(folder))),'utf-8')
        reader=Reader('qq');reader.open=lambda *a:db
        reader.maps_at=time.monotonic();reader.buddy={};reader.group={};reader.names={10:'测试群'};reader.own='self'
        reader.label_status={}  # Fixture bypasses qq_maps(), which normally sets this field.
        reader.media=parser;reader.media_at=time.monotonic();reader.media_stickers=False
        # Never consult the personal database for background media retries.
        from chatlocal.store import Store
        from live_media import QQMediaRetries
        reader.media_retries=QQMediaRetries(Store(folder/'retry.sqlite3').path)
        reader.request=dict(checkpoint=dict(account='test',cursors={'qq:10':[1,100]}),
            conversations=['test:group:10'],since=0,load_stickers=False)
        with patch('live_reader.DATA',folder),patch.object(q,'load_cached_key_for_account',return_value='test'),patch.object(q,'_QQ_MESSAGE_TABLE_SPECS',[spec]):
            payload,checkpoint=reader.qq()
            messages=payload['messages']
            assert [r['source_id'] for r in messages]==['110','50','150','160','170']
            assert all(r['content']=='已更新' and r['reply_to']==reply for r in messages[:2])
            assert messages[2]['content']=='租房介绍' and len(messages[2]['media'])==1
            assert messages[2]['media'][0]['kind']=='image' and messages[2]['media'][0]['status']!='omitted'
            assert messages[3]['content']=='租房介绍' and len(messages[3]['media'])==2
            assert messages[3]['media'][0]['status']!='omitted' and messages[3]['media'][1]['status']=='omitted'
            assert messages[4]['media'][0]['status']=='omitted'
            assert checkpoint['cursors']['qq:10']==[8,180], 'Unsupported system events do not block cursor'
            reader.request['checkpoint']=checkpoint
            assert reader.qq()[0]['messages']==[]
        db.close()
    print('PASS: quotes, mixed text/photos, per-element sticker opt-out, shared historical/incremental/live types, scoped increment with decreasing UID, system-event skip, repeated-read idempotence. Synthetic only.')


if __name__=='__main__':main()
