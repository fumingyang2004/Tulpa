"""Read QQ history from this installation's owned SnowLuma Docker instance.

This is a distinct source, not a mounted/native database. It uses only read
APIs, pins the account and instance, and never starts/stops QQ or extracts keys.
"""
import json
import os
from pathlib import Path
import re
import sys
import time

from .config import ROOT, DATA
from .native_sources import source_settings
from .snowluma_secure import read_json, SecretVault

METHOD = 'snowluma-history-v1'
SOURCE_LABEL = 'SL Docker 历史消息'
HISTORY_NOTE = '读取 SL / QQ 当前可返回的历史；不保证包含所有云端或已删除记录。'
READ_ACTIONS = frozenset({'get_login_info', 'get_group_list', 'get_friend_list',
                         'get_group_msg_history', 'get_friend_msg_history', 'get_msg', 'get_image'})
# SL exposes these fixed file-transfer devices in get_friend_list. They do not
# have ordinary friend message histories (runtime DATALINE_DEVICES).
DATALINE_DEVICES = frozenset({'2113929217', '2113929218', '2113929219'})


def state(root=ROOT):
    return read_json(Path(root)/'data/snowluma-managed/state.json')


def selected(root=ROOT):
    """Explicit native roots win. Auto uses Docker only without a host QQ DB."""
    cfg = source_settings(root)['platforms']['qq']
    source = cfg.get('source', 'auto')
    if source == 'snowluma':
        return True
    if source == 'database' or cfg['data_root'] or os.environ.get('CHATLOG_QQ_DATA_ROOT'):
        return False
    if (Path(root)/'data/qq-source-root.json').exists() or not sys.platform.startswith('linux'):
        return False
    if state(root).get('backend') != 'docker':
        return False
    home = Path.home()
    roots = [home/'.config/QQ']
    for app in ('com.qq.QQ', 'com.qq.QQlinux'):
        roots += [home/'.var/app'/app/'config/QQ', home/'.var/app'/app/'.config/QQ']
    for folder in roots:
        try:
            for index, child in enumerate(folder.iterdir()):
                if index >= 200:
                    break
                if any((child/rel).is_file() for rel in ('nt_db/nt_msg.db', 'nt_qq/nt_db/nt_msg.db')):
                    return False
        except OSError:
            continue
    return True


def source_info(root=ROOT):
    if not selected(root):
        return dict(source_type='database', source_label='本机数据库', requires_key=True)
    value = state(root)
    ready = bool(value.get('enabled') and value.get('phase') == 'ready')
    account = str(value.get('account') or '')
    if not re.fullmatch(r'[1-9][0-9]{4,19}', account):
        account = ''
    detail = HISTORY_NOTE if ready else 'SL 尚未就绪，请在「SL 托管 → 扫码登录」登录原账号后读取历史。'
    return dict(source_type='snowluma', source_label=SOURCE_LABEL, requires_key=False,
                source_ready=ready, source_account=account, detail=detail)


def accounts(root=ROOT):
    info = source_info(root)
    return [info['source_account']] if info.get('source_account') else []


class HistoryClient:
    """Independent reader processes can read a verified binding, never manage it."""
    def __init__(self, root=ROOT, account=None):
        from .onebot import Client
        from .snowluma_docker import DockerRuntime, docker_manifest
        from .snowluma_managed import load_manifest, consent_key
        self.root = Path(root)
        value = state(self.root)
        self.binding = self.identity(value)
        self.account = str(value.get('account') or '')
        self.assert_current()
        if account is not None and str(account) != self.account:
            raise ValueError('所选 QQ 账号与 SL 绑定账号不同，未读取其他账号。')
        manifest = docker_manifest(load_manifest())
        consent = value.get('consent') or {}
        if (consent.get('fingerprint') != consent_key(manifest) or consent.get('scope') != manifest['scope']
                or consent.get('source') != value.get('installation', {}).get('fingerprint')):
            raise ValueError('SL 托管授权已变化，请先在 SL 托管中恢复连接。')
        DockerRuntime(self.root).validate(value['owner'], value['installation'], value['instance'], value['ports'], running=True)
        credentials = SecretVault(self.root/'data/snowluma-managed').read()
        self.client = Client(dict(url=f'http://127.0.0.1:{int(value["ports"]["http"])}',
                                  token=credentials['http_token']), timeout=20)
        self.verify_account()

    @staticmethod
    def identity(value):
        return (value.get('instance'), value.get('generation'), value.get('account'),
                value.get('owner', {}).get('id'))

    def assert_current(self):
        value = state(self.root)
        if value.get('backend') != 'docker' or not value.get('enabled') or value.get('phase') != 'ready':
            raise ValueError('SL QQ 尚未登录或托管已停止，请在「SL 托管 → 扫码登录」恢复后重试。')
        if self.identity(value) != self.binding:
            raise ValueError('SL 账号或连接已变化，本次读取已停止，未推进同步进度。')
        if time.time()-value.get('verified_at', 0) > 30:
            raise ValueError('SL 连接验证已过期，请等待托管恢复就绪后重试。')

    def call(self, action, params):
        if action not in READ_ACTIONS:
            raise ValueError('历史读取器只允许读取 QQ 数据。')
        self.assert_current()
        result = self.client.call(action, params)
        self.assert_current()
        return result

    def verify_account(self):
        info = self.call('get_login_info', {})
        if not isinstance(info, dict) or str(info.get('user_id')) != self.account:
            raise ValueError('SL 返回的 QQ 账号与绑定不符，未读取其他账号。')


def catalog(client):
    rows = {}
    for action, kind, key in [('get_group_list', 'group', 'group_id'), ('get_friend_list', 'direct', 'user_id')]:
        values = client.call(action, {})
        if not isinstance(values, list) or len(values) > 10000:
            raise ValueError('SL 会话目录格式无效或超过读取上限。')
        for value in values:
            peer = str(value.get(key, '')) if isinstance(value, dict) else ''
            if not re.fullmatch(r'[1-9][0-9]{0,19}', peer):
                raise ValueError('SL 会话目录包含无效 QQ 标识。')
            if kind == 'direct' and peer in DATALINE_DEVICES:
                continue
            name = value.get('group_name') if kind == 'group' else value.get('remark') or value.get('nickname')
            cid = f'{client.account}:{kind}:{peer}'
            rows[cid] = dict(platform='qq', conversation_id=cid, conversation=str(name or peer)[:300],
                             conversation_type=kind, count=None, source_type='snowluma')
    client.verify_account()
    return list(rows.values())


def message_identity(row):
    value = row.get('message_id')
    if type(value) is not int or value == 0 or abs(value) > 2**53-1:
        raise ValueError('SL 历史消息缺少可靠消息编号，未推进进度。')
    return value


def message_time(row):
    value = row.get('time')
    if type(value) not in (int, float) or not 946684800 <= value <= 4102444800:
        raise ValueError('SL 历史消息时间无效，未推进进度。')
    return int(value)


def check_message(row, conversation, account):
    if not isinstance(row, dict):
        raise ValueError('SL 历史消息格式无效。')
    message_identity(row); message_time(row)
    if not isinstance(row.get('sender') or {}, dict):
        raise ValueError('SL 消息发送者格式无效，未导入。')
    owner, kind, peer = conversation['conversation_id'].split(':')
    if owner != account or row.get('self_id') not in (None, int(account), account):
        raise ValueError('SL 消息账号不匹配，未导入。')
    if kind == 'group':
        valid = row.get('message_type') == 'group' and str(row.get('group_id')) == peer
    else:
        # Friend history can contain incoming and outgoing messages. A self
        # message must identify its recipient; a sender ID alone is insufficient.
        sender = str(row.get('user_id') or (row.get('sender') or {}).get('user_id') or '')
        valid = row.get('message_type') == 'private' and row.get('sub_type', 'friend') == 'friend'
        valid = valid and (sender == peer or sender == account and str(row.get('target_id')) == peer)
    if not valid:
        raise ValueError('SL 返回了其他会话或临时会话的数据，未导入。')


def image_media(data, client=None, *, load_stickers=True):
    from .media import unavailable, omitted_sticker, cache_image
    from .mcp_chat_media import download, image_ref
    kind = 'sticker' if str(data.get('sub_type', 0)) == '1' else 'image'
    filename = str(data.get('file', ''))
    metadata = dict(recovered_by='snowluma')
    if re.fullmatch(r'[a-zA-Z0-9_.-]{1,160}', filename):
        metadata['filename'] = filename
    if kind == 'sticker' and not load_stickers:
        return omitted_sticker(kind, **metadata)
    reference = image_ref(data)
    if client and reference.get('url'):
        try:
            class Cancellation:
                def is_set(self):
                    client.assert_current()
                    return False
            raw = download(reference['url'], Cancellation())
            return cache_image(raw, kind, load_stickers=load_stickers, **metadata)
        except (OSError, ValueError):
            client.assert_current()
    return unavailable(kind, reason='SL 图片暂不可用，可在登录后补载媒体。', **metadata)


def convert_message(row, conversation, account, *, client=None, load_stickers=False):
    check_message(row, conversation, account)
    sender = row.get('sender') or {}
    uid = str(sender.get('user_id') or row.get('user_id') or '')
    if not uid.isdigit():
        raise ValueError('SL 消息缺少发送者标识，未推进进度。')
    content, media, reply = [], [], None
    segments = row.get('message')
    if isinstance(segments, str):
        content.append(segments)
    elif isinstance(segments, list):
        for segment in segments:
            if not isinstance(segment, dict) or not isinstance(segment.get('data') or {}, dict):
                raise ValueError('SL 消息片段格式无效，未推进进度。')
            data = segment.get('data') or {}; kind = segment.get('type')
            if not isinstance(kind, str):
                raise ValueError('SL 消息片段类型无效，未推进进度。')
            if kind == 'text': content.append(str(data.get('text', '')))
            elif kind == 'at': content.append('@'+str(data.get('qq', '')))
            elif kind == 'reply':
                mid = str(data.get('id', ''))
                if re.fullmatch(r'-?[0-9]{1,20}', mid): reply = dict(source_id='sl:'+mid)
            elif kind == 'image':
                if len(media) >= 16: raise ValueError('单条 SL 消息图片超过导入上限。')
                media.append(image_media(data, client, load_stickers=load_stickers))
            elif kind == 'face': content.append('[QQ表情 '+str(data.get('id', ''))+']')
            elif kind == 'file': content.append('[文件] '+str(data.get('name') or data.get('file_name') or ''))
            else: content.append('['+{'record':'语音', 'video':'视频', 'forward':'合并转发', 'json':'卡片', 'xml':'卡片'}.get(kind, str(kind))+']')
    else:
        raise ValueError('SL 消息内容格式无效，未推进进度。')
    return dict(platform='qq', conversation=conversation['conversation'], conversation_id=conversation['conversation_id'],
                conversation_type=conversation['conversation_type'], sender=str(sender.get('card') or sender.get('nickname') or uid),
                sender_id=uid, timestamp=message_time(row), content=''.join(content), is_self=uid == account,
                source_id='sl:'+str(message_identity(row)), media=media, reply_to=reply)


def history(client, conversation, *, start=None, end=None, limit=None, observe=lambda: None, max_pages=10000):
    """Newest first, bounded pages; a repeated anchor never advances a cursor."""
    _, kind, peer = conversation['conversation_id'].split(':')
    api = 'get_group_msg_history' if kind == 'group' else 'get_friend_msg_history'
    anchor = 0; seen = set(); yielded = 0
    for _ in range(max_pages):
        observe()
        result = client.call(api, {'group_id' if kind == 'group' else 'user_id': int(peer),
                                   'message_id': anchor, 'count': 100, 'reverse_order': True})
        if not isinstance(result, dict) or not isinstance(result.get('messages'), list):
            raise ValueError('SL 历史接口返回无效，未推进同步进度。')
        page = result['messages']
        if len(page) > 1000: raise ValueError('SL 单页历史超出读取上限。')
        for row in page: check_message(row, conversation, client.account)
        if not page: return
        page.sort(key=lambda m: (message_time(m), int(m.get('message_seq') or 0)), reverse=True)
        fresh = [row for row in page if message_identity(row) not in seen]
        if not fresh:
            if all(message_identity(row) == anchor for row in page): return
            raise ValueError('SL 历史分页重复，已停止读取；已导入批次保留，未推进同步进度。')
        for row in fresh:
            mid = message_identity(row); seen.add(mid); ts = message_time(row)
            if start is not None and ts < start: continue
            if end is not None and ts >= end: continue
            yield row
            yielded += 1
            if limit is not None and yielded >= limit: return
        if start is not None and min(message_time(row) for row in page) < start: return
        next_anchor = message_identity(page[-1])
        if next_anchor == anchor:
            raise ValueError('SL 历史分页未向前移动，未推进同步进度。')
        anchor = next_anchor
    raise ValueError('SL 历史超过本次分页上限，请缩小日期或会话范围后继续。')


def export_history(args, *, client=None, data_dir=DATA):
    from .reader_batches import BatchWriter, atomic_json, stage
    from .import_scope import bounds
    from .config import local_path
    client = client or HistoryClient(account=args.account)
    directory = catalog(client); by_id = {row['conversation_id']:row for row in directory}
    output = local_path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    if args.list:
        atomic_json(output, dict(reader=METHOD, conversations=directory, account=client.account, note=HISTORY_NOTE))
        return dict(conversations=len(directory))
    request = json.loads(local_path(args.incremental_request).read_text()) if args.incremental_request else None
    previous = (request or {}).get('checkpoint') or {}
    if previous and (previous.get('account') != client.account or previous.get('method') != METHOD):
        raise ValueError('同步来源已变化，请先按范围补读 SL 历史并重新接续。')
    wanted = request.get('conversations') if request else args.chat
    if wanted is not None and (not wanted or any(cid not in by_id for cid in wanted)):
        raise ValueError('所选会话不在当前 SL 账号的群或好友目录中，未扩大读取范围。')
    chosen = [by_id[cid] for cid in dict.fromkeys(wanted)] if wanted is not None else directory
    lo, hi = bounds(args.start, args.end)
    lo = lo//1000 if lo is not None else None; hi = hi//1000 if hi is not None else None
    if lo is None and not args.date_range: lo = int(time.time())-args.days*86400
    now = time.time(); cursors = dict(previous.get('cursors') or {})
    rows = BatchWriter(args.batch_dir, dict(reader=METHOD)) if args.batch_dir else []
    stage('reading')
    for conversation in chosen:
        cid = conversation['conversation_id']; since = lo
        if request:
            prior = cursors.get(cid) or {}
            # bootstrap_time already includes its five-minute overlap.
            since = int(prior['at'])-300 if prior.get('at') else int(request.get('bootstrap_since') or 0)
            if since <= 0: raise ValueError('尚无 SL 历史基线，请先按范围导入一次。')
        limit = None if request or args.all_messages else args.per_chat
        for raw in history(client, conversation, start=since, end=None if request else hi, limit=limit,
                           observe=lambda: rows.observe(0) if isinstance(rows, BatchWriter) else None):
            message = convert_message(raw, conversation, client.account, client=client, load_stickers=not args.no_stickers)
            rows.append(message)
            if not isinstance(rows, BatchWriter) and len(rows) > 50000:
                raise ValueError('SL 单文件导出超过 50000 条，请使用分批导入。')
        # Advance only in the sealed receipt; overlap captures same-second and
        # late arrivals. A bounded historical backfill is not a sync watermark.
        if request: cursors[cid] = dict(at=int(now))
    client.verify_account()
    meta = dict(source_type='snowluma', account=client.account, snapshot_at=now)
    labels = [dict(conversation_id=row['conversation_id'],conversation=row['conversation']) for row in chosen]
    header = dict(reader=METHOD, snapshot=meta, read_scope=dict(conversations=wanted, start=args.start, end=args.end),
                  per_chat=args.per_chat, load_stickers=not args.no_stickers, label_account=client.account,
                  conversation_labels=labels, source_note=HISTORY_NOTE,
                  sync_checkpoint=dict(method=METHOD, account=client.account, cursors=cursors or {'_empty':dict(at=int(now))}) if request else None)
    payload = rows.finish(header) if isinstance(rows, BatchWriter) else dict(header, messages=rows)
    atomic_json(output, payload)
    # Bind source only after a complete export. A failed scan cannot replace it.
    data_dir = Path(data_dir); data_dir.mkdir(parents=True, exist_ok=True)
    atomic_json(data_dir/'qq-snapshot-info.json', meta)
    return dict(messages=len(rows), source_type='snowluma', note=HISTORY_NOTE)


def refresh_media_messages(payload, *, client=None):
    """Revisit only exact imported SL message IDs; never fetch extra history."""
    from .media import media_path, is_sticker
    client = client or HistoryClient()
    stats = dict(recovered=0, unavailable=0, omitted=0, skipped_sticker_messages=0, metadata_missing_messages=0)
    changed = []
    for row in payload['messages']:
        if not row['conversation_id'].startswith(client.account+':'):
            raise ValueError('媒体消息不属于当前 SL 绑定账号。')
        if not str(row.get('source_id', '')).startswith('sl:'):
            stats['metadata_missing_messages'] += 1
            continue
        current = None; items = []; updated = False
        for index, old in enumerate(row['media']):
            if not payload['load_stickers'] and is_sticker(old):
                items.append(old); stats['omitted'] += 1; continue
            if media_path(old): items.append(old); continue
            if current is None:
                try:
                    mid = int(row['source_id'][3:])
                    current = client.call('get_msg', dict(message_id=mid))
                    check_message(current, row, client.account)
                    if message_identity(current) != mid or message_time(current)*1000 != row['timestamp']:
                        raise ValueError('SL 媒体原消息身份已变化。')
                    current = [part.get('data') or {} for part in current.get('message', []) if part.get('type') == 'image']
                except (ValueError, TypeError):
                    client.assert_current(); current = []
            new = image_media(current[index], client, load_stickers=payload['load_stickers']) if index < len(current) else old
            if new.get('status') == 'available': stats['recovered'] += 1; updated = True
            else: stats['unavailable'] += 1
            items.append(new)
        if updated:
            changed.append(dict({k:v for k,v in row.items() if not k.startswith('_')}, media=items))
    return dict(messages=changed, stats=stats)
