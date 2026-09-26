"""Small isolated regression for behavior growth, scope, toggles and provenance."""
import json
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from chatlocal.store import Store
from chatlocal.tulpa import Tulpa,collect
from chatlocal.retrieval import Plan
from chatlocal.agent_tools import ChatTools
from chatlocal.tulpa_reply import context_for


def run():
    with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as temp:
        store=Store(Path(temp)/'test.sqlite3');layer=Tulpa(store);clock=int(time.time()*1000);counter=0
        def add(content,self=1,cid='g',kind='group',reply=None,person='friend',live=True):
            nonlocal clock,counter
            clock+=1000;counter+=1
            with store.connect() as db:
                db.execute('''INSERT INTO messages(id,dedup_key,platform,conversation_id,conversation,sender_id,sender,
                  timestamp,content,is_self,source_id,conversation_type,reply_to) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (counter,str(counter),'qq',cid,cid,'me' if self else person,'本人' if self else '朋友',clock,content,self,str(counter),kind,json.dumps(reply) if reply else None))
                if live:collect(db,counter)
            return counter
        # Cold imports never collect; old timestamps cannot grow on a live replay.
        add('旧档案',live=False)
        with store.connect() as db:
            db.execute('UPDATE messages SET timestamp=1 WHERE id=1');collect(db,1)
        assert not layer.listing()['scopes']
        add('[动画表情]');add('[图片]');add('😋');add('[文件] 项目报告.pdf');add('[语音] 12秒')
        assert not layer.listing()['scopes']
        target=add('晚上打游戏吗',0)
        reply=add('不玩了，困了',reply={'source_id':str(target)})
        add('哈哈，这不是回复谁')
        with store.connect() as db:collect(db,reply)
        sid=layer.listing()['scopes'][0]['id'];d=layer.detail(sid)
        assert d['total']==2 and len(d['episodes'])==1
        with store.connect() as db:r=dict(db.execute('SELECT * FROM messages WHERE id=?',(target,)).fetchone())
        assert len(layer.retrieve(r,as_of=clock+1)['episodes'])==0 # Target itself never recycled as a demo.
        other=add('今晚来不来',0)
        with store.connect() as db:r=dict(db.execute('SELECT * FROM messages WHERE id=?',(other,)).fetchone())
        assert len(layer.retrieve(r,as_of=clock+1)['episodes'])==1
        # Global off blocks counters, episodes, retrieval. Resume starts at current time.
        layer.toggle(False);before=layer.detail(sid)
        add('关闭期间',reply={'source_id':str(target)})
        assert layer.detail(sid)['total']==before['total'] and not layer.retrieve(r)['enabled']
        layer.toggle(True)
        for i in range(98):add('今天写代码，哈哈 '+str(i))
        calls=[]
        def fake(payload):
            calls.append(payload);return dict(memory='有限样本：本人在该群常用简短口语。',evidence_ids=[payload['stage'][0]['id']],usage={'total_tokens':1})
        manual=layer.edit(sid,'我希望不要把玩笑当成亲密关系。',layer.detail(sid)['revision'])
        result=layer.consolidate(sid,fake)
        assert result['status']=='updated' and len(calls[0]['stage'])==100 and len(calls[0]['context'])<=5
        assert layer.detail(sid)['manual']==manual['manual']
        assert not layer.consolidate(sid,fake)['api_called']
        # Scope restriction cannot import a whole-conversation summary into a narrow plan.
        plan=Plan(platforms=['qq'],conversations=[json.dumps(['qq','g'])],start=clock-2000,snapshot_max_id=counter)
        tools=ChatTools(store,plan)
        assert not context_for(tools,r).get('memory')
        # Future or different group episodes never enter the retrieved set.
        r['conversation_id']='other';assert not layer.retrieve(r,as_of=clock+1)['episodes'];r['conversation_id']='g'
        assert not layer.retrieve(r,as_of=1)['episodes']
        # Source edits invalidate both the trajectory and derived memory, preserving manual edits.
        with store.connect() as db:db.execute('UPDATE messages SET content=? WHERE id=?',('原文修订',reply))
        assert not layer.detail(sid)['valid'] and not layer.retrieve(r,as_of=clock+1)['memory']
        assert not layer.retrieve(r,as_of=clock+1)['episodes']
        # Private input/output blocks, no profile for one-way notifications.
        a=add('在吗',0,'p','direct');b=add('晚上吃饭？',0,'p','direct')
        c=add('不了不了',1,'p','direct');d=add('今天有事',1,'p','direct')
        private=next(s for s in layer.listing()['scopes'] if s['conversation_id']=='p')
        episode=layer.detail(private['id'])['episodes'][0]
        assert json.loads(episode['incoming'])==[a,b] and json.loads(episode['outgoing'])==[c,d]
        # Expanded examples retain complete turns beyond the former 4/6000 cap.
        for i in range(9):
            incoming=add('一起讨论代码'+('这次要先看清上下文。'*30),0,'long','group')
            add('可以，先这样处理'+('先把这个情况讲清楚。'*30),1,'long','group',reply={'source_id':str(incoming)})
        target_id=add('一起讨论代码',0,'long','group')
        with store.connect() as db:long_target=dict(db.execute('SELECT * FROM messages WHERE id=?',(target_id,)).fetchone())
        long_plan=Plan(platforms=['qq'],conversations=[json.dumps(['qq','long'])],end=clock+1,snapshot_max_id=counter)
        roomy=ChatTools(store,long_plan,max_messages=300,max_chars=72000)
        expanded=context_for(roomy,long_target)
        assert len(expanded['episodes'])==8
        assert sum(len(m['content']) for e in expanded['episodes'] for m in e['messages'])>6000
        assert all(set(e['incoming']+e['outgoing']).issubset({m['id'] for m in e['messages']}) for e in expanded['episodes'])
        # A tight normal Agent budget still applies; rejected partial examples leak no fragments.
        tight=ChatTools(store,long_plan,max_messages=300,max_chars=700)
        tight._admit([long_target]);before_chars=tight.used_chars
        assert not context_for(tight,long_target)['episodes']
        assert list(tight.messages)==[target_id] and tight.used_chars==before_chars
        for i in range(100):add('单向通知 '+str(i),0,'notify','direct')
        notify=next(s for s in layer.listing()['scopes'] if s['conversation_id']=='notify')
        assert layer.consolidate(notify['id'],fake)['status']=='no_self_participation'
        for i in range(100):add('继续交流 '+str(i),1,'g')
        def failing(_):raise RuntimeError('fixture')
        before=layer.detail(sid)['processed']
        try:layer.consolidate(sid,failing)
        except RuntimeError:pass
        assert layer.detail(sid)['processed']==before
        def raced(payload):layer.toggle(False);return fake(payload)
        assert layer.consolidate(sid,raced)['status']=='superseded'
        assert layer.detail(sid)['processed']==before
        # Reopening and repeating migrations preserve state. No automatic historical backfill.
        reopened=Tulpa(Store(store.path));assert not reopened.control()['enabled']
        assert reopened.detail(sid)['manual']==manual['manual']
        print('PASS: archive/off/resume/dedup, group quotes, private turns, 100-message gate, bounded provider, source invalidation, scope/time isolation, manual edits, failure/race/restart.')

if __name__=='__main__':run()
