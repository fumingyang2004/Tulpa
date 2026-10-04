"""User-initiated client configuration. Never available as an MCP tool."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import tomllib

_lock = threading.Lock()


def codex_home():
    return Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex').expanduser()


def codex_config(url, token, scope=None):
    stanza = ('[mcp_servers.tulpa]\nurl = '+json.dumps(url)+'\nhttp_headers = { Authorization = '+
              json.dumps('Bearer '+token)+' }\ntool_timeout_sec = 240\n')
    for flag,tool in [('send','send_qq_message'),('manage','manage_qq_group')]:
        if (scope or {}).get(flag) is True:
            stanza += f'\n[mcp_servers.tulpa.tools.{tool}]\napproval_mode = "approve"\n'
    if (scope or {}).get('chat') is True:
        for tool in ('start_chat_session','get_chat_session','list_chat_sessions','wait_chat_messages','send_chat_message','stop_chat_session','list_chat_groups'):
            stanza += f'\n[mcp_servers.tulpa.tools.{tool}]\napproval_mode = "approve"\n'
        from .mcp_chat_media import MEDIA_TOOLS
        for tool,flag in MEDIA_TOOLS.items():
            if (scope or {}).get(flag):
                stanza += f'\n[mcp_servers.tulpa.tools.{tool}]\napproval_mode = "approve"\n'
    return stanza


def install_codex(url, token, *, home=None, scope=None):
    """Append only our stanza, verify structural equivalence, back up exact bytes."""
    home = Path(home) if home is not None else codex_home()
    home.mkdir(parents=True, exist_ok=True)
    path = home/'config.toml'
    with _lock:
        original = path.read_bytes() if path.exists() else b''
        try:
            before = tomllib.loads(original.decode('utf-8-sig'))
        except (ValueError, UnicodeError):
            raise ValueError('Codex 配置无法解析，未修改。请使用复制配置方式并检查原文件。') from None
        stanza = codex_config(url, token, scope)
        entry = tomllib.loads(stanza)['mcp_servers']['tulpa']
        existing = before.get('mcp_servers', {}).get('tulpa')
        if existing == entry:
            return dict(installed=True, changed=False)
        if existing is not None:
            raise ValueError('Codex 已有名为 tulpa 的其他连接，未覆盖。请先在 Codex 移除旧连接，或复制配置使用其他名称。')
        text = original.decode('utf-8-sig')
        updated = (text.rstrip()+'\n\n'+stanza).encode('utf-8')
        after = tomllib.loads(updated.decode('utf-8'))
        expected = dict(before)
        expected['mcp_servers'] = {**before.get('mcp_servers', {}), 'tulpa': entry}
        if after != expected:
            raise ValueError('无法安全合并 Codex 配置，未修改。')
        if original:
            backup = home/'backups'/('config-before-tulpa-'+str(time.time_ns())+'.toml')
            backup.parent.mkdir(exist_ok=True)
            backup.write_bytes(original)
        with tempfile.NamedTemporaryFile(prefix='tulpa-config-', suffix='.tmp', dir=home, delete=False) as f:
            temp = Path(f.name)
        try:
            temp.write_bytes(updated)
            if (path.read_bytes() if path.exists() else b'') != original:
                raise ValueError('Codex 配置刚被其他程序修改，未覆盖。请重试。')
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
        return dict(installed=True, changed=True)


async def test_connection(url, token):
    """Actual SDK initialization/list/read, no model calls or source body reads."""
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+token}, timeout=20, trust_env=False) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = await client.list_tools()
                status = await client.call_tool('get_data_status', {})
                if status.isError:
                    raise ValueError('资料状态读取未通过，请重新检查授权范围。')
                return dict(ok=True, tools=len(tools.tools), onebot=any(t.name=='read_qq_group' for t in tools.tools))
