"""SQL time/conversation filtering before decoding or restoring any media."""
import hashlib
import json
import heapq
import itertools
import sqlite3
from chatlocal.import_scope import bounds
from incremental_wechat import checked_open


def export_selected(reader, selected_chats, metadata, output, limit, coverage,load_stickers=True,start='',end='',batch_dir=None):
    from wechatauto.db import MSG_TYPE_NAMES
    from reader_media import WeChatMedia
    lo,hi=bounds(start,end)
    clauses=[];params=[]
    if lo is not None:clauses.append('create_time>=?');params.append(lo/1000)
    if hi is not None:clauses.append('create_time<?');params.append(hi/1000)
    where=' WHERE '+' AND '.join(clauses) if clauses else ''
    conns=[];messages=[];chats=[];limited=0;unknown=0;media_reader=None
    nicknames=reader._nickname_index()
    from chatlocal.reader_batches import BatchWriter
    names={c['username']:c['name'] for c in selected_chats}
    if batch_dir:
        messages=BatchWriter(batch_dir,lambda rows:dict(reader='wechatauto-replica',wxid=reader.wxid,
            identity_method='per-shard Name2Id + verified group prefix',snapshot_at=metadata['snapshot_at'],load_stickers=load_stickers,
            chats=[dict(username=u,name=names[u]) for u in {r['chat'] for r in rows}]))
    identity_db=sqlite3.connect(str(batch_dir/'identities.sqlite3')) if batch_dir else None
    if identity_db:identity_db.execute('CREATE TABLE IF NOT EXISTS seen(k TEXT PRIMARY KEY,sender TEXT)')
    try:
        for rel in reader._message_dbs():
            conn=checked_open(reader,rel)
            conns.append((rel,conn,dict(conn.execute('SELECT rowid,user_name FROM Name2Id'))))
        for chat in selected_chats:
            user=chat['username'];table='Msg_'+hashlib.md5(user.encode()).hexdigest()
            streams=[];total=0;found=False
            for rel,conn,identities in conns:
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():continue
                found=True
                total+=conn.execute(f'SELECT count(*) FROM "{table}"'+where,params).fetchone()[0]
                # Top N per shard is sufficient for the top N across shards.
                query=f'SELECT local_id,local_type,server_id,real_sender_id,create_time,message_content,packed_info_data,sort_seq FROM "{table}"'+where+' ORDER BY sort_seq DESC,local_id DESC'+(' LIMIT ?' if limit is not None else '')
                def stream(cursor,rel,identities):
                    for row in cursor:yield row,rel,identities.get(row['real_sender_id'],'')
                streams.append(stream(conn.execute(query,[*params,limit] if limit is not None else params),rel,identities))
            if not found:raise ValueError('选中微信会话缺少消息表，未导入。')
            rows=heapq.merge(*streams,key=lambda item:(item[0]['sort_seq'],item[0]['local_id']),reverse=True)
            if limit is not None:rows=itertools.islice(rows,limit)
            if not batch_dir:rows=list(reversed(list(rows)))
            read_count=0
            identities_seen={}
            if identity_db:identity_db.execute('DELETE FROM seen')
            for row,rel,sender in rows:
                read_count+=1
                if batch_dir:messages.observe()
                message=reader._export_row(row,MSG_TYPE_NAMES)
                identity=(row['local_id'],row['create_time'],row['server_id'])
                if identity_db:
                    key=json.dumps(identity);prior=identity_db.execute('SELECT sender FROM seen WHERE k=?',(key,)).fetchone()
                    if prior and prior[0]!=sender:raise ValueError('微信跨分片发送者冲突')
                    identity_db.execute('INSERT OR IGNORE INTO seen VALUES(?,?)',(key,sender))
                else:
                    if identity in identities_seen and identities_seen[identity]!=sender:raise ValueError('微信跨分片发送者冲突')
                    identities_seen[identity]=sender
                if user.endswith('@chatroom') and (not sender or sender==user):
                    prefix=message['content'].partition(':\n')[0]
                    if prefix in nicknames:sender=prefix
                message.update(chat=user,sender_username=sender,sender_name=nicknames.get(sender,sender) or '未知发送者',
                    is_self=sender==reader.wxid if sender else None,source_db=rel.replace('\\','/'),source_table=table)
                if int(message.get('type_code',0))&0xffffffff==34:text,media,reply=None,[],None
                else:
                    if media_reader is None:media_reader=WeChatMedia(reader,metadata,load_stickers=load_stickers)
                    text,media,reply=media_reader.parse(message)
                if text is not None:message['text']=text
                message.update(media=media,reply_to=reply);messages.append(message);unknown+=not bool(sender)
            if read_count!=(total if limit is None else min(total,limit)):raise ValueError('微信范围导出条数不一致；已完成批次保留。')
            truncated=limit is not None and total>limit
            chats.append(dict(username=user,name=chat['name'],message_count=read_count,local_message_count=chat['message_count'],
                range_message_count=total,truncated=truncated))
            limited+=truncated
    finally:
        for _,conn,_ in conns:conn.close()
        if identity_db:
            identity_db.close()
            (batch_dir/'identities.sqlite3').unlink(missing_ok=True)
    coverage=dict(coverage,selected_chats=len(chats),exported_chats=len(chats),per_chat_limit=limit,
        limited_chats=limited,exported_raw_messages=len(messages),start=start,end=end,
        exported_text_messages=messages.texts if batch_dir else sum((int(m.get('type_code',0))&0xffffffff)==1 and bool(m.get('content')) for m in messages))
    payload=dict(reader='wechatauto-replica',wxid=reader.wxid,chats=chats,messages=[] if batch_dir else messages,coverage=coverage,
        identity_method='per-shard Name2Id + verified group prefix',snapshot_at=metadata['snapshot_at'],load_stickers=load_stickers)
    if batch_dir:payload=messages.finish(payload)
    output.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
    return dict(messages=len(messages),chats=len(chats),coverage=coverage,unknown_sender=unknown,
        media=messages.media if batch_dir else sum(len(m['media']) for m in messages),
        media_available=messages.available if batch_dir else sum(i['status']=='available' for m in messages for i in m['media']))
