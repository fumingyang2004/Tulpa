"""Own group identities from explicit is_self records; names are only @ clues."""
import re
from dataclasses import replace

from .retrieval import Plan,scope_sql


IDENTITY_NOTE = ('身份来自已导入本人发言的账号ID和同群显示名；这是历史记录中的显示名，不保证当前实时群名片。'
                 '昵称只在原平台、原群使用，不把一个群的别名套到其他群。没有本人发言的群暂不能确认群内昵称。')
MENTION_NOTE = ('当前导出未保留原生@目标账号ID；这里只按同群已确认的本人显示名或账号号文本查找候选，'
                '不能确认真实@接收者，也不能从未匹配推断没有人@本人。同名、改名、手打@、引用及未导入消息均可能影响结果。'
                '@全体成员单独统计，不等于单独@本人。')


def group_identities(store,plan=None,*,strict_dates=False):
    # Identity metadata may predate the question's time range. No older chat body
    # is returned here. Platform/conversation restrictions remain hard boundaries.
    plan=(plan or Plan()) if strict_dates else replace(plan or Plan(),start=None,end=None)
    where,args=scope_sql(plan)
    with store.connect() as db:
        groups=[dict(r) for r in db.execute(f'''SELECT platform,conversation_id,max(conversation) conversation
            FROM messages m WHERE {where} AND conversation_type='group'
            GROUP BY platform,conversation_id ORDER BY platform,conversation''',args)]
        aliases=[dict(r) for r in db.execute(f'''SELECT platform,conversation_id,sender_id,sender name,
            min(timestamp) first_seen,max(timestamp) last_seen,count(*) count,
            max(CASE WHEN trim(content)!='' THEN id END) source_message_id
            FROM messages m WHERE {where} AND conversation_type='group' AND is_self=1
            GROUP BY platform,conversation_id,sender_id,sender ORDER BY count(*) DESC''',args)]
        others={(r[0],r[1],r[2]) for r in db.execute(f'''SELECT DISTINCT platform,conversation_id,sender
            FROM messages m WHERE {where} AND conversation_type='group' AND is_self=0''',args)}
    by_group={(g['platform'],g['conversation_id']):g for g in groups}
    for group in groups:group['identities']=[]
    for alias in aliases:
        group=by_group[(alias.pop('platform'),alias.pop('conversation_id'))]
        alias['name_collision']=(group['platform'],group['conversation_id'],alias['name']) in others
        group['identities'].append(alias)
    return sorted(groups,key=lambda g:(not g['identities'],g['platform'],g['conversation']))


def _tagged(text,name):
    if not name:return False
    # Require a complete target string: @张三 must not match @张三丰. Exclude
    # ordinary email addresses, while allowing Chinese prose immediately before @.
    return bool(re.search(r'(?<![A-Za-z0-9._%+\-])[@＠]'+re.escape(name)+r'(?![\w])',text))


def classify_mention(row,group):
    text=row['content']
    names=[];accounts=[];collisions=[]
    for identity in (group or {}).get('identities',[]):
        if _tagged(text,identity['name']):
            names.append(identity['name'])
            if identity['name_collision']:collisions.append(identity['name'])
        if identity['sender_id'] and _tagged(text,identity['sender_id']):accounts.append(identity['sender_id'])
    broadcast=any(_tagged(text,n) for n in ('全体成员','所有人','全体','all'))
    return dict(message_id=row['id'],kind='self_candidate' if names or accounts else 'all' if broadcast else 'unresolved',
                matched_names=list(dict.fromkeys(names)),matched_account_ids=list(dict.fromkeys(accounts)),
                name_collisions=list(dict.fromkeys(collisions)),native_target_verified=False)
