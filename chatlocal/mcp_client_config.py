"""Shared loopback-only MCP credential loading; never echo configuration values."""
import json
import os
from pathlib import Path
import tomllib
from urllib.parse import urlsplit


def add_bridge_arguments(parser):
    parser.add_argument('--config',required=True,help='Tulpa 导出的 MCP JSON/TOML 凭据文件')
    parser.add_argument('--server',help='配置中的连接名称；有多个连接时必填')
    parser.add_argument('--conversation',required=True,help='固定的 ACCOUNT:group:GROUP 或 ACCOUNT:direct:QQ')
    parser.add_argument('--idle-wait-seconds',type=float,default=1800,help='合并空闲等待的上限，默认 1800；宿主工具超时需更长')
    parser.add_argument('--duration-seconds',type=float,help='从首次 start/get 接续起计时，到期独立停止该会话；省略则不限总时长')


class ClientConfigError(ValueError):
    """Safe local MCP configuration errors, without credentials or request data."""


def connection(path, server=None):
    try:
        text = Path(path).expanduser().read_text('utf-8-sig')
        config = tomllib.loads(text) if str(path).endswith('.toml') else json.loads(text)
        config = config.get('client_config', config)
        servers = config.get('mcpServers', config.get('mcp_servers', {}))
        entries = [servers[server]] if server else list(servers.values())
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        raise ClientConfigError('无法读取 MCP 配置或找不到指定连接；支持 Tulpa 导出的 JSON 和客户端 TOML。') from None
    found = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        url = entry.get('url', '')
        try:
            parts = urlsplit(url)
            if (parts.scheme != 'http' or parts.hostname not in ('127.0.0.1', 'localhost')
                    or not parts.port or parts.path != '/mcp' or parts.username is not None
                    or parts.password is not None or parts.query or parts.fragment):
                continue
        except (ValueError, TypeError, AttributeError):
            continue
        headers = entry.get('headers', entry.get('http_headers', {}))
        if not isinstance(headers, dict):
            continue
        auth = next((v for k, v in headers.items() if k.lower() == 'authorization'), '')
        env_name = entry.get('bearer_token_env_var')
        if not auth and isinstance(env_name, str):
            token = os.environ.get(env_name, '')
            if token:
                auth = 'Bearer ' + token
        if isinstance(auth, str) and auth.startswith('Bearer ') and auth[7:].strip() and '\n' not in auth and '\r' not in auth:
            found.append((url, auth))
    if len(found) != 1:
        raise ClientConfigError('配置需唯一匹配带凭据的本机 Tulpa /mcp 地址；多个连接请用 --server 指定，远程服务请先建立 SSH 本地转发。')
    return found[0]
