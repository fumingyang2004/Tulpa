"""Loopback OneBot v11 event receiver. No native reader, model or message store.

One connection per service; reconnect and health IO run outside MCP worker slots.
Tokens never enter status/logs. Socket connection alone isn't QQ receive health.
"""
import json
import logging
import os
import threading
import time
from urllib.parse import urlparse

from dotenv import dotenv_values

from . import onebot


def configuration(root=None):
    root = root or onebot.ROOT
    values = dotenv_values(root / '.env')
    def value(key):
        return str(os.environ.get(key, values.get(key) or '')).strip()
    token = value('REPLY_ONEBOT_WS_TOKEN')
    if value('REPLY_ONEBOT_WS_TOKEN_MODE') != 'independent':
        token = token or onebot.configuration(root)['token']
    return dict(url=value('REPLY_ONEBOT_WS_URL'), token=token)


def validate_url(url):
    p = urlparse(url)
    if (p.scheme not in ('ws', 'wss') or p.hostname not in ('127.0.0.1', 'localhost', '::1')
            or p.username or p.password or p.query or p.fragment):
        raise ValueError('持续群聊需要本机 OneBot WebSocket 地址（ws://127.0.0.1:端口）和对应 Token。')
    return url


class EventReceiver:
    def __init__(self, on_event, on_change, *, config_factory=configuration, client_factory=onebot.Client, metadata_only=False):
        self.on_event, self.on_change = on_event, on_change
        self.config_factory, self.client_factory = config_factory, client_factory
        self.metadata_only = metadata_only
        self.lock = threading.RLock()
        self.halt = threading.Event()
        self.thread = None
        self.state = dict(state='stopped', account='', last_event_at=0, last_message_at=0, connected_at=0,
                          note='事件接收尚未启动。')
        self.log = logging.getLogger('tulpa.onebot.events')
        self.http_binding = None  # Private in-memory binding; never in status/logs.

    def matches_client(self, client):
        with self.lock:
            return self.http_binding == (client.url, client.token)

    def status(self):
        with self.lock:
            return dict(self.state)

    def update(self, state, note, **fields):
        with self.lock:
            changed = state != self.state['state']
            self.state.update(state=state, note=note, **fields)
        if changed:
            self.log.info('receive_state=%s', state)
            self.on_change(state)

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.halt.clear()
            self.thread = threading.Thread(target=self.run, name='tulpa-onebot-events', daemon=True)
            self.thread.start()

    def stop(self):
        self.halt.set()
        thread = self.thread
        if thread and thread is not threading.current_thread():
            thread.join(6)

    def run(self):
        from websockets.sync.client import connect
        from websockets.exceptions import InvalidStatus
        delay = 1
        while not self.halt.is_set():
            try:
                cfg = self.config_factory()
                if not cfg['url']:
                    self.update('unconfigured', '请在 OneBot 设置填写实时事件 WebSocket 地址。')
                    self.halt.wait(2)
                    continue
                validate_url(cfg['url'])
                self.update('connecting', '正在连接 SnowLuma 实时事件…', account='')
                # Independent identity check before accepting the WS self_id.
                http_client = self.client_factory(timeout=3)
                account = http_client.login()
                with self.lock:
                    self.http_binding = (http_client.url, http_client.token)
                with connect(cfg['url'], additional_headers={'Authorization':'Bearer '+cfg['token']} if cfg['token'] else {},
                             proxy=None, open_timeout=3, close_timeout=1, max_size=1024*1024,
                             max_queue=16, ping_interval=20, ping_timeout=20) as ws:
                    self.update('verifying', '事件通道已连接，正在核对账号和接收状态。', account=account)
                    verified = False
                    healthy = True
                    last_identity = time.monotonic()
                    last_packet = time.monotonic()
                    while not self.halt.is_set():
                        if self.config_factory() != cfg:
                            raise ValueError('configuration_changed')
                        if time.monotonic()-last_identity > 15:
                            if self.client_factory(timeout=3).login() != account:
                                raise ValueError('account_changed')
                            last_identity = time.monotonic()
                        if time.monotonic()-last_packet > 90:
                            raise ValueError('event_timeout')
                        try:
                            raw = ws.recv(timeout=.5)
                        except TimeoutError:
                            continue
                        try:
                            event = json.loads(raw)
                        except (ValueError, UnicodeError):
                            continue
                        if not isinstance(event, dict) or 'post_type' not in event:
                            continue
                        if self.metadata_only and (event.get('post_type') != 'meta_event' or event.get('meta_event_type') not in ('lifecycle', 'heartbeat')):
                            continue  # Setup discards messages; only lifecycle/heartbeat proves identity.
                        if str(event.get('self_id', '')) != account:
                            self.update('account_mismatch', 'HTTP 与事件通道的 QQ 账号不一致，已停止接收。', account='')
                            raise ValueError('account_mismatch')
                        last_packet = time.monotonic()
                        if self.metadata_only and event.get('meta_event_type') == 'heartbeat':
                            status = event.get('status')
                            if isinstance(status, dict) and (status.get('online') is False or status.get('good') is False):
                                self.update('upstream_offline', 'WS 已连接，但 QQ 心跳报告接收链路不健康。', account=account)
                                continue
                        if not verified:
                            verified = True
                            self.update('connected', 'SnowLuma 实时事件已连接。', account=account, connected_at=time.time())
                            delay = 1
                        status = event.get('status')
                        if event.get('meta_event_type') == 'heartbeat' and isinstance(status, dict):
                            healthy = status.get('online') is not False and status.get('good') is not False
                        if event.get('notice_type') == 'bot_status':
                            healthy = event.get('sub_type') == 'online'
                        if event.get('post_type') in ('message', 'message_sent'):
                            healthy = True
                            with self.lock:
                                self.state['last_message_at'] = time.time()
                        self.update('connected' if healthy else 'upstream_offline',
                                    'SnowLuma 实时事件已连接。' if healthy else 'SnowLuma 已连接，但 QQ 接收链路不健康。',
                                    last_event_at=time.time())
                        if healthy:
                            self.on_event(event)
            except InvalidStatus as exc:
                auth = exc.response.status_code in (401, 403)
                self.update('auth_error' if auth else 'handshake_error',
                            'WS 认证被拒绝，请核对该事件节点自己的 Token。' if auth else
                            'WS 握手失败，请核对正向 WebSocket 节点的端口、path 和启用状态。', account='')
            except Exception:
                # Exception strings can include a handshake URL or Authorization.
                if self.status()['state'] != 'account_mismatch':
                    self.update('disconnected', '实时事件已断开，正在重连；断线期间的消息不保证补齐。', account='')
            if not self.halt.is_set():
                self.halt.wait(delay)
                delay = min(15, delay*2)
        self.update('stopped', '实时事件接收已停止。', account='')


def probe_connection(http, ws, expected_account, timeout=5):
    """Bounded isolated use of the real receiver; no persistence or chat callback."""
    receiver = EventReceiver(lambda event: None, lambda state: None,
        config_factory=lambda: dict(ws), client_factory=lambda **kw: onebot.Client(http, **kw), metadata_only=True)
    receiver.start()
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = receiver.status()
            if status['state'] in ('connected', 'upstream_offline', 'account_mismatch', 'auth_error', 'handshake_error', 'disconnected'):
                if status['state'] == 'connected' and status['account'] != expected_account:
                    return dict(state='account_mismatch', note='WS 与 HTTP 的账号不一致。')
                if status['state'] == 'disconnected':
                    return dict(state='disconnected', note='WS 未取得可核对的事件连接，请检查节点地址及运行状态。')
                return dict(state=status['state'], note=status['note'])
            time.sleep(.04)
        return dict(state='unverified', note='WS 未在检测时间内返回可核对账号的生命周期或心跳事件；尚未确认连接可用，请稍后重新检测。')
    finally:
        receiver.stop()
