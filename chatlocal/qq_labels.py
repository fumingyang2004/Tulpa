"""Repair missing display labels by exact account/conversation ID, never names.

Only placeholder labels change; content, message IDs, source archives, cursors
and user-provided real display names remain intact. No messages are inserted.
"""
import re


def placeholder(label,peer,kind):
    return str(label or '').strip() in {'',str(peer),f'qq_friend_{peer}',f'qq_group_{peer}',
                                       f'QQ {kind} {peer}',f'QQ 会话 {peer}'}


def apply_labels(db,account,items):
    account=str(account or '')
    if not account or ':' in account or not isinstance(items,list) or len(items)>200000:return 0
    labels={}
    for item in items:
        if not isinstance(item,dict):continue
        cid=item.get('conversation_id','');name=item.get('conversation')
        if not isinstance(cid,str) or not isinstance(name,str):continue
        parts=cid.split(':')
        if len(parts)!=3 or parts[0]!=account or parts[1] not in ('direct','group') or not re.fullmatch('[1-9][0-9]{0,19}',parts[2]):continue
        if not name.strip() or len(name)>1100 or any(ord(c)<32 for c in name) or placeholder(name,parts[2],parts[1]):continue
        labels[cid]=name.strip()
    if not labels:return 0
    changed=0
    # Bound work to existing QQ conversations; never materialize the full contact
    # directory as messages or expose contacts outside an imported conversation.
    existing=db.execute("SELECT DISTINCT conversation_id,conversation FROM messages WHERE platform='qq' AND conversation_id LIKE ?",(account+':%',)).fetchall()
    for cid,old in existing:
        parts=cid.split(':')
        if cid not in labels or len(parts)!=3 or not placeholder(old,parts[2],parts[1]):continue
        result=db.execute("UPDATE messages SET conversation=? WHERE platform='qq' AND conversation_id=? AND conversation=?",(labels[cid],cid,old))
        changed+=result.rowcount
    return changed


def payload_labels(db,payload):
    return apply_labels(db,payload.get('label_account'),payload.get('conversation_labels',[]))
