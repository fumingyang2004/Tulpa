"""Synthetic realtime learning contract. No QQ, historical database or model calls."""
import json
from pathlib import Path
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
import time

ROOT = Path(__file__).resolve().parents[1]
if '--package' in sys.argv:ROOT=Path(sys.argv[sys.argv.index('--package')+1]).resolve()
sys.path.insert(0, str(ROOT))


class Clock:
    def __init__(self): self.now = 1000.
    def __call__(self): return self.now
    def advance(self, seconds): self.now += seconds


def expression_fixture(message,situation,style):
    quote=message['text'][:12].strip()
    return dict(situation=situation,style=style,source_id=message['source_id'],
                evidence_quote=quote,surface_form=quote,form_type='wording')


def review_fixture(task,*,reject=(),reason='原文固定用词支持该表达的观察描述'):
    from chatlocal.mcp_expression_evidence import CHECKS
    return dict(reviews=[dict(index=i,accept=i not in reject,reason=reason,evidence_quote=e['evidence_quote'],
                             checks={k:i not in reject for k in CHECKS}) for i,e in enumerate(task['material']['expressions'])])


def ground_fixture_rows(store,scope):
    """Explicit synthetic records for selection/count tests, never real data."""
    from chatlocal.mcp_expression_evidence import VERSION
    with store.db() as db:
        db.execute('''INSERT OR IGNORE INTO expression_grounding
                      SELECT scope,id,?,'wording','合成形式','fixture-batch','fixture-source','fixture-hash',updated
                      FROM expressions WHERE scope=?''',(VERSION,scope))


def main():
    from chatlocal.mcp_language_store import LanguageStore, LearningError
    assert Path(__import__('chatlocal.mcp_language_store',fromlist=['LanguageStore']).__file__).resolve()==ROOT/'chatlocal/mcp_language_store.py'
    clock = Clock()
    with tempfile.TemporaryDirectory(prefix='tulpa-language-') as tmp:
        store = LanguageStore(Path(tmp)/'learning.sqlite3', clock=clock)
        scope = 'qq:111:group:222'
        binding = dict(scope=scope, gid='grant-a', sid='session-a', epoch='connection-a')
        store.configure(binding, enabled=True, allow_degraded=True)
        serial=0
        def add(n, tag='sample', b=None):
            for i in range(n):
                store.observe(b or binding, dict(source_id=f'{tag}-{i}', text=f'合成材料 云朵开机 {tag} {i}', at=clock(), peer=True))
        def claim(b=None,context=None):return store.claim(b or binding,context or ('fresh-'+(b or binding)['gid']))
        def submit(t,result,b=None,invocation=None):
            nonlocal serial
            serial+=1
            return store.submit(b or binding,t['job_id'],t['lease'],invocation or f'model-call-{serial}',result)
        def rejected(code,fn):
            try:fn()
            except LearningError as exc:assert exc.code==code,(exc.code,code)
            else:raise AssertionError('expected '+code)
        rejected('invalid_learning_policy',lambda:LanguageStore(Path(tmp)/'bad.sqlite3',policy=['wrong']))
        def extraction(t,tag='base',words=True):
            ids=[m['source_id'] for m in t['material']['messages']]
            return dict(expressions=[expression_fixture(t['material']['messages'][i],f'{tag} 情境 {i}',f'{tag} 抽象方式 {i}') for i in range(3)],
                        jargon=[dict(term='云朵开机',source_id=ids[3])] if words else [])
        def finish(t,tag='base',b=None,accept=True):
            r=extraction(t,tag);submit(t,r,b)
            review=claim(b)
            assert review['stage']=='review'
            assert len(review['material']['messages'])>=10
            return submit(review,review_fixture(review,reject=() if accept else (0,1,2)),b)
        def batch(tag='base',b=None):
            clock.advance(31);add(10,tag+'-'+str(clock()),b)
            t=claim(b);assert t and t['stage']=='extract',t
            finish(t,tag,b)
            return t
        add(9)
        clock.advance(29)
        assert claim() is None
        clock.advance(1)
        assert claim() is None
        add(1, 'tenth')
        task = claim()
        assert task['stage'] == 'extract' and len(task['material']['messages']) == 10
        assert claim() is None
        bad=extraction(task);bad['expressions'][0]['source_id']='fabricated'
        rejected('invalid_source',lambda:submit(task,bad))
        with store.db() as db:assert db.execute('SELECT count(*) FROM expressions').fetchone()[0]==0
        valid=extraction(task)
        submit(task,valid,invocation='same-invocation')
        assert submit(task,valid)['state']=='already_completed'
        review=claim();assert review['stage']=='review'
        reviews=review_fixture(review,reject=(1,))
        rejected('separate_model_call_required',lambda:submit(review,reviews,invocation='same-invocation'))
        submit(review,reviews)
        assert submit(review,reviews)['state']=='already_completed'
        assert store.status(binding)['expression_count']==2
        assert store.status(binding)['jargon_count']==1
        assert not store.context(binding,'plan-0',['云朵开机'],'intent')['candidates']
        # The exact delivered IDs cannot create another batch after a retry.
        add(9);add(1,'tenth');clock.advance(31)
        assert claim() is None
        print('PASS boundaries: 9/10, 29/30; fake sources, distinct invocation, review rejection, replay')

        # Duplicate expressions and terms contribute only once per batch.
        add(10,'duplicates');task=claim();r=extraction(task)
        r['expressions']=[r['expressions'][0]]*3;r['jargon']*=3
        submit(task,r);review=claim();submit(review,review_fixture(review))
        data=store.status(binding,records=True)
        assert next(x for x in data['expressions'] if x['situation']=='base 情境 0')['count']==2
        assert data['jargon'][0]['count']==2
        # No model service is configured; weighted fallback + external selection.
        for i in range(3):batch('set'+str(i))
        candidates=store.context(binding,'plan-1',['云朵开机'],'合成意图')
        assert 5<=len(candidates['candidates'])<=10 and candidates['method']=='count_weighted'
        ids=[x['id'] for x in candidates['candidates']]
        assert len(store.select(binding,'plan-1',ids[:5])['selected'])==5
        assert store.select(binding,'plan-1',ids[:5])['selected']==store.select(binding,'plan-1',ids[:5])['selected']
        rejected('invalid_selection',lambda:store.select(binding,'plan-1',ids+['made-up']))
        store.context(binding,'plan-zero',[],'intent');assert store.select(binding,'plan-zero',[])['selected']==[]
        vector=store.context(binding,'plan-vector',[],'intent',retriever=lambda s,q,k:ids*20+['foreign-id'])
        assert vector['method']=='configured_retriever' and len(vector['candidates'])<=50 and all(x['id'] in ids for x in vector['candidates'])
        # A retrieval integration cannot return foreign rows or exceed 50.
        with store.db() as db:
            for i in range(60):db.execute('INSERT INTO expressions(id,scope,situation,style,count,updated,independence) VALUES(?,?,?,?,2,?,?)',
                                         (f'vector-{i}',scope,f'vector fixture {i}','abstract',clock(),'degraded'))
            db.execute('INSERT INTO expressions(id,scope,situation,style,count,updated,independence) VALUES(?,?,?,?,2,?,?)',
                       ('foreign-vector','qq:999:group:222','private sentinel','private sentinel',clock(),'degraded'))
        ground_fixture_rows(store,scope)
        ground_fixture_rows(store,'qq:999:group:222')
        vector=store.context(binding,'plan-large-vector',[],'intent',retriever=lambda s,q,k:['foreign-vector']+[f'vector-{i}' for i in range(60)])
        assert len(vector['candidates'])==50 and 'private sentinel' not in json.dumps(vector)
        began=time.perf_counter()
        for _ in range(100):assert store.context(binding,'plan-large-vector',[],'intent')==vector
        print(json.dumps(dict(fixture_expressions=71,cached_context_calls=100,cached_context_ms=round((time.perf_counter()-began)*1000,2),
                              max_candidates=50,payload_chars=len(json.dumps(vector)),remote_calls=0)))

        # Count jumped from 2 to 5: infer current count once, not each threshold.
        t=claim();assert t['stage']=='with_context'
        assert t['material']['term']=='云朵开机'
        submit(t,dict(meaning='开始进入工作状态的群内戏称',insufficient=False,reason='反复出现在开始做事时'))
        plain=claim();assert plain['stage']=='without_context' and set(plain['material'])=={'term'}
        submit(plain,dict(meaning='',insufficient=True,reason='普通词典未确定'))
        compare=claim();assert compare['stage']=='compare'
        submit(compare,dict(is_similar=False,sufficient=True,reason='合成上下文支持特别用法'))
        word=store.status(binding,records=True)['jargon'][0]
        assert word['last_inference_count']==5 and word['is_jargon'] and not claim()
        assert len(store.context(binding,'plan-word',['今天云朵开机'],'intent')['jargon'])==1
        assert store.context(binding,'plan-no-word',['无关'],'intent')['jargon']==[]
        print('PASS expression review/count/weighted/vector caps/selection; jargon extraction/context/three stages')

        # Scope isolation, including a reused host context and foreign sources.
        other=dict(binding,scope='qq:111:group:223',gid='grant-beta',sid='session-beta')
        second=dict(binding,scope='qq:999:group:222',gid='grant-other-account',sid='session-other')
        for b in (other,second):
            store.configure(b,enabled=True,allow_degraded=True)
            # The foreign sentinel is a fixture for the optional retriever,
            # remove it before the truly empty account-switch scenario.
            if b==second:
                with store.db() as db:db.execute("DELETE FROM expressions WHERE id='foreign-vector'")
            assert store.status(b,records=True)['expressions']==[] and store.status(b,records=True)['jargon']==[]
            assert store.context(b,'blank-'+b['gid'],['云朵开机'],'intent')['jargon']==[]
        rejected('new_host_context_required',lambda:store.claim(other,'fresh-grant-a'))
        rejected('new_host_context_required',lambda:store.configure(dict(binding,scope=other['scope']),enabled=True))
        rejected('task_unavailable',lambda:submit(task,r,second))
        add(10,'b',second);clock.advance(30);bt=claim(second);bad=extraction(bt);bad['expressions'][0]['source_id']=task['material']['messages'][0]['source_id']
        rejected('invalid_source',lambda:submit(bt,bad,second))
        store.cancel(dict(sid=second['sid']))
        rejected('task_unavailable',lambda:submit(bt,extraction(bt),second))
        saved=store.status(binding,records=True)
        store.cancel();binding['epoch']='connection-restored'
        assert store.status(binding,records=True)['expressions']==saved['expressions']
        assert store.context(binding,'restore',['云朵开机'],'intent')['jargon']
        print('PASS A/alpha → A/beta empty → B/alpha empty → foreign and late denied → A/alpha preserved')

        # Four concurrent scopes; global limit three, same session exclusive.
        scopes=[dict(binding,scope=f'qq:111:group:{800+i}',gid=f'parallel-{i}',sid=f'parallel-{i}') for i in range(4)]
        for b in scopes:store.configure(b,enabled=True);add(10,b['sid'],b)
        clock.advance(30)
        with ThreadPoolExecutor(max_workers=8) as pool:tasks=list(pool.map(claim,scopes))
        assert sum(t is not None for t in tasks)==3
        assert sum(t is not None for t in list(ThreadPoolExecutor(4).map(claim,scopes)))==0
        store.cancel();clock.advance(31)

        # Failure budgets, evidence expiry and durable restart cancellation.
        add(10,'timeout');t=claim();assert t
        store.submit(binding,t['job_id'],t['lease'],'timeout-1',failure='timeout')
        assert claim() is None
        clock.advance(store.cfg['retry_seconds'])
        retry=claim();assert retry['job_id']==t['job_id'] and retry['lease']!=t['lease']
        store.submit(binding,retry['job_id'],retry['lease'],'timeout-2',failure='invalid_json')
        assert not claim()
        clock.advance(31);add(10,'restart');pending=claim();assert pending
        store=LanguageStore(store.path,clock=clock)
        rejected('task_unavailable',lambda:submit(pending,extraction(pending)))
        assert store.status(binding,records=True)['expressions']==saved['expressions']
        clock.advance(31);add(10,'expire');pending=claim();clock.advance(86401)
        rejected('task_unavailable',lambda:submit(pending,extraction(pending)))
        store.status(binding)
        with store.db() as db:
            assert db.execute("SELECT count(*) FROM batches WHERE scope=? AND evidence!='[]'",(scope,)).fetchone()[0]==0
            assert db.execute('SELECT count(*) FROM jargon_evidence WHERE scope=?',(scope,)).fetchone()[0]==0
        print('PASS concurrent max3, lease exclusivity, bounded failure, restart preserves counts, evidence expiry')

        # Milestones at 4/8/25/100, completion and manual precedence. These are
        # direct synthetic fixtures for counting edge states, not real learning.
        with store.db() as db:
            jid=word['id']
            db.execute('UPDATE jargon SET last_inference_count=0,count=4 WHERE id=?',(jid,))
        for count in (4,8,25,100):
            clock.advance(31)
            # Fresh batch supplies real bounded fixture evidence at each stage.
            add(10,'milestone'+str(count));t=claim();finish(t,'milestone'+str(count))
            with store.db() as db:db.execute('UPDATE jargon SET count=? WHERE id=?',(count,jid))
            t=claim();assert t and t['stage']=='with_context',t
            submit(t,dict(meaning='合成释义',insufficient=False,reason='合成证据'))
            t=claim();submit(t,dict(meaning='通常意义',insufficient=False,reason='通常语义'))
            t=claim();submit(t,dict(is_similar=False,sufficient=True,reason='特定语义不同'))
            word=next(x for x in store.status(binding,records=True)['jargon'] if x['id']==jid)
            assert word['last_inference_count']==count and bool(word['complete'])==(count==100)
            assert not claim()
            clock.advance(3601) # each scenario has its own hourly budget
        store.manage(binding,'jargon',jid,meaning='人工固定定义')
        with store.db() as db:db.execute('UPDATE jargon SET count=101,complete=0 WHERE id=?',(jid,))
        assert not claim()
        assert store.status(binding,records=True)['jargon'][0]['meaning']=='人工固定定义'
        # A human correction arriving during the model's compare stage wins.
        with store.db() as db:
            db.execute("INSERT INTO jargon(id,scope,term,folded,count,updated) VALUES('manual-race',?,'合成待核词','合成待核词',4,?)",(scope,clock()))
            db.execute("INSERT INTO jargon_evidence SELECT scope,'manual-race',batch_id,expires,body FROM jargon_evidence WHERE item_id=?",(jid,))
        t=claim();assert t['stage']=='with_context'
        submit(t,dict(meaning='模型旧释义',insufficient=False,reason='fixture'))
        t=claim();submit(t,dict(meaning='通常意义',insufficient=False,reason='fixture'))
        t=claim();assert t['stage']=='compare'
        store.manage(binding,'jargon','manual-race',meaning='人工新释义')
        submit(t,dict(is_similar=False,sufficient=True,reason='迟到模型结果'))
        protected=next(w for w in store.status(binding,records=True)['jargon'] if w['id']=='manual-race')
        assert protected['meaning']=='人工新释义' and protected['manual'] and protected['independence']=='manual'
        # Casefold substring match is limited to ten; candidates stay invisible.
        with store.db() as db:
            for i in range(12):
                db.execute("INSERT INTO jargon(id,scope,term,folded,count,meaning,is_jargon,manual,updated) VALUES(?,?,?,?,4,'fixture',1,1,?)",(f'manual-{i}',scope,f'TERM{i}',f'term{i}',clock()))
        assert len(store.context(binding,'max-words',[' '.join(f'term{i}' for i in range(12))],'intent')['jargon'])==10
        store.configure(binding,enabled=True,allow_degraded=False)
        ctx=store.context(binding,'quality', ['云朵开机'],'intent')
        assert ctx['candidates'] and ctx['jargon'][0]['meaning']=='人工固定定义'
        store.configure(binding,enabled=False)
        paused=store.status(binding,records=True)
        assert not paused['enabled'] and paused['expressions'] and paused['independence']=='degraded'
        rejected('learning_disabled',lambda:claim())
        store.configure(binding,enabled=True)
        clock.advance(30);add(10,'empty-extraction');t=claim()
        assert submit(t,dict(expressions=[],jargon=[]))['state']=='completed'
        assert not claim()
        print('PASS 4/8/25/100, manual precedence, max10/casefold, default self-check usability, pause retains library')


if __name__ == '__main__': main()
