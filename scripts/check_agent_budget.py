"""Exercise deeper reasoning/tool budgets through the installed Harness; no cloud."""
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from chatlocal.agent import run_agent,thinking_options
from chatlocal.agent_tools import ChatTools
from chatlocal.config import agent_options,settings
from chatlocal.retrieval import make_plan
from chatlocal.store import Store


def main():
    defaults=agent_options({})
    assert defaults['max_requests']==12 and defaults['max_tool_calls']==30
    for config in ({'AGENT_MAX_REQUESTS':'oops'},{'AGENT_MAX_SECONDS':'0'},
                   {'AGENT_REASONING_EFFORT':'ultra'},{'AGENT_MAX_REQUEST_CHARS':'16000'}):
        try:agent_options(config)
        except ValueError:pass
        else:raise AssertionError('Invalid configuration accepted')
    with patch.dict('os.environ',{'AGENT_MAX_REQUESTS':'15'}):
        assert agent_options(settings())['max_requests']==15
    assert thinking_options({'API_BASE':'http://localhost:8000'},defaults)=={}
    assert thinking_options({'API_BASE':'https://api.deepseek.com'},dict(defaults,reasoning_effort='none'))=={'thinking':{'type':'disabled'}}
    captured=[]
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            captured.append(body);number=len(captured)
            delta=dict(role='assistant',reasoning_content=f'fixture-reasoning-{number}')
            if number<12:
                delta['tool_calls']=[dict(index=0,id=f'read-{number}',type='function',function=dict(
                    name='read_conversation',arguments=json.dumps(dict(platform='qq',conversation_id='selected',
                        limit=20,offset=(number-1)*20))))]
                reason='tool_calls'
            else:
                delta['content']=json.dumps(dict(claims=[dict(text='已分批读取指定会话的相关记录。',evidence_ids=[1,220])],insufficient=False),ensure_ascii=False)
                reason='stop'
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            self.wfile.write(('data: '+json.dumps(dict(choices=[dict(index=0,delta=delta,finish_reason=reason)]))+'\n\ndata: [DONE]\n\n').encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    original_stream=httpx.Client.stream
    def local_only(client,method,url,*args,**kwargs):
        # Test official-provider parameters without sending anything to that host.
        assert url=='https://api.deepseek.com/chat/completions',url
        return original_stream(client,method,f'http://127.0.0.1:{server.server_port}/chat/completions',*args,**kwargs)
    try:
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='budget-check-') as folder:
            folder=Path(folder);store=Store(folder/'chats.sqlite3')
            rows=[dict(platform='qq',conversation_id='selected',conversation='合成群',sender='甲',
                timestamp=1789956000000+i*1000,content=f'第{i}条安排，请核对后续。',is_self=False,source_id=str(i)) for i in range(320)]
            rows.append(dict(rows[0],platform='wechat',source_id='excluded'))
            source=folder/'input.json';source.write_text(json.dumps(rows,ensure_ascii=False),'utf-8');store.import_file(source)
            plan=make_plan('详细读取群消息',platforms=['qq'])
            scoped=ChatTools(store,plan,max_calls=2)
            assert 'error' not in scoped.execute('get_data_status',{})
            assert 'error' not in scoped.execute('get_data_status',{})
            assert 'error' in scoped.execute('get_data_status',{})
            config=dict(API_BASE='https://api.deepseek.com',API_KEY='synthetic-only',MODEL='deepseek-flash')
            with patch.object(httpx.Client,'stream',local_only):
                done=list(run_agent(store,plan,'详细读取群消息',config=config))[-1]
            r=done['record']
            assert done['status']=='completed',r.get('result')
            assert r['requests']==12 and len(captured)==12
            assert sum(e['type']=='tool_start' for e in r['events'])==11
            assert len(r['bundle']['messages'])==220 and r['result']['claims']
            assert {m['platform'] for m in r['bundle']['messages']}=={'qq'}
            assert all(b['max_tokens']==16384 for b in captured)
            assert all(b['thinking']=={'type':'enabled'} and b['reasoning_effort']=='high' for b in captured[1:-1])
            assert captured[0]['thinking']==captured[-1]['thinking']=={'type':'disabled'}
            assert captured[-1]['tool_choice']=='none'
            for index,body in enumerate(captured[1:],2):
                reasoning=[m.get('reasoning_content') for m in body['messages'] if m['role']=='assistant']
                assert all(f'fixture-reasoning-{n}' in reasoning for n in range(1,index)),(index,reasoning)
            assert r['audit'][-1]['reasoning_input_chars']>0
            assert all(a['reasoning_output_chars']>0 for a in r['audit'])
            assert all(e['request_limit']==12 for e in r['events'] if e['type']=='model_start')
            assert 'fixture-reasoning-' not in json.dumps(r),'Raw reasoning leaked into UI/history record'
            assert r['budget']['max_messages']==300 and r['budget']['max_seconds']==600
        print('PASS: 12 model requests, 11 tool calls, 220 scoped messages; high thinking parameters and complete reasoning roundtrip via real Harness; dynamic limits, final-call stop, configuration validation. No cloud API.')
    finally:server.shutdown();server.server_close()


if __name__=='__main__':main()
