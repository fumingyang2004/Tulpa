"""Isolated approval, sender and route tests. Never contacts real QQ or cloud."""
import json,sys,tempfile,time,threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from chatlocal.store import Store
from chatlocal.replies import Replies,ReplyRunner,dump
from chatlocal.reply_routes import install_reply_routes
from chatlocal.reply_agent import prepare,draft_result
from chatlocal.agent_tools import ChatTools
from chatlocal.retrieval import Plan
from chatlocal.message_sender import QQSender,SendError,SendUncertain


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='reply-check-') as tmp:
        folder=Path(tmp);s=Store(folder/'test.sqlite3');path=folder/'input.json'
        rows=[]
        for n,(content,own,cid) in enumerate([('晚上打游戏吗？',False,'1:direct:2'),('不去不去今天躺平😋',True,'1:direct:2'),
          ('你这代码又炸了？',False,'1:direct:2'),('老师辛苦了，我再核对一下。',True,'1:direct:3'),('微信文字',False,'wx')]):
            rows.append(dict(platform='wechat' if cid=='wx' else 'qq',conversation_id=cid,conversation='老师' if cid.endswith('3') else '好友',
              conversation_type='direct',sender='我' if own else '对方',sender_id='1' if own else cid.split(':')[-1],is_self=own,
              content=content,timestamp=f'2026-09-24 10:00:0{n}',source_id=str(1000000000000000000+n)))
        path.write_text(dump(rows),encoding='utf-8');s.import_file(path)
        calls=[];mode={'timeout':False,'wrong_account':False}
        def transport(req):
            action=req.url.path.lstrip('/');p=json.loads(req.content)
            if action=='get_login_info':data=dict(user_id=99 if mode['wrong_account'] else 1)
            elif action=='get_friend_list':data=[dict(user_id=2,nickname='好友'),dict(user_id=3,nickname='老师')]
            elif action=='get_group_info':data=dict(group_id=p['group_id'],group_name='测试群')
            elif action=='get_friend_msg_history':
                data=dict(messages=[dict(user_id=2,message_type='private',sender=dict(user_id=2),time=rows_time,message_id=11,
                  message=[dict(type='text',data=dict(text=rows[0]['content']))])])
            elif action.startswith('send_'):
                calls.append((action,p));time.sleep(.05)
                if mode['timeout']:raise httpx.ReadTimeout('fixture')
                data=dict(message_id=1234)
            else:raise AssertionError(action)
            return httpx.Response(200,json=dict(status='ok',retcode=0,data=data))
        sender=lambda platform: QQSender(dict(REPLY_ONEBOT_URL='http://127.0.0.1:3000',REPLY_ONEBOT_TOKEN='fixture'),httpx.MockTransport(transport)) if platform=='qq' else (_ for _ in ()).throw(SendError('微信不发送'))
        rows_time=s.message(1)['timestamp']//1000
        layer=Replies(s,sender)
        d=layer.create(1);sid=d['id']
        assert layer.create(1)['id']==sid
        def draft(sid):
            with s.connect() as db:db.execute("UPDATE reply_drafts SET ai_text='不了不了今天废了😋',final_text='不了不了今天废了😋',revision=revision+1 WHERE id=?",(sid,))
            return layer.get(sid)
        d=draft(sid)
        # Generation context cannot leak the teacher's different style or another platform.
        tools=ChatTools(s,Plan(platforms=['qq'],conversations=[dump(['qq','1:direct:2'])],snapshot_max_id=5))
        seed=prepare(tools,dict(message_id=1))
        assert set(tools.messages)=={1,2,3} and '老师辛苦' not in dump(seed)
        assert 'error' in tools.execute('get_context',dict(message_id=4))
        result=draft_result('不去不去😋',tools,dict(message_id=1))
        assert result['reply_text']=='不去不去😋' and set(result['context_ids'])=={1,2,3}
        assert result['self_message_ids']==[2] and not result['claims']
        # No model-authored citations or style fields are needed.
        assert not {'basis','evidence_ids','style_ids'} & result.keys()
        tools.context_requirements[1]={999999}
        assert draft_result('还得是你😋',tools,dict(message_id=1))['reply_text']=='还得是你😋'
        try:layer.send(sid,'forged',True);assert False
        except ValueError:pass
        p=layer.preview(sid,d['revision'],True);assert p['destination']['quote_id']==11 and not calls
        edited=layer.edit(sid,'[CQ:at,qq=all] 不了哈哈，下次',d['revision'])
        try:layer.send(sid,p['token'],True);assert False
        except ValueError:pass
        p=layer.preview(sid,edited['revision'])
        try:layer.send(sid,p['token'],False);assert False
        except ValueError:pass
        with ThreadPoolExecutor(max_workers=2) as pool:
            fs=[pool.submit(layer.send,sid,p['token'],True) for _ in range(2)]
            results=[]
            for f in fs:
                try:results.append(f.result())
                except ValueError:pass
        assert layer.get(sid)['status']=='SENT' and len(calls)==1
        assert calls[0][1]['message']==[dict(type='text',data=dict(text=edited['final_text']))], 'CQ injection'
        assert layer.send(sid,p['token'],True)['status']=='SENT' and len(calls)==1
        assert layer.get(sid)['ai_text']!=layer.get(sid)['final_text']
        with s.connect() as db:
            states=[r[0] for r in db.execute('SELECT state FROM reply_audit WHERE draft_id=? ORDER BY id',(sid,))]
        assert all(x in states for x in ['DRAFT','USER_APPROVED','SENDING','SENT'])
        d2=draft(layer.create(3)['id']);mode['wrong_account']=True
        try:layer.preview(d2['id'],d2['revision']);assert False
        except SendError:pass
        mode['wrong_account']=False;p2=layer.preview(d2['id'],d2['revision']);mode['timeout']=True
        assert layer.send(d2['id'],p2['token'],True)['status']=='UNKNOWN'
        assert layer.send(d2['id'],p2['token'],True)['status']=='UNKNOWN' and len(calls)==2
        assert layer.create(3)['status']=='UNKNOWN'  # Reopen can't bypass ambiguity.
        d3=draft(layer.create(4)['id']);p3=layer.preview(d3['id'],d3['revision'])
        with s.connect() as db:db.execute("UPDATE messages SET content='已变更' WHERE id=4")
        try:layer.send(d3['id'],p3['token'],True);assert False
        except ValueError:pass
        assert len(calls)==2
        # API: neither a bare form nor cross-origin JS can approve.
        app=FastAPI();install_reply_routes(app,s,sender);client=TestClient(app)
        assert client.post('/api/replies',json=dict(message_id=1)).status_code==403
        assert client.post('/api/replies',json=dict(message_id=1),headers={'X-Reply-UI':'1','Origin':'https://evil.test'}).status_code==403
        assert client.post('/api/replies',json=dict(message_id=1),headers={'X-Reply-UI':'1'}).json()['status']=='SENT'
        # Reject revokes the exact approval, preserves original/edited text and
        # permits a fresh draft without reopening ambiguous sends.
        headers={'X-Reply-UI':'1'}
        rejected=draft(layer.create(1,fresh=True)['id'])
        pending=layer.preview(rejected['id'],rejected['revision'])
        rejected=layer.edit(rejected['id'],'拒绝后保留的编辑',rejected['revision'])
        pending=layer.preview(rejected['id'],rejected['revision'])
        endpoint='/api/replies/'+rejected['id']+'/reject'
        assert client.post(endpoint,json=dict(revision=rejected['revision'])).status_code==403
        assert client.post(endpoint,json=dict(revision=rejected['revision']-1),headers=headers).status_code==400
        rejected=client.post(endpoint,json=dict(revision=rejected['revision']),headers=headers).json()
        assert rejected['status']=='REJECTED' and rejected['final_text']=='拒绝后保留的编辑' and rejected['ai_text']!=rejected['final_text']
        try:layer.send(rejected['id'],pending['token'],True);assert False
        except ValueError:pass
        assert layer.create(1)['id']==rejected['id'] and len(calls)==2
        assert client.post('/api/replies/'+sid+'/reject',json=dict(revision=edited['revision']),headers=headers).status_code==400
        assert layer.create(3,fresh=True)['status']=='UNKNOWN'
        fresh=layer.create(1,fresh=True);assert fresh['id']!=rejected['id'] and fresh['status']=='DRAFT'
        # A provider that returns after rejection cannot resurrect its draft.
        ready=threading.Event();release=threading.Event()
        def late_agent(*args,**kwargs):
            ready.set();assert release.wait(10)
            yield dict(type='done',status='completed',record=dict(result=dict(reply_text='迟到的模型输出')))
        runner=ReplyRunner(layer,late_agent)
        running=runner.start(fresh['id']);assert ready.wait(10)
        assert runner.reject(fresh['id'],running['revision'])['status']=='REJECTED'
        release.set()
        until=time.monotonic()+10
        while time.monotonic()<until:
            with runner.lock:active=bool(runner.active)
            if not active:break
            time.sleep(.01)
        assert not active
        cancelled=layer.get(fresh['id'])
        assert cancelled['status']=='REJECTED' and not cancelled['ai_text'] and cancelled['generations'][0]['status']=='cancelled'
        assert len(calls)==2
        with s.connect() as db:
            db.execute("UPDATE reply_drafts SET status='SENDING' WHERE id=?",(d3['id'],))
        layer.recover();assert layer.get(d3['id'])['status']=='UNKNOWN'
        assert Replies(Store(folder/'test.sqlite3'),sender).get(sid)['receipt']['message_id']==1234
    print('PASS isolated: scope/style, exact approval/replay, sender safety, reject/revoke, late generation cancellation, fresh drafts, UNKNOWN no-retry, origin, restart and persistence. No real sends.')


if __name__=='__main__':main()
