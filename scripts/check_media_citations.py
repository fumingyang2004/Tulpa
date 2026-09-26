"""Text-supported conclusions, attachment display and bounded citation repair."""
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from chatlocal.agent import run_agent
from chatlocal.llm import validate_answer,INSUFFICIENT
from chatlocal.render import answer_html
from chatlocal.retrieval import make_plan
from chatlocal.store import Store


def main():
    state={}
    bad=dict(claims=[dict(text='文字中双方约定由本人画封面。',evidence_ids=[1,2,3])],insufficient=False)
    fixed=dict(claims=[dict(text='文字中双方约定由本人画封面，附件是否成品尚未核实。',
                           evidence_ids=[1,3],attachment_ids=[2])],insufficient=False)
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['requests']+=1
            if state['requests']==1:
                delta=dict(role='assistant',tool_calls=[dict(index=0,id='context',type='function',
                    function=dict(name='get_context',arguments='{"message_id":1,"radius":3}'))])
                reason='tool_calls'
            else:
                if state['requests']==3:
                    assert any(m['role']=='user' and 'validation_feedback' in m.get('content','') for m in body['messages'])
                    assert any(m['role']=='tool' for m in body['messages']), 'Repair lost prior evidence'
                value=fixed if state['requests']==3 and state['repair_ok'] else bad
                delta=dict(role='assistant',content=json.dumps(value,ensure_ascii=False));reason='stop'
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            item=dict(choices=[dict(index=0,delta=delta,finish_reason=reason)])
            self.wfile.write(('data: '+json.dumps(item)+'\n\ndata: [DONE]\n\n').encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='media-citations-') as folder:
            folder=Path(folder);store=Store(folder/'chats.sqlite3')
            rows=[dict(platform='qq',conversation_id='q',conversation='合成私聊',conversation_type='direct',
                       sender='甲',timestamp=f'2026-09-20 12:0{i}',content=content,is_self=i<2,source_id=str(i),
                       media=[{'kind':'image'}] if i==1 else [])
                  for i,content in enumerate(('这次我来给你画封面。','','对，就这个。'))]
            source=folder/'input.json';source.write_text(json.dumps(rows,ensure_ascii=False),'utf-8');store.import_file(source)
            evidence=[store.message(i) for i in (1,2,3)]
            rejected=validate_answer(bad,evidence,inspections={})
            assert rejected['media_rejected']==1 and rejected['rejection_details'][0]['reason']=='uninspected_image'
            valid=validate_answer(fixed,evidence,inspections={})
            assert valid['claims'][0]['attachment_ids']==[2]
            assert '仅展示发送记录' in answer_html(valid,dict(messages=evidence),compact=True)
            visual=dict(claims=[dict(text='图上写着封面两字。',evidence_ids=[2])])
            assert not validate_answer(visual,evidence,inspections={})['claims']
            assert validate_answer(visual,evidence,inspections={'2:0':dict(message_id=2,ocr='封面')})['claims']
            assert not validate_answer(dict(claims=[dict(text='图上写着封面。',evidence_ids=[],attachment_ids=[2])]),evidence)['claims']
            for mid in (1,999):
                wrong=dict(claims=[dict(text='约定做封面。',evidence_ids=[1],attachment_ids=[mid])])
                assert not validate_answer(wrong,evidence)['claims']
            different=dict(evidence[1],id=4,conversation_id='other')
            wrong=dict(claims=[dict(text='约定做封面。',evidence_ids=[1],attachment_ids=[4])])
            assert not validate_answer(wrong,evidence+[different])['claims']
            many=[dict(evidence[0],id=i) for i in range(1,14)]
            ids=list(range(1,14))
            assert validate_answer(dict(claims=[dict(text='完整引用。',evidence_ids=ids)]),many)['claims'][0]['evidence_ids']==ids
            plan=make_plan('我给朋友做封面的事情呢',platforms=['qq'])
            config=dict(API_BASE=f'http://127.0.0.1:{server.server_port}',API_KEY='synthetic',MODEL='deepseek-flash')
            for repair_ok,max_requests in ((True,6),(False,6),(True,2)):
                state.update(requests=0,repair_ok=repair_ok)
                done=list(run_agent(store,plan,'我给朋友做封面的事情呢',config=config,max_requests=max_requests))[-1]
                r=done['record'];result=r['result']
                assert r['requests']==(3 if max_requests>2 else 2)
                assert r['validation_repairs']==int(max_requests>2)
                if max_requests>2:
                    assert result['earlier_rejections'][0]['raw_claim']==bad['claims'][0]
                    assert '自动纠正前的版本' in answer_html(result,r['bundle'])
                    retry=next(e for e in r['events'] if e['type']=='validation_retry')
                    assert all('raw_claim' not in d for d in retry['reasons'])
                if repair_ok and max_requests>2:
                    assert done['status']=='completed' and result['claims'][0]['attachment_ids']==[2]
                    assert result['rejected']==0
                else:
                    assert done['status']=='error' and not result['insufficient'] and not result['claims']
                assert INSUFFICIENT not in answer_html(result,r['bundle'])
            old=dict(claims=[],insufficient=True,rejected=2)
            assert INSUFFICIENT not in answer_html(old,dict(messages=evidence))
        print('PASS: attachment/text distinction, image-content guard, complete citations, same-scope attachments, real Harness one repair, unchanged request cap and honest failure rendering. No cloud API called.')
    finally:
        server.shutdown();server.server_close()


if __name__=='__main__':main()
