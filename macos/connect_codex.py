"""Explicitly authorized local Codex setup; never prints bearer credentials."""
import asyncio
import json
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from chatlocal.store import Store
from chatlocal.mcp_access import MCPAccess
from chatlocal.mcp_connect import codex_home, install_codex, test_connection


async def main():
    config = codex_home()/'config.toml'
    existing = tomllib.loads(config.read_text()).get('mcp_servers', {}).get('tulpa') if config.exists() else None
    access = MCPAccess(Store())
    if existing:
        token = existing.get('http_headers', {}).get('Authorization', '').removeprefix('Bearer ')
        access.authorize(token)
        result = await test_connection(existing['url'], token)
        print(json.dumps(dict(configured=True, changed=False, **result)))
        return
    from chatlocal.terminal_client import TerminalClient
    from chatlocal.terminal_launcher import ensure_backend
    ensure_backend(root=ROOT, timeout=90)
    client = TerminalClient()
    status = client.api_get('/api/mcp')
    url = client.api_put('/api/mcp', dict(enabled=True, port=status['port']))['url']
    grant = access.create(dict(name='Codex · 全部聊天资料（只读）', platforms=['qq', 'wechat'],
                             all_conversations=True, allow_future_imports=True,
                             media=True, prepare=True, voice=False))
    try:
        tested = await test_connection(url, grant['token'])
        scope = access.authorize(grant['token'])['scope']
        installed = install_codex(url, grant['token'], scope=scope)
    except Exception:
        access.revoke(grant['id'])
        raise
    print(json.dumps(dict(configured=True, url=url, **installed, **tested), ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
