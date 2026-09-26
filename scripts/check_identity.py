"""Own identity/mention candidates with hard group/date boundaries; no cloud."""
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from chatlocal.agent import run_agent,mentions_shortcut
from chatlocal.agent_tools import ChatTools
from chatlocal.identity import group_identities
from chatlocal.retrieval import make_plan
from chatlocal.render import identity_html,answer_html
from chatlocal.store import Store


def main():
    state={}
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));state['requests']+=1
            if state['requests']==1:
                assert body['tool_choice']['function']['name']=='find_mentions'
                delta=dict(role='assistant',tool_calls=[dict(index=0,id='mentions',type='function',
                    function=dict(name='find_mentions',arguments='{}'))]);reason='tool_calls'
            else:
                output=json.loads(next(m['content'] for m in body['messages'] if m['role']=='tool'))
                assert output['coverage']['native_target_ids_available'] is False
                ids=[m['id'] for m in output['messages']]
                assert ids==[8,2] and all(not m['is_self'] for m in output['messages'])
                assert 'PRIVATE OLD BODY' not in json.dumps(body)
                delta=dict(role='assistant',content=json.dumps(dict(claims=[dict(
                    text='找到两条与本人同群昵称一致的消息，属于疑似@本人，尚不能确认原生目标。',evidence_ids=ids)],insufficient=False),ensure_ascii=False));reason='stop'
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            self.wfile.write(('data: '+json.dumps(dict(choices=[dict(index=0,delta=delta,finish_reason=reason)]))+'\n\ndata: [DONE]\n\n').encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='identity-') as tmp:
            tmp=Path(tmp);store=Store(tmp/'chats.sqlite3')
            def row(group,sender,content,self=False,platform='qq',old=False):
                return dict(platform=platform,conversation_id=group,conversation=group,conversation_type='group',
                    sender=sender,sender_id=('self-qq' if platform=='qq' else 'self-wx') if self else 'other',
                    content=content,is_self=self,timestamp='2026-09-18 10:00' if old else '2026-09-21 10:00')
            rows=[row('A','阿甲','PRIVATE OLD BODY',True,old=True),row('A','乙','@阿甲 帮忙看下'),
                  row('B','阿乙','PRIVATE OLD BODY',True,old=True),row('B','乙','@阿甲 不要跨群认人'),
                  row('A','微信本人','PRIVATE OLD BODY',True,'wechat',True),row('A','乙','@阿甲 不要跨平台认人',platform='wechat'),
                  row('A','乙','@阿甲乙 同名开头不是本人'),row('A','乙','＠阿甲 看这里'),
                  row('A','乙','@全体成员 开会'),row('A','阿甲','同名的其他人'),
                  row('C','乙','@阿甲 这个群身份未知'),row('A','乙','mail@阿甲'),
                  row('A','乙','@阿甲 范围外')]
            rows[-1]['timestamp']='2026-09-22 10:00'
            for i,r in enumerate(rows):r['source_id']=str(i)
            path=tmp/'input.json';path.write_text(json.dumps(rows,ensure_ascii=False),'utf-8');store.import_file(path)
            plan=make_plan('今天有没有人艾特我',platforms=['qq'],start='2026-09-21',end='2026-09-21')
            groups=group_identities(store,plan)
            assert {g['platform'] for g in groups}=={'qq'}
            a=next(g for g in groups if g['conversation_id']=='A')
            assert a['identities'][0]['sender_id']=='self-qq' and a['identities'][0]['name_collision']
            scoped=make_plan('我在群里什么ID',platforms=['qq'],conversations=[json.dumps(['qq','A'])],start='2026-09-21',end='2026-09-21')
            tools=ChatTools(store,scoped);identity=tools.execute('get_my_identity',{})
            assert len(identity['identities'])==1 and not identity['messages'], 'Date-excluded body leaked'
            assert 'source_message_id' not in json.dumps(identity) and 'PRIVATE OLD BODY' not in json.dumps(identity)
            assert 'error' in tools.execute('get_my_identity',{'platform':'wechat'})
            result=tools.execute('find_mentions',{})
            assert [m['id'] for m in result['messages']]==[8,2]
            assert all(m['name_collisions']==['阿甲'] and not m['native_target_verified'] for m in result['matches'])
            assert result['coverage']['self_candidates']==2 and result['coverage']['all_mentions']==1
            assert {m['id'] for m in ChatTools(store,scoped).execute('find_mentions',{'include_all':True})['messages']}=={2,8,9}
            narrow=ChatTools(store,scoped).execute('find_mentions',{'start':'2026-09-21 11:00'})
            assert not narrow['messages']
            first=ChatTools(store,scoped).execute('find_mentions',{'limit':1})
            extra=dict(rows[1],source_id='new',timestamp='2026-09-21 12:00')
            extra_path=tmp/'extra.json';extra_path.write_text(json.dumps([extra],ensure_ascii=False),'utf-8');store.import_file(extra_path)
            second=ChatTools(store,scoped).execute('find_mentions',{'limit':1,'offset':1,'snapshot_max_id':first['snapshot_max_id']})
            assert second['messages'][0]['id']==2
            tiny=ChatTools(store,scoped,max_chars=10).execute('find_mentions',{})
            assert tiny['budget_limited'] and not tiny['messages']
            assert '我的群内身份' in identity_html(store)
            assert mentions_shortcut('我今天被艾特了吗') and not mentions_shortcut('我给别人发消息')
            # Keep the actual runtime example on the original imported snapshot.
            plan.end=1789956000001  # 2026-09-21 10:00:00.001 +08:00
            config=dict(API_BASE=f'http://127.0.0.1:{server.server_port}',API_KEY='synthetic',MODEL='deepseek-flash')
            state['requests']=0
            done=list(run_agent(store,plan,'今天有没有人艾特我',config=config))[-1]
            assert done['status']=='completed' and done['record']['requests']==2
            assert done['record']['result']['claims'] and not done['record']['result']['rejected']
            assert '不能据此断言无人' in answer_html(done['record']['result'],done['record']['bundle'])
        print('PASS: stable own IDs, per-group aliases, collisions, unknown identity, date/platform/group boundaries, candidate-only semantics, paging/budgets and real Harness first-call routing. No cloud API.')
    finally:server.shutdown();server.server_close()


if __name__=='__main__':main()
