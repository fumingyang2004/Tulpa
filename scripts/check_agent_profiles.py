"""Preset selection through the actual UI callback + Harness, with local fake API."""
import json
import html
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from chatlocal.agent_profiles import profile_catalog,profile_config
from chatlocal.agent_sessions import Sessions
from chatlocal.config import agent_options
from chatlocal.store import Store
from chatlocal.ui import build_app


def main():
    catalog=profile_catalog()
    assert catalog['default']=='deep' and len(catalog['profiles'])==4
    captured=[]
    thought='模拟思考第一行\n\n<script>不执行</script>\n最后一行'
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            captured.append(body)
            if len(captured)==1:
                delta=dict(role='assistant',tool_calls=[dict(index=0,id='read',type='function',function=dict(
                    name='read_conversation',arguments=json.dumps(dict(platform='qq',conversation_id='selected'))))])
                finish='tool_calls'
            else:
                delta=dict(role='assistant',content=json.dumps(dict(claims=[dict(text='选中群里有课程通知。',evidence_ids=[1])],insufficient=False),ensure_ascii=False))
                if body.get('thinking',{}).get('type')=='enabled':delta['reasoning_content']=thought
                finish='stop'
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            self.wfile.write(('data: '+json.dumps(dict(choices=[dict(index=0,delta=delta,finish_reason=finish)]))+'\n\ndata: [DONE]\n\n').encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    original_stream=httpx.Client.stream
    def local_only(client,method,url,*args,**kwargs):
        assert url=='https://api.deepseek.com/chat/completions',url
        return original_stream(client,method,f'http://127.0.0.1:{server.server_port}/chat/completions',*args,**kwargs)
    try:
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='profiles-check-') as tmp:
            tmp=Path(tmp);sessions=Sessions(tmp/'sessions.sqlite3');store=Store(tmp/'chats.sqlite3')
            source=tmp/'input.json';source.write_text(json.dumps([dict(platform='qq',conversation_id=cid,
                conversation=cid,sender='甲',timestamp='2026-09-20 10:00',content='课程通知',is_self=False,source_id=cid)
                for cid in ('selected','excluded')],ensure_ascii=False),'utf-8');store.import_file(source)
            cfg=dict(API_BASE='https://api.deepseek.com',API_KEY='synthetic-secret',MODEL='deepseek-flash',AGENT_MAX_REQUESTS='9')
            assert profile_config(cfg,None)==cfg
            assert 'synthetic-secret' not in json.dumps(catalog)
            for bad in ('unknown','../../.env',{},3):
                try:profile_config(cfg,bad)
                except ValueError:pass
                else:raise AssertionError('Unknown preset accepted')
            with patch('chatlocal.ui.Sessions',return_value=sessions),patch('chatlocal.ui.settings',return_value=cfg),patch('chatlocal.qq_tools.available_client',return_value=None):
                app=build_app(store)
                fns={fn.api_name:fn.fn for fn in app.fns.values()}
                assert '问题不能为空' in list(fns['agent_action'](None,['qq'],[],None,None,None,None))[-1][1]
                for preset in catalog['profiles']:
                    captured.clear();sid=sessions.create();profile=preset['id'];expected=preset['budget']
                    with patch.object(httpx.Client,'stream',local_only):
                        updates=list(fns['agent_action_preset']('查课程通知',['qq'],[json.dumps(['qq','selected'])],
                            '2026-09-20','2026-09-20','',sid,profile))
                        output=updates[-1]
                    turn=sessions.history(sid)[-1];r=turn['record']
                    assert turn['status']=='completed',r.get('result')
                    assert turn['options']['profile']==r['profile']==profile
                    assert r['budget']==expected and '本轮强度：'+preset['label'] in output[3]
                    assert all(m['conversation_id']=='selected' for m in r['bundle']['messages'])
                    assert len(captured)==2 and captured[-1]['max_tokens']==expected['max_output_tokens']
                    if profile=='quick':assert captured[-1]['thinking']=={'type':'disabled'}
                    else:assert captured[-1]['reasoning_effort']==expected['reasoning_effort']
                    assert all(e['request_limit']==expected['max_requests'] for e in r['events'] if e['type']=='model_start')
                    restored=fns['load_session'](sid)
                    assert '本轮强度：'+preset['label'] in restored[3]
                    assistant=restored[1][-1]['content']
                    assert '<script>' not in assistant and '\n' not in assistant
                    assert thought not in output[1], 'Reasoning mixed into query log'
                    if profile=='quick':
                        assert not r['reasoning'] and 'model-reasoning' not in assistant
                    else:
                        assert r['reasoning']==[dict(request=2,text=thought)]
                        assert thought in html.unescape(assistant)
                        assert any('model-reasoning' in u[0][-1]['content'] and 'pending-answer' in u[0][-1]['content'] for u in updates)
                        assert Sessions(sessions.path).history(sid)[-1]['record']['reasoning']==r['reasoning']
                        assert thought not in json.dumps(sessions.memory(sid),ensure_ascii=False)
                before=len(sessions.choices())
                invalid=list(fns['agent_action_preset']('查课程',['qq'],[],None,None,None,None,'bad'))[-1]
                assert '分析强度无效' in invalid[1] and len(sessions.choices())==before
                assert cfg['AGENT_MAX_REQUESTS']=='9','Preset changed global settings'
                assert agent_options(profile_config(cfg,None))['max_requests']==9
                app.close()
        print('PASS: all four presets through real UI callback + Harness, wire effort/tokens, persisted budgets/labels, scope isolation, invalid preset rejection, legacy API and .env config preserved. No cloud API.')
    finally:server.shutdown();server.server_close()


if __name__=='__main__':main()
