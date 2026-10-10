"""Peer-only jargon rediscovery, synthetic batches/clock; no network or model."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).parent))
from check_mcp_language_learning import Clock, ROOT
from chatlocal.mcp_jargon_match import find_known_terms
from chatlocal.mcp_language_store import LanguageStore, LearningError


def main():
    assert Path(__import__('chatlocal.mcp_jargon_match',fromlist=['x']).__file__).resolve()==ROOT/'chatlocal/mcp_jargon_match.py'
    assert find_known_terms(['cat','C++','云朵开机','ABC'],[
        dict(source_id='1',text='concatenate C++ 现在云朵开机 abc')])==[
        dict(term='C++',source_id='1'),dict(term='云朵开机',source_id='1'),dict(term='ABC',source_id='1')]
    assert not find_known_terms(['abc'],[dict(source_id='1',text='xabc abc_ 中文abc中文')])
    clock=Clock()
    with tempfile.TemporaryDirectory(prefix='tulpa-jargon-matching-') as tmp:
        store=LanguageStore(Path(tmp)/'language.sqlite3',clock=clock)
        b=dict(scope='qq:111:group:222',gid='a',sid='a',epoch='a')
        store.attach(b);serial=0
        def batch(body='云朵开机',terms=(),binding=None):
            nonlocal serial
            serial+=1;binding=binding or b;clock.advance(31)
            for i in range(10):
                store.observe(binding,dict(peer=True,source_id=f'{serial}-{i}',text=f'{body} 样本{i}',at=clock()))
            t=store.claim(binding,'context-'+binding['gid']);assert t and t['stage']=='extract',t
            result=dict(expressions=[],jargon=[dict(term=word,source_id=t['material']['messages'][0]['source_id']) for word in terms])
            response=store.submit(binding,t['job_id'],t['lease'],f'call-{serial}',result)
            return t,result,response
        def word(scope=None):
            with store.db() as db:
                r=db.execute('SELECT * FROM jargon WHERE scope=? AND folded=?',(scope or b['scope'],'云朵开机')).fetchone()
                return dict(r) if r else None
        batch(terms=['云朵开机','云朵开机'])
        assert word()['count']==1 and not word()['is_jargon']
        t,result,response=batch()
        assert response['jargon_observation']==dict(model_terms=0,cached_terms_checked=1,cached_terms_matched=1,supplemented_terms=1,new_batch_hits=1)
        assert word()['count']==2
        with ThreadPoolExecutor(4) as pool:
            retries=list(pool.map(lambda _:store.submit(b,t['job_id'],t['lease'],'retry-call',result),range(4)))
        assert all(r['state']=='already_completed' for r in retries) and word()['count']==2
        batch(terms=['云朵开机']);assert word()['count']==3
        batch();assert word()['count']==4
        assert not store.context(b,'unconfirmed',['云朵开机'],'test')['jargon']
        for stage,result in [
            ('with_context',dict(meaning='合成样本里的开始工作',insufficient=False,reason='合成用例支持')),
            ('without_context',dict(meaning='',insufficient=True,reason='通常词义未确定')),
            ('compare',dict(is_similar=False,sufficient=True,reason='用例有一致的特定含义'))]:
            t=store.claim(b,'context-a');assert t['stage']==stage,t
            if stage=='with_context':
                assert len(t['material']['evidence'])<=3
                assert all('云朵开机' in item['messages'][0]['text'] for item in t['material']['evidence'])
            if stage=='without_context':assert set(t['material'])=={'term'}
            store.submit(b,t['job_id'],t['lease'],'meaning-'+stage,result)
        assert word()['is_jargon'] and word()['last_inference_count']==4
        assert store.context(b,'confirmed',['云朵开机'],'test')['jargon'][0]['term']=='云朵开机'
        assert word()['independence']=='degraded'
        print('PASS omitted extraction rematched; batch/concurrent replay dedup; real evidence; threshold4 and three stages')

        # A failed model stage cannot manufacture hits from the cached words.
        clock.advance(31)
        for i in range(10):store.observe(b,dict(peer=True,source_id=f'failed-{i}',text='云朵开机',at=clock()))
        t=store.claim(b,'context-a');assert t['stage']=='extract'
        store.submit(b,t['job_id'],t['lease'],'failure-call',failure='timeout')
        assert word()['count']==4
        store.cancel()
        store=LanguageStore(store.path,clock=clock)
        b=dict(b,gid='new-grant',sid='new-session',epoch='restarted');store.attach(b)
        batch();assert word()['count']==5
        try:store.submit(dict(b,gid='a',sid='a',epoch='a'),t['job_id'],t['lease'],'late',result)
        except LearningError:pass
        else:raise AssertionError('old lease revived')
        for scope in ('qq:111:group:223','qq:999:group:222'):
            other=dict(b,scope=scope,gid=scope,sid=scope);store.attach(other)
            batch(binding=other)
            assert word(scope) is None and not store.context(other,scope,['云朵开机'],'test')['jargon']
        # No actual peer batch can be formed using model/self messages.
        clock.advance(31)
        for i in range(10):store.observe(b,dict(peer=False,source_id=f'self-{i}',text='云朵开机',at=clock()))
        assert store.claim(b,'context-new-grant') is None
        assert word()['count']==5
        store.manage(b,'jargon',word()['id'],enabled=False)
        batch(terms=['云朵开机']);assert word()['count']==5 and not word()['enabled']
        store.manage(b,'jargon',word()['id'],enabled=True,meaning='人工固定释义')
        batch(terms=['云朵开机']);assert word()['count']==5 and word()['meaning']=='人工固定释义'
        print('PASS restart/new grant continues; old lease, foreign account/group and SELF blocked; manual/disabled precedence')

        # More terms than the recent pool: bounded lookup, no hidden full-library scan.
        limited=dict(b,scope='qq:111:group:444',gid='limited',sid='limited');store.attach(limited)
        with store.db() as db:
            for i in range(70):
                term=f'term{i:03}'
                db.execute('INSERT INTO jargon(id,scope,term,folded,count,updated) VALUES(?,?,?,?,1,?)',
                           (term,limited['scope'],term,term,clock()+i))
        _,_,out=batch(' '.join(f'term{i:03}' for i in range(70)),binding=limited)
        assert out['jargon_observation']['cached_terms_checked']==50
        assert out['jargon_observation']['new_batch_hits']==50
        with store.db() as db:
            assert db.execute('SELECT count(*) FROM jargon WHERE scope=? AND count=2',(limited['scope'],)).fetchone()[0]==50
        limited_store=LanguageStore(Path(tmp)/'disabled-cache.sqlite3',clock=clock,policy=dict(jargon_cache_terms=0))
        limited_store.attach(b)
        with limited_store.db() as db:
            db.execute("INSERT INTO jargon(id,scope,term,folded,count,updated) VALUES('known',?,'云朵开机','云朵开机',1,?)",(b['scope'],clock()))
        clock.advance(31)
        for i in range(10):limited_store.observe(b,dict(peer=True,source_id=f'no-match-{i}',text='云朵开机',at=clock()))
        task=limited_store.claim(b,'no-cache-context')
        out=limited_store.submit(b,task['job_id'],task['lease'],'no-cache-call',dict(expressions=[],jargon=[]))
        assert out['jargon_observation']['new_batch_hits']==out['jargon_observation']['cached_terms_checked']==0
        messages=[dict(source_id=str(i),text=' '.join(f'term{j:03}' for j in range(50))) for i in range(20)]
        began=time.perf_counter()
        for _ in range(100):assert len(find_known_terms([f'term{i:03}' for i in range(50)],messages))==50
        print(json.dumps(dict(status='passed',sample_batches=100,terms_per_batch=50,messages_per_batch=20,
            matching_ms=round((time.perf_counter()-began)*1000,2),extra_model_calls=0,network_calls=0)))


if __name__=='__main__':main()
