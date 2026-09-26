"""Platform account lookup from imported metadata, never name-based merging."""
import re
import unicodedata
from dataclasses import replace

from .retrieval import scope_sql

NOTE = ('同一平台的相同发送者账号ID连接历史显示名，不按同名或相似名字合并，也不跨QQ/微信合并。'
        '身份目录不按提问日期截断，仍受平台、会话和入库快照限制；不代表当前昵称或完整联系人列表。'
        '目录不是聊天事实证据，回答沟通内容须read_person_messages读取正文；群里发言不等于在对本人说话。')


def fold(value):
    return unicodedata.normalize('NFKC', value).strip().casefold()


def directory(store, plan, *, strict_dates=False):
    """Only IDs, names and counts enter this local directory, never chat bodies."""
    where, args = scope_sql(plan if strict_dates else replace(plan, start=None, end=None))
    with store.connect() as db:
        rows = [dict(r) for r in db.execute(f'''SELECT platform,conversation_id,conversation,
            conversation_type,sender_id,sender,is_self,count(*) count,
            min(timestamp) first_seen,max(timestamp) last_seen
            FROM messages m WHERE {where}
            GROUP BY platform,conversation_id,conversation,conversation_type,sender_id,sender,is_self''', args)]
    people = {}
    unresolved = []

    def person(platform, account):
        return people.setdefault((platform, account), dict(platform=platform, sender_id=account,
            aliases=[], direct_conversations=[], message_count=0))

    incoming = {}
    for row in rows:
        if row['conversation_type']=='direct' and row['is_self']==0 and row['sender_id']:
            incoming.setdefault((row['platform'],row['conversation_id']),set()).add(row['sender_id'])
    for row in rows:
        if row['sender_id'].strip().casefold() in ('','0','unknown','none','null'):
            unresolved.append(row)
            continue
        entry = person(row['platform'], row['sender_id'])
        entry['message_count'] += row['count']
        entry['aliases'].append(dict(name=row['sender'], conversation_id=row['conversation_id'],
            conversation=row['conversation'], conversation_type=row['conversation_type'],
            first_seen=row['first_seen'], last_seen=row['last_seen'], count=row['count'],
            basis='sender_id'))

    for row in rows:
        if row['conversation_type'] != 'direct':
            continue
        peer = None
        if row['platform'] == 'qq':
            match = re.fullmatch(r'\d+:direct:(\d+)', row['conversation_id'])
            if match:
                peer = match[1]
        elif row['platform'] == 'wechat' and re.fullmatch(r'wxid_[\w-]+', row['conversation_id']):
            peer = row['conversation_id']
        # Generic imports need an explicit non-self author; never assign the
        # conversation title to the user merely because only own messages exist.
        if peer is None:
            peers = incoming.get((row['platform'],row['conversation_id']),set())
            if len(peers)==1:
                peer = next(iter(peers))
        if not peer or peer.strip().casefold() in ('0','unknown','none','null'):
            continue
        entry = person(row['platform'], peer)
        item = dict(conversation_id=row['conversation_id'], conversation=row['conversation'])
        if item not in entry['direct_conversations']:
            entry['direct_conversations'].append(item)
            entry['aliases'].append(dict(name=row['conversation'], conversation_id=row['conversation_id'],
                conversation=row['conversation'], conversation_type='direct', basis='direct_peer'))
    return people, unresolved


def find(index, query, platform=None, conversation_id=None):
    needle = fold(query)
    found = []
    for person in index.values():
        if platform and person['platform'] != platform:
            continue
        aliases = [a for a in person['aliases'] if not conversation_id or a['conversation_id']==conversation_id]
        if not aliases:
            continue
        matches = [a for a in aliases if needle in fold(a['name'])]
        if matches or needle == fold(person['sender_id']):
            # Matching aliases first; other aliases remain evidence of the same
            # account, even when none of their characters match the query.
            ordered = sorted(aliases, key=lambda a:(a not in matches, -a.get('last_seen', 0), a['name']))
            found.append(dict(person, aliases=ordered,
                direct_conversations=[c for c in person['direct_conversations'] if not conversation_id or c['conversation_id']==conversation_id],
                message_count=sum(a.get('count',0) for a in aliases)))
    return sorted(found, key=lambda p:(needle!=fold(p['sender_id']), p['platform'], p['sender_id']))


def public_person(person, alias_limit=16):
    aliases = person['aliases'][:alias_limit]
    return dict(platform=person['platform'], sender_id=person['sender_id'],
        aliases=aliases, alias_count=len(person['aliases']), aliases_truncated=len(aliases)<len(person['aliases']),
        direct_conversations=person['direct_conversations'][:16],
        direct_conversations_truncated=len(person['direct_conversations'])>16,
        message_count=person['message_count'], identity_basis='same_platform_account_id')
