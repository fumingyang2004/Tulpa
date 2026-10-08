"""Read existing SnowLuma configuration; never start or modify that application.

Schema checked against the 1.14.19/1.14.20 runtime's onebot/src/config.ts:
snapshot ignores global defaults; overlays merge complete adapters by name.
Private tokens stay in this process. Public previews contain only node metadata.
"""
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path


class SetupError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def fail(code, message):
    raise SetupError(code, message)


def read_json(path):
    try:
        with path.open('rb') as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            fail('unsupported_format', '配置文件过大，未读取。请使用高级设置手动连接。')
        value = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(value, dict):
            fail('unsupported_format', '配置格式不受支持，应为 JSON 对象。')
        return value, hashlib.sha256(raw).hexdigest()
    except FileNotFoundError:
        fail('file_missing', '配置文件不存在，请重新选择 SnowLuma 文件夹并检测。')
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, SetupError):
            raise
        fail('invalid_json', '配置 JSON 损坏或编码无效。原文件未修改，可在 SnowLuma 修复或使用手动连接。')
    except OSError:
        fail('file_unreadable', '无法读取配置文件，请检查目录权限。')


def node_from(raw, kind, name):
    """Canonicalize only supported local server fields, without guessing a port."""
    result = dict(id=kind + ':' + hashlib.sha256(name.encode()).hexdigest()[:24], name=name, enabled=raw.get('enabled', True),
                  usable=False, url='', reason='', token_present=False)
    token = raw.get('accessToken', '')
    try:
        if type(result['enabled']) is not bool or not isinstance(token, str) or len(token) > 4096:
            raise ValueError()
        if token != token.strip() or any(ord(c) < 32 for c in token):
            raise ValueError()
        result['token_present'] = bool(token)
        host, port, path = raw.get('host', '0.0.0.0'), raw.get('port'), raw.get('path', '/') or '/'
        if not isinstance(host, str) or type(port) is not int or not 1 <= port <= 65535 or not isinstance(path, str):
            raise ValueError()
        host = host.strip().lower()
        # A wildcard bind is reachable through loopback; never use a LAN address.
        host = {'0.0.0.0': '127.0.0.1', '::': '::1', '[::]': '::1', '[::1]': '::1'}.get(host, host)
        if host not in ('127.0.0.1', 'localhost', '::1'):
            result['reason'] = 'Tulpa 仅支持本机接口，请在 SnowLuma 检查节点绑定地址。'
            return result, token
        if (not path.startswith('/') or path.startswith('//') or any(c in path for c in '?#\\')
                or any(ord(c) <= 32 for c in path) or any(p in ('.', '..') for p in path.split('/'))):
            raise ValueError()
        scheme = 'http' if kind == 'http' else 'ws'
        result['url'] = f'{scheme}://{"[::1]" if host == "::1" else host}:{port}{path}'
        if token and (token in name or token in path):
            result['name'] = '节点名称已隐藏'
            raise ValueError()
        if not result['enabled']:
            result['reason'] = '此节点已禁用，请在 SnowLuma 中自行启用后重新检测。'
        elif kind == 'ws' and str(raw.get('role', 'Universal')).lower() not in ('event', 'universal'):
            result['reason'] = '此 WS 节点只提供 API，不推送事件；请选择 Event 或 Universal 节点。'
        else:
            result['usable'] = True
    except (ValueError, TypeError):
        result.update(url='', reason='节点字段格式不受支持，请检查 host、port、path 和 Token，或手动配置。')
    return result, token


def adapters(sources):
    nodes, private = {'http': [], 'ws': []}, {}
    names = set()
    for field, kind in (('httpServers', 'http'), ('wsServers', 'ws')):
        merged, counter = {}, 0
        for source in sources:
            if 'mode' in source and source['mode'] not in ('snapshot', 'overlay'):
                fail('unsupported_format', '不支持这个账号配置的 mode，请使用手动连接。')
            if 'networks' in source and not isinstance(source['networks'], dict):
                fail('unsupported_format', 'networks 字段格式不受支持。')
            for group in (source.get('networks', {}), source):
                items = group.get(field, [])
                if not isinstance(items, list) or len(items) > 100:
                    fail('unsupported_format', '节点列表格式不受支持或节点过多。')
                for raw in items:
                    if not isinstance(raw, dict):
                        fail('unsupported_format', '节点应为 JSON 对象，请检查 SnowLuma 配置。')
                    name = raw.get('name', '')
                    if not isinstance(name, str) or len(name) > 160 or any(ord(c) < 32 for c in name):
                        fail('unsupported_format', '节点名称格式不受支持。')
                    name = name.strip()
                    if not name:
                        counter += 1
                        while f'{kind}-{counter}' in merged:
                            counter += 1
                        name = f'{kind}-{counter}'
                    merged[name] = raw
        for name, raw in merged.items():
            if name in names:
                fail('ambiguous_nodes', 'HTTP 与 WS 节点名称重复，请先在 SnowLuma 核对，不会盲试节点。')
            names.add(name)
            public, token = node_from(raw, kind, name)
            nodes[kind].append(public)
            private[public['id']] = dict(url=public['url'], token=token)
            if kind == 'http' and raw.get('enableWebSocket') is True:
                # SnowLuma's HTTP server can explicitly expose an Event/Universal
                # websocket on the same host/port/path. This is not a port guess.
                shared, token = node_from(raw, 'ws', name + ' · 同端口事件')
                shared['id'] = 'http-ws:' + hashlib.sha256(name.encode()).hexdigest()[:24]
                nodes['ws'].append(shared)
                private[shared['id']] = dict(url=shared['url'], token=token)
    return nodes, private


def inspect_folder(folder):
    if not isinstance(folder, str) or not folder.strip() or len(folder) > 2048 or any(ord(c) < 32 for c in folder):
        fail('folder_required', '请选择已解压的 SnowLuma 文件夹，也可粘贴完整路径。')
    base = Path(folder.strip()).expanduser()
    if not base.is_absolute() or str(base).startswith(('\\\\', '//')):
        fail('folder_required', '请选择本机绝对路径，不读取网络共享目录。')
    try:
        base = base.resolve(strict=True)
        if str(base).startswith(('\\\\', '//')):
            fail('folder_required', '请选择本机目录，不读取指向网络共享的链接。')
        if not base.is_dir():
            fail('folder_missing', '所选位置不是文件夹，请先完整解压 SnowLuma。')
    except (OSError,RuntimeError):
        fail('folder_missing', '所选文件夹不存在或无法访问。')
    package, package_hash = read_json(base / 'package.json')
    if package.get('name') != '@snowluma/runtime' or not (base / 'index.mjs').is_file():
        fail('not_snowluma', '这不是已知的 SnowLuma 运行目录，请选择包含 launcher.bat、index.mjs 和 config 的文件夹。')
    # Fail closed on unverified future formats, while keeping manual entry.
    version = package.get('version')
    if version not in ('1.14.19', '1.14.20'):
        fail('unsupported_version', '此 SnowLuma 版本的配置格式尚未核对，请先使用高级设置手动连接。')
    directory = base / 'config'
    try:
        with os.scandir(directory) as entries:
            paths = []
            for i, entry in enumerate(entries):
                if i >= 512:
                    fail('too_many_files', '配置目录内容过多，未继续扫描，请使用手动连接。')
                if re.fullmatch(r'onebot_\d{5,10}\.json', entry.name) and entry.is_file():
                    paths.append(Path(entry.path))
        if len(paths) > 32:
            fail('too_many_accounts', '配置账号过多，未继续读取，请使用手动连接。')
    except FileNotFoundError:
        paths = []
    except OSError:
        fail('file_unreadable', '无法读取 SnowLuma 的 config 目录，请检查目录权限。')
    accounts, private, fingerprints = [], {}, [package_hash]
    for path in sorted(paths):
        account = path.stem.removeprefix('onebot_')
        try:
            own, digest = read_json(path)
            fingerprints.append((account, digest))
            if not set(own).intersection(('mode','networks','httpServers','wsServers','httpClients','wsClients','statusCommand','historySync','notifications','musicSignUrl','messageFormat','reportSelfMessage')):
                fail('unsupported_format', '账号配置中没有已知的 OneBot 节点结构。')
            sources = []
            if own.get('mode') != 'snapshot' and (directory / 'onebot.json').exists():
                global_config, digest = read_json(directory / 'onebot.json')
                fingerprints.append(('global', digest))
                sources.append(global_config)
            sources.append(own)
            nodes, credentials = adapters(sources)
            private[account] = credentials
            usable_http = any(n['usable'] for n in nodes['http'])
            usable_ws = any(n['usable'] for n in nodes['ws'])
            accounts.append(dict(account=account, nodes=nodes, code='ok' if usable_http else 'no_http_node',
                message=('可选择 HTTP 与事件节点。' if usable_ws else '当前没有可用事件节点；可仅导入 HTTP，持续群聊暂不可用。') if usable_http
                else '没有启用且可用的本机 HTTP 服务节点，请在 SnowLuma 配置后重新检测。'))
        except SetupError as exc:
            accounts.append(dict(account=account, nodes={'http': [], 'ws': []}, code=exc.code, message=str(exc)))
    if not accounts:
        fail('account_config_missing', '没有找到已生成的账号配置。这不能证明 QQ 未登录；请手动启动 SnowLuma、完成它的首次设置、登录并加载 QQ，再重新检测。')
    public = dict(folder=str(base), version=version, accounts=accounts,
                  message='请选择实际要连接的账号和节点；识别不会修改任何配置。')
    fingerprint = hashlib.sha256(json.dumps(fingerprints, sort_keys=True).encode()).hexdigest()
    return public, private, fingerprint


class SetupSessions:
    """Short previews bind the explicit UI selection to the same on-disk content."""
    def __init__(self):
        self.lock = threading.Lock()
        self.items = {}

    def inspect(self, folder):
        public, _, fingerprint = inspect_folder(folder)
        with self.lock:
            self.items = {k: v for k, v in self.items.items() if time.monotonic() - v[0] < 600}
            if len(self.items) >= 8:
                del self.items[next(iter(self.items))]
            key = secrets.token_urlsafe(24)
            self.items[key] = (time.monotonic(), public['folder'], fingerprint)
        return dict(public, selection_id=key)

    def select(self, body):
        if set(body) != {'selection_id', 'account', 'http_id', 'ws_id'} or any(not isinstance(v, str) for v in body.values()):
            fail('selection_required', '请重新识别并明确选择账号、HTTP 和事件节点。')
        with self.lock:
            item = self.items.get(body['selection_id'])
        if not item or time.monotonic() - item[0] >= 600:
            fail('selection_expired', '识别结果已过期，请重新检测文件夹。')
        public, private, fingerprint = inspect_folder(item[1])
        if fingerprint != item[2]:
            fail('config_changed', 'SnowLuma 配置在识别后发生变化，请重新检测并确认节点。尚未保存。')
        chosen = next((a for a in public['accounts'] if a['account'] == body['account']), None)
        if not chosen:
            fail('selection_required', '请选择本次识别到的账号，不会尝试其他账号。')
        def pick(kind, key):
            node = next((n for n in chosen['nodes'][kind] if n['id'] == key and n['usable']), None)
            if not node:
                fail('selection_required', '请选择可用的节点；禁用或不支持的节点不能导入。')
            return private[chosen['account']][node['id']]
        http = pick('http', body['http_id'])
        ws = pick('ws', body['ws_id']) if body['ws_id'] else dict(url='', token='')
        return chosen['account'], http, ws


def check_account_scope(service, account):
    if not service:
        return
    from .client_accounts import bound_account
    expected = set()
    try:
        source = bound_account(service.access.store, 'qq')
        if source:
            expected.add(source)
        with service.access.connect() as db:
            for row in db.execute('SELECT accounts FROM grants WHERE revoked=0'):
                value = json.loads(row['accounts']).get('qq')
                if value:
                    expected.add(str(value))
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        fail('scope_unavailable', '现有 QQ 来源或 MCP 授权记录无法核对；保留原连接，请先检查本机资料状态。')
    if expected and expected != {account}:
        fail('scope_mismatch', '所选 QQ 账号与本机已导入来源或现有 MCP 授权不一致。尚未保存；请选择相符账号，不会自动扩大授权或切换资料来源。')


def test_connection(http, ws, *, expected_account='', service=None):
    from .onebot import Client, OneBotError
    from .onebot_events import probe_connection
    try:
        account = Client(http, timeout=3).login()
        if expected_account and account != expected_account:
            fail('account_mismatch', 'HTTP 实际登录账号与所选配置账号不同。请核对 SnowLuma 已加载的账号，尚未保存。')
        check_account_scope(service, account)
    except OneBotError as exc:
        return dict(ok=False, code=exc.code, message=str(exc), http_ok=False, receiver=None)
    except SetupError as exc:
        return dict(ok=False, code=exc.code, message=str(exc), http_ok=False, receiver=None)
    if not ws['url']:
        return dict(ok=True, http_ok=True, account=account, code='http_only', receiver=None,
                    message='HTTP 已核对 QQ 账号；未配置事件连接，持续群聊暂不可用。')
    receiver = probe_connection(http, ws, account)
    ok = receiver['state'] == 'connected'
    return dict(ok=ok, http_ok=True, account=account, code='ok' if ok else 'ws_' + receiver['state'], receiver=receiver,
                message=('HTTP 与 WS 均已核对同一 QQ 账号。' if ok else 'HTTP 已连接；' + receiver['note']))
