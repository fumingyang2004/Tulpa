"""Small adapters for documented exports; no client protocol implementation."""
import hashlib
import html
import json
import re
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))


def safe_reply(value):
    """Keep quoted text/identity only; attachment XML may contain client keys."""
    if not isinstance(value, dict):
        return None
    result = {}
    for key, limit in (('source_id', 200), ('sender', 200), ('content', 2000)):
        part = value.get(key)
        if isinstance(part, (str, int)) and not isinstance(part, bool):
            result[key] = str(part)[:limit]
    if re.search(r'<(?:\?xml|msg\b|appmsg\b|emoji\b|img\b)', html.unescape(result.get('content', '')), re.I):
        result['content'] = '[引用非文本消息]'
    return result or None


def stamp(value):
    if value is None or isinstance(value, bool):
        raise ValueError('缺少有效 timestamp')
    try:
        n = float(value)
        if n < 100_000_000_000:
            n *= 1000
        ts = int(n)
        date = datetime.fromtimestamp(ts / 1000, TZ)
    except (ValueError, TypeError):
        date = datetime.fromisoformat(str(value).replace('Z', '+00:00').replace('/', '-'))
        if date.tzinfo is None:
            date = date.replace(tzinfo=TZ)
        ts = int(date.timestamp() * 1000)
    if not 2000 <= date.year <= 2100:
        raise ValueError('timestamp 超出 2000–2100 年范围')
    return ts


def display_time(ts):
    return datetime.fromtimestamp(ts / 1000, TZ).strftime('%Y-%m-%d %H:%M:%S')


def truth(value):
    if value is None or value == '':
        return None
    if value is True or value == 1 or str(value).lower() == 'true':
        return True
    if value is False or value == 0 or str(value).lower() == 'false':
        return False
    raise ValueError('is_self 必须为 true/false/null')


def first(obj, *keys, default=None):
    for key in keys:
        if obj.get(key) is not None and obj[key] != '':
            return obj[key]
    return default


def platform_name(value):
    value = str(value or '').lower()
    value = {'微信': 'wechat', 'weixin': 'wechat', 'wx': 'wechat'}.get(value, value)
    if value not in ('qq', 'wechat'):
        raise ValueError('无法确定平台，请选择 qq 或 wechat')
    return value


def decode_export(path):
    if path.stat().st_size > 100 * 1024 * 1024:
        raise ValueError('MVP 单文件上限 100 MB，请在导出工具中按会话/时间拆分。')
    text = path.read_text(encoding='utf-8-sig')
    if path.suffix.lower() in ('.jsonl', '.ndjson'):
        records = []
        for line, content in enumerate(text.splitlines(), 1):
            if content.strip():
                records.append((f'line:{line}', json.loads(content)))
        return {}, records, 'canonical'
    data = json.loads(text)
    if isinstance(data, list):
        return {}, [(f'/{i}', m) for i, m in enumerate(data)], 'canonical'
    if not isinstance(data, dict):
        raise ValueError('导出文件必须是 JSON 对象/数组或统一 Schema JSONL')
    if 'chunked' in data or ('messages' not in data and 'chunks' in data):
        raise ValueError('请在 QCE 选择单文件 JSON 导出；此版本不导入分块 manifest。')
    # wx --json may wrap its payload.
    if isinstance(data.get('data'), dict) and 'messages' in data['data']:
        data = data['data']
    kind = ('wechatauto' if data.get('reader') == 'wechatauto-replica' or ('chats' in data and 'wxid' in data)
            else 'qce' if 'chatInfo' in data else 'chatlab' if 'chatlab' in data
            else 'weflow' if 'weflow' in data else 'wx' if 'chat' in data else 'canonical')
    records = data.get('messages')
    if not isinstance(records, list):
        raise ValueError('找不到 messages 数组；支持 QCE、wx-cli、ChatLab、WeFlow JSON 和统一 JSONL。')
    return data, [(f'/messages/{i}', m) for i, m in enumerate(records)], kind


def normalize(raw, head, kind, platform='', conversation='', self_ids=''):
    if not isinstance(raw, dict):
        raise ValueError('消息不是对象')
    if raw=={'_chatlocal_deleted':True}:return None
    owner_ids = {s.strip() for s in (self_ids or '').replace('，', ',').split(',') if s.strip()}
    self_value = None
    original_id = first(raw, 'source_id', 'platformMessageId', 'id', 'messageId', 'msg_id')
    conv_id = None
    conv_type = str(raw.get('conversation_type') or '').lower()
    content = raw.get('content')
    ts = first(raw, 'timestamp', 'time')
    sender = raw.get('sender')
    sender_id = first(raw, 'sender_id')
    p = platform or raw.get('platform')
    conv = conversation or raw.get('conversation')
    media=raw.get('media') or []
    from .voice_metadata import clean as clean_voice, wechat_voice, PLACEHOLDER
    voice=clean_voice(raw.get('voice'))
    from .artifact_metadata import clean_descriptor, wechat_file
    raw_files=raw.get('files') or []
    if not isinstance(raw_files,list):raise ValueError('files 必须是数组')
    files=[d for v in raw_files[:16] if (d:=clean_descriptor(v))]
    reply=safe_reply(raw.get('reply_to'))
    if kind == 'qce':
        p = 'qq'
        info = head['chatInfo']
        conv_type = str(info.get('type') or '').lower()
        conv = conversation or info.get('name')
        peer = first(info, 'peerUid', 'peerUin')
        conv_id = f"{info.get('type', 'chat')}:{peer}" if peer else None
        owner_ids.update(str(info[k]) for k in ('selfUid', 'selfUin') if info.get(k))
        details = raw.get('sender') or {}
        if not isinstance(details, dict):
            raise ValueError('QCE sender 格式错误')
        sender_id = first(details, 'uid', 'uin')
        sender = first(details, 'name', 'groupCard', 'nickname', 'uin', 'uid')
        if owner_ids and (details.get('uid') or details.get('uin')):
            self_value = bool(owner_ids.intersection(str(details[k]) for k in ('uid', 'uin') if details.get(k)))
        content = content.get('text') if isinstance(content, dict) else content
        if raw.get('system') or raw.get('isSystemMessage') or raw.get('recalled') or raw.get('isRecalled'):
            return None
    elif kind == 'wx':
        p = 'wechat'
        conv = conversation or head.get('chat')
        conv_id = head.get('username')
        conv_type = 'group' if str(conv_id).endswith('@chatroom') else 'direct'
        sender_id = first(raw, 'sender_username', 'sender_id')
        original_id = first(raw, 'server_id', 'msg_svr_id', 'id', 'msg_id')
        if original_id is None and raw.get('local_id') is not None:
            original_id = f"local:{raw['local_id']}:{stamp(ts)}"
        # wx-cli intentionally omits the peer label on some private messages.
        # Preserve that uncertainty rather than invent a sender.
        sender = sender or '未知发送者（导出未提供）'
        # wx often exports human readable type names; media is not transcribed.
        msg_type = first(raw, 'type', 'msg_type')
        if msg_type is not None and str(msg_type).lower() not in ('1', 'text', '文本', '文本消息'):
            return None
        self_value = truth(first(raw, 'is_self', 'is_send'))
    elif kind == 'weflow':
        p = 'wechat'
        info = head.get('session') or {}
        conv = conversation or first(info, 'displayName', 'remark', 'nickname', 'wxid')
        conv_id = info.get('wxid')
        conv_type = 'group' if str(conv_id).endswith('@chatroom') else 'direct'
        sender_id = raw.get('senderUsername')
        sender = first(raw, 'senderDisplayName', 'senderUsername')
        ts = first(raw, 'createTime', 'formattedTime')
        self_value = truth(raw.get('isSend'))
        original_id = first(raw, 'platformMessageId', 'localId')
        if raw.get('type') not in ('文本消息', '引用消息'):
            return None
    elif kind == 'wechatauto':
        p = 'wechat'
        conv_id = raw.get('chat')
        conv_type = 'group' if str(conv_id).endswith('@chatroom') else 'direct'
        chats = {c['username']:c.get('name',c['username']) for c in head.get('chats',[])}
        conv = conversation or chats.get(conv_id,conv_id)
        sender_id = raw.get('sender_username','') if head.get('identity_method') else ''
        sender = str(first(raw,'sender_name',default=sender_id or '未知发送者'))
        ts = raw.get('create_time')
        original_id = raw.get('server_id')
        if not original_id or str(original_id)=='0':
            original_id = f"local:{raw.get('local_id')}:{stamp(ts)}"
        if sender_id and head.get('wxid'):
            self_value = sender_id == head['wxid']
        base_type=int(raw.get('type_code',0)) & 0xffffffff
        if base_type==34:
            voice=wechat_voice(raw,head.get('wxid',''));content=PLACEHOLDER
        elif base_type in (3,47):
            if not media:media=[dict(kind='sticker' if base_type==47 else 'image',status='unavailable')]
            content=raw.get('text','')
        elif base_type==49 and (attachment:=wechat_file(raw.get('content',''))):
            files=[attachment]
            content='[文件] '+attachment['filename']
        elif base_type==49 and reply:
            content=raw.get('text','')
        elif base_type!=1:return None
        # Group plaintext can be prefixed by the actual sender username.
        if sender_id and isinstance(content,str) and content.startswith(sender_id+':\n'):
            content = content[len(sender_id)+2:]
    elif kind == 'chatlab':
        info = head.get('meta') or {}
        p = info.get('platform') or p
        conv = conversation or info.get('name')
        conv_id = info.get('groupId')
        sender_id = str(raw.get('sender') or '')
        members = {str(m.get('platformId')): m for m in head.get('members', [])}
        member = members.get(sender_id, {})
        sender = first(raw, 'groupNickname', 'accountName') or first(member, 'groupNickname', 'accountName') or sender_id
        if info.get('ownerId'):
            owner_ids.add(str(info['ownerId']))
        if raw.get('type') != 0:
            return None
    else:
        conv_id = raw.get('conversation_id')
        if head.get('reader') == 'chatlog-keeper' and not conv_type:
            conv_type = 'direct' if ':direct:' in str(conv_id) else 'group' if ':group:' in str(conv_id) else ''
        self_value = truth(raw.get('is_self'))
    if files and (content is None or isinstance(content,str) and not content.strip()):
        content='\n'.join('[文件] '+f['filename'] for f in files)
    if (content is None or (isinstance(content, str) and not content.strip())) and not media and not reply:
        return None
    if content is None:content=''
    if not isinstance(content, str):
        raise ValueError('content 必须是文本')
    if not conv or not isinstance(conv, str) or not sender or not isinstance(sender, str):
        raise ValueError('缺少会话名或发送者；请填写会话名或使用带元信息的 JSON')
    if self_value is None and owner_ids and sender_id:
        self_value = str(sender_id) in owner_ids
    p = platform_name(p)
    conv_id = str(conv_id or 'name:' + conv)
    result = dict(platform=p, conversation=conv, conversation_id=conv_id,
                  conversation_type='direct' if conv_type in ('direct','friend','private','c2c') else 'group' if conv_type=='group' else 'unknown',
                  sender=sender, sender_id=str(sender_id or ''), timestamp=stamp(ts),
                  content=content, is_self=self_value,
                  source_id=str(original_id) if original_id not in (None, '', 0, '0') else '',media=media,reply_to=reply)
    if files:result['files']=files
    if voice:result['voice']=voice
    return result


def stable_key(message, occurrence=0):
    # Identity remains within one platform and one conversation; never merge contacts.
    identity = [message['platform'], message['conversation_id']]
    if message['source_id']:
        identity += ['id', message['source_id']]
    else:
        identity += ['text', message['sender_id'] or message['sender'], message['timestamp'],
                     message['content'], occurrence]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
