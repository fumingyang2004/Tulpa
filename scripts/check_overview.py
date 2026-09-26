"""Overview sampling and evidence-first shortcut checks; no cloud calls."""
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from chatlocal.agent import run_agent
from chatlocal.agent_tools import ChatTools,SCHEMAS
from chatlocal.retrieval import make_plan
from chatlocal.store import Store
from chatlocal.render import answer_html


def main():
    state={}
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['requests']+=1
            n=state['requests']
            if n==1:
                assert body['tool_choice']==dict(type='function',function={'name':'read_overview'})
                name='list_conversations' if state['directory_only'] else 'read_overview'
                args={}
            elif n==2 and not state['directory_only']:
                previous=json.loads(next(m['content'] for m in reversed(body['messages']) if m['role']=='tool'))
                state['mid']=previous['messages'][0]['id']
                name,args='get_context',{'message_id':state['mid'],'radius':1}
            else:name=None
            if name:
                delta=dict(role='assistant',tool_calls=[dict(index=0,id=f'call-{n}',type='function',
                    function=dict(name=name,arguments=json.dumps(args)))])
                reason='tool_calls'
            else:
                claims=[] if state['directory_only'] else [dict(text='通知中有提交安排。',evidence_ids=[state['mid']])]
                delta=dict(role='assistant',content=json.dumps(dict(claims=claims,insufficient=not claims)))
                reason='stop'
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            body=json.dumps(dict(choices=[dict(index=0,delta=delta,finish_reason=reason)]))
            self.wfile.write(('data: '+body+'\n\ndata: [DONE]\n\n').encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='overview-check-') as folder:
            folder=Path(folder);store=Store(folder/'chats.sqlite3')
            rows=[]
            for platform in ('qq','wechat'):
                for cid in ('busy','quiet'):
                    for day in (18,19):
                        for index in range(15 if cid=='busy' else 2):
                            rows.append(dict(platform=platform,conversation_id=cid,conversation=cid,
                                source_id=f'{cid}-{day}-{index}',sender='甲',is_self=False,
                                timestamp=f'2026-09-{day} 10:{index:02d}',
                                content='通知：请周一提交文档。' if index==0 else '日常闲聊'))
            source=folder/'input.json';source.write_text(json.dumps(rows,ensure_ascii=False),'utf-8')
            store.import_file(source)
            question='What did I miss? / 我错过了什么？'
            plan=make_plan(question,start='2026-09-18',end='2026-09-19')
            tools=ChatTools(store,plan)
            first=tools.execute('read_overview',{'limit':8})
            assert len(first['messages'])==8 and first['sampled'] and first['has_more']
            assert len({(m['platform'],m['conversation_id']) for m in first['messages']})==4
            assert all('通知' in m['content'] for m in first['messages'])
            assert len({m['time'][:10] for m in first['messages']})==2
            later=folder/'later.json'
            later.write_text(json.dumps([dict(rows[0],source_id='new',timestamp='2026-09-19 12:00')]),'utf-8')
            store.import_file(later)
            second=ChatTools(store,plan).execute('read_overview',dict(limit=8,offset=first['next_offset'],snapshot_max_id=first['snapshot_max_id']))
            assert not ({m['id'] for m in first['messages']} & {m['id'] for m in second['messages']})
            assert all(m['id']<=first['snapshot_max_id'] for m in second['messages'])
            scoped=make_plan(question,platforms=['qq'],conversations=[json.dumps(['qq','quiet'])],start='2026-09-19',end='2026-09-19')
            selected=ChatTools(store,scoped).execute('read_overview',{})
            assert len(selected['messages'])==2 and not selected['has_more']
            assert all(m['platform']=='qq' and m['conversation_id']=='quiet' and m['time'].startswith('2026-09-19') for m in selected['messages'])
            tiny=ChatTools(store,plan,max_chars=600).execute('read_overview',{})
            assert tiny['budget_limited'] and len(tiny['messages'])<32
            for name,limit in [('list_conversations',30),('read_overview',41),('read_conversation',51)]:
                args={'limit':limit}
                if name=='read_conversation':args.update(platform='qq',conversation_id='busy')
                error=tools.execute(name,args)
                assert error['error_code']=='invalid_arguments' and 'limit' in error['error']
                spec=next(t for t in SCHEMAS if t['name']==name)['parameters']['properties']['limit']
                assert spec['maximum']<limit
            config=dict(API_BASE=f'http://127.0.0.1:{server.server_port}',API_KEY='synthetic',MODEL='deepseek-flash')
            for directory_only in (False,True):
                state.update(requests=0,directory_only=directory_only)
                done=list(run_agent(store,plan,question,config=config))[-1]
                result=done['record']['result']
                if directory_only:
                    assert done['status']=='error' and '尚未读取消息正文' in result['error']
                    assert result['insufficient'] is False
                else:
                    assert done['status']=='completed' and len(result['claims'])==1
                    assert done['record']['requests']==3
                    assert [e['name'] for e in done['record']['events'] if e['type']=='tool_start']==['read_overview','get_context']
                    assert '没有读取客户端已读状态' in answer_html(result,done['record']['bundle'])
        print('PASS: scope/date/platform diversity, notification leads, stable paging, budgets, schema limits, real Harness overview/context/citations and metadata-only failure guard. No cloud API called.')
    finally:
        server.shutdown();server.server_close()


if __name__=='__main__':main()
