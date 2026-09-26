"""OneBot mention identity/quote preflight regression. Mock transport; no QQ sends."""
import copy
import json
import sys
from pathlib import Path

import httpx

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from chatlocal.message_sender import QQSender,SendError


def main():
    target=dict(platform='qq',conversation_id='1:group:9',conversation='测试群',sender_id='2',
                timestamp=1790252870000,content='@群内称呼 怎么看？')
    original=dict(message_type='group',group_id=9,sender=dict(user_id=2),time=1790252870,
                  message_id=-392931577,message=[dict(type='at',data=dict(qq='1')),dict(type='text',data=dict(text=' 怎么看？'))])
    member=dict(group_id=9,user_id=1,card='群内称呼',nickname='其他昵称')
    state=dict(rows=[copy.deepcopy(original)],member=member);calls=[]
    def transport(req):
        action=req.url.path.lstrip('/');body=json.loads(req.content);calls.append((action,body))
        if action=='get_login_info':data=dict(user_id=1)
        elif action=='get_group_info':data=dict(group_id=9,group_name='测试群')
        elif action=='get_group_msg_history':data=dict(messages=state['rows'])
        elif action=='get_group_member_info':
            assert body==dict(group_id=9,user_id=1,no_cache=True)
            data=state['member']
        elif action=='send_group_msg':data=dict(message_id=123)
        else:raise AssertionError(action)
        return httpx.Response(200,json=dict(status='ok',retcode=0,data=data))
    sender=QQSender(dict(REPLY_ONEBOT_URL='http://127.0.0.1:3000',REPLY_ONEBOT_TOKEN='fixture'),httpx.MockTransport(transport))
    def no_send():assert not any(action.startswith('send_') for action,_ in calls)
    def denied():
        try:sender.prepare(target,True)
        except SendError as exc:assert '未发送' in str(exc)
        else:raise AssertionError('Ambiguous or changed quote was accepted')
        no_send()

    prepared=sender.prepare(target,True)
    assert prepared['quote_id']==original['message_id'];no_send()
    # Native ID is not used; all literal text and the independently resolved @
    # account must match, and duplicate candidate rows remain ambiguous.
    for changed in [dict(group_id=8),dict(sender=dict(user_id=3)),dict(time=1790252868),
                    dict(message_id=7689077534612479173),dict(message=[dict(type='text',data=dict(text=' 怎么看？'))]),
                    dict(message=original['message']+[dict(type='image',data=dict(file='x'))]),
                    dict(message=original['message'][:1]+[dict(type='text',data=dict(text=' 怎么看！'))])]:
        state['rows']=[dict(original,**changed)];denied()
    state['rows']=[original,dict(original,message_id=42)];before=len(calls);denied()
    assert sum(action=='get_group_member_info' for action,_ in calls[before:])==1
    state['rows']=[original]
    for changed in [dict(user_id=3),dict(group_id=8),dict(card='同名无关者',nickname='也不匹配')]:
        state['member']=dict(member,**changed);denied()
    state['member']=member
    state['rows']=[dict(original,message_id=43)]
    try:sender.send(target,'已审核',prepared)
    except SendError as exc:assert '引用原消息已变化' in str(exc)
    else:raise AssertionError('A changed quote bypassed preflight')
    no_send()
    state['rows']=[original]
    receipt=sender.send(target,'已审核',prepared)
    assert receipt['quote_message_id']==original['message_id']
    sends=[body for action,body in calls if action=='send_group_msg']
    assert sends==[dict(group_id=9,message=[dict(type='reply',data=dict(id=str(original['message_id']))),dict(type='text',data=dict(text='已审核'))])]
    print('PASS mock: @ account/card resolution, exact text/sender/time/group, ambiguity, per-lookup cache, send-time recheck and reply segment. No real sends.')


if __name__=='__main__':main()
