"""Exercise failed cloud responses through the real Harness and local bridge."""
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from chatlocal.agent import run_agent
from chatlocal.llm import INSUFFICIENT
from chatlocal.render import answer_html, evidence_html
from chatlocal.retrieval import make_plan
from chatlocal.store import Store


def main():
    state={}
    secret='do-not-persist-provider-body'
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            state['requests']+=1
            if state['requests']==1:
                delta=dict(role='assistant',tool_calls=[dict(index=0,id='read',type='function',
                    function=dict(name='read_conversation',arguments=json.dumps(
                        dict(platform='qq',conversation_id='selected',order='newest',limit=10))))])
                body='data: '+json.dumps(dict(choices=[dict(index=0,delta=delta,finish_reason='tool_calls')]))+'\n\ndata: [DONE]\n\n'
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream')
                self.end_headers()
                self.wfile.write(body.encode())
            elif state['mode']=='http':
                self.send_response(503);self.end_headers();self.wfile.write(secret.encode())
            else:
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream')
                self.send_header('Content-Length','100000')
                self.end_headers()
                self.wfile.write(('data: '+json.dumps(dict(choices=[dict(index=0,
                    delta=dict(reasoning_content='已接收的部分思考'))]))+'\n\n').encode())
                self.wfile.flush()
                self.wfile.write(b': incomplete stream\n\n')
                self.close_connection=True
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='failure-check-') as folder:
            folder=Path(folder)
            rows=[dict(platform='qq',conversation=cid,conversation_id=cid,sender='甲',
                timestamp='2026-09-20 10:00',content='课程通知',is_self=False,source_id=cid)
                for cid in ('selected','excluded')]
            source=folder/'input.json';source.write_text(json.dumps(rows,ensure_ascii=False),'utf-8')
            store=Store(folder/'chats.sqlite3');store.import_file(source)
            plan=make_plan('课程资讯',conversations=[json.dumps(['qq','selected'])])
            config=dict(API_BASE=f'http://127.0.0.1:{server.server_port}',API_KEY=secret,MODEL='deepseek-flash')
            for mode,kind in [('http','provider_http'),('transport','provider_transport')]:
                state.update(mode=mode,requests=0)
                done=list(run_agent(store,plan,'课程资讯',config=config,include_reasoning=True))[-1]
                record=done['record'];result=record['result'];bundle=record['bundle']
                assert done['status']=='error' and state['requests']==2
                assert result['failure']['kind']==kind and result['failure']['request']==2,result
                assert result['insufficient'] is False and not result['claims']
                assert {m['conversation_id'] for m in bundle['messages']}=={'selected'}
                assert any(e['type']=='model_error' for e in record['events'])
                assert secret not in json.dumps(record)
                assert record['reasoning']==([] if mode=='http' else [dict(request=2,text='已接收的部分思考')])
                # Includes old saved failures where insufficient=True was persisted.
                for old_flag in (True,False):
                    rendered=answer_html(dict(result,insufficient=old_flag),bundle,compact=True)
                    assert INSUFFICIENT not in rendered and '已检索到 1 条消息' in rendered
                    assert rendered.count(result['error'])==1
                assert '课程通知' in evidence_html(store,bundle)
            assert INSUFFICIENT in answer_html(dict(claims=[],insufficient=True),dict(messages=[]))
        print('PASS: real Harness + synthetic HTTP 503 / truncated stream; scope, retained evidence, safe diagnostics and failure/insufficiency distinction. No cloud API called.')
    finally:
        server.shutdown();server.server_close()


if __name__=='__main__':main()
