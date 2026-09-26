"""Synthetic data + real installed Harness + local fake provider; no cloud calls."""
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from deepseek_harness import DeepSeekHarness

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from chatlocal.agent import run_agent, Bridge
from chatlocal.agent_tools import ChatTools, SCHEMAS
from chatlocal.agent_sessions import Sessions
from chatlocal.retrieval import make_plan
from chatlocal.store import Store
from chatlocal.render import answer_html,evidence_html
from chatlocal.watches import Watches


def main():
    captured=[]
    reasoning_fixture='【模拟接口思考】先核对聊天原文。\n<script>not_executable()</script>\n'+'保留全部文本。'*900
    children=[]
    class ObservedHarness(DeepSeekHarness):
        def start(self):
            super().start()
            children.append(self.client._proc)
    def drive(*args,**kwargs):
        with patch('deepseek_harness.DeepSeekHarness',ObservedHarness),patch('chatlocal.qq_tools.available_client',return_value=None):
            yield from run_agent(*args,**kwargs)
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            captured.append(body)
            index=len(captured)
            if 'GREETING_UNVERIFIED' in json.dumps(body['messages']):
                delta=dict(role='assistant',content=json.dumps(dict(claims=[dict(text='你好！',evidence_ids=[])],insufficient=False),ensure_ascii=False))
                reason='stop'
            elif index==1:
                delta=dict(role='assistant',tool_calls=[dict(index=0,id='call-search',type='function',
                    function=dict(name='search_messages',arguments=json.dumps({'query':'星桥','limit':10},ensure_ascii=False)))])
                reason='tool_calls'
            elif index==2:
                delta=dict(role='assistant',tool_calls=[dict(index=0,id='call-context',type='function',
                    function=dict(name='get_context',arguments='{"message_id":1,"radius":2}'))])
                reason='tool_calls'
            elif index in (4,7):
                feedback=json.loads([m for m in body['messages'] if m['role']=='user'][-1]['content'])
                assert feedback['validation_feedback'], 'Missing citation repair feedback'
                claim=({'text':'星桥项目在两个平台有讨论。','evidence_ids':[1,3]} if index==4 else
                       {'text':'同一 QQ 群的最新记录。','evidence_ids':[2]})
                delta=dict(role='assistant',content=json.dumps({'claims':[claim],'insufficient':False},ensure_ascii=False))
                reason='stop'
            elif index==5:
                # This is the next *turn*, not the next call in the first turn.
                prior=[m for m in body['messages'] if m['role']=='user']
                assert prior[0]['content']=='星桥项目'
                current=json.loads(prior[-1]['content'])
                assert current['question']=='就读最新的50个'
                assert 'keywords' not in current['allowed_scope'] and 'history' not in current
                assistants=[m for m in body['messages'] if m['role']=='assistant']
                remembered=json.loads(assistants[0]['content'])
                assert any(c['platform']=='qq' and c['conversation_id']=='q' for c in remembered['query_state']['conversations'])
                assert remembered['query_state']['reads'][-1]['order']=='newest'
                assert not any(m['role']=='tool' for m in body['messages']), 'Old tool results replayed'
                assert reasoning_fixture not in json.dumps(body,ensure_ascii=False),'Reasoning leaked into next-turn memory'
                delta=dict(role='assistant',tool_calls=[dict(index=0,id='followup-read',type='function',
                    function=dict(name='read_conversation',arguments=json.dumps(
                        {'platform':'qq','conversation_id':'q','order':'newest','limit':50})))])
                reason='tool_calls'
            elif index==6:
                delta=dict(role='assistant',content=json.dumps({'claims':[
                    {'text':'同一 QQ 群的最新记录。','evidence_ids':[2]},
                    {'text':'历史微信引用没有在本轮重查，必须丢弃。','evidence_ids':[3]}],'insufficient':False},ensure_ascii=False))
                reason='stop'
            elif index==8:
                current=json.loads([m for m in body['messages'] if m['role']=='user'][-1]['content'])
                assert current['question']=='星桥项目' and 'watch_context' not in current
                assert 'processed_count' not in json.dumps(body,ensure_ascii=False)
                delta=dict(role='assistant',tool_calls=[dict(index=0,id='watch-search',type='function',
                    function=dict(name='search_messages',arguments=json.dumps(dict(query='星桥'))))])
                reason='tool_calls'
            elif index==9:
                delta=dict(role='assistant',content=json.dumps(dict(claims=[dict(text='星桥请周一提交文档。',evidence_ids=[1])],insufficient=False)))
                reason='stop'
            else:
                delta=dict(role='assistant',content=json.dumps({'claims':[
                    {'text':'星桥项目在两个平台有讨论。','evidence_ids':[1,3]},
                    {'text':'这项伪造引用应被丢弃。','evidence_ids':[99999]}],'insufficient':False},ensure_ascii=False))
                reason='stop'
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            if index in (2,3,4):
                for part in (reasoning_fixture[:17],reasoning_fixture[17:]):
                    chunk=dict(choices=[dict(index=0,delta=dict(reasoning_content=part))])
                    self.wfile.write(('data: '+json.dumps(chunk)+'\n\n').encode())
                    self.wfile.flush()
            for item in [dict(id='fixture',object='chat.completion.chunk',model='deepseek-flash',choices=[dict(index=0,delta=delta,finish_reason=None)]),
                         dict(id='fixture',object='chat.completion.chunk',model='deepseek-flash',choices=[dict(index=0,delta={},finish_reason=reason)],usage=dict(prompt_tokens=100,completion_tokens=20,total_tokens=120))]:
                self.wfile.write(('data: '+json.dumps(item)+'\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='agent-check-') as folder:
            folder=Path(folder)
            path=folder/'synthetic.json'
            rows=[dict(platform='qq',conversation='合成 QQ 群',conversation_id='q',sender='甲',timestamp='2026-09-19 10:00',content='星桥项目请周一提交文档。',is_self=False,source_id='q1'),
                  dict(platform='qq',conversation='合成 QQ 群',conversation_id='q',sender='我',timestamp='2026-09-19 10:01',content='好的，我来准备星桥文档。',is_self=True,source_id='q2'),
                  dict(platform='wechat',conversation='合成微信',conversation_id='w',sender='乙',timestamp='2026-09-20 12:00',content='星桥会议改到周二。<script>bad()</script>',is_self=False,source_id='w1'),
                  dict(platform='wechat',conversation='合成微信',conversation_id='w',sender='乙',timestamp='2026-09-21 12:00',content='不应越过用户选定日期。',is_self=False,source_id='w2')]
            path.write_text(json.dumps(rows,ensure_ascii=False),encoding='utf-8')
            store=Store(folder/'chats.sqlite3');store.import_file(path)
            plan=make_plan('星桥项目怎么讨论的？',start='2026-09-19',end='2026-09-20')
            scoped=ChatTools(store,make_plan('星桥',platforms=['qq'],start='2026-09-19',end='2026-09-19'))
            assert 'error' in scoped.execute('get_context',{'message_id':3})
            assert 'error' in scoped.execute('search_messages',{'query':'星桥','platform':'wechat'})
            assert 'error' in scoped.execute('search_messages',{'query':'星桥','limit':True})
            assert 'error' in scoped.execute('shell',{'command':'whoami'})
            assert 'error' in scoped.execute('read_conversation',{'platform':'qq','conversation_id':''})
            assert len(scoped.execute('search_messages',{'query':'星桥'})['messages'])==2
            assert 'error' in scoped.execute('get_messages_since',{'after_id':0,'platform':'wechat'})
            assert [m['id'] for m in scoped.execute('get_messages_since',{'after_id':0})['messages']]==[1,2]
            scoped.read_only=True
            assert 'error' in scoped.execute('evidence_collection',{'action':'create','title':'No background write','items':[]})
            assert len(scoped.execute('list_conversations',{})['conversations'])==1
            limited=ChatTools(store,plan,max_messages=1,max_chars=5000)
            assert len(limited.execute('search_messages',{'query':'星桥'})['messages'])==1
            assert len(limited.messages)==1
            config=dict(API_BASE=f'http://127.0.0.1:{server.server_port}',API_KEY='synthetic-key',MODEL='deepseek-flash')
            events=list(drive(store,plan,'星桥项目怎么讨论的？',config=config,include_reasoning=True))
            done=events[-1]
            assert done['status']=='completed',done
            record=done['record']
            assert record['requests']==4 and len(captured)==4
            assert record['reasoning']==[dict(request=n,text=reasoning_fixture) for n in (2,3,4)]
            reasoning_events=[e for e in events if e['type']=='model_reasoning']
            assert ''.join(e['delta'] for e in reasoning_events)==reasoning_fixture*3
            assert not any(e['type']=='model_reasoning' for e in record['events'])
            assert record['usage']['total_tokens']==480
            assert len(record['result']['claims'])==1 and record['result']['rejected']==0
            assert len(record['result']['earlier_rejections'])==1
            assert 99999 in record['result']['earlier_rejections'][0]['raw_claim']['evidence_ids']
            assert len([e for e in events if e['type']=='tool_start'])==2
            assert {m['platform'] for m in record['bundle']['messages']}=={'qq','wechat'}
            assert all(m['id']!=4 for m in record['bundle']['messages'])
            assert 'SHA256' in evidence_html(store,record['bundle'])
            assert 'SHA256' in evidence_html(store,json.loads(json.dumps(record['bundle'])))
            assert '<script>' not in answer_html(record['result'],record['bundle'])
            assert all(set(t['function']['name'] for t in b['tools'])=={t['name'] for t in SCHEMAS if t['name']!='get_group_knowledge'} for b in captured)
            wire=json.dumps(captured,ensure_ascii=False)
            assert 'dsh_session_log' not in wire and 'dedup_key' not in wire
            tool_payloads=json.dumps([m for b in captured for m in b['messages'] if m['role']=='tool'])
            assert 'source_id' not in tool_payloads, 'Raw importer IDs leaked through message tools'
            assert 'synthetic-key' not in wire and str(ROOT) not in wire and 'C:/Lab0921' not in wire
            assert any(m.get('role')=='tool' for m in captured[-1]['messages'])
            sessions=Sessions(folder/'sessions.sqlite3');sid=sessions.create()
            tid=sessions.start(sid,'星桥项目',{})
            read_args={'platform':'qq','conversation_id':'q','limit':1,'order':'newest'}
            read=ChatTools(store,plan).execute('read_conversation',read_args)
            memory_record=dict(record,events=record['events']+[dict(type='tool_end',name='read_conversation',arguments=read_args,result=read)])
            sessions.finish(tid,'completed',memory_record)
            restored=sessions.history(sid)[0]['record']
            assert 'SHA256' in evidence_html(store,restored['bundle'])
            assert sessions.memory(sid)['previous_turns'][0]['claims'][0]['evidence_ids']==[1,3]
            assert 'messages' not in json.dumps(sessions.memory(sid))
            followup=list(drive(store,plan,'就读最新的50个',memory=sessions.memory(sid),config=config))[-1]
            assert followup['status']=='completed',followup
            assert followup['record']['requests']==3
            assert followup['record']['result']['claims'][0]['evidence_ids']==[2]
            assert followup['record']['result']['rejected']==0
            old_rejections=followup['record']['result']['earlier_rejections']
            assert len(old_rejections)==1 and 3 in old_rejections[0]['raw_claim']['evidence_ids'], 'History became fresh evidence'
            assert [m['id'] for m in followup['record']['bundle']['messages']]==[2,1]
            assert all(sum(m['role']=='user' and m['content']=='星桥项目' for m in b['messages'])==1 for b in captured[4:7])
            # A real Watch calls the SAME Harness question path. Most of these
            # messages are irrelevant; no demand to read them all before answering.
            incremental=[dict(platform='qq',conversation='合成 QQ 群',conversation_id='q',sender='甲',timestamp='2026-09-19 12:00',
                content='普通课程消息 '+str(i),is_self=False,source_id='increment-'+str(i)) for i in range(95)]
            path.write_text(json.dumps(incremental,ensure_ascii=False),encoding='utf-8');store.import_file(path)
            before_children=len(children)
            watches=Watches(store);card=watches.create('星桥','星桥项目',['qq'],[json.dumps(['qq','q'])])
            with patch('chatlocal.watch_check.settings',return_value=config):
                card=watches.check(card['id'],refresh=False,agent=drive)
            watch=watches.timeline(card['id'])[0][-1]
            assert watch['status']=='completed' and card['last_result']['mode']=='scheduled_question'
            assert watch['record']['result']['claims'][0]['evidence_ids']==[1]
            assert watch['record']['requests']==2 and len(children)==before_children+1
            assert watch['record']['bundle']['message_count']<10
            captured.clear()
            greeting=list(drive(store,plan,'GREETING_UNVERIFIED',config=config,show_unverified=True))[-1]
            assert greeting['status']=='completed' and greeting['record']['requests']==1
            result=greeting['record']['result']
            assert not result['claims'] and result['display_claims'][0]['text']=='你好！'
            prose=answer_html(result,greeting['record']['bundle'])
            assert prose.index('你好！')<prose.index('未通过检查') and '已隐藏' not in prose
            for stop_at in ('started','model_start'):
                cancelled=threading.Event()
                cancel_events=[]
                for event in drive(store,plan,'停止测试',cancel=cancelled,config=config):
                    cancel_events.append(event)
                    if event['type']==stop_at: cancelled.set()
                assert cancel_events[-1]['status']=='cancelled'
            assert children and all(p.poll() is not None for p in children),'Harness child leaked after cancellation'
            print('PASS: real Harness loop with local fake provider; cross-platform tools, hard scope, budgets, real progress, citation rejection, safe wire fields, local persistence/reload and cancellation. No cloud API called.')
    finally:
        server.shutdown();server.server_close()


if __name__=='__main__':main()
