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
    def __init__(self, message, code='http_query_failed', *, retcode=None, http_status=None):
        super().__init__(message)
        self.code = code
        self.retcode, self.http_status = retcode, http_status


class OneBotUncertain(OneBotError):
    pass


READS = frozenset(('get_login_info', 'get_version_info', 'get_group_info', 'get_group_list',
    'get_group_member_info', 'get_group_member_list', 'get_group_system_msg',
    '_get_group_notice', 'get_essence_msg_list', 'get_group_file_system_info',
    'get_group_root_files', 'get_group_files_by_folder', 'get_group_file_url', 'get_image', 'fetch_custom_face_detail'))
WRITES = frozenset(('set_group_ban', 'set_group_kick', 'set_group_name', 'set_group_add_request', 'add_custom_face', 'set_msg_emoji_like'))


def configuration(root=None):
    from .snowluma_managed import connection
    managed = connection(root or ROOT)
    if managed is not None:
        return managed
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
        self.managed_config = config
        self.url = config.get('url', '').rstrip('/')
        self.token = config.get('token', '')
        parsed = urlparse(self.url)
        if (not self.url or parsed.scheme not in ('http', 'https') or
                parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or
                parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise OneBotError('请先配置有效的本机 OneBot HTTP 地址和 Token。')
        self.transport, self.timeout = transport, timeout

    def call(self, action, payload, *, approved=False):
        from .snowluma_managed import guard
        try:
            guard(self.managed_config)
        except ValueError:
            raise OneBotError('QQ 托管授权、进程或账号绑定已失效，未执行操作。', 'managed_not_ready') from None
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
                if action=='set_msg_emoji_like':
                    code=result.get('retcode') if isinstance(result,dict) else None
                    raise OneBotUncertain('回应回执格式不明确，结果未知；不得自动重试。','protocol_error',retcode=code if type(code) is int else None)
                raise ValueError('invalid receipt')
            if result['status'] != 'ok' or result['retcode'] != 0:
                if action == 'set_msg_emoji_like':
                    # Classify known adapter errors, never expose raw server text:
                    # wording may contain credentials or unrelated message content.
                    detail = ' '.join(str(result.get(k, '')) for k in ('message','wording','error')).lower()
                    reasons = [('sequence', 'target_sequence_missing', '原消息没有可信 QQ sequence，未执行回应。'),
                        ('message not found', 'target_missing', 'OneBot 缓存已找不到原消息，未执行回应。'),
                        ('permission', 'permission_denied', 'QQ 拒绝了此账号的回应权限。'),
                        ('unsupported', 'emoji_unsupported', 'QQ 或当前接入服务不支持该表情回应。'),
                        ('not supported', 'emoji_unsupported', 'QQ 或当前接入服务不支持该表情回应。'),
                        ('rate', 'remote_rate_limited', 'QQ 接口限制了操作频率，请稍后核对。')]
                    code, message = next(((c,m) for needle,c,m in reasons if needle in detail),
                                         ('remote_rejected','QQ 接口拒绝回应，请核对目标、表情与账号权限。'))
                    raise OneBotError(message, code, retcode=result['retcode'])
                raise OneBotError('OneBot 拒绝了操作；请检查账号权限、目标和客户端状态。')
            return result.get('data')
        except OneBotError:
            raise
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise OneBotError('未连接到本机 OneBot，请核对节点地址、启用状态和接入程序是否运行。', 'http_unreachable') from None
        except httpx.HTTPStatusError as exc:
            if action == 'set_msg_emoji_like' and exc.response.status_code in (401,403,404,429):
                code, message = {401:('http_auth_error','HTTP 认证被拒绝，请检查节点 Token。'),
                    403:('permission_denied','HTTP 接口拒绝此操作。'),
                    404:('action_unavailable','当前节点没有消息回应接口。'),
                    429:('remote_rate_limited','HTTP 接口限流，未执行回应。')}[exc.response.status_code]
                raise OneBotError(message,code,http_status=exc.response.status_code) from None
            if writing:
                raise OneBotUncertain('未取得可靠回执，操作结果未知。请在 QQ 核对，不会自动重试。', 'http_response_unknown', http_status=exc.response.status_code) from None
            if exc.response.status_code in (401, 403):
                raise OneBotError('HTTP 认证被拒绝，请核对所选 HTTP 节点自己的 Token。', 'http_auth_error') from None
            if exc.response.status_code == 404:
                raise OneBotError('HTTP 接口路径不存在，请核对节点的端口和 path。', 'http_path_error') from None
            raise OneBotError('HTTP 服务返回错误，未取得 OneBot 成功回执。', 'http_response_error') from None
        except httpx.TimeoutException:
            if writing:
                raise OneBotUncertain('未取得可靠回执，操作结果未知。请在 QQ 核对，不会自动重试。','http_timeout') from None
            raise OneBotError('HTTP 检测超时，请检查该节点和 QQ 连接状态。', 'http_timeout') from None
        except (httpx.HTTPError, ValueError, TypeError):
            if writing:
                raise OneBotUncertain('未取得可靠回执，操作结果未知。请在 QQ 核对，不会自动重试。') from None
            raise OneBotError('OneBot 查询失败、超时或响应不兼容。') from None

    def login(self):
        data = self.call('get_login_info', {})
        if not isinstance(data, dict) or not str(data.get('user_id', '')).isdigit():
            raise OneBotError('OneBot 尚未返回有效的已登录 QQ 账号。', 'http_identity_missing')
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
