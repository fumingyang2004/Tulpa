"""Dialogue referents and latest-page regression; synthetic local data, no API."""
import json
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from chatlocal.agent_sessions import Sessions, history_messages
from chatlocal.agent_tools import ChatTools
from chatlocal.retrieval import make_plan
from chatlocal.store import Store


def main():
    assert make_plan('继续读最近50条').start is None
    assert make_plan('总结最近一周').start is not None
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='dialogue-check-') as folder:
        folder=Path(folder)
        store=Store(folder/'chats.sqlite3')
        rows=[dict(platform='qq',conversation='合成星桥群',conversation_id='q',sender='甲',
                   timestamp=f'2026-09-19 10:{i//2:02d}:00',content=f'合成第{i}条',source_id=str(i),is_self=False)
              for i in range(110)]
        rows.append(dict(rows[0],platform='wechat',conversation='另一个群',source_id='other'))
        source=folder/'input.json'
        source.write_text(json.dumps(rows,ensure_ascii=False),encoding='utf-8')
        store.import_file(source)
        plan=make_plan('查看星桥群',platforms=['qq'],conversations=[json.dumps(['qq','q'])],end='2026-09-20')
        args=dict(platform='qq',conversation_id='q',order='newest',limit=50)
        tools=ChatTools(store,plan)
        first=tools.execute('read_conversation',args)
        assert [m['id'] for m in first['messages']]==list(range(110,60,-1)),first
        assert first['next_offset']==50 and not first['budget_limited']
        # A fresh import between turns must not shift a previously read page.
        later=folder/'later.json'
        later.write_text(json.dumps([dict(rows[0],timestamp='2026-09-19 12:00',source_id='later')]),encoding='utf-8')
        store.import_file(later)
        continued=ChatTools(store,plan).execute('read_conversation',dict(args,offset=50,snapshot_max_id=first['snapshot_max_id']))
        assert [m['id'] for m in continued['messages']]==list(range(60,10,-1))
        assert not ({m['id'] for m in first['messages']} & {m['id'] for m in continued['messages']})
        assert tools.execute('read_conversation',dict(args,platform='wechat')).get('error')
        other=tools.execute('read_conversation',dict(args,conversation_id='not-selected'))
        assert not other['messages']
        assert tools.execute('read_conversation',dict(args,limit=51)).get('error')
        assert tools.execute('read_conversation',dict(args,order='newest;DROP TABLE messages')).get('error')
        small=ChatTools(store,plan,max_chars=700).execute('read_conversation',args)
        assert small['budget_limited'] and len(small['messages'])<50
        sessions=Sessions(folder/'sessions.sqlite3'); sid=sessions.create()
        record=dict(result={'claims':[{'text':'已定位到星桥群。','evidence_ids':[110]}]},bundle=tools.bundle(),
                    events=[dict(type='tool_end',name='read_conversation',arguments=args,result=first)])
        for question,status in [('看看星桥群','completed'),('就读最新的50个','cancelled')]:
            tid=sessions.start(sid,question,{})
            sessions.finish(tid,status,record if status=='completed' else {})
        memory=sessions.memory(sid)
        saved=memory['previous_turns'][0]['query_state']
        assert saved['conversations'][0]['conversation_id']=='q'
        assert saved['reads'][0]['next_offset']==50 and saved['reads'][0]['snapshot_max_id']==110
        wire=history_messages(memory)
        assert [m['role'] for m in wire]==['user','assistant','user','assistant']
        assert 'cancelled' in wire[-1]['content']
        encoded=json.dumps(memory,ensure_ascii=False)
        assert '合成第' not in encoded and 'source_id' not in encoded and 'archive' not in encoded
        other_sid=sessions.create()
        assert not sessions.memory(other_sid)['previous_turns']
        print('PASS: same-group referents, role history, cancelled-turn intent, latest 50, tie order, stable next page after import, hard scope and evidence budget; no API.')


if __name__=='__main__': main()
