"""Explicit, bounded historical replay. Private outputs, no send adapter imported.

Default dry run selects only two active groups and one private chat. --models
permits up to ten real reply A/B pairs and one consolidation per 100 counted
messages. It never writes memories to the user's main database.
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from chatlocal.config import DB,DATA,settings
from chatlocal.store import Store,tokens
from chatlocal.tulpa import Tulpa,collect,effective,dumps
from chatlocal.normalize import TZ


def recover_quotes(rows):
    """Only selected historical quote headers, exact native lookup, read-only."""
    selected=[r for r in rows if r['platform']=='qq' and r['reply_to'] and r['is_self']==1]
    if not selected:return 0
    sys.path.insert(0,str(ROOT/'tools/qq-reader'))
    os.environ['CHATLOG_KEEPER_DATA_DIR']=str(DATA/'qq-reader')
    from live_vfs import configure_qq_worker,ReadView
    configure_qq_worker()
    import chatlog_keeper.qq_db as q
    from reader_media import QQMedia,resolve_qq_quote
    meta=json.loads((DATA/'qq-snapshot-info.json').read_text(encoding='utf-8'))
    key=q.load_cached_key_for_account(meta['account']) or q.load_cached_key()
    view=ReadView(Path(meta['source'])/'nt_msg.db',key,'qq')
    parser=QQMedia.__new__(QQMedia);parser.q=q
    parser.resolve=lambda *a,**k:dict(status='unavailable')
    resolved=0
    try:
        for r in selected:
            spec=next(s for s in q._QQ_MESSAGE_TABLE_SPECS if s.conversation_type==r['conversation_type'])
            blob=view.db.execute(f'SELECT "40800" FROM "{spec.table}" WHERE "40001"=? AND "{spec.conversation_column}"=?',
              (int(r['source_id']),r['conversation_id'].split(':')[-1])).fetchone()
            if not blob:continue
            reply=parser.parse(blob[0],0)[2]
            if not reply:continue
            # APSW cursor lacks fetchall; the live adapter preserves that contract.
            from live_reader import Connection
            reply=resolve_qq_quote(Connection(view),spec,r['conversation_id'].split(':')[-1],reply)
            if reply.get('source_id'):
                prior=json.loads(r['reply_to']);prior['source_id']=reply['source_id'];r['reply_to']=dumps(prior);resolved+=1
        view.view.assert_fresh()
    finally:view.close()
    return resolved


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--models',action='store_true');parser.add_argument('--cases',type=int,default=10)
    parser.add_argument('--label',default='tulpa')
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args();assert 1<=args.cases<=10 and re.fullmatch(r'tulpa(?:-[a-z0-9]+)?',args.label)
    report_dir=ROOT/'reports/private'/args.label;report_dir.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(f'file:{DB.as_posix()}?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row;end=db.execute('SELECT max(timestamp) FROM messages').fetchone()[0]
        chosen=[]
        for kind,limit in [('group',2),('direct',1)]:
            chosen += [dict(r) for r in db.execute('''SELECT platform,conversation_id,conversation,conversation_type,
              count(*) n,sum(is_self=1) own FROM messages WHERE timestamp>=? AND conversation_type=?
              GROUP BY platform,conversation_id HAVING own>=50 ORDER BY own DESC LIMIT ?''',(end-14*86400000,kind,limit))]
        rows=[]
        for c in chosen:
            rows += [dict(r) for r in db.execute('SELECT * FROM messages WHERE platform=? AND conversation_id=? AND timestamp>=? ORDER BY timestamp,id LIMIT 3500',
              (c['platform'],c['conversation_id'],end-14*86400000))]
    if args.resume:
        snapshot=json.loads((report_dir/'source.json').read_text(encoding='utf-8'))
        rows=snapshot['rows'];chosen=snapshot['chosen'];resolved=snapshot['resolved']
    else:
        resolved=recover_quotes(rows)
        (report_dir/'source.json').write_text(dumps(dict(rows=rows,chosen=chosen,resolved=resolved)),encoding='utf-8')
    rows.sort(key=lambda r:(r['timestamp'],r['id']))
    source={(r['platform'],r['conversation_id'],r['source_id']):r for r in rows};counts={};last={};candidates={}
    for r in rows:
        key=(r['platform'],r['conversation_id']);counts.setdefault(key,0);candidates.setdefault(key,[])
        if effective(r):
            target=None
            if r['is_self']==1 and counts[key]>=(30 if r['conversation_type']=='group' else 100):
                if r['conversation_type']=='group':
                    ref=json.loads(r['reply_to'] or '{}').get('source_id');target=source.get((*key,str(ref)))
                elif key in last and last[key]['is_self']==0 and r['timestamp']-last[key]['timestamp']<=1800000:target=last[key]
                if target and effective(target) and target['is_self']==0 and target['timestamp']<r['timestamp'] and len(r['content'])<200:
                    candidates[key].append((r,target))
            if r['conversation_type']=='direct' or r['is_self']==1:counts[key]+=1
            last[key]=r
    # Spread each conversation's cases across time, rather than adjacent turns.
    for key,bucket in candidates.items():
        if len(bucket)>6:
            candidates[key]=[bucket[round(i*(len(bucket)-1)/5)] for i in range(6)]
    selected=[]
    while len(selected)<args.cases and any(candidates.values()):
        for bucket in candidates.values():
            if bucket and len(selected)<args.cases:selected.append(bucket.pop(0))
    selected.sort(key=lambda pair:(pair[0]['timestamp'],pair[0]['id']))
    summary=dict(conversations=chosen,messages=len(rows),recovered_native_quotes=resolved,cases=len(selected),
      source_hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('chatlocal/tulpa.py','chatlocal/reply_agent.py','chatlocal/tulpa_reply.py')})
    (report_dir/'selection.json').write_text(dumps(summary),encoding='utf-8')
    print(dumps(dict(conversations=len(chosen),messages=len(rows),recovered_quotes=resolved,cases=len(selected))),flush=True)
    if not args.models:return
    path=report_dir/'replay.sqlite3'
    if path.exists() and not args.resume:raise ValueError('Replay database already exists; preserve previous run or use --resume.')
    store=Store(path);layer=Tulpa(store)
    with store.connect() as db:db.execute('UPDATE tulpa_control SET since=?',(rows[0]['timestamp']-1,))
    from chatlocal.agent import run_agent
    from chatlocal.agent_profiles import profile_config
    from chatlocal.retrieval import Plan
    from chatlocal.vision import with_vision_mode
    config=with_vision_mode(profile_config(settings(),'quick'),'ocr')
    cases={r['id']:(r,target) for r,target in selected};results=[];updates=[];start=time.monotonic()
    if args.resume:
        results=json.loads((report_dir/'cases.json').read_text(encoding='utf-8'))
        with store.connect() as db:
            updates=[dict(status='updated',api_called=True,**json.loads(r[0])) for r in db.execute("SELECT details FROM tulpa_history WHERE kind='automatic'")]
    def consolidate(sid):
        from chatlocal.tulpa_model import summarize
        def audited(payload):
            response=summarize(payload)
            (report_dir/f'consolidation-{time.time_ns()}.json').write_text(dumps(dict(input=payload,response=response)),encoding='utf-8')
            return response
        res=layer.consolidate(sid,audited);updates.append(res);print(dumps(dict(update=len(updates),status=res['status'])),flush=True)
    if args.resume:
        with store.connect() as db:pending=[r[0] for r in db.execute('SELECT id FROM tulpa_scopes WHERE total-processed>=100')]
        for sid in pending:consolidate(sid)
    for r in rows:
        if args.resume:
            with store.connect() as db:
                if db.execute('SELECT 1 FROM messages WHERE id=?',(r['id'],)).fetchone():continue
        if r['id'] in cases:
            actual,target=cases[r['id']];pair=dict(case=len(results)+1,conversation=r['conversation'],kind=r['conversation_type'],
              target_id=target['id'],actual_id=r['id'],target=target['content'],actual=actual['content'],as_of=r['timestamp'])
            for mode in ('off','on'):
                plan=Plan(platforms=[r['platform']],conversations=[dumps([r['platform'],r['conversation_id']])],end=r['timestamp'])
                context=dict(message_id=target['id'],instruction='',tulpa_disabled=mode=='off',as_of_time=datetime.fromtimestamp(r['timestamp']/1000,TZ).isoformat())
                final=None
                for event in run_agent(store,plan,'帮我回复这条消息。',memory={'read_only':True},config=config,
                      max_requests=3,max_seconds=75,reply_context=context):
                    if event['type']=='done':final=event
                record=(final or {}).get('record',{});result=record.get('result',{})
                pair[mode]=dict(status=(final or {}).get('status'),reply=result.get('reply_text',''),
                  usage=record.get('usage',{}),seconds=record.get('seconds'),requests=record.get('requests'),
                  tulpa=result.get('tulpa',{}),context_ids=result.get('context_ids',[]))
                assert r['id'] not in pair[mode]['context_ids'],'held-out reply leaked'
                with store.connect() as db:
                    assert not db.execute('SELECT 1 FROM messages WHERE timestamp>=? LIMIT 1',(r['timestamp'],)).fetchone(),'future timestamp leaked'
            results.append(pair)
            (report_dir/'cases.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
            print(dumps(dict(case=pair['case'],off=pair['off']['status'],on=pair['on']['status'],memory=pair['on']['tulpa'])),flush=True)
        with store.connect() as db:
            fields=list(r)
            db.execute(f'INSERT INTO messages({",".join(fields)}) VALUES({",".join("?" for _ in fields)})',[r[k] for k in fields])
            db.execute('INSERT INTO message_fts(rowid,terms) VALUES(?,?)',(r['id'],tokens(r['content'])))
            collect(db,r['id'])
        with store.connect() as db:ready=[x[0] for x in db.execute('SELECT id FROM tulpa_scopes WHERE total-processed>=100')]
        for sid in ready:
            try:consolidate(sid)
            except Exception as e:
                print('Consolidation failed:',type(e).__name__,flush=True);raise
    summary.update(seconds=round(time.monotonic()-start,2),updates=updates,scopes=layer.listing()['scopes'],cases=results,sent=False)
    (report_dir/'report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (report_dir/'memories.json').write_text(json.dumps([layer.detail(s['id']) for s in summary['scopes']],ensure_ascii=False,indent=2),encoding='utf-8')
    print(dumps(dict(completed=len(results),seconds=summary['seconds'],updates=len(updates),sent=False)),flush=True)

if __name__=='__main__':main()
