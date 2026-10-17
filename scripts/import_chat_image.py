"""Upload a local image through an existing Tulpa MCP grant; never send to QQ.

The client reads the file, so Tulpa and the image generator may run on different
machines (with a local SSH forward). No paths or credentials enter tool output.
"""
import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
import re
import stat
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chatlocal.mcp_chat_media import MAX_BYTES


from chatlocal.mcp_client_config import ClientConfigError as UploadError, connection


def image_bytes(path):
    try:
        # POSIX must not wait on FIFOs; Windows has no O_NONBLOCK and needs
        # binary mode. The fstat check below rejects non-regular handles.
        flags = os.O_RDONLY | (os.O_BINARY if os.name == 'nt' else os.O_NONBLOCK)
        fd = os.open(Path(path).expanduser(), flags)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
                raise UploadError('请选择非空的普通图片文件，原图不能超过 6 MiB。')
            raw = stream.read(MAX_BYTES + 1)
    except OSError:
        raise UploadError('无法读取图片文件。') from None
    if not raw or len(raw) > MAX_BYTES:
        raise UploadError('图片为空或超过 6 MiB。')
    return raw


async def upload(config, session_id, path, server=None):
    if not re.fullmatch('[a-f0-9]{32}', session_id):
        raise UploadError('--session 需要当前群聊会话的 32 位 session_id。')
    url, auth = connection(config, server)
    raw = image_bytes(path)
    async with httpx.AsyncClient(headers={'Authorization': auth}, timeout=65, trust_env=False, follow_redirects=False) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as client:
                info = await client.initialize()
                if info.serverInfo.name != 'tulpa':
                    raise UploadError('连接到的 MCP 服务不是 Tulpa，未上传。')
                available = await client.list_tools()
                if 'import_chat_image' not in {t.name for t in available.tools}:
                    raise UploadError('连接未提供 import_chat_image；请更新后台并核对持续聊天、看图和发送表情权限。')
                result = await client.call_tool('import_chat_image', {
                    'session_id': session_id, 'data_base64': base64.b64encode(raw).decode('ascii'),
                })
    data = result.structuredContent
    if not isinstance(data, dict):
        raise UploadError('Tulpa 未返回图片导入结果。')
    if result.isError or not data.get('sticker_id'):
        # Only Tulpa's structured, sanitized diagnostics; never SDK exceptions
        # (which may contain request headers or the uploaded body).
        return {k: data[k] for k in ('error', 'error_code', 'recovery') if k in data}, False
    data = {k: data[k] for k in ('sticker_id', 'sha256', 'format', 'dimensions', 'total_frames', 'frame_indices', 'bytes', 'knowledge_record', 'note') if k in data}
    data['preview'] = {'tool': 'read_chat_sticker', 'arguments': {'session_id': session_id, 'sticker_id': data['sticker_id']}}
    return data, True


def main():
    parser = argparse.ArgumentParser(description='通过 Tulpa MCP 导入本机图片；只导入，不向 QQ 发消息。')
    parser.add_argument('image', help='PNG/JPEG/GIF/WebP 文件，最多 6 MiB')
    parser.add_argument('--config', required=True, help='含连接凭据的 MCP JSON/TOML 配置文件')
    parser.add_argument('--server', help='配置里的 MCP 连接名称；多个连接时指定')
    parser.add_argument('--session', required=True, help='已启动的群聊 session_id')
    args = parser.parse_args()
    try:
        data, ok = asyncio.run(upload(args.config, args.session, args.image, args.server))
    except UploadError as exc:
        data, ok = {'error': str(exc)}, False
    except Exception:
        data, ok = {'error': '上传失败，请核对 Tulpa 后台、网络和连接凭据；未请求发送 QQ 消息。'}, False
    print(json.dumps(data, ensure_ascii=False), file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
