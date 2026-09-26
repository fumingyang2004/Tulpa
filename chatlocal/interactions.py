"""Bounded interaction candidates. Explicit provenance is distinct from inference."""
import hashlib
import json
import re
import sqlite3
import time
from collections import defaultdict

from .normalize import display_time,safe_reply


def extract(store,where,params,participants,*,mode='strict',conversation_ids=(),query='',offset=0,limit=8):
    if not 2<=len(set(participants))<=6:raise ValueError('请给出2–6个不同的同平台稳定账号。')
    started=time.monotonic();ids=set(participants);params=list(params)
    if conversation_ids:
        where+=' AND m.conversation_id IN ('+','.join('?' for _ in conversation_ids)+')';params+=list(conversation_ids)
    # Keep all senders in the candidate groups, so intervening speakers cannot
    # disappear and manufacture a fake A/B exchange.
    with store.connect() as db:
        db.set_progress_handler(lambda:int(time.monotonic()-started>2.5),2000)
        chats=db.execute(f'''SELECT m.platform,m.conversation_id FROM messages m WHERE {where}
            AND m.sender_id IN ({','.join('?' for _ in ids)}) GROUP BY m.platform,m.conversation_id
            HAVING count(DISTINCT m.sender_id)>=2 ORDER BY max(m.timestamp) DESC LIMIT 41''',params+list(ids)).fetchall()
        groups=[];capped=len(chats)>40;remaining=20000
        for chat in chats[:40]:
            rows=db.execute(f'''SELECT m.* FROM messages m WHERE {where} AND m.platform=? AND m.conversation_id=?
                ORDER BY timestamp DESC,id DESC LIMIT ?''',params+[chat[0],chat[1],remaining+1]).fetchall()
            capped |= len(rows)>remaining
            rows=[dict(r) for r in rows[:remaining]][::-1];groups.append(rows);remaining-=len(rows)
            if remaining<=0:capped=True;break
    threads=[]
    for rows in groups:
        if time.monotonic()-started>3:raise ValueError('交互查询超过3秒，请缩小会话或日期范围。')
        native=defaultdict(list);quotes=defaultdict(list);aliases=defaultdict(set)
        for i,m in enumerate(rows):
            if i%128==0 and time.monotonic()-started>3:raise ValueError('交互查询超过3秒，请缩小范围。')
            if m['source_id']:native[m['source_id']].append(i)
            if m['content'].strip():quotes[m['content'].strip()].append(i)
            aliases[m['sender']].add(m['sender_id'])
        edges=[]
        for i,m in enumerate(rows):
            if i%128==0 and time.monotonic()-started>3:raise ValueError('交互查询超过3秒，请缩小范围。')
            if m['sender_id'] not in ids:continue
            raw=m.get('reply_to')
            reply=safe_reply(json.loads(raw) if isinstance(raw,str) and raw else raw);target=None;basis=None
            if reply and reply.get('source_id'):
                matches=native.get(str(reply['source_id']),[])
                if len(matches)==1:target=matches[0];basis='reply'
            if target is None and reply and len(reply.get('content','').strip())>=6:
                matches=[j for j in quotes.get(reply['content'].strip(),[]) if j<i and
                    (not reply.get('sender') or rows[j]['sender']==reply['sender'])]
                # Full, unique quote match, not a nickname/prefix guess.
                if len(matches)==1:target=matches[0];basis='quote'
            if target is not None and target<i and rows[target]['sender_id'] in ids and rows[target]['sender_id']!=m['sender_id']:
                edges.append(dict(ids=[rows[target]['id'],m['id']],positions=[target,i],basis=basis,
                    detail='原生引用ID精确匹配同会话消息' if basis=='reply' else '原生引用全文在可见同会话中唯一精确匹配；并非原生目标ID'))
            for account in ids-{m['sender_id']}:
                if re.search(r'@'+re.escape(account)+r'(?![\w])',m['content']):
                    # A numeric/wxid textual address is explicit, but not a native mention field.
                    edges.append(dict(ids=[m['id']],positions=[i],basis='mention',target=account,
                        detail='正文明确@稳定账号；导出未保留原生@目标字段，仍需核对'))
        if mode=='contextual':
            run=[]
            def flush():
                if len(run)>=3 and len({rows[j]['sender_id'] for j in run})>=2:
                    switches=sum(rows[a]['sender_id']!=rows[b]['sender_id'] for a,b in zip(run,run[1:]))
                    if switches>=2:
                        edges.append(dict(ids=[rows[j]['id'] for j in run],positions=list(run),basis='contextual',
                            detail='指定账号连续发言、相邻不超过120秒且至少两次交替；仅候选，可能碰巧同聊'))
            for i,m in enumerate(rows):
                if m['sender_id'] not in ids or (run and (m['timestamp']-rows[run[-1]]['timestamp']>120000 or len(run)>=16)):
                    flush();run=[]
                if m['sender_id'] in ids:run.append(i)
            flush()
        # Merge overlapping evidence endpoints, not the entire day's messages.
        clusters=[]
        for edge in sorted(edges,key=lambda e:max(e['positions'])):
            if time.monotonic()-started>3:raise ValueError('交互查询超过3秒，请缩小范围。')
            touched=next((c for c in reversed(clusters) if c['ids'].intersection(edge['ids']) and len(c['ids']|set(edge['ids']))<=24),None)
            if touched:touched['ids'].update(edge['ids']);touched['edges'].append(edge)
            else:clusters.append(dict(ids=set(edge['ids']),edges=[edge]))
        for cluster in clusters:
            positions=sorted({p for e in cluster['edges'] for p in e['positions']})
            evidence=[rows[i] for i in positions]
            if query and not any(t in (m['content']+' '+(m.get('reply_to') or '')).lower() for m in evidence for t in query.lower().split()[:12]):continue
            expanded=sorted({j for i in positions for j in range(max(0,i-1),min(len(rows),i+2))})
            basis=set(e['basis'] for e in cluster['edges']);members={m['sender_id'] for m in evidence}
            members.update(e['target'] for e in cluster['edges'] if e.get('target'))
            token=json.dumps([evidence[0]['platform'],evidence[0]['conversation_id'],sorted(cluster['ids'])])
            threads.append(dict(thread_id='T'+hashlib.sha256(token.encode()).hexdigest()[:16],
                platform=evidence[0]['platform'],conversation_id=evidence[0]['conversation_id'],conversation=evidence[0]['conversation'],
                participants=sorted(members),start_time=display_time(evidence[0]['timestamp']),end_time=display_time(evidence[-1]['timestamp']),
                interaction_basis=next(iter(basis)) if len(basis)==1 else 'mixed',
                relations=[{k:v for k,v in e.items() if k!='positions'} for e in cluster['edges']],
                messages=[rows[j] for j in expanded],sort_time=evidence[-1]['timestamp']))
    threads.sort(key=lambda t:t['sort_time'],reverse=True);total=len(threads)
    page=threads[offset:offset+limit]
    for t in page:t.pop('sort_time',None)
    return dict(threads=page,match_count=total,has_more=offset+limit<total,next_offset=offset+limit if offset+limit<total else None,
        scan_limited=capped,scanned_messages=20000-remaining,execution_time=round(time.monotonic()-started,4),
        note='严格模式仅返回原生回复/完整引用或明确@账号；昵称@不作为确定关系。contextual是连续发言候选，需读原文判断。扫描限40会话/最近2万条；未命中不等于没有互动。')
