"""Cold-import reply examples, scope, complete turns and budget. No model or send."""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from chatlocal.store import Store
from chatlocal.tulpa import Tulpa, collect
from chatlocal.retrieval import Plan
from chatlocal.agent_tools import ChatTools
from chatlocal.reply_agent import prepare
from chatlocal.tulpa_reply import context_for
from chatlocal.reply_history import QUOTE_SOURCE, QUOTE_CONTENT, TEXT_QUOTE


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='reply-history-') as tmp:
        root=Path(tmp);store=Store(root/'messages.sqlite3');rows=[]
        start=int(time.time()*1000)-60*86400000
        def add(text,own=False,cid='g',kind='group',person='friend',reply=None,source=None,platform='qq'):
            mid=len(rows)+1
            rows.append(dict(platform=platform,conversation_id=cid,conversation=cid,conversation_type=kind,
                sender_id='self' if own else person,sender='我' if own else '相同昵称',is_self=own,
                timestamp=start+mid*1000,content=text,source_id=source or str(mid),reply_to=reply))
            return mid
        invitation=add('晚上联机打游戏吗')
        response=add('不了不了，今天躺平😋',True,reply={'source_id':str(invitation)})
        add('下一条是自言自语')
        adjacency=add('哈哈我先睡了',True)
        for n in range(130):add('周末闲聊 '+str(n),person='someone_else')
        other=add('代码又炸了吗',person='other_person')
        other_response=add('笑死这也能炸',True,reply={'source_id':str(other)})
        group_target=add('晚上联机打游戏吗')
        future=add('未来答复不当示例',True,reply={'source_id':str(group_target)})
        # Same name / native ID in another platform or conversation must not match.
        add('晚上联机打游戏吗',cid='other',source=str(invitation))
        add('其他会话的表达',True,cid='other',reply={'source_id':str(invitation)})
        add('微信同号',cid='g',source=str(invitation),platform='wechat')
        pi=add('晚上吃饭吗',cid='p',kind='direct')
        pi2=add('还是昨天那家',cid='p',kind='direct')
        po=add('不了不了',True,cid='p',kind='direct')
        po2=add('今天要改代码',True,cid='p',kind='direct')
        pt=add('今晚要一起吃饭吗',cid='p',kind='direct')
        for n in range(10):
            i=add('谢谢你帮忙解决代码问题 '+str(n),cid='many')
            add('客气啥，下次还来找我 '+str(n),True,cid='many',reply={'source_id':str(i)})
        mt=add('谢谢你帮忙解决代码问题',cid='many')
        unknown=add('谁在说话',cid='ambiguous',source='duplicate')
        duplicate=add('同号另一条',cid='ambiguous',person='second')
        add('引用无法唯一确认',True,cid='ambiguous',reply={'source_id':'duplicate'})
        at=add('谁在说话',cid='ambiguous')
        # Older QQ data has actual quoted text but no quoted native ID.
        qi=add('就知道说我',cid='text_quote')
        qo=add('@朋友 这么强？？？',True,cid='text_quote',reply={'content':'就知道说我'})
        for n in range(70):add('本人另一个话题 '+str(n),True,cid='text_quote')
        qt=add('[动画表情]',cid='text_quote')
        # Same text on another platform/conversation does not make this ambiguous.
        add('就知道说我',cid='elsewhere')
        add('就知道说我',cid='text_quote',platform='wechat')
        duplicate_text=add('你这代码又炸了',cid='text_duplicates')
        second_text=add('你这代码又炸了',cid='text_duplicates',person='second')
        duplicate_reply=add('又炸了哈哈',True,cid='text_duplicates',reply={'content':'你这代码又炸了','sender':'相同昵称'})
        dt=add('代码修好了',cid='text_duplicates')
        wrong=add('这条作者可核对',cid='text_invalid')
        wrong_reply=add('作者不一致',True,cid='text_invalid',reply={'content':'这条作者可核对','sender':'other'})
        broken_native=add('编号已不存在',True,cid='text_invalid',reply={'source_id':'missing','content':'这条作者可核对'})
        prefix=add('引用只有前半段，原文还有后半段',cid='text_invalid')
        prefix_reply=add('不要猜截断预览',True,cid='text_invalid',reply={'content':'引用只有前半段'})
        add('[图片]',cid='text_invalid')
        placeholder_reply=add('不要把占位当原文',True,cid='text_invalid',reply={'content':'[图片]'})
        future_reply=add('原文不能晚于回复',True,cid='text_invalid',reply={'content':'稍后才出现的原文'})
        add('稍后才出现的原文',cid='text_invalid')
        it=add('还有别的事情',cid='text_invalid')
        si=add('原文应该留在范围里',cid='text_scope')
        so=add('收到这句了',True,cid='text_scope',reply={'content':'原文应该留在范围里','sender':'friend'})
        st=add('收到没有',cid='text_scope')
        path=root/'cold.json';path.write_text(json.dumps(rows,ensure_ascii=False),encoding='utf-8')
        store.import_file(path)
        # Simulate an ambiguous legacy archive; the normal importer rejects collisions.
        with store.connect() as db:db.execute("UPDATE messages SET source_id='duplicate' WHERE id=?",(duplicate,))
        layer=Tulpa(store)
        assert not layer.listing()['scopes'], 'historical import must not grow memory'
        def tools_for(mid,**scope):
            row=store.message(mid)
            return ChatTools(store,Plan(platforms=[row['platform']],conversations=[json.dumps([row['platform'],row['conversation_id']])],**scope),max_chars=72000)
        tools=tools_for(group_target)
        seed=prepare(tools,dict(message_id=group_target,instruction='拒绝他'))
        examples=seed['interaction_examples']
        assert examples[0]['incoming']==[invitation] and examples[0]['outgoing']==[response]
        assert adjacency not in [i for e in examples for i in e['outgoing']]
        assert future not in [i for e in examples for i in e['outgoing']]
        assert all(m['conversation_id']=='g' and m['platform']=='qq' for e in examples for m in e['messages'])
        assert tools.tulpa_usage['history_examples']>=1 and not layer.listing()['scopes']
        # Closing Tulpa stops growth, not ordinary scoped archive retrieval.
        layer.toggle(False)
        off=prepare(tools_for(group_target),dict(message_id=group_target))
        assert not off['tulpa']['enabled'] and off['interaction_examples']
        assert not off['tulpa'].get('memory')
        private=prepare(tools_for(pt),dict(message_id=pt))['interaction_examples']
        assert len(private)==1 and private[0]['incoming']==[pi,pi2] and private[0]['outgoing']==[po,po2]
        assert len(prepare(tools_for(mt),dict(message_id=mt))['interaction_examples'])==8
        assert not prepare(tools_for(at),dict(message_id=at))['interaction_examples']
        text_examples=prepare(tools_for(qt),dict(message_id=qt))['interaction_examples']
        assert len(text_examples)==1 and text_examples[0]['incoming']==[qi] and text_examples[0]['outgoing']==[qo]
        assert text_examples[0]['linkage']=='quoted_text_exact'
        assert not store.message(qo)['reply_to'].get('source_id'), 'lookup must not rewrite imported quotes'
        assert not prepare(tools_for(dt),dict(message_id=dt))['interaction_examples']
        assert not prepare(tools_for(dt,start=store.message(second_text)['timestamp']),dict(message_id=dt))['interaction_examples'], 'scope must not hide ambiguity'
        assert not prepare(tools_for(it),dict(message_id=it))['interaction_examples']
        assert prepare(tools_for(st),dict(message_id=st))['interaction_examples'][0]['incoming']==[si]
        assert not prepare(tools_for(st,start=store.message(so)['timestamp']),dict(message_id=st))['interaction_examples']
        assert not context_for(tools_for(st,snapshot_max_id=si),store.message(st),with_history=True)['episodes']
        with store.connect() as db:
            text_plan=' '.join(str(tuple(r)) for r in db.execute(f'EXPLAIN QUERY PLAN SELECT id FROM messages WHERE {TEXT_QUOTE} AND platform=? AND conversation_id=? AND {QUOTE_CONTENT}=?',('qq','text_quote','就知道说我')))
            assert 'msg_reply_text' in text_plan
            db.execute('UPDATE messages SET content=? WHERE id=?',('已修订的引用原文',qi))
        assert not prepare(tools_for(qt),dict(message_id=qt))['interaction_examples'], 'stale quote text must not retarget after source edit'
        # Source below the allowed time range is not smuggled in via quote IDs.
        limited=tools_for(group_target,start=store.message(response)['timestamp'])
        e=context_for(limited,store.message(group_target),with_history=True)['episodes']
        assert not any(invitation in x['incoming'] for x in e)
        # Snapshot must apply to both sides, even when a historical message's timestamp fits.
        snap=tools_for(group_target,snapshot_max_id=response-1)
        assert not context_for(snap,store.message(group_target),with_history=True)['episodes']
        # Repeated lookup neither writes an index of private facts nor advances counters.
        assert not layer.listing()['scopes']
        with store.connect() as db:
            assert db.execute('SELECT count(*) FROM tulpa_episodes').fetchone()[0]==0
            explain=' '.join(str(tuple(r)) for r in db.execute(f'EXPLAIN QUERY PLAN SELECT id FROM messages WHERE is_self=1 AND platform=? AND conversation_id=? AND {QUOTE_SOURCE}=?',('qq','g',str(invitation))))
            assert 'msg_reply_source' in explain
            db.execute('UPDATE messages SET content=? WHERE id=?',('今天想休息，下次吧',response))
        revised=prepare(tools_for(group_target),dict(message_id=group_target))['interaction_examples']
        assert next(m['content'] for e in revised for m in e['messages'] if m['id']==response)=='今天想休息，下次吧'
        with store.connect() as db:
            db.execute('DELETE FROM provenance WHERE message_id=?',(invitation,))
            db.execute('DELETE FROM messages WHERE id=?',(invitation,))
        deleted=prepare(tools_for(group_target),dict(message_id=group_target))['interaction_examples']
        assert not any(response in e['outgoing'] for e in deleted)
        # No partial example may consume scarce evidence space.
        tight=tools_for(mt);tight.max_chars=200;tight.max_messages=1
        assert not context_for(tight,store.message(mt),with_history=True)['episodes']
        assert not tight.messages and tight.used_chars==0
        assert not Tulpa(Store(store.path)).listing()['scopes']
        print('PASS: cold archive examples at 0/100, OFF lookup, old FTS match, native/text quote indexes, exact legacy quotes, ambiguous/invalid/truncated/future quotes rejected, no adjacency guesses, private blocks, 8-example cap, scope/snapshot, edits/deletes, budget and no growth writes.')


if __name__=='__main__':main()
