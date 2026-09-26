"""Recover old withheld text from matching local Harness logs; no model calls.

Dry run by default. --apply backs up changed records, then adds review metadata
only. Accepted answers, status, imported chats and original logs stay unchanged.
"""
import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from chatlocal.config import DATA


def scope_key(question,scope):
    return json.dumps([question,{k:scope.get(k) for k in ('platforms','conversations','start','end')}],sort_keys=True)


def text_content(blocks):
    return ''.join(b.get('text','') for b in blocks if isinstance(b,dict) and b.get('type')=='text')


def log_index():
    index={}
    for path in (DATA/'harness/sessions').rglob('session.v3.jsonl'):
        prompt=None;responses={}
        for line in path.open(encoding='utf-8'):
            try:
                event=json.loads(line);data=event.get('data',{})
                if event.get('type')=='user/message' and prompt is None:
                    value=json.loads(text_content(data.get('content',[])))
                    if isinstance(value,dict) and 'question' in value and 'allowed_scope' in value:prompt=value
                elif event.get('type')=='assistant/message':
                    text=text_content(data.get('message',{}).get('content',[])).strip()
                    if text.startswith('```'):text=text.removeprefix('```json').removeprefix('```').removesuffix('```').strip()
                    value=json.loads(text)
                    if isinstance(value,dict) and isinstance(value.get('claims'),list):responses[data.get('turn')]=value
            except (ValueError,TypeError,KeyError):continue
        if prompt and responses:
            key=scope_key(prompt['question'],prompt['allowed_scope'])
            index.setdefault(key,[]).append(dict(path=path,responses=list(responses.values())))
    return index


def rejected_indices(raw,saved):
    """Require exact accepted text/citations, in order, plus the old reject count."""
    candidates=raw['claims'][:12];accepted=[]
    for claim in saved.get('claims',[]):
        matching=[]
        for i,candidate in enumerate(candidates):
            if not isinstance(candidate,dict) or not isinstance(candidate.get('text'),str):continue
            ids=candidate.get('evidence_ids')
            if not isinstance(ids,list) or any(type(mid) is not int for mid in ids):continue
            ids=list(dict.fromkeys(ids))
            if (candidate['text'].strip()==claim['text'] and
                    claim['evidence_ids'] in (ids,ids[:8]) and
                    candidate.get('attachment_ids',[])==claim.get('attachment_ids',[])):
                matching.append(i)
        if len(matching)!=1:return None
        accepted.extend(matching)
    if accepted!=sorted(set(accepted)):return None
    omitted=[i for i in range(len(candidates)) if i not in accepted]
    if len(omitted)!=saved.get('rejected',0):return None
    details=saved.get('rejection_details',[])
    if details and sorted(d.get('claim',-1) for d in details)!=omitted:return None
    return omitted


def recover(record,entry):
    saved=record['result'];responses=entry['responses'];raw=responses[-1]
    omitted=rejected_indices(raw,saved)
    if omitted is None:return None
    changes={}
    if omitted and (not saved.get('rejection_details') or any('raw_claim' not in d for d in saved['rejection_details'])):
        details={d['claim']:d for d in saved.get('rejection_details',[])}
        changes['rejection_details']=[dict(details.get(i,dict(claim=i,reason='historical_rejection')),
                                         raw_claim=raw['claims'][i]) for i in omitted]
    if record.get('validation_repairs') and not saved.get('earlier_rejections') and len(responses)>=2:
        retries=[e for e in record.get('events',[]) if e.get('type')=='validation_retry']
        if len(retries)==1:
            details=retries[0].get('reasons',[]);draft=responses[-2]['claims']
            if details and all(type(d.get('claim')) is int and 0<=d['claim']<len(draft) for d in details):
                changes['earlier_rejections']=[dict(d,raw_claim=draft[d['claim']]) for d in details]
    return changes


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    index=log_index();updates=[];unmatched=0
    with sqlite3.connect(DATA/'agent-sessions.sqlite3',timeout=10) as db:
        for tid,question,original in db.execute("SELECT id,question,record FROM turns WHERE status!='running'"):
            record=json.loads(original);result=record.get('result',{})
            needs_final=result.get('rejected') and (not result.get('rejection_details') or any('raw_claim' not in d for d in result['rejection_details']))
            needs_earlier=record.get('validation_repairs') and not result.get('earlier_rejections')
            if not (needs_final or needs_earlier):continue
            key=scope_key(question,record.get('bundle',{}).get('plan',{}))
            matches=[]
            for entry in index.get(key,[]):
                changes=recover(record,entry)
                if changes:matches.append((entry,changes))
            if len(matches)!=1:unmatched+=1;continue
            entry,changes=matches[0]
            changes['review_recovery']=dict(source=str(entry['path'].relative_to(ROOT)),
                sha256=hashlib.sha256(entry['path'].read_bytes()).hexdigest())
            result.update(changes)
            updates.append(dict(id=tid,original=original,updated=json.dumps(record,ensure_ascii=False),
                recovered=sum(len(changes.get(k,[])) for k in ('rejection_details','earlier_rejections'))))
        backup=None
        if args.apply and updates:
            folder=DATA/'backups';folder.mkdir(exist_ok=True)
            backup=folder/('before-withheld-review-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.json')
            backup.write_text(json.dumps([{k:u[k] for k in ('id','original')} for u in updates],ensure_ascii=False),encoding='utf-8')
            with db:
                for item in updates:
                    cursor=db.execute('UPDATE turns SET record=? WHERE id=? AND record=?',
                        (item['updated'],item['id'],item['original']))
                    if cursor.rowcount!=1:raise RuntimeError('Record changed during recovery; transaction rolled back')
        print(json.dumps(dict(applied=args.apply,matched_turns=len(updates),recovered_items=sum(u['recovered'] for u in updates),
            unmatched_turns=unmatched,backup=str(backup.relative_to(ROOT)) if backup else None,
            turns=[dict(id=u['id'],items=u['recovered']) for u in updates]),ensure_ascii=False))


if __name__=='__main__':main()
