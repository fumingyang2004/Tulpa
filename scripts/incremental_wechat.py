"""Read only new native rows, reusing WeChatDB decryption and decoding."""
import json
import os
import re
from pathlib import Path
from incremental_common import boundary
from chatlocal.store import Store


def checked_open(reader,rel):
    conn=reader._open(rel)
    try:
        src=Path(reader._db_path(rel));wal=reader._wal_path(rel)
        stamp=Path(reader.workdir)/(rel.replace(os.sep,'__')+'.stamp')
        parts=stamp.read_text().split(',')
        expected=(src.stat().st_mtime,src.stat().st_size,os.path.getmtime(wal) if wal else 0,os.path.getsize(wal) if wal else 0)
        actual=(float(parts[1]),int(parts[2]),float(parts[3]),int(parts[4]))
        if expected!=actual or int(parts[5])<0:
            raise ValueError('微信读取结果可能过期或 WAL 未合并；本轮未推进进度，请保持登录后重试。')
        return conn
    except Exception:
        conn.close();raise


def export_delta(reader,metadata,request,load_stickers,batch_dir=None):
    from wechatauto.db import MSG_TYPE_NAMES
    from export_wechat import SYSTEM_CHATS
    from reader_media import WeChatMedia
    previous=request['checkpoint']
    if previous and previous['account']!=metadata['account']:raise ValueError('微信账号变化；未推进同步进度。')
    rels=reader._message_dbs()
    # Compare real source inventory to the reader inventory, not just known keys.
    real={str(p.relative_to(metadata['source_db'])).replace('\\','/') for p in Path(metadata['source_db']).glob('message/message_*.db') if re.fullmatch(r'message_\d+\.db',p.name)}
    if real!={r.replace('\\','/') for r in rels}:raise ValueError('微信发现未读取或已移除的消息分片；请重新刷新，未推进进度。')
    for rel in rels:
        conn=checked_open(reader,rel);conn.close()
    idx=reader._build_md5_index();nicks=reader._nickname_index()
    with Store().connect() as db:
        known={(r[0],r[1]) for r in db.execute("SELECT conversation_id,source_id FROM messages WHERE platform='wechat'")}
    messages=[];chats={};cursors={};media_reader=None;unsupported=0
    from chatlocal.reader_batches import BatchWriter
    if batch_dir:
        messages=BatchWriter(batch_dir,lambda rows:dict(reader='wechatauto-replica',wxid=reader.wxid,
            identity_method='per-shard Name2Id + verified group prefix',snapshot_at=metadata['snapshot_at'],load_stickers=load_stickers,
            chats=[dict(username=u,name=nicks.get(u,u)) for u in {r['chat'] for r in rows}]))
    for rel in rels:
        conn=checked_open(reader,rel)
        try:
            identities=dict(conn.execute('SELECT rowid,user_name FROM Name2Id'))
            for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'").fetchall():
                if not re.fullmatch(r'Msg_[a-fA-F0-9]{32}',table):raise ValueError('微信表结构异常')
                key=rel.replace('\\','/')+'/'+table
                prior=previous.get('cursors',{}).get(key)
                top=boundary(conn,table,'local_id',['server_id','create_time'],prior,allow_server_ack=True)
                replay=top.pop('replay_from',prior['last'] if prior else 0)
                if request.get('conversations') is not None:
                    import hashlib
                    if table[4:] not in {hashlib.md5(v.encode()).hexdigest() for v in request['conversations']}:
                        cursors[key]=top;continue
                user=idx.get(table[4:])
                if not user:
                    if top['last'] and (not prior or top['last']>prior['last']):
                        raise ValueError('微信有新增消息但会话身份未解析；保留进度，请重试或补读历史。')
                    cursors[key]=top;continue
                if user.startswith('gh_') or user in SYSTEM_CHATS:
                    cursors[key]=top;continue
                if request.get('conversations') is not None and user not in request['conversations']:
                    cursors[key]=top;continue
                condition='local_id>? AND local_id<=?' if prior else 'create_time>=? AND local_id<=?'
                values=(replay if prior else (request['bootstrap_since'] if not previous else 0),top['last'])
                query=f'SELECT local_id,local_type,server_id,real_sender_id,create_time,message_content,packed_info_data,sort_seq FROM "{table}" WHERE {condition} ORDER BY local_id'
                for row in conn.execute(query,values):
                    if batch_dir:messages.observe()
                    base=row['local_type'] & 0xffffffff
                    if base not in (1,3,34,47,49):unsupported+=1;continue
                    source_id=str(row['server_id']) if row['server_id'] else f"local:{row['local_id']}:{row['create_time']*1000}"
                    if (user,source_id) in known:continue
                    if not batch_dir and len(messages)>=50000:raise ValueError('单文件新增超过 50000 条，请使用产品中的分批刷新。')
                    message=reader._export_row(row,MSG_TYPE_NAMES)
                    sender=identities.get(row['real_sender_id'],'')
                    if user.endswith('@chatroom') and (not sender or sender==user):
                        prefix=message['content'].partition(':\n')[0]
                        if prefix in nicks:sender=prefix
                    message.update(chat=user,sender_username=sender,sender_name=nicks.get(sender,sender) or '未知发送者',
                        is_self=sender==reader.wxid if sender else None,source_db=rel.replace('\\','/'),source_table=table)
                    if base not in (1,34):
                        if media_reader is None:media_reader=WeChatMedia(reader,metadata,load_stickers=load_stickers)
                        text,media,reply=media_reader.parse(message)
                        if text is not None:message['text']=text
                        message.update(media=media,reply_to=reply)
                    messages.append(message)
                    chats[user]=dict(username=user,name=nicks.get(user,user))
                cursors[key]=top
        finally:conn.close()
    if set(previous.get('cursors',{}))-set(cursors):raise ValueError('微信原消息表/分片消失；未推进进度，请确认客户端历史是否清理。')
    payload=dict(reader='wechatauto-replica',wxid=reader.wxid,chats=list(chats.values()),messages=[] if batch_dir else messages,
        identity_method='per-shard Name2Id + verified group prefix',snapshot_at=metadata['snapshot_at'],
        unsupported=unsupported,load_stickers=load_stickers,
        sync_checkpoint=dict(account=metadata['account'],cursors=cursors,fingerprint=metadata['fingerprint']))
    return messages.finish(payload) if batch_dir else payload
