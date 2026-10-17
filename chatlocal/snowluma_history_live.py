"""Bounded, acknowledged QQ archival through the existing OneBot receiver."""
from collections import deque
import json
import threading
import time
import uuid

from .snowluma_history import HistoryClient, catalog, convert_message


class SnowLumaLiveWorker:
    def __init__(self, *, client=None, receiver_factory=None):
        from .onebot_events import EventReceiver
        self.client = client or HistoryClient()
        self.directory = {row['conversation_id']:row for row in catalog(self.client)}
        self.token = uuid.uuid4().hex
        self.lock = threading.RLock()
        self.pending = deque()
        self.sequence = self.bytes = 0
        self.overflow = False
        self.gap = True  # No claim of complete recovery across a worker restart.
        self.last_stage = 'messages'
        self.receiver = (receiver_factory or EventReceiver)(self.receive, self.changed)
        self.receiver.start()

    def changed(self, state):
        if state != 'connected': self.gap = True

    def receive(self, event):
        if event.get('post_type') not in ('message', 'message_sent'):
            return
        account = self.client.account
        if str(event.get('self_id')) != account:
            return
        kind = event.get('message_type')
        if kind == 'group': peer = str(event.get('group_id', '')); kind = 'group'
        elif kind == 'private' and event.get('sub_type') == 'friend':
            sender = str(event.get('user_id', ''))
            peer = str(event.get('target_id', '')) if sender == account else sender
            kind = 'direct'
        else: return
        cid = f'{account}:{kind}:{peer}'
        conversation = self.directory.get(cid)
        if not conversation:
            # A newly joined group can legitimately precede the next catalog
            # refresh. Direct sessions must be present in the verified friends.
            if kind != 'group' or not peer.isdigit(): return
            conversation = dict(conversation_id=cid, conversation_type=kind,
                                conversation=str(event.get('group_name') or peer))
        size = len(json.dumps(event, ensure_ascii=False).encode())
        with self.lock:
            if len(self.pending) >= 5000 or self.bytes+size > 20*1024*1024:
                self.overflow = True
                return
            self.sequence += 1
            self.pending.append((self.sequence, event, conversation, size))
            self.bytes += size

    def read(self, request):
        self.client.assert_current()
        old = request.get('checkpoint') or {}
        if old and old.get('account') != self.client.account:
            raise ValueError('account_changed')
        with self.lock:
            if self.overflow: raise ValueError('snowluma_live_overflow')
            acknowledged = old.get('cursor', 0) if old.get('worker') == self.token else 0
            while self.pending and self.pending[0][0] <= acknowledged:
                self.bytes -= self.pending.popleft()[3]
            batch = list(self.pending)[:500]
        # Keep pending events until the next read carries the committed cursor.
        # Import failures therefore replay exactly the same stable message IDs.
        wanted = request.get('conversations')
        messages = [convert_message(row, conv, self.client.account, client=self.client,
                                    load_stickers=request.get('load_stickers', False))
                    for _, row, conv, _ in batch if wanted is None or conv['conversation_id'] in wanted]
        self.client.assert_current()
        cursor = batch[-1][0] if batch else acknowledged
        return dict(ok=True, payload=dict(reader='snowluma-live', messages=messages,
                    more=len(batch) == 500, source_gap=self.gap),
                    checkpoint=dict(method='snowluma-live-v1', account=self.client.account,
                                    worker=self.token, cursor=cursor, at=time.time()),
                    metrics=dict(candidates=len(batch), source='snowluma'), observed_at=time.time(),
                    source='qq_onebot', verification_state='onebot_received')

    def close(self):
        self.receiver.stop()
