"""Peer-language evidence vs bot-policy regressions. Entirely synthetic."""
from copy import deepcopy
from pathlib import Path
import json,sqlite3,sys,tempfile
sys.path.insert(0,str(Path(__file__).parent))
from check_mcp_language_learning import ROOT,Clock,review_fixture,ground_fixture_rows
from chatlocal.mcp_language_store import LanguageStore,LearningError
from chatlocal.mcp_expression_evidence import VERSION


def main():
    assert Path(__import__('chatlocal.mcp_expression_evidence',fromlist=['VERSION']).__file__).resolve()==ROOT/'chatlocal/mcp_expression_evidence.py'
    clock=Clock();b=dict(scope='qq:111:group:222',gid='grant-a',sid='session-a',epoch='epoch-a')
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/'language.sqlite3';s=LanguageStore(path,clock=clock);s.attach(b)
        samples=['不然你来？','又聊起来了','好好好，又开始了','一张接一张地画','🧐🧐🧐','/合成指令 随机参数']+['合成材料']*4
        for i,line in enumerate(samples):s.observe(b,dict(source_id=str(i),text=line,peer=True,at=clock()))
        clock.advance(30);t=s.claim(b,'fixture-context');messages=t['material']['messages']
        def item(i,style,form,kind='sentence_pattern'):
            return dict(situation='合成对话场景',style=style,source_id=messages[i]['source_id'],evidence_quote=samples[i],surface_form=form,form_type=kind)
        valid=dict(expressions=[item(0,'群友用“不然+对象”的短反问反转提议','不然{对象}？'),
            item(1,'群友用“又”强调事情再次发生','又{动作}了'),item(2,'群友以连续的“好”表达夸张或反讽','好好好，{补充}')],jargon=[])
        serial=0
        def submit(task,result):
            nonlocal serial
            serial+=1;return s.submit(b,task['job_id'],task['lease'],'fixture-model-'+str(serial),result)
        def denied(code,fn):
            try:fn()
            except LearningError as e:assert e.code==code,(e.code,code)
            else:raise AssertionError('accepted '+code)
        for style in ['不打断，等话题自己告一段落','用短反问顶回去，不解释','可以调侃接住，也可以不接','不插手别人的指令，安静看着','保持沉默、不表态']:
            bad=deepcopy(valid);bad['expressions'][0]['style']=style
            denied('expression_is_policy',lambda:submit(t,bad))
        for change,code in [({'source_id':'foreign'},'invalid_source'),({'evidence_quote':'机器人自己说的话'},'expression_quote_mismatch'),
                            ({'surface_form':'让我想想{对象}'},'expression_form_mismatch'),({'surface_form':'{全部内容}'},'invalid_expression_form')]:
            bad=deepcopy(valid);bad['expressions'][0].update(change);denied(code,lambda:submit(t,bad))
        bad=deepcopy(valid);bad['expressions'][0]=item(5,'群友发出命令','/合成指令 {参数}','wording')
        denied('expression_is_policy',lambda:submit(t,bad))
        legacy=deepcopy(valid)
        for row in legacy['expressions']:
            for key in ('evidence_quote','surface_form','form_type'):del row[key]
        denied('expression_evidence_required',lambda:submit(t,legacy))
        assert s.status(b)['expression_count']==0
        submit(t,valid);review=s.claim(b,'fixture-context');good=review_fixture(review)
        for reason in ('符合小鲸鱼人格','这是本轮实际做法','机器人应该这样回应'):
            bad=deepcopy(good);bad['reviews'][0]['reason']=reason;denied('expression_review_unsupported',lambda:submit(review,bad))
        bad=deepcopy(good);bad['reviews'][0]['checks']['source_supported']=False
        denied('expression_review_unsupported',lambda:submit(review,bad))
        bad=deepcopy(good);bad['reviews'][0]['evidence_quote']='替换来源'
        denied('expression_quote_mismatch',lambda:submit(review,bad))
        out=submit(review,good);assert out['state']=='completed'
        assert submit(review,good)['state']=='already_completed'
        state=s.status(b,records=True);assert state['library']['checked_expressions']==3
        assert all(e['count']==1 and e['evidence_version']==VERSION for e in state['expressions'])
        # Legacy knowledge is retained, never silently trusted or recounted.
        with s.db() as db:
            db.execute("INSERT INTO expressions(id,scope,situation,style,count,updated,enabled,independence) VALUES('legacy',?,'旧场景','不插话，安静看着',7,1,0,'degraded')",(b['scope'],))
            db.execute('DROP TABLE expression_grounding')
        s=LanguageStore(path,clock=clock);s.attach(b)
        assert len(list(Path(tmp).glob('*.before-peer-evidence-*.sqlite3')))==1
        state=s.status(b,records=True);assert state['expression_count']==4 and state['library']['checked_expressions']==0 and state['library']['pending_expressions']==4
        assert next(e for e in state['expressions'] if e['id']=='legacy')['count']==7
        assert not next(e for e in state['expressions'] if e['id']=='legacy')['enabled']
        assert not s.context(b,'legacy-plan',[],'')['candidates']
        s=LanguageStore(path,clock=clock);assert len(list(Path(tmp).glob('*.before-peer-evidence-*.sqlite3')))==1
        assert s.status(b)['expression_count']==4
        print(json.dumps(dict(status='passed',source='synthetic',policy_regressions=5,evidence_contract=VERSION,
            legitimate_forms=3,duplicate_count=1,legacy_preserved=True,legacy_excluded=True,qq_writes=0,model_calls=0)))


if __name__=='__main__':main()
