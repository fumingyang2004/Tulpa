"""Exercise the actual Gradio queue. --with-api sends selected real chat snippets."""
import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from chatlocal.agent_sessions import Sessions
from chatlocal.store import Store
from chatlocal.config import local_path
from chatlocal.normalize import decode_export,normalize
from chatlocal.render import answer_html,evidence_html,esc


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-api',action='store_true')
    parser.add_argument('--verify-saved',action='store_true',help='复核已保存真实案例及浏览器会话恢复，不再次调用 API')
    parser.add_argument('--check-cancel',action='store_true',help='启动一轮并立即通过停止按钮回调取消，可能有一个在途 API 请求')
    args=parser.parse_args()
    session=uuid.uuid4().hex
    with httpx.Client(base_url='http://127.0.0.1:7860/legacy',trust_env=False,timeout=190) as client:
        config=client.get('/config').json()
        ids={d['api_name']:d['id'] for d in config['dependencies']}
        def call(name,data,stop_on_first=False):
            joined=client.post('/gradio_api/queue/join',json=dict(fn_index=ids[name],data=data,
                session_hash=session,event_data=None,trigger_id=None))
            joined.raise_for_status()
            updates=0
            with client.stream('GET','/gradio_api/queue/data',params={'session_hash':session}) as stream:
                for line in stream.iter_lines():
                    if not line.startswith('data: '):continue
                    event=json.loads(line[6:])
                    if event.get('msg')=='process_generating':
                        updates+=1
                        if stop_on_first and updates==1:
                            stopped=client.post('/gradio_api/run/stop_agent',json=dict(data=[None],fn_index=ids['stop_agent'],session_hash=session))
                            stopped.raise_for_status()
                            assert '停止' in stopped.json()['data'][0]
                    if event.get('msg')=='process_completed':
                        assert event.get('success'),f'{name}: UI callback failed'
                        return event['output']['data'],updates
            raise AssertionError('Queue returned no completion')
        result,_=call('agent_action',[None,['qq','wechat'],None,None,None,None,None])
        assert '问题不能为空' in result[1]
        assert '问题不能为空' in result[10] and result[11]['visible'] is False
        checks=['agent empty question via real queue']
        if args.check_cancel:
            result,_=call('agent_action',['[验收] 停止测试',['qq','wechat'],None,None,None,None,None],True)
            sid=result[6]['value']
            turn=Sessions().history(sid)[-1]
            assert turn['status']=='cancelled',turn['status']
            print(json.dumps(dict(check='actual UI stop callback',status=turn['status'],requests=turn['record'].get('requests')),ensure_ascii=False))
            return
        if not args.with_api and not args.verify_saved:
            print(json.dumps(dict(passed=checks,cloud_called=False)));return
        folder=ROOT/'reports/private/agent';folder.mkdir(parents=True,exist_ok=True)
        store=Store();sessions=Sessions()
        cases=[
            ('holiday','分别查 QQ 的中秋或国庆课程通知，以及微信的中秋假期讨论。分别说明记录里的安排，附两个平台的证据；区分正式通知与个人聊天。','2026-09-01','2026-09-21'),
            ('followup','刚才 QQ 那项课程安排，后面有没有更正或改时间的消息？请继续查同一个会话；找不到就说明无法确认。','2026-09-01','2026-09-21'),
            ('requests','最近一周别人明确让我做过什么事情？请展开上下文，区分针对我的请求与群公告；无法判断完成状态就明确说明。','2026-09-14','2026-09-21'),
        ]
        summaries=[]
        for name,question,start,end in cases:
            begin=time.monotonic()
            if args.verify_saved:
                turn=json.loads((folder/(name+'.json')).read_text(encoding='utf-8'))
                sid=turn['session_id']
                updates=None
                progress='\n'.join(e.get('text') or (e['type']+' '+e.get('name','')+' '+json.dumps(e.get('arguments',{}),ensure_ascii=False)) for e in turn['record']['events'])
                result=[None,progress]
            else:
                result,updates=call('agent_action',[question,['qq','wechat'],[],start,end,'',None])
                sid=result[6]['value']
                turn=sessions.history(sid)[-1]
            record=turn['record']
            (folder/(name+'.json')).write_text(json.dumps(turn,ensure_ascii=False,indent=2),encoding='utf-8')
            assert turn['status']=='completed',dict(case=name,status=turn['status'],error=record['result'].get('error'),progress=result[1])
            assert record['requests']>=2 and (args.verify_saved or updates>=3)
            assert any(e['type']=='tool_start' for e in record['events'])
            assert all(set(a['fields'])<=set(('model','messages','stream','stream_options','tools','tool_choice','max_tokens','thinking')) for a in record['audit'])
            cited={mid for claim in record['result']['claims'] for mid in claim['evidence_ids']}
            by_id={m['id']:m for m in record['bundle']['messages']}
            cache={}
            for mid in cited:
                source=store.provenance(mid)[0]
                if source['hash'] not in cache:
                    import hashlib
                    path=local_path(source['archive'])
                    assert hashlib.sha256(path.read_bytes()).hexdigest()==source['hash']
                    head,rows,kind=decode_export(path)
                    cache[source['hash']]=(head,dict(rows),kind)
                head,rows,kind=cache[source['hash']]
                original=normalize(rows[source['pointer']],head,kind)
                assert all(original[k]==by_id[mid][k] for k in ('platform','conversation_id','sender','timestamp','content','is_self','source_id'))
            platforms=sorted({by_id[mid]['platform'] for mid in cited})
            if name=='holiday':assert platforms==['qq','wechat'],platforms
            html='<meta charset="utf-8"><style>body{font:16px system-ui;max-width:1000px;margin:30px auto}pre{white-space:pre-wrap}</style>'
            html+='<h1>'+esc(question)+'</h1><h2>真实查询过程</h2><pre>'+esc(result[1])+'</pre>'
            html+=answer_html(record['result'],record['bundle'])+'<h2>原文证据</h2>'+evidence_html(store,record['bundle'])
            (folder/(name+'.html')).write_text(html,encoding='utf-8')
            summary=dict(case=name,requests=record['requests'],updates=updates,claims=len(record['result']['claims']),
                         cited_platforms=platforms,messages=len(by_id),seconds=record['seconds'],usage=record['usage'])
            summaries.append(summary)
            print(json.dumps(summary,ensure_ascii=False),flush=True)
        assert len(sessions.history(sid))==3,'Multi-turn session did not persist'
        session=uuid.uuid4().hex
        call('refresh_sessions',[])
        restored,_=call('load_session',[sid])
        assert len(restored[1])==6 and '<details' in restored[2]
        assert 'SHA256' in restored[4]
        for message in restored[1]:
            text=''.join(part.get('text','') for part in message['content'])
            assert '\n' not in text, 'Untrusted blank lines can reopen Markdown parsing'
            if message['role']=='assistant':
                assert 'citation-group' in text and '查看附近聊天' in text
                assert '<script' not in text
        assert '已恢复对话' in restored[12] and restored[13]['visible'] is False
        (folder/'verification.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2),encoding='utf-8')
        (folder/'index.html').write_text('<meta charset="utf-8"><h1>Agent 真实验收</h1><p>真实 Harness、DeepSeek、Gradio 多轮会话；引用已回查归档。</p>'+''.join(
            f'<p><a href="{name}.html">{esc(question)}</a></p>' for name,question,_,_ in cases),encoding='utf-8')
        print('PASS: '+('saved real-provider records rechecked; ' if args.verify_saved else 'real DeepSeek agent loop and progress; ')+
              'three turns, both platforms, archive provenance and actual Gradio reload.')


if __name__=='__main__':main()
