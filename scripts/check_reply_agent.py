"""Real installed Harness, isolated chat data and local deterministic provider."""
import json,sys,tempfile,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from chatlocal.agent import run_agent
from chatlocal.agent_profiles import profile_config
from chatlocal.retrieval import Plan
from chatlocal.store import Store
from chatlocal.reply_agent import PROMPT


def main():
    captured=[];case=dict(text='不了不了今天躺平😋',previous='',use_tools=True)
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));captured.append(body)
            assert not any('send' in t['function']['name'] or 'approv' in t['function']['name'] for t in body['tools'])
            system='\n'.join(m['content'] for m in body['messages'] if m['role']=='system')
            # The persona is user editable. Verify delivery, not old wording.
            assert PROMPT in system
            assert '最终输出会原样填入回复编辑框' in body['messages'][-1]['content']
            if len(captured)==1:
                assert body['tool_choice']=='auto', 'Preloaded reply context must not force extra searches'
                current=json.loads(next(m['content'] for m in body['messages'] if m['role']=='user'))
                assert current['reply']['current']['messages'] and current['reply']['style_examples']
                assert current['reply']['message_id']==1
                assert current['reply']['previous_draft']==case['previous']
            if len(captured)==1 and case['use_tools']:
                delta=dict(role='assistant',tool_calls=[dict(index=0,id='context',type='function',function=dict(name='get_context',arguments='{"message_id":1,"radius":2}'))]);reason='tool_calls'
            else:
                if case['use_tools']:
                    assert body['tool_choice']=='none'
                    assert any('纯文本回复' in m.get('content','') for m in body['messages'][-2:])
                    assert not any('约定的最终 JSON' in m.get('content','') for m in body['messages'][-2:])
                delta=dict(role='assistant',reasoning_content='fixture hidden reasoning; never part of the editable draft',content=case['text']);reason='stop'
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            for d,finish in ((delta,None),({},reason)):
                self.wfile.write(('data: '+json.dumps(dict(id='fixture',object='chat.completion.chunk',choices=[dict(index=0,delta=d,finish_reason=finish)],usage=dict(prompt_tokens=100,completion_tokens=20,total_tokens=120)))+'\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider);threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='reply-agent-') as tmp:
            p=Path(tmp);s=Store(p/'test.sqlite3');source=p/'input.json'
            source.write_text(json.dumps([dict(platform='qq',conversation_id='1:direct:2',conversation='好友',sender='朋友' if i==1 else '我',sender_id='2' if i==1 else '1',is_self=i==2,
                content=text,timestamp=f'2026-09-24 10:00:0{i}',source_id=str(i)) for i,text in ((1,'晚上打游戏吗？'),(2,'不了不了今天废了😋'))]),encoding='utf-8');s.import_file(source)
            config=profile_config(dict(API_KEY='fixture',API_BASE=f'http://127.0.0.1:{server.server_port}/v1',MODEL='fixture'),'quick')
            for text,previous,use_tools in [('不了不了今天躺平😋','',True),('今天已经废了哈哈，下次喊我','不了不了今天躺平😋',True),('几点啊','',False)]:
                captured.clear();case.update(text=text,previous=previous,use_tools=use_tools)
                events=list(run_agent(s,Plan(platforms=['qq'],conversations=[json.dumps(['qq','1:direct:2'])]),'帮我婉拒',config=config,max_requests=2,
                    reply_context=dict(message_id=1,instruction='婉拒',previous_draft=previous)))
                final=events[-1];assert final['status']=='completed',final
                result=final['record']['result']
                assert result['reply_text']==text and result['context_ids']==[1,2]
                assert not {'basis','evidence_ids','style_ids'} & result.keys()
                assert final['record']['requests']==len(captured)==(2 if use_tools else 1) and final['record']['validation_repairs']==0
                assert final['record']['bundle']['tool_calls']>=(3 if use_tools else 2)
                assert 'fixture hidden reasoning' not in result['reply_text']
            # A longer real self utterance remains eligible as a style example.
            long_text='这个方案我先把背景讲一下。'*40
            source.write_text(json.dumps([dict(platform='qq',conversation_id='1:direct:2',conversation='好友',sender='我',sender_id='1',is_self=True,
                content=long_text,timestamp='2026-09-24 10:00:03',source_id='3')]),encoding='utf-8');s.import_file(source)
            from chatlocal.reply_agent import prepare
            from chatlocal.agent_tools import ChatTools
            tools=ChatTools(s,Plan(platforms=['qq'],conversations=[json.dumps(['qq','1:direct:2'])]))
            seed=prepare(tools,dict(message_id=1,tulpa_disabled=True))
            assert seed['message_id']==1
            assert any(m['content']==long_text for example in seed['style_examples'] for m in example['messages'])
    finally:server.shutdown()
    print('PASS: real Harness plain-text initial/re-generation, user persona preserved, optional search/direct answer, scoped reads, output reminder after tools, budget instruction, reasoning separated, zero evidence-format repairs; no cloud calls or sends.')


if __name__=='__main__':main()
