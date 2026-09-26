"""Identity linkage and scoped communication retrieval; isolated, no cloud."""
import json
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from chatlocal.store import Store
from chatlocal.retrieval import make_plan
from chatlocal.agent_tools import ChatTools
from chatlocal.agent_sessions import turn_memory,history_messages


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='people-check-') as tmp:
        root=Path(tmp);store=Store(root/'test.sqlite3')
        def row(cid,name,account,text,*,platform='qq',self=False,old=False,direct=False,title=None):
            return dict(platform=platform,conversation_id=cid,conversation=title or cid,
                conversation_type='direct' if direct else 'group',sender=name,sender_id=account,
                content=text,is_self=self,timestamp='2026-09-18 10:00' if old else '2026-09-22 10:00')
        rows=[row('1:direct:100','联系人甲','100','收到你的需求',direct=True,title='备注：联系人甲'),
              row('1:direct:100','本人','1','请帮忙检查配置',self=True,direct=True,title='备注：联系人甲'),
              row('A','甲在A群','100','配置已经看过了'),
              row('B','甲的另一个名字','100','需要确认下一步'),
              row('B','路人','200','提到了联系人甲，但不是甲发的'),
              row('A','甲在A群','200','同名不同账号'),
              row('C','旧昵称','100','OLD BODY MUST NOT LEAK',old=True),
              row('W','联系人甲','100','微信同号不是同一账号',platform='wechat'),
              row('1:direct:300','本人','1','只有本人发言也能识别私聊对端',self=True,direct=True,title='仅出站联系人'),
              row('U','缺ID联系人','','不能凭名字与另一个人合并'),
              row('Z','零ID联系人','0','零号不是稳定身份')]
        for i,r in enumerate(rows):r['source_id']=str(i+1)
        path=root/'rows.json';path.write_text(json.dumps(rows,ensure_ascii=False),encoding='utf-8');store.import_file(path)
        plan=make_plan('查看联系人甲的沟通',platforms=['qq'],start='2026-09-22',end='2026-09-22')
        tools=ChatTools(store,plan,max_messages=100,max_chars=30000,max_calls=30)
        identity=tools.execute('find_people',{'query':'联系人甲'})
        assert identity['candidate_count']==1 and not identity['ambiguous']
        person=identity['people'][0]
        assert person['sender_id']=='100' and {a['name'] for a in person['aliases']}>={'甲在A群','甲的另一个名字','旧昵称'}
        assert 'OLD BODY' not in json.dumps(identity) and 'messages' not in identity
        assert 'source_message_id' not in json.dumps(identity)
        found=tools.execute('read_person_messages',{'platform':'qq','sender_id':'100','limit':20})
        assert set(found['hit_ids'])=={1,2,3,4},found
        assert all(m.get('sender_id') for m in found['messages'])
        assert 'OLD BODY' not in json.dumps(found) and all(m['platform']=='qq' for m in found['messages'])
        group=tools.execute('read_person_messages',{'platform':'qq','sender_id':'100','conversation_type':'group'})
        assert set(group['hit_ids'])=={3,4}
        assert set(tools.execute('search_messages',{'platform':'qq','sender_id':'100','query':''})['hit_ids'])=={1,3,4}
        assert 'error' in tools.execute('search_messages',{'sender_id':'100','query':''})
        assert 'error' in tools.execute('read_person_messages',{'platform':'wechat','sender_id':'100'})
        both=ChatTools(store,make_plan('联系人甲')).execute('find_people',{'query':'联系人甲'})
        assert both['candidate_count']==2 and both['ambiguous']
        assert tools.execute('find_people',{'query':'甲在A群'})['candidate_count']==2
        assert tools.execute('find_people',{'query':'仅出站联系人'})['people'][0]['sender_id']=='300'
        outgoing=tools.execute('read_person_messages',{'platform':'qq','sender_id':'300'})
        assert outgoing['hit_ids']==[9]
        for query in ('缺ID联系人','零ID联系人'):
            missing=tools.execute('find_people',{'query':query})
            assert not missing['people'] and missing['unresolved_count']==1
        scoped=ChatTools(store,make_plan('查看甲',platforms=['qq'],conversations=[json.dumps(['qq','B'])],start='2026-09-22',end='2026-09-22'))
        assert scoped.execute('find_people',{'query':'联系人甲'})['candidate_count']==0
        local=scoped.execute('find_people',{'query':'100'})['people'][0]
        assert {a['conversation_id'] for a in local['aliases']}=={'B'} and not local['direct_conversations']
        assert scoped.execute('read_person_messages',{'platform':'qq','sender_id':'100'})['hit_ids']==[4]
        # The original filters remain hard even if a later tool asks to widen.
        assert scoped.execute('read_person_messages',{'platform':'qq','sender_id':'100','conversation_id':'A'})['hit_ids']==[]
        page=tools.execute('read_person_messages',{'platform':'qq','sender_id':'100','limit':1})
        fresh=dict(rows[2],source_id='late',timestamp='2026-09-22 10:01')
        path.write_text(json.dumps([fresh]),encoding='utf-8');store.import_file(path)
        next_page=tools.execute('read_person_messages',{'platform':'qq','sender_id':'100','limit':20,'offset':1,'snapshot_max_id':page['snapshot_max_id']})
        assert 12 not in next_page['hit_ids'] and 12 not in {m['id'] for m in next_page['messages']}
        # Carry the confirmed account rather than requiring the next turn to
        # rediscover every nickname. Metadata never becomes a claimed citation.
        record=dict(bundle=tools.bundle(),result={'claims':[]},events=[])
        memory=turn_memory(dict(record=record,question='联系人甲',status='completed'))
        assert any(p['sender_id']=='100' for p in memory['query_state']['people'])
        wire=history_messages({'previous_turns':[memory]})
        assert 'sender_id' in wire[-1]['content'] and 'OLD BODY' not in wire[-1]['content']
    print('PASS: account aliases, namesakes/platform separation, outgoing-only peer, missing IDs, hard scopes/dates, direct both sides, exact author search, frozen paging and person memory. No cloud.')


if __name__=='__main__':main()
