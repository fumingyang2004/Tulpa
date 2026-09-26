"""Search hits must carry enough bounded dialogue to resolve nearby referents."""
import json
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from chatlocal.agent_tools import ChatTools
from chatlocal.retrieval import make_plan
from chatlocal.store import Store
from chatlocal.agent import compact_tool_messages
from chatlocal.llm import validate_answer


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='search-context-') as folder:
        folder=Path(folder);store=Store(folder/'chats.sqlite3')
        texts=['问往返车票能否报销。','请咨询宋老师。','在哪里联系老师？',
               '课程咨询可以找周老师。','我问的是宋老师，咨询车票的。',
               '十月后到岗，到行政楼找她。','谢谢。']
        rows=[dict(platform='qq',conversation_id='q',conversation='合成私聊',conversation_type='direct',
                   sender='我' if i%2==0 else '甲',is_self=i%2==0,source_id=str(i),
                   timestamp=f'2026-09-19 10:0{i}',content=t) for i,t in enumerate(texts)]
        # Corrections can be imported later, so message IDs need not be chronological.
        rows.append(rows.pop(4))
        rows.extend([dict(rows[0],platform='wechat',source_id='other-platform',content='十月后到岗：另一平台'),
                     dict(rows[0],conversation_id='excluded',source_id='excluded',content='十月后到岗：未选会话')])
        source=folder/'input.json';source.write_text(json.dumps(rows,ensure_ascii=False),'utf-8');store.import_file(source)
        plan=make_plan('十月后到岗',conversations=[json.dumps(['qq','q'])],start='2026-09-19',end='2026-09-19')
        tools=ChatTools(store,plan)
        result=tools.execute('search_messages',dict(query='十月后到岗',limit=1))
        assert len(result['hit_ids'])==1 and len(result['messages'])==7
        assert [m['content'] for m in result['messages']]==texts
        window=result['context_windows'][0]
        assert len(window['message_ids'])==7 and not window['budget_limited']
        assert not result['has_more'] and result['next_offset'] is None
        assert all(m['platform']=='qq' and m['conversation_id']=='q' for m in result['messages'])
        wire=json.dumps(result,ensure_ascii=False)
        assert 'source_id' not in wire and 'media_json' not in wire and str(ROOT) not in wire
        # A tool's narrower dates apply to attached context as well as the hits.
        narrow=ChatTools(store,plan).execute('search_messages',dict(query='十月后到岗',start='2026-09-19 10:04',end='2026-09-19 10:06'))
        assert [m['content'] for m in narrow['messages']]==texts[4:6]
        tiny_tools=ChatTools(store,plan,max_messages=1,max_chars=5000)
        tiny=tiny_tools.execute('search_messages',dict(query='十月后到岗'))
        assert tiny['hit_ids']==[tiny['messages'][0]['id']] and len(tiny['messages'])==1
        assert tiny['context_budget_limited'] and tiny['context_windows'][0]['budget_limited']
        mid=tiny['hit_ids'][0]
        assert mid in tiny_tools.unresolved_context_ids()
        claim=dict(claims=[dict(text='缺少指代的结论不能展示。',evidence_ids=[mid])],insufficient=False)
        blocked=validate_answer(claim,list(tiny_tools.messages.values()),unresolved_context_ids=tiny_tools.unresolved_context_ids())
        assert not blocked['claims'] and blocked['context_rejected']==1
        # A targeted read can satisfy the original missing-context requirement.
        tiny_tools.max_messages=100
        tiny_tools.execute('get_context',dict(message_id=mid,radius=8))
        assert not tiny_tools.unresolved_context_ids()
        assert validate_answer(claim,list(tiny_tools.messages.values()),unresolved_context_ids=tiny_tools.unresolved_context_ids())['claims']
        limited_tools=ChatTools(store,plan,max_chars=700)
        limited=limited_tools.execute('search_messages',dict(query='十月后到岗'))
        assert limited['hit_ids'] and limited['context_budget_limited'] and limited_tools.used_chars<=700
        overlapping=ChatTools(store,plan).execute('search_messages',dict(query='车票'))
        assert len(overlapping['hit_ids'])==2
        assert len(overlapping['messages'])==len({m['id'] for m in overlapping['messages']})
        assert all(set(w['message_ids'])<={m['id'] for m in overlapping['messages']} for w in overlapping['context_windows'])
        # References must point to a full copy earlier in this *same* request.
        raw=[dict(role='tool',tool_call_id=str(i),content=json.dumps(value,ensure_ascii=False))
             for i,value in enumerate((result,overlapping))]
        packed,count=compact_tool_messages(raw)
        assert count>0 and len(json.dumps(packed,ensure_ascii=False))<len(json.dumps(raw,ensure_ascii=False))
        seen={}
        for item in packed:
            value=json.loads(item['content'])
            assert set(value.get('previously_returned_ids',[]))<=seen.keys()
            seen.update((m['id'],m) for m in value['messages'])
            assert all(set(w['message_ids'])<=seen.keys() for w in value['context_windows'])
        alone,n=compact_tool_messages(raw[1:])
        assert n==0 and alone==raw[1:]  # No dependency on a previous API request.
        changed=json.loads(raw[0]['content']);changed['messages'][0]['content']='更正后的正文'
        updated,_=compact_tool_messages(raw[:1]+[dict(role='tool',content=json.dumps(changed))])
        assert json.loads(updated[-1]['content'])['messages'][0]['content']=='更正后的正文'
    print('PASS: complete dense private dialogue, correction order, scope and narrower dates, hit pagination, unique context, safe wire fields and clipping. No cloud API called.')


if __name__=='__main__':main()
