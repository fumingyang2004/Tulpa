"""Opt-in model evaluation on synthetic MCP chat, with NO QQ sender.

Only tool *choices* are recorded; no chosen tool is dispatched. No model
reasoning, API credentials or real chat data is written to the report.
"""
import argparse
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from chatlocal import mcp_chat_prompts as p
from chatlocal.mcp_chat import CHAT_INSTRUCTIONS, schemas


def cases():
    def m(i,text,*,own=False,segments=None):
        return dict(id=i,onebot_message_id=str(100+i),sender_id='111' if own else '333',
                    sender='小鲸鱼' if own else '群友A',is_self=own,content=text,received_at=time.time(),segments=segments or [])
    return [
        dict(name='banter',messages=[m(1,'大肥鱼又在偷懒了')],expected=['send_chat_message']),
        dict(name='third_party',messages=[m(2,'@群友B 你上次借的充电器明天记得还我',segments=[{'type':'at','qq':'555'}])],expected=['wait_chat_messages']),
        dict(name='unfinished',messages=[m(3,'我跟你说')],expected=['wait_chat_messages']),
        dict(name='idle_after_own',messages=[],recent=[m(4,'这图怎么越看越像我',own=True)],event='idle',expected=['wait_chat_messages']),
        dict(name='serious',messages=[m(5,'这次考试就差两分又没过，真有点难受')],expected=['send_chat_message']),
        dict(name='code_joke',messages=[m(6,'我就删了一个逗号'),m(7,'整个项目起不来了')],expected=['send_chat_message']),
        dict(name='direct_reply',messages=[m(9,'[引用你的消息] 就你最懂摸鱼',segments=[{'type':'reply','onebot_message_id':'108'}])],recent=[m(8,'这是技术性休息',own=True)],expected=['send_chat_message']),
        dict(name='image_first',messages=[m(10,'@小鲸鱼 这图像不像你 [图片]',segments=[{'type':'at','qq':'111'},{'type':'image','image_index':0}])],expected=['read_chat_image']),
        dict(name='teasing_fresh',messages=[m(11,'@小鲸鱼 你这回消息比校园网还慢',segments=[{'type':'at','qq':'111'}])],expected=['send_chat_message']),
        dict(name='self_echo',messages=[m(13,'等等 好像我才是输的那个',own=True)],recent=[m(12,'这局稳了',own=True)],expected=['wait_chat_messages']),
        dict(name='image_fresh',messages=[m(14,'@小鲸鱼 看这张 [图片]',segments=[{'type':'at','qq':'111'},{'type':'image','image_index':0}])],expected=['read_chat_image']),
        dict(name='familiar_sticker',messages=[m(15,'@小鲸鱼 上次那个无语猫，再发一下',segments=[{'type':'at','qq':'111'}])],
             stickers=[dict(sticker_id='b'*32,model_note='猫呆滞地看着镜头，适合无语、无奈的反应',tags=['猫','无语'],next_step='read_chat_sticker 看图核对')],expected=['read_chat_sticker']),
        dict(name='custom_persona',messages=[m(16,'今天临时加了好多事 头都大了')],persona='沉稳温和的群友，不打趣，不讲大道理，用日常短句回应',expected=['send_chat_message']),
    ]


def run():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',help='Call the locally configured model using synthetic messages only')
    args=parser.parse_args()
    if not args.live:
        print('No model calls. Use --live for synthetic model evaluation (never sends QQ messages).')
        return
    import httpx
    import jsonschema
    from chatlocal.config import settings
    from chatlocal.mcp_tools import INSTRUCTIONS
    cfg=settings()
    if not cfg['API_KEY']:raise SystemExit('No configured API key; model evaluation not run.')
    sid='a'*32
    row=dict(id=sid,conversation_id='111:group:222',persona=p.resolve_persona(preset='little_whale'),
             persona_preset='little_whale',participation='natural',note='')
    scope=dict(chat=True,send=True,chat_images=True,chat_sticker_send=True,chat_sticker_collect=True)
    def tool(name,description,properties,required):
        return dict(name=name,description=description,parameters=dict(type='object',properties=properties,required=required,additionalProperties=False))
    from chatlocal.mcp_chat_media import schemas as media_schemas
    functions=schemas(tool)+media_schemas(tool,scope)
    assert {'read_chat_image','read_chat_sticker','send_chat_sticker'}<={f['name'] for f in functions}
    report=[]
    with httpx.Client(timeout=50,trust_env=False) as client:
        for case in cases():
            current=dict(row,persona=case['persona'],persona_preset='') if case.get('persona') else row
            items=case['messages'];recent=case.get('recent',[])+items
            start=dict(session=dict(id=sid,**{k:current[k] for k in ('conversation_id','participation','persona_preset')}),
                       instructions=CHAT_INSTRUCTIONS,context=dict(messages=case.get('recent',[])),
                       chat_prompt=p.packet(current,case.get('recent',[]),[],full=True,scope=scope))
            wait=dict(event=case.get('event','messages'),session_id=sid,messages=items,
                      read_through_id=items[-1]['id'] if items else 4,has_more=False,continue_waiting=True,
                      chat_guidance=p.packet(current,recent,items,event=case.get('event','messages'),scope=scope,stickers=case.get('stickers')))
            def call(mid,name,arg):
                return dict(role='assistant',content=None,tool_calls=[dict(id=mid,type='function',function=dict(name=name,arguments=json.dumps(arg,ensure_ascii=False)))])
            messages=[dict(role='system',content='你是一个使用 MCP 工具的外部 Agent。\n'+INSTRUCTIONS),
                      dict(role='user',content='在测试群持续聊天，自然参与，直到我停止；已授权发言、看图和表情。人格：'+(case.get('persona') or '使用小鲸鱼预设')),
                      call('s','start_chat_session',dict(conversation_id='111:group:222',idempotency_key='eval-fixture',**({'persona':case['persona']} if case.get('persona') else {'persona_preset':'little_whale'}))),
                      dict(role='tool',tool_call_id='s',content=json.dumps(start,ensure_ascii=False)),
                      call('w','wait_chat_messages',dict(session_id=sid,known_prompt_version=p.version(current))),
                      dict(role='tool',tool_call_id='w',content=json.dumps(wait,ensure_ascii=False))]
            body=dict(model=cfg['MODEL'],messages=messages,tools=[dict(type='function',function=f) for f in functions],max_tokens=700,stream=False)
            if urlsplit(cfg['API_BASE']).hostname=='api.deepseek.com':body['thinking']={'type':'disabled'}
            began=time.monotonic()
            try:
                response=client.post(cfg['API_BASE'].rstrip('/')+'/chat/completions',headers={'Authorization':'Bearer '+cfg['API_KEY']},json=body)
                response.raise_for_status()
                data=response.json();message=data['choices'][0]['message']
                actions=[dict(name=t['function']['name'],args=json.loads(t['function']['arguments'])) for t in message.get('tool_calls',[])]
                choice_ok=bool(actions) and all(a['name'] in case['expected'] for a in actions)
                for action in actions:
                    schema=next((f['parameters'] for f in functions if f['name']==action['name']),None)
                    if schema is None or list(jsonschema.Draft202012Validator(schema).iter_errors(action['args'])):
                        choice_ok=False
                    if 'session_id' in action['args'] and action['args']['session_id']!=sid:choice_ok=False
                texts=[a['args'].get('text','') for a in actions if a['name']=='send_chat_message']
                concise=all(len(text)<=60 and '\n' not in text for text in texts)
                report.append(dict(case=case['name'],input=items,expected=case['expected'],actions=actions,
                                   public_content=message.get('content'),choice_ok=choice_ok,concise=concise,
                                   seconds=round(time.monotonic()-began,2),usage=data.get('usage')))
                print(json.dumps({k:report[-1][k] for k in ('case','actions','choice_ok','concise')},ensure_ascii=False),flush=True)
            except Exception as exc:
                # HTTP exception strings can include a configured private URL.
                report.append(dict(case=case['name'],error_type=type(exc).__name__,choice_ok=False))
                print(case['name']+': '+type(exc).__name__,flush=True)
    path=ROOT/'.tmp/mcp-chat-style-eval.json';path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(dict(model=cfg['MODEL'],prompt_version=p.version(row),cases=report),ensure_ascii=False,indent=2),'utf-8')
    print('Choices matching expected: '+str(sum(c.get('choice_ok',False) for c in report))+'/'+str(len(report)))
    print('Report: '+str(path))


if __name__=='__main__':run()
