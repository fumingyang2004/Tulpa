"""Loopback-only management client shared by the CLI and terminal UI."""
import os
from pathlib import Path
import stat
from urllib.parse import urlparse

from .config import ROOT

DEFAULT_URL = 'http://127.0.0.1:7861'


class TerminalError(ValueError):
    """A message suitable for display without HTTP headers or credentials."""

    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def validate_url(value):
    value = value.rstrip('/')
    try:
        parsed = urlparse(value)
        valid = (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1')
                 and parsed.port and not parsed.username and not parsed.password
                 and not parsed.path and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise TerminalError('管理地址须为本机 HTTP 地址，例如 http://127.0.0.1:7861。')
    return value


def read_token(path):
    path = Path(path)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'r', encoding='utf-8') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise TerminalError('管理凭据必须是普通文件。')
            if os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise TerminalError('管理凭据须归当前用户所有，文件权限应为 600。')
            token = stream.read(256).strip()
        if not 32 <= len(token) <= 200 or not token.isascii() or any(c.isspace() for c in token):
            raise TerminalError('管理凭据格式无效，请检查所选实例。')
        return token
    except FileNotFoundError:
        raise TerminalError('Tulpa 服务尚未初始化，请先运行 ./tulpa start。') from None
    except OSError:
        raise TerminalError('无法读取本机管理凭据，请检查文件路径与权限。') from None


class TerminalClient:
    def __init__(self, base_url=None, token_file=None, timeout=12):
        from .terminal_instances import metadata
        instance = metadata(ROOT)
        default_url = f'http://127.0.0.1:{instance["management_port"]}' if instance else DEFAULT_URL
        self.base_url = validate_url(base_url or os.environ.get('TULPA_URL') or default_url)
        self.token_file = Path(token_file or os.environ.get('TULPA_TOKEN_FILE') or ROOT/'data/terminal-admin.token')
        self.timeout = timeout

    def for_instance(self, descriptor):
        """Build an account-specific client without exposing its credential."""
        root = Path(descriptor['root']).resolve()
        token_file = Path(descriptor['token_file'])
        if token_file != root / 'data/terminal-admin.token':
            raise TerminalError('实例管理凭据路径无效。')
        return TerminalClient(descriptor['url'], token_file, timeout=self.timeout)

    def request(self, method, path, *, params=None, body=None, native=False, timeout=None):
        import httpx
        if not isinstance(path,str) or not path.startswith('/api/' if native else '/') or any(x in path for x in ('?', '#', '..', '://', '%', '\\')):
            raise TerminalError('管理接口路径无效。')
        token = read_token(self.token_file)
        try:
            request_timeout = self.timeout if timeout is None else timeout
            if not native and method != 'GET' and path.startswith('/instances'):
                request_timeout = max(request_timeout, 60)
            with httpx.Client(timeout=request_timeout, trust_env=False, follow_redirects=False) as client:
                response = client.request(method, self.base_url+('' if native else '/api/terminal')+path,
                    params=params, json=body, headers=self.headers(token))
            if response.status_code == 401:
                raise TerminalError('管理凭据与服务不匹配，请核对地址和 token 文件。', status_code=401)
            if response.is_redirect:
                raise TerminalError('服务返回重定向，已停止连接；请核对本机地址。', status_code=response.status_code)
            result = response.json()
            if response.is_error:
                detail = result.get('detail') if isinstance(result, dict) else None
                raise TerminalError(detail if isinstance(detail, str) else '请求参数无效或操作未完成。',
                                    status_code=response.status_code)
            if not isinstance(result, (dict,list) if native else dict):
                raise TerminalError('服务响应格式无效，请核对 Tulpa 版本。')
            return result
        except httpx.ConnectError:
            raise TerminalError('无法连接 Tulpa，请先运行 ./tulpa start，或核对服务端口。') from None
        except httpx.TimeoutException:
            raise TerminalError('请求超时，请检查服务状态；写操作请先核对结果，再决定是否重试。') from None
        except httpx.HTTPError:
            raise TerminalError('本机服务连接中断，请刷新状态后重试。') from None
        except ValueError as exc:
            if isinstance(exc, TerminalError):
                raise
            raise TerminalError('服务响应不是有效 JSON，请核对 Tulpa 地址。') from None

    def get(self, path, **params):
        return self.request('GET', path, params=params)

    def post(self, path, body=None):
        return self.request('POST', path, body={} if body is None else body)

    def put(self, path, body):
        return self.request('PUT', path, body=body)

    @staticmethod
    def headers(token):
        return {'Authorization':'Bearer '+token, 'X-ChatWeave-UI':'1',
                'X-Reply-UI':'1', 'X-Tulpa-UI':'1', 'X-Group-Admin-UI':'1'}

    def api_get(self, path, **params):
        return self.request('GET',path,params=params,native=True)

    def api_post(self, path, body=None, *, timeout=None):
        return self.request('POST',path,body={} if body is None else body,native=True,timeout=timeout)

    def api_put(self, path, body):
        return self.request('PUT',path,body=body,native=True)

    def api_patch(self, path, body):
        return self.request('PATCH',path,body=body,native=True)

    def api_delete(self, path, body=None):
        return self.request('DELETE',path,body={} if body is None else body,native=True)

    def download(self, path, output):
        import httpx
        if path.startswith('/media/'):path='/api'+path
        if not path.startswith('/api/') or any(x in path for x in ('?', '#', '..', '://', '%', '\\')):
            raise TerminalError('下载路径无效。')
        output=Path(output).expanduser()
        written=False
        try:
            with httpx.Client(timeout=self.timeout,trust_env=False,follow_redirects=False) as client:
                with client.stream('GET',self.base_url+path,headers=self.headers(read_token(self.token_file))) as response:
                    if not response.is_success:
                        raise TerminalError('下载未完成，请检查材料状态。',status_code=response.status_code)
                    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
                    written=True
                    with os.fdopen(fd,'wb') as stream:
                        size=0
                        for chunk in response.iter_bytes():
                            size+=len(chunk)
                            if size>100*1024*1024:raise TerminalError('下载超过 100 MB 限制。')
                            stream.write(chunk)
                        stream.flush();os.fsync(stream.fileno())
            return str(output)
        except (OSError,httpx.HTTPError,TerminalError) as exc:
            if written:output.unlink(missing_ok=True)
            if isinstance(exc,TerminalError):raise
            raise TerminalError('无法保存下载；请选择不存在的新文件并检查本机连接。') from None

    def close(self):
        # Each request owns its client, allowing safe use from Textual workers.
        pass
