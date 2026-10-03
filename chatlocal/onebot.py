"""Local OneBot transport. Credentials and mutation authority never enter tools."""
import json
import os
import threading
import time
from urllib.parse import urlparse

import httpx
from dotenv import dotenv_values
from .config import ROOT


class OneBotError(ValueError):
    pass


class OneBotUncertain(OneBotError):
    pass


READS = frozenset(('get_login_info', 'get_version_info', 'get_group_info',
    'get_group_member_info', 'get_group_member_list', 'get_group_system_msg',
    '_get_group_notice', 'get_essence_msg_list', 'get_group_file_system_info',
    'get_group_root_files', 'get_group_files_by_folder', 'get_group_file_url'))
WRITES = frozenset(('set_group_ban', 'set_group_kick', 'set_group_name', 'set_group_add_request'))


def configuration(root=None):
    values = dotenv_values((root or ROOT) / '.env')
    def value(key):
        return str(os.environ.get(key, values.get(key) or '')).strip()
    # The product's OneBot settings are shared. Preserve explicitly configured
    # legacy read credentials as a fallback, never mix two endpoints' tokens.
    prefix = 'REPLY_ONEBOT' if value('REPLY_ONEBOT_URL') else 'ARTIFACT_ONEBOT'
    return dict(url=value(prefix + '_URL'), token=value(prefix + '_TOKEN'))


class Client:
    def __init__(self, config=None, transport=None, timeout=12):
        config = configuration() if config is None else config
        self.url = config.get('url', '').rstrip('/')
        self.token = config.get('token', '')
        parsed = urlparse(self.url)
        if (not self.url or parsed.scheme not in ('http', 'https') or
                parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or
                parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise OneBotError('请先配置有效的本机 OneBot HTTP 地址和 Token。')
        self.transport, self.timeout = transport, timeout

    def call(self, action, payload, *, approved=False):
        if action not in (READS | (WRITES if approved else frozenset())):
            raise OneBotError('该接口不在允许范围内，管理操作需要审批。')
        writing = action in WRITES
        try:
            with httpx.Client(timeout=httpx.Timeout(self.timeout, connect=3),
                              transport=self.transport, trust_env=False, follow_redirects=False) as client:
                with client.stream('POST', self.url + '/' + action, json=payload,
                                   headers={'Authorization': 'Bearer ' + self.token} if self.token else {}) as response:
                    response.raise_for_status()
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 8 * 1024 * 1024:
                            raise ValueError('oversize')
            result = json.loads(raw)
            if not isinstance(result, dict) or result.get('status') not in ('ok', 'failed') or type(result.get('retcode')) is not int:
                raise ValueError('invalid receipt')
            if result['status'] != 'ok' or result['retcode'] != 0:
                raise OneBotError('OneBot 拒绝了操作；请检查账号权限、目标和客户端状态。')
            return result.get('data')
        except OneBotError:
            raise
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise OneBotError('未连接到本机 OneBot，请保持 QQ 和接入程序运行。') from None
        except (httpx.HTTPError, ValueError, TypeError):
            if writing:
                raise OneBotUncertain('未取得可靠回执，操作结果未知。请在 QQ 核对，不会自动重试。') from None
            raise OneBotError('OneBot 查询失败、超时或响应不兼容。') from None

    def login(self):
        data = self.call('get_login_info', {})
        if not isinstance(data, dict) or not str(data.get('user_id', '')).isdigit():
            raise OneBotError('OneBot 尚未返回有效的已登录 QQ 账号。')
        return str(data['user_id'])


_probe_lock = threading.Lock()
_probe_cache = {}


def invalidate_availability():
    with _probe_lock:
        _probe_cache.clear()


def available_client():
    """Short, credential-sensitive cache; actions always recheck identity."""
    cfg = configuration()
    if not cfg['url']:
        return None
    key = (cfg['url'], cfg['token'])
    with _probe_lock:
        cached = _probe_cache.get(key)
        if cached and time.monotonic() - cached[0] < 10:
            return Client(cfg) if cached[1] else None
    try:
        client = Client(cfg, timeout=3)
        client.login()
        ok = True
    except OneBotError:
        ok = False
    with _probe_lock:
        _probe_cache.clear()
        _probe_cache[key] = (time.monotonic(), ok)
    return Client(cfg) if ok else None
