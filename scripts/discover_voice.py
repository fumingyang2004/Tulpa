"""Explicit bounded discovery from the last authenticated local client snapshot.

Never changes live cursors, copies audio, or invokes ASR. Historical import also
discovers voices normally; this is for voice rows earlier versions had skipped.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tools/qq-reader'),str(ROOT/'tools/wechat-reader')]
from chatlocal.config import DATA
os.environ['CHATLOG_KEEPER_DATA_DIR']=str(DATA/'qq-reader')
os.environ['WECHATAUTO_KEYS_DIR']=str(DATA/'wechat-reader/keys')
from chatlocal.voice_metadata import qq_voice,PLACEHOLDER
from chatlocal.store import Store
from chatlocal.normalize import normalize,stable_key
from chatlocal.import_scope import bounds,get_scope

def discover(platform,start,end,conversations=None,limit=1000):
    if platform not in ('qq','wechat') or not start or not end:raise ValueError('请选择平台和起止日期')
    lo,hi=bounds(start,end)
    if lo is None or hi is None or lo>=hi or hi-lo>366*86400000:raise ValueError('发现范围应为1年以内的有效日期')
    store=Store();allowed=get_scope(store)[platform]
    if not allowed['enabled']:raise ValueError('该平台当前未启用导入')
    with store.connect() as db:
        imported={r[0] for r in db.execute('SELECT DISTINCT conversation_id FROM messages WHERE platform=?',(platform,))}
    selected=set(conversations) if conversations else imported
    if allowed['conversations'] is not None:selected &= set(allowed['conversations'])
    if not selected:raise ValueError('没有所选且已允许的会话')
    messages=[];truncated=False
    if platform=='qq':
        import chatlog_keeper.qq_db as q
        from chatlocal.qq_storage import qq_workspace_lock
        from live_reader import Reader
        meta=json.loads((DATA/'qq-snapshot-info.json').read_text('utf-8'))
        reader=Reader('qq');reader.labels(meta['account'])
        with qq_workspace_lock():
            db=q._open_qq_sqlite_connection(DATA/'qq-reader/decrypted/messages.db')
            try:
                own=q._query_account_qq_number(db) or meta['account']
                for spec in q._QQ_MESSAGE_TABLE_SPECS:
                    peers=[cid.split(':',2)[2] for cid in selected if cid.startswith(meta['account']+':'+spec.conversation_type+':')]
                    if not peers:continue
                    rows=db.execute(f'''SELECT "40001","40050","40090","40033","40800","{spec.conversation_column}",
                      {spec.table_rank},rowid,'{spec.conversation_type}',"40020" FROM "{spec.table}"
                      WHERE "40011"=6 AND "40050">=? AND "40050"<? AND "{spec.conversation_column}" IN ({','.join('?' for _ in peers)})
                      ORDER BY "40050" DESC LIMIT ?''',[lo/1000,hi/1000,*peers,limit+1]).fetchall()
                    for row in rows:
                        voice=qq_voice(q,row[4])
                        if not voice:continue
                        if len(messages)>=limit:truncated=True;break
                        rec=q._qq_message_page_record(tuple(row),account_id=meta['account'],conversation_id=str(row[5]),buddy_map=reader.buddy,
                            group_map=reader.group,group_name_map=reader.names,recover_unreadable_rows=True)
                        sid=str(rec['sender_qq'] or rec['sender_uid'] or '')
                        messages.append(dict(platform='qq',conversation_id=f"{meta['account']}:{spec.conversation_type}:{row[5]}",
                            conversation=rec['chat_name'],conversation_type=spec.conversation_type,sender=rec['sender_name'],sender_id=sid,
                            timestamp=rec['ts'],source_id=str(rec['msg_id']),is_self=sid==str(own) if sid else None,content=PLACEHOLDER,voice=voice))
            finally:db.close()
        payload=dict(reader='qq-voice-discovery',messages=messages)
    else:
        from wechatauto.db import WeChatDB,MSG_TYPE_NAMES
        meta=json.loads((DATA/'wechat-snapshot-info.json').read_text('utf-8'))
        wx=WeChatDB(db_dir=str(DATA/'wechat-reader-source'),account=meta['account'],workdir=str(DATA/'wechat-reader/work'),keys_file=str(DATA/'wechat-reader/keys.json'))
        nicks=wx._nickname_index();chats={}
        for rel in wx._message_dbs():
            db=wx._open(rel)
            try:
                identities=dict(db.execute('SELECT rowid,user_name FROM Name2Id'))
                for user in sorted(selected):
                    table='Msg_'+hashlib.md5(user.encode()).hexdigest()
                    if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():continue
                    rows=db.execute(f'''SELECT local_id,local_type,server_id,real_sender_id,create_time,message_content,packed_info_data,sort_seq FROM "{table}"
                       WHERE (local_type & 4294967295)=34 AND create_time>=? AND create_time<? ORDER BY create_time DESC LIMIT ?''',(lo/1000,hi/1000,limit+1)).fetchall()
                    for row in rows:
                        if len(messages)>=limit:truncated=True;break
                        m=wx._export_row(row,MSG_TYPE_NAMES);sender=identities.get(row['real_sender_id'],'')
                        m.update(chat=user,sender_username=sender,sender_name=nicks.get(sender,sender) or '未知发送者',source_db=rel.replace('\\','/'),source_table=table)
                        messages.append(m);chats[user]=dict(username=user,name=nicks.get(user,user))
            finally:db.close()
        payload=dict(reader='wechatauto-replica',wxid=wx.wxid,identity_method='per-shard Name2Id',chats=list(chats.values()),messages=messages)
    kind='wechatauto' if platform=='wechat' else 'canonical'
    # Treat deletion tombstones as authoritative. Discovery is not a restore.
    with store.connect() as db:
        deleted={r[0] for r in db.execute('SELECT dedup_key FROM deleted_message_keys')}
        natives={(r[0],r[1]) for r in db.execute('SELECT conversation_id,locator FROM deleted_native_ids WHERE platform=?',(platform,))}
    def kept(m):
        if stable_key(normalize(m,payload,kind)) in deleted:return False
        locator=json.dumps([payload.get('wxid'),str(m.get('source_db','')).replace('\\','/'),m.get('source_table'),int(m.get('local_id') or 0)])
        return (m.get('chat'),locator) not in natives
    payload['messages']=[m for m in messages if kept(m)]
    path=ROOT/'.tmp'/('voice-discovery-'+uuid.uuid4().hex+'.json')
    path.write_text(json.dumps(payload,ensure_ascii=False),'utf-8')
    try:result=store.import_file(path,load_stickers=False,discovery=True)
    finally:path.unlink(missing_ok=True)
    return dict(platform=platform,found=len(payload['messages']),imported=result['imported'],duplicate=result['duplicate'],
        truncated=truncated,snapshot_at=meta.get('snapshot_at'),note='只发现最近一次本地读取快照中的语音元数据；未转写、未移动同步游标。更新快照可在导入界面按范围读取。')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--platform',required=True,choices=['qq','wechat']);p.add_argument('--start',required=True);p.add_argument('--end',required=True);p.add_argument('--conversation',action='append')
    args=p.parse_args()
    print(json.dumps(discover(args.platform,args.start,args.end,args.conversation),ensure_ascii=False))
