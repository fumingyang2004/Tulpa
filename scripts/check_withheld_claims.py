"""User review of withheld conclusions: exact text, local sources and persistence."""
import json
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from chatlocal.llm import validate_answer,rejection_feedback
from chatlocal.render import answer_html
from chatlocal.agent_sessions import Sessions


class Markup(HTMLParser):
    def __init__(self):
        super().__init__();self.tags=[]
    def handle_starttag(self,tag,attrs):self.tags.append((tag,dict(attrs)))


def main():
    message=dict(id=1,platform='qq',conversation_id='q',conversation='测试',sender='甲',
                 timestamp=1789977600000,content='附近原文',is_self=0,media=[])
    other=dict(message,id=2)
    good=dict(text='已采用的结论。',evidence_ids=[1])
    hidden=dict(text='完整保留\n<script>alert(1)</script>\n![图](https://example.invalid/x)',
                evidence_ids=[2])
    unknown=dict(text='未知引用。',evidence_ids=[999])
    raw=dict(claims=[good,hidden,unknown])
    result=validate_answer(raw,[message,other],unresolved_context_ids={2})
    assert result['claims']==[good] and result['rejected']==2
    assert [d['raw_claim'] for d in result['rejection_details']]==[hidden,unknown]
    assert all('raw_claim' not in d for d in rejection_feedback(result))
    bundle=dict(messages=[message,other])
    rendered=answer_html(result,bundle,compact=True)
    parser=Markup();parser.feed(rendered)
    groups=[a for tag,a in parser.tags if tag=='div' and a.get('class')=='withheld-claims']
    assert len(groups)==1 and not any(tag=='details' and a.get('class')=='withheld-claims' for tag,a in parser.tags)
    assert '已隐藏' not in rendered and '未通过检查' in rendered
    assert rendered.index('&lt;script&gt;') < rendered.index('unverified-note'), 'Reason must follow visible prose'
    assert '未通过检查的引用：M2' in rendered
    assert '&lt;script&gt;' in rendered and '<script>' not in rendered
    assert not any(a.get('src','').startswith('https:') for _,a in parser.tags)
    assert 'M999' in rendered and 'data-message-id="999"' not in rendered
    assert 'data-message-id="2"' in rendered
    assert len([a for _,a in parser.tags if a.get('class')=='claim-text'])==3, 'Rejected prose must be visible and copyable'
    # Even malformed or excess candidates stay available as exact raw output.
    malformed=validate_answer(dict(claims=[None]+[good]*13),[message])
    assert malformed['rejected']==3 and malformed['rejection_details'][0]['raw_claim'] is None
    assert malformed['rejection_details'][-1]['raw_claim']==good
    assert 'null' in answer_html(malformed,bundle)
    old=answer_html(dict(claims=[],insufficient=True,rejected=1),bundle)
    assert '没有保留原始结论' in old and 'withheld-claims' in old
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='withheld-') as tmp:
        sessions=Sessions(Path(tmp)/'sessions.sqlite3');sid=sessions.create()
        tid=sessions.start(sid,'测试',{})
        sessions.finish(tid,'completed',dict(result=result,bundle=bundle,events=[]))
        restored=sessions.history(sid)[0]['record']
        assert restored['result']['rejection_details'][0]['raw_claim']==hidden
        assert answer_html(restored['result'],restored['bundle'],compact=True)==rendered
        memory=json.dumps(sessions.memory(sid),ensure_ascii=False)
        assert 'raw_claim' not in memory and 'alert(1)' not in memory, 'Review drafts leaked into model memory'
    print('PASS: visible rejected prose with reasons underneath, known-source links, unknown-ID isolation, HTML escaping, persistence and evidence-memory exclusion. No cloud API.')


if __name__=='__main__':main()
