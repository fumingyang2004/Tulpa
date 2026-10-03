"""Explicit local source selection. One active account per platform/data directory."""
import json
import os
import subprocess
import sys
from pathlib import Path
from .config import ROOT

PLATFORMS = {'qq': 'QQ', 'wechat': '微信'}


def account_id(value):
    if not isinstance(value, str) or not value or len(value) > 200 or value in ('.', '..'):
        raise ValueError('请选择有效的本机账号')
    if any(ord(c) < 32 or c in '/\\:<>"|?*' for c in value) or value.endswith((' ', '.')):
        raise ValueError('账号必须是检测到的本机账号名称，不能是路径')
    return value


def choose_account(platform, candidates, requested=None):
    label = PLATFORMS[platform]
    if requested is not None:
        requested = account_id(requested)
        if requested not in candidates:
            raise ValueError(f'所选 {label} 账号在本机未找到；请检查数据目录并重新检测账号，不会自动改读其他账号。')
        return requested
    if not candidates:
        raise ValueError(f'未找到 {label} 本机账号数据，请先登录客户端并保留本地聊天记录，再点击“重新检测账号”。')
    if len(candidates) != 1:
        raise ValueError(f'检测到多个 {label} 账号，请在“数据与同步”中选择本机账号，再读取会话列表或消息。命令行可使用 --account 指定。')
    return account_id(next(iter(candidates)))


def discover_accounts(platform):
    if platform not in PLATFORMS:
        raise ValueError('无效平台')
    try:
        result = subprocess.run([sys.executable, str(ROOT/'scripts/list_client_accounts.py'), platform],
            cwd=ROOT, env=dict(os.environ, PYTHONUTF8='1'), capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=25,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ValueError('账号目录检测失败，请检查客户端安装和数据目录后重试。')
        rows = json.loads(result.stdout)
        if not isinstance(rows, list):
            raise ValueError('账号检测结果无效')
        return [account_id(row) for row in rows]
    except subprocess.TimeoutExpired:
        raise ValueError('检测本机账号超时，请稍后重新检测。') from None


def bound_account(store, platform):
    """Never reuse another account's per-platform cursors, even if metadata is lost."""
    values = set()
    metadata = store.path.parent/f'{platform}-snapshot-info.json'
    if metadata.exists():
        try:
            values.add(account_id(json.loads(metadata.read_text('utf-8-sig'))['account']))
        except (OSError, ValueError, KeyError):
            raise ValueError('已有账号来源记录无法读取，请保留数据并检查本机诊断；不会自动选择其他账号。') from None
    with store.connect() as db:
        for table in ('sync_state', 'live_state'):
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                continue
            row = db.execute(f'SELECT checkpoint FROM {table} WHERE platform=?', (platform,)).fetchone()
            if row and (account := json.loads(row[0]).get('account')):
                values.add(account_id(account))
    if len(values) > 1:
        raise ValueError('已有来源与同步进度的账号不一致，请保留数据并检查；未启动读取。')
    return next(iter(values), None)


def resolve_account(store, platform, requested=None):
    from .import_scope import get_scope
    bound = bound_account(store, platform)
    requested = requested or bound or get_scope(store)[platform].get('account')
    if bound and requested != bound:
        raise ValueError(f'{PLATFORMS[platform]} 已有读取来源属于账号 {bound}。本数据目录每个平台使用一个账号；读取其他账号请使用独立的 Tulpa 文件夹，现有消息和进度保留。')
    return choose_account(platform, discover_accounts(platform), requested)


def accounts_view(store):
    from .import_scope import get_scope
    scope = get_scope(store)
    result = {}
    for platform in PLATFORMS:
        selected = scope[platform].get('account')
        try:
            selected = bound_account(store, platform) or selected
            candidates = discover_accounts(platform)
            if not selected and len(candidates) == 1:
                selected = candidates[0]
            detail = '' if candidates else f'未找到 {PLATFORMS[platform]} 本机账号数据，请登录客户端后重新检测。'
            if selected and selected not in candidates:
                detail = '上次使用的账号当前未找到，不会自动选择其他账号。'
            result[platform] = dict(accounts=candidates, selected=selected, detail=detail)
        except (OSError, ValueError):
            result[platform] = dict(accounts=[], selected=selected, detail='账号检测未完成，请重新检测；已有数据保留。')
    return result
