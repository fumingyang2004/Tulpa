"""Explicitly approved sends only. This module is never registered as an Agent tool."""
import os
import time
from typing import Protocol
from urllib.parse import urlparse

import httpx
from dotenv import dotenv_values
from .config import ROOT


class SendError(ValueError):pass
class SendUncertain(SendError):pass


class MessageSender(Protocol):
    def prepare(self,target:dict,quote:bool)->dict: ...
    def send(self,target:dict,text:str,prepared:dict)->dict: ...


def sender_config():
    values=dotenv_values(ROOT/'.env')
    return {k:(os.environ.get(k,values.get(k)) or '').strip() for k in ('REPLY_ONEBOT_URL','REPLY_ONEBOT_TOKEN')}


def capability(platform):
    if platform!='qq':return dict(available=False,note='微信暂不支持发送，可以生成、编辑并复制草稿。')
    if not sender_config()['REPLY_ONEBOT_URL']:
        return dict(available=False,note='尚未配置本机发送接口。请在 .env 配置 REPLY_ONEBOT_URL 和 REPLY_ONEBOT_TOKEN；草稿仍可生成和复制。')
    return dict(available=True,note='批准后将核对登录账号和目标会话，并发送当前编辑框中的文字。')


def qq_address(target):
    parts=target['conversation_id'].split(':')
    if target['platform']!='qq' or len(parts)!=3 or parts[1] not in ('group','direct') or not all(p.isascii() and p.isdigit() and int(p)>0 for p in (parts[0],parts[2])):
        raise SendError('所选记录没有可靠 QQ 账号和会话编号，不能发送。')
    return parts[0],parts[1],parts[2]


class QQSender:
    """OneBot v11 text segments; CQ-looking user text stays literal. No retries."""
    def __init__(self,config=None,transport=None):
        self.config=config or sender_config();self.url=self.config['REPLY_ONEBOT_URL'].rstrip('/');self.transport=transport
        parsed=urlparse(self.url)
        if not self.url:raise SendError('尚未配置 REPLY_ONEBOT_URL，未发送。')
        if parsed.scheme not in ('http','https') or parsed.hostname not in ('127.0.0.1','localhost','::1') or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise SendError('发送接口必须是不含凭据参数的 localhost HTTP(S) 地址。')

    def call(self,action,payload,*,sending=False):
        allowed={'get_login_info','get_group_info','get_group_member_info','get_friend_list','get_group_msg_history','get_friend_msg_history','get_msg'}
        if action not in (allowed|({'send_group_msg','send_private_msg'} if sending else set())):raise SendError('接口不在发送适配器白名单中。')
        try:
            with httpx.Client(timeout=httpx.Timeout(20,connect=4),trust_env=False,follow_redirects=False,transport=self.transport) as client:
                token=self.config['REPLY_ONEBOT_TOKEN']
                r=client.post(self.url+'/'+action,json=payload,headers={'Authorization':'Bearer '+token} if token else {})
                r.raise_for_status()
                if len(r.content)>2*1024*1024:raise ValueError('large')
                value=r.json()
                if not isinstance(value,dict):raise ValueError('format')
                if value.get('status')!='ok' or value.get('retcode')!=0:
                    # Async/queued responses can already have delivered; never retry.
                    if sending:raise SendUncertain('发送接口未给出确定成功回执，请先到 QQ 核对；本条不会自动重发。')
                    raise SendError('OneBot 未返回成功结果，请检查接口和登录状态。')
                return value.get('data')
        except SendError:raise
        except (httpx.HTTPError,ValueError,TypeError):
            if sending:raise SendUncertain('发送结果未知（超时、连接中断或回执无效）。请到 QQ 核对，本条禁止再次发送以免重复。') from None
            raise SendError('无法核对本机 OneBot 接口，请检查地址、Token 和登录状态；未发送。') from None

    def check_target(self,target):
        account,kind,peer=qq_address(target)
        info=self.call('get_login_info',{})
        if not isinstance(info,dict) or str(info.get('user_id'))!=account:raise SendError('发送接口登录账号与原始 QQ 记录的本人账号不一致，未发送。')
        if kind=='group':
            info=self.call('get_group_info',dict(group_id=int(peer),no_cache=True))
            if not isinstance(info,dict) or str(info.get('group_id'))!=peer:raise SendError('无法核对目标群，未发送。')
            name=str(info.get('group_name') or target['conversation'])
        else:
            friends=self.call('get_friend_list',{})
            match=next((f for f in friends if isinstance(f,dict) and str(f.get('user_id'))==peer),None) if isinstance(friends,list) else None
            if not match:raise SendError('目标不是当前账号已核对的好友，暂不发送临时会话。')
            name=str(match.get('remark') or match.get('nickname') or target['conversation'])
        return dict(account=account,kind=kind,peer=peer,name=name)

    def quote_text_matches(self,segments,content,address,members):
        """Match native display text without dropping OneBot mention identities.

        Resolve only mentions in candidate rows through their exact group/account.
        No fuzzy names, text-suffix matching or assumptions about media contents.
        """
        if not content or not isinstance(segments,list) or not 1<=len(segments)<=100:return False
        offsets={0}
        for segment in segments:
            if not isinstance(segment,dict) or not isinstance(segment.get('data'),dict):return False
            data=segment['data'];kind=segment.get('type')
            if kind=='text' and isinstance(data.get('text'),str):
                alternatives={data['text']}
            elif kind=='at' and address['kind']=='group':
                account=str(data.get('qq',''))
                if not account.isascii() or not account.isdigit() or int(account)<=0:return False
                if account not in members:
                    if len(members)>=8:return False
                    info=self.call('get_group_member_info',dict(group_id=int(address['peer']),user_id=int(account),no_cache=True))
                    if not isinstance(info,dict) or str(info.get('group_id'))!=address['peer'] or str(info.get('user_id'))!=account:
                        raise SendError('无法核对原消息中 @ 的群成员账号，未发送。')
                    members[account]={'@'+info[k] for k in ('card','nickname') if isinstance(info.get(k),str) and info[k]}
                alternatives=members[account]
            else:return False
            offsets={offset+len(text) for offset in offsets for text in alternatives if content.startswith(text,offset)}
            if not offsets:return False
        return len(content) in offsets

    def quote_id(self,target,address):
        # Local native IDs and OneBot message_id use different namespaces. Never
        # pass or hash an NT ID blindly. Resolve a unique, verified history row.
        kind=address['kind'];key='group_id' if kind=='group' else 'user_id'
        value=self.call('get_group_msg_history' if kind=='group' else 'get_friend_msg_history',
                        {key:int(address['peer']),'count':100})
        rows=value.get('messages',[]) if isinstance(value,dict) else []
        found=[];members={}
        for row in rows[:100]:
            if not isinstance(row,dict):continue
            if kind=='group' and str(row.get(key))!=address['peer']:continue
            if kind=='direct':
                # SnowLuma user_id is the sender, target_id is the recipient.
                # For own messages the peer cannot be inferred from user_id.
                outgoing=str((row.get('sender') or {}).get('user_id'))==address['account']
                if outgoing and str(row.get('target_id'))!=address['peer']:continue
                if not outgoing and str(row.get('user_id'))!=address['peer']:continue
            if row.get('message_type')!=('group' if kind=='group' else 'private'):continue
            if str((row.get('sender') or {}).get('user_id'))!=str(target['sender_id']):continue
            if type(row.get('time')) is not int or abs(row['time']*1000-target['timestamp'])>=1000:continue
            mid=row.get('message_id')
            if type(mid) is not int or not -(2**31)<=mid<2**31:continue
            if self.quote_text_matches(row.get('message'),target['content'],address,members):found.append(mid)
        if len(found)!=1:raise SendError('无法唯一核对原消息的 OneBot 引用编号，未发送。可取消“引用原消息”后，再次点击“批准”发送纯文本。')
        return found[0]

    def prepare(self,target,quote=False):
        address=self.check_target(target)
        return dict(address,quote_id=self.quote_id(target,address) if quote else None)

    def verify_chat_message(self,address,response_target):
        """Shared native quote/reaction identity check; returns the verified record."""
        mid=response_target['onebot_message_id']
        digits=mid[1:] if isinstance(mid,str) and mid.startswith('-') else mid
        if (not isinstance(digits,str) or not digits.isascii() or not digits.isdigit()
                or len(mid)>11 or str(int(mid))!=mid or not -(2**31)<=int(mid)<2**31 or int(mid)==0):
            raise SendError('原消息没有有效的 OneBot 消息编号，未发送。')
        info=self.call('get_msg',dict(message_id=int(mid)))
        sender=info.get('sender') if isinstance(info,dict) and isinstance(info.get('sender'),dict) else {}
        if (not isinstance(info,dict) or str(info.get('message_id'))!=mid
                or info.get('message_type')!='group' or str(info.get('group_id'))!=address['peer']
                or ('self_id' in info and str(info['self_id'])!=address['account'])
                or str(sender.get('user_id'))!=response_target['sender_id']
                or type(info.get('time')) is not int or info['time']!=response_target['timestamp']//1000
                or any(info.get(k) for k in ('recalled','is_recalled','deleted','is_deleted'))):
            raise SendError('无法核对引用目标的账号、群、发送者和时间，或原消息已撤回；未发送。')
        return info

    def chat_targets(self,address,response_target,quote,mentions):
        """Verify a cached live event against OneBot, never a local M/NT ID.

        The caller resolves the event in its own authorized session. OneBot
        lookups are repeated before dispatch to reject stale mappings/members.
        """
        if address['kind']!='group':raise SendError('原生引用和 @ 仅用于当前持续群聊，未发送。')
        if response_target and response_target.get('conversation_id')!=f'{address["account"]}:group:{address["peer"]}':
            raise SendError('回应目标不属于当前账号和群，未发送。')
        quote_id=None
        if quote:
            if not response_target:raise SendError('显示引用需要本会话已读取的回应目标，未发送。')
            info=self.verify_chat_message(address,response_target)
            quote_id=int(info['message_id'])
        for uid in mentions:
            info=self.call('get_group_member_info',dict(group_id=int(address['peer']),user_id=int(uid),no_cache=True))
            if not isinstance(info,dict) or str(info.get('group_id'))!=address['peer'] or str(info.get('user_id'))!=uid:
                raise SendError('无法核对 @ 对象仍是当前群的指定成员，未发送。')
        return quote_id

    def prepare_chat(self,target,*,response_target=None,quote=False,mention_user_ids=()):
        address=self.check_target(target)
        return dict(address,chat_send=True,response_target=response_target,mention_user_ids=list(mention_user_ids),
                    quote_id=self.chat_targets(address,response_target,quote,mention_user_ids))

    def send(self,target,text,prepared,*,guard=None):
        address=self.check_target(target)
        if any(address[k]!=prepared[k] for k in ('account','kind','peer','name')):raise SendError('目标会话信息已变化，请重新确认，未发送。')
        quote_id=prepared.get('quote_id')
        mentions=prepared.get('mention_user_ids',[]) if prepared.get('chat_send') else []
        if prepared.get('chat_send'):
            if self.chat_targets(address,prepared.get('response_target'),quote_id is not None,mentions)!=quote_id:
                raise SendError('引用原消息已变化，未发送。')
        elif quote_id is not None and self.quote_id(target,address)!=quote_id:raise SendError('引用原消息已变化，请重新确认，未发送。')
        segments=[dict(type='at',data=dict(qq=uid)) for uid in mentions]+[dict(type='text',data=dict(text=text))]
        if quote_id is not None:segments.insert(0,dict(type='reply',data=dict(id=str(quote_id))))
        group=address['kind']=='group';key='group_id' if group else 'user_id'
        if guard:guard()
        data=self.call('send_group_msg' if group else 'send_private_msg',{key:int(address['peer']),'message':segments},sending=True)
        if not isinstance(data,dict) or type(data.get('message_id')) is not int:
            raise SendUncertain('接口未返回可靠消息编号，请到 QQ 核对，本条不会自动重发。')
        receipt=dict(message_id=data['message_id'],conversation_id=target['conversation_id'],timestamp=time.time(),content=text,
                    quote_message_id=quote_id,platform='qq',note='OneBot 已返回发送成功回执。协议发送可能不写入桌面 QQ 的本地记录；此回执不代表客户端已同步。')
        if prepared.get('chat_send'):
            receipt.update(message_segments=segments,mention_user_ids=mentions,
                           reply_to_event_id=(prepared.get('response_target') or {}).get('event_id'),
                           segment_source='submitted_to_onebot',
                           display_note='以上为程序提交的消息段。引用是否由 OneBot / QQ 额外显示 @，以实际客户端为准。')
        return receipt


def sender_for(platform):
    if platform!='qq':raise SendError('本版不支持微信发送。')
    return QQSender()
