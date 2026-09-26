"""Supply only scoped, source-valid, bounded behavior to the existing reply agent."""
from .tulpa import Tulpa, fingerprint
from .retrieval import scope_sql

MAX_EPISODE_CHARS=16000


def context_for(tools,target,instruction='',disabled=False,with_history=False):
    layer=Tulpa(tools.store)
    result=dict(enabled=False,episodes=[]) if disabled else layer.retrieve(target,instruction,as_of=tools.plan.end)
    if not result['enabled'] and not with_history:return result
    # Derived memory cannot bypass a narrower tool scope, including replay time.
    where,params=scope_sql(tools.plan)
    with tools.store.connect() as db:
        sid=result.get('scope_id')
        if sid is not None and db.execute(f'''SELECT 1 FROM tulpa_memory_sources s LEFT JOIN messages m
          ON m.id=s.message_id WHERE s.scope_id=? AND (m.id IS NULL OR NOT ({where})) LIMIT 1''',[sid,*params]).fetchone():
            result['memory']='';result['manual']=''
    episodes=[dict(e,origin='growth') for e in result['episodes']]
    if with_history:
        from .reply_history import candidates, rank
        episodes=candidates(tools,target,instruction)+episodes
    prepared=[]
    with tools.store.connect() as db:
        for episode in episodes:
            rows=[]
            for mid in episode['message_ids']:
                row=db.execute(f'SELECT m.* FROM messages m WHERE {where} AND m.id=?',[*params,mid]).fetchone()
                if row is None:break
                row=dict(row)
                if with_history and (row['platform']!=target['platform'] or row['conversation_id']!=target['conversation_id'] or row['timestamp']>=target['timestamp']):break
                if episode.get('fingerprints') and fingerprint(row)!=episode['fingerprints'].get(mid):break
                rows.append(row)
            else:
                episode=dict(episode,person_id=next((r['sender_id'] for r in rows if r['id'] in episode['incoming']),''),
                    timestamp=max(r['timestamp'] for r in rows),
                    _incoming_text=' '.join(r['content'] for r in rows if r['id'] in episode['incoming']),
                    _outgoing_text=' '.join(r['content'] for r in rows if r['id'] in episode['outgoing']))
                prepared.append((episode,rows))
    if with_history:prepared.sort(key=lambda item:rank(item[0],target,instruction),reverse=True)
    accepted=[];used=0;covered=set()
    for episode,rows in prepared:
        if covered.intersection(episode['outgoing']):continue
        if with_history and rank(episode,target,instruction)[0]==0:continue
        size=sum(len(r['content']) for r in rows)
        if used+size>MAX_EPISODE_CHARS:continue
        before=set(tools.messages);before_chars=tools.used_chars
        try:
            admitted=tools._admit(rows)
        except ValueError:break
        if len(admitted)!=len(rows):
            # Keep a complete input/output turn. An oversized example must not
            # consume the remaining budget with fragments the model never sees.
            for mid in set(tools.messages)-before:
                tools.messages.pop(mid,None);tools.originals.pop(mid,None)
            tools.used_chars=before_chars
            continue
        accepted.append(dict(incoming=episode['incoming'],outgoing=episode['outgoing'],messages=admitted,origin=episode['origin'],
            linkage=episode.get('linkage','growth_episode')))
        covered.update(episode['outgoing'])
        used+=size
        if len(accepted)>=8:break
    result['episodes']=accepted
    result['note']='案例保留完整的对方输入和本人回应；重点模仿 outgoing / is_self=1 的表达、节奏与接话方式。合适的短句与口头语可以直接沿用，旧事实和承诺不能搬用。自动记忆是可修订推断，人工记忆是用户补充。'
    return result
