"""Run explicitly chosen real-data cases, storing private answers/evidence locally.

Usage: python scripts/verify_live.py reports/private/cases.json --execute-api
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from chatlocal.config import local_path
from chatlocal.store import Store
from chatlocal.retrieval import make_plan,retrieve
from chatlocal.llm import answer
from chatlocal.normalize import decode_export,normalize
from chatlocal.render import scope_text,answer_html,evidence_html,esc


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('cases')
    p.add_argument('--execute-api',action='store_true',required=True)
    args=p.parse_args()
    cases=json.loads(local_path(args.cases).read_text(encoding='utf-8-sig'))
    store=Store()
    folder=ROOT/'reports'/'private'
    folder.mkdir(parents=True,exist_ok=True)
    outputs=[]
    for case in cases:
        bundle=retrieve(store,make_plan(case['question'],**case.get('options',{})))
        start=time.monotonic()
        result=answer(case['question'],bundle)
        assert result['api_called'] and result['claims'],f"No live claims: {case['name']}"
        by_id={m['id']:m for m in bundle['messages']}
        cited={i for claim in result['claims'] for i in claim['evidence_ids']}
        original={}
        archive_cache={}
        for mid in cited:
            provenance=store.provenance(mid)
            assert provenance
            source=provenance[0]
            path=local_path(source['archive'])
            if source['hash'] not in archive_cache:
                import hashlib
                assert hashlib.sha256(path.read_bytes()).hexdigest()==source['hash']
                head,records,kind=decode_export(path)
                archive_cache[source['hash']]=(head,dict(records),kind)
            head,records,kind=archive_cache[source['hash']]
            m=normalize(records[source['pointer']],head,kind)
            evidence=by_id[mid]
            assert all(m[k]==evidence[k] for k in ('platform','conversation_id','sender','timestamp','is_self','source_id'))
            assert m['content'].startswith(evidence['content']) if evidence.get('truncated') else m['content']==evidence['content']
            original[str(mid)]=dict(message=m,source=source)
        cited_platforms={by_id[i]['platform'] for i in cited}
        if case.get('require_both'):
            assert cited_platforms=={'qq','wechat'},'Answer must cite both platforms'
        record=dict(name=case['name'],question=case['question'],options=case.get('options',{}),
            checked_at=datetime.now(timezone.utc).isoformat(),seconds=round(time.monotonic()-start,2),
            retrieval=bundle,result=result,cited_originals=original,stats=store.stats())
        target=local_path(folder/(case['name']+'.json'))
        target.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        html='<meta charset="utf-8"><style>body{font:16px system-ui;max-width:1080px;margin:32px auto;padding:0 20px}details{margin:10px 0}pre{white-space:pre-wrap}summary{cursor:pointer}</style>'
        html+='<h1>'+esc(case['question'])+'</h1><p>'+esc(scope_text(bundle))+'</p>'
        html+=answer_html(result,bundle)+'<h2>原文和附近聊天</h2>'+evidence_html(store,bundle)
        target.with_suffix('.html').write_text(html,encoding='utf-8')
        output=dict(name=case['name'],question=case['question'],messages=len(bundle['messages']),
                    cited_platforms=sorted(cited_platforms),seconds=record['seconds'],model=result['model'],
                    usage=result['usage'],claims=len(result['claims']),rejected=result['rejected'])
        outputs.append(output)
        print(json.dumps(output,ensure_ascii=False),flush=True)
    (folder/'verification.json').write_text(json.dumps(outputs,ensure_ascii=False,indent=2),encoding='utf-8')
    index='<meta charset="utf-8"><h1>真实数据端到端验收</h1><p>所有引用已回查本地归档的 SHA256 和原始记录位置。事实解释仍需人工判断。</p><ul>'
    for case in cases:
        index+=f'<li><a href="{esc(case["name"])}.html">{esc(case["question"])}</a></li>'
    (folder/'index.html').write_text(index+'</ul>',encoding='utf-8')


if __name__=='__main__':
    main()
