"""Scriptable terminal entry; no arguments opens the optional Textual frontend."""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys
import uuid

from .terminal_client import TerminalClient, TerminalError
from .version import VERSION
from .mcp_qq_permissions import QQ_FLAGS
from .mcp_access import PERMISSION_FLAGS


def parser():
    from .config import ROOT
    from .terminal_instances import metadata
    instance = metadata(ROOT)
    management_port = instance['management_port'] if instance else 7861
    p = argparse.ArgumentParser(prog='tulpa', description='Tulpa · Linux 终端与 MCP 管理')
    p.add_argument('--version', action='version', version='Tulpa '+VERSION)
    p.add_argument('--url', help='本机管理地址，默认 http://127.0.0.1:7861')
    p.add_argument('--token-file', help='本机管理凭据文件；不是 SL 或 MCP 客户端 Token')
    p.add_argument('--json', action='store_true', help='输出 JSON，适合脚本调用')
    commands = p.add_subparsers(dest='command')
    commands.add_parser('tui', help='准备依赖、启动后台并打开终端界面（默认操作）')
    commands.add_parser('start', help='准备依赖并启动或复用本机后台')
    commands.add_parser('stop', help='停止本目录的后台服务')
    serve = commands.add_parser('serve', help='运行 Tulpa 服务；可由 systemd 等进程管理器托管')
    serve.add_argument('--port', type=int, default=management_port)
    instances = commands.add_parser('instances', help='在主实例管理独立 QQ/SL 后台').add_subparsers(dest='action', required=True)
    instances.add_parser('list')
    instance_create = instances.add_parser('create')
    instance_create.add_argument('id', help='小写字母开头的实例 ID，如 deepseek 或 claude')
    instance_create.add_argument('--name', required=True)
    instance_create.add_argument('--management-port', type=int)
    instance_create.add_argument('--mcp-port', type=int)
    for action in ('start', 'stop', 'rename'):
        sub = instances.add_parser(action); sub.add_argument('id')
        if action == 'rename': sub.add_argument('--name', required=True)
    commands.add_parser('status', help='查看本实例状态')
    from .mcp_client_config import add_bridge_arguments as bridge_arguments
    bridge_arguments(commands.add_parser('chat-bridge',help='专用 stdio MCP 聊天连接，合并空闲等待'))
    commands.add_parser('setup', help='交互配置已有 SnowLuma 服务')
    sl = commands.add_parser('sl', help='查看或检测 SL 连接').add_subparsers(dest='action', required=True)
    sl.add_parser('show')
    sl.add_parser('test')
    mcp = commands.add_parser('mcp', help='启停 MCP 接口').add_subparsers(dest='action', required=True)
    on = mcp.add_parser('enable'); on.add_argument('--port', type=int)
    mcp.add_parser('disable')
    commands.add_parser('groups', help='从 SL 读取当前账号的群目录')
    commands.add_parser('friends', help='从 SL 读取当前账号的好友目录')
    grants = commands.add_parser('connections', help='管理 MCP 授权').add_subparsers(dest='action', required=True)
    grants.add_parser('list')
    grants.add_parser('permissions',help='显示可选 SL 扩展权限及账号/会话范围')
    create = grants.add_parser('create')
    create.add_argument('--name', required=True)
    selections=create.add_mutually_exclusive_group(required=True)
    selections.add_argument('--group','--conversation', dest='group', action='append', help='完整会话编号 ACCOUNT:group:GROUP 或 ACCOUNT:direct:QQ，可重复')
    selections.add_argument('--all-conversations',action='store_true',help='明确授权当前账号的全部会话')
    create.add_argument('--start', default=''); create.add_argument('--end', default='')
    for flag in ('onebot','send','manage','chat','chat-images','chat-sticker-send','chat-sticker-collect','chat-reactions','media','prepare','voice'):
        create.add_argument('--'+flag, action='store_true', help='明确开放此权限')
    for flag,description in QQ_FLAGS.items():
        create.add_argument('--'+flag.replace('_','-'),action='store_true',help=description)
    create.add_argument('--output', help='把新连接的 MCP 配置保存为权限 600 的新文件')
    edit = grants.add_parser('edit', help='修改现有连接的权限，保留原凭证和会话范围')
    edit.add_argument('id')
    edit.add_argument('--expected-revision', type=int, help='防止覆盖其他修改；默认读取连接的最新版本')
    for flag in PERMISSION_FLAGS:
        edit.add_argument('--'+flag.replace('_','-'), action=argparse.BooleanOptionalAction, default=None,
                          help=QQ_FLAGS.get(flag, '开启/关闭此权限；未指定则保持原值'))
    revoke = grants.add_parser('revoke'); revoke.add_argument('id')
    commands.add_parser('personas', help='列出人格卡')
    sessions = commands.add_parser('sessions', help='管理 Tulpa 群聊会话').add_subparsers(dest='action', required=True)
    sessions.add_parser('list')
    start = sessions.add_parser('start')
    start.add_argument('--connection', required=True); start.add_argument('--group','--conversation',dest='group', required=True)
    start.add_argument('--persona', required=True); start.add_argument('--key', help='稳定的幂等操作编号')
    stop = sessions.add_parser('stop'); stop.add_argument('id')
    learning = sessions.add_parser('learning'); learning.add_argument('id')
    learning.add_argument('--enabled', choices=['yes','no'])
    search = commands.add_parser('search'); search.add_argument('query'); search.add_argument('--limit', type=int, default=20)
    imp = commands.add_parser('import'); imp.add_argument('path'); imp.add_argument('--platform', choices=['qq','wechat'], default='')
    commands.add_parser('events', help='最近 MCP 工具调用记录')
    service=commands.add_parser('service',help='用户 systemd 常驻服务').add_subparsers(dest='action',required=True)
    for action in ('status','start','stop','restart','uninstall'):service.add_parser(action)
    install=service.add_parser('install');install.add_argument('--port',type=int,default=management_port);install.add_argument('--start',action='store_true')
    commands.add_parser('desktop-install',help='安装终端桌面入口')
    package=commands.add_parser('package',help='生成不含账号数据和凭据的源码包');package.add_argument('output')
    model=commands.add_parser('model',help='模型配置').add_subparsers(dest='action',required=True)
    model.add_parser('show');config=model.add_parser('configure')
    config.add_argument('--base');config.add_argument('--model');config.add_argument('--reasoning',choices=['none','low','high','max'])
    key=config.add_mutually_exclusive_group();key.add_argument('--key-stdin',action='store_true');key.add_argument('--clear-key',action='store_true')
    native=commands.add_parser('native',help='本机 QQ/微信来源').add_subparsers(dest='action',required=True)
    native.add_parser('show');native.add_parser('install')
    configure=native.add_parser('configure');configure.add_argument('platform',choices=['qq','wechat']);configure.add_argument('data_root');configure.add_argument('--keys-file',default='')
    extract=native.add_parser('extract');extract.add_argument('platform',choices=['qq','wechat']);extract.add_argument('--account')
    extract.add_argument('--method',choices=['passive','active'],default='passive');extract.add_argument('--confirm-start-client',action='store_true')
    components=commands.add_parser('components').add_subparsers(dest='action',required=True)
    components.add_parser('status');component=components.add_parser('install');component.add_argument('name',choices=['voice','ocr','office','readers'])
    job=components.add_parser('job');job.add_argument('id')
    agent=commands.add_parser('agent',help='原版 Tulpa 模型对话').add_subparsers(dest='action',required=True)
    agent.add_parser('list');agent.add_parser('create')
    for action in ('history','delete','stop'):
        sub=agent.add_parser(action);sub.add_argument('id')
    ask=agent.add_parser('ask');ask.add_argument('id');ask.add_argument('question')
    ask.add_argument('--platform',action='append',choices=['qq','wechat']);ask.add_argument('--conversation',action='append',default=[])
    ask.add_argument('--start',default='');ask.add_argument('--end',default='');ask.add_argument('--profile',choices=['quick','standard','deep','thorough'],default='standard')
    ask.add_argument('--vision',choices=['ocr','native'],default='ocr')
    api=commands.add_parser('api',help='脚本调用原生完整 API；仍执行原版预览、版本和确认验证')
    api.add_argument('method',choices=['GET','POST','PUT','PATCH','DELETE']);api.add_argument('path',help='/api/ 开头的路径')
    api.add_argument('--body-file',help='JSON 请求文件，- 表示标准输入');api.add_argument('--param',action='append',default=[],help='GET 查询参数 key=value')
    download=commands.add_parser('download',help='下载证据、音频或材料到新文件');download.add_argument('path');download.add_argument('output')
    return p


def setup(client):
    if not sys.stdin.isatty():
        raise TerminalError('setup 需要交互终端；请在终端运行 ./tulpa setup。')
    current = client.get('/sl')
    http = input(f'SL HTTP 地址 [{current["http_url"]}]: ').strip() or current['http_url']
    ws = input(f'SL 正向 WS 地址 [{current["ws_url"]}]: ').strip() or current['ws_url']
    http_token = getpass.getpass('HTTP Token（留空保留）: ')
    ws_token = getpass.getpass('WS Token（留空保留）: ')
    return client.put('/sl', dict(http_url=http, ws_url=ws, http_token=http_token, ws_token=ws_token))


def execute(args, client):
    command = args.command
    if command == 'instances':
        if args.action == 'list': return client.get('/instances')
        if args.action == 'create':
            body = dict(id=args.id, name=args.name)
            if args.management_port is not None: body['management_port'] = args.management_port
            if args.mcp_port is not None: body['mcp_port'] = args.mcp_port
            return client.post('/instances', body)
        from .terminal_instances import ID
        if args.id != 'default' and not ID.fullmatch(args.id):
            raise ValueError('实例 ID 无效。')
        if args.action == 'rename': return client.put('/instances/'+args.id, dict(name=args.name))
        return client.post('/instances/'+args.id+'/'+args.action)
    if command == 'start':
        from .terminal_launcher import ensure_backend
        return ensure_backend(args.url, args.token_file,
                              notify=lambda text: print(text, file=sys.stderr, flush=True))
    if command == 'stop':
        from .terminal_launcher import stop_backend
        return stop_backend(client)
    if command == 'status': return client.get('/overview')
    if command == 'friends': return client.get('/friends')
    if command == 'setup': return setup(client)
    if command == 'sl': return client.get('/sl') if args.action == 'show' else client.post('/sl/test')
    if command == 'mcp':
        port = args.port if args.action == 'enable' and args.port is not None else client.get('/overview')['mcp']['port']
        return client.put('/mcp', dict(enabled=args.action == 'enable', port=port))
    if command == 'groups': return client.get('/groups')
    if command == 'personas': return client.get('/personas')
    if command == 'events': return client.get('/events')
    if command == 'search': return client.get('/search', query=args.query, limit=args.limit)
    if command == 'import': return client.post('/import', dict(path=args.path, platform=args.platform))
    if command=='service':
        from .linux_service import service_action
        return service_action(args.action,port=getattr(args,'port',7861),start=getattr(args,'start',False))
    if command=='desktop-install':
        from .linux_service import desktop_install
        return desktop_install()
    if command=='package':
        from .linux_service import source_archive
        return source_archive(args.output)
    if command=='model':
        if args.action=='show':return client.get('/model')
        body={k:v for k,v in dict(api_base=args.base,model=args.model,reasoning_effort=args.reasoning).items() if v is not None}
        if args.key_stdin:
            body['api_key']=getpass.getpass('API Key: ') if sys.stdin.isatty() else sys.stdin.readline(4097).strip()
        if args.clear_key:body['clear_api_key']=True
        return client.put('/model',body)
    if command=='native':
        if args.action=='show':return client.get('/native')
        if args.action=='install':return client.post('/native/install')
        if args.action=='configure':return client.put('/native/'+args.platform,dict(data_root=args.data_root,keys_file=args.keys_file))
        return client.post('/native/'+args.platform+'/extract',dict(account=args.account,method=args.method,confirm_start_client=args.confirm_start_client))
    if command=='components':
        if args.action=='status':return client.get('/components')
        if args.action=='job':return client.get('/jobs/'+args.id)
        return client.post('/components/'+args.name+'/install')
    if command=='agent':
        path='/api/terminal/agent/sessions'
        if args.action=='list':return client.api_get(path)
        if args.action=='create':return client.api_post(path)
        if args.action=='history':return client.api_get(path+'/'+args.id)
        if args.action=='delete':return client.api_delete(path+'/'+args.id)
        if args.action=='stop':return client.post('/agent/turns/'+args.id+'/stop')
        return client.api_post(path+'/'+args.id+'/ask',dict(question=args.question,platforms=args.platform or ['qq','wechat'],
            conversations=args.conversation,start=args.start,end=args.end,profile=args.profile,vision=args.vision))
    if command=='api':
        body=None
        if args.body_file:
            raw=sys.stdin.read(512001) if args.body_file=='-' else Path(args.body_file).read_text()
            if len(raw)>512000:raise ValueError('请求文件过大。')
            body=json.loads(raw)
            if not isinstance(body,dict):raise ValueError('请求须为 JSON 对象。')
        params={}
        for value in args.param:
            if '=' not in value:raise ValueError('查询参数格式为 key=value。')
            k,v=value.split('=',1);params[k]=v
        return client.request(args.method,args.path,params=params,body={} if body is None and args.method!='GET' else body,native=True)
    if command=='download':return dict(path=client.download(args.path,args.output))
    if command == 'connections':
        if args.action == 'list': return client.get('/grants')
        if args.action == 'permissions': return client.get('/grant-permissions')
        if args.action == 'revoke': return client.post(f'/grants/{args.id}/revoke')
        if args.action == 'edit':
            changes = {flag:getattr(args,flag) for flag in PERMISSION_FLAGS if getattr(args,flag) is not None}
            if not changes:raise ValueError('请至少指定一项权限，例如 --chat-voice 或 --no-qq-qzone。')
            revision = args.expected_revision
            if revision is None:
                grant = next((g for g in client.get('/grants')['items'] if g['id']==args.id), None)
                if not grant or grant.get('revoked'):raise ValueError('连接不存在或已撤销，不能修改权限。')
                revision = grant.get('revision',0)
            return client.put(f'/grants/{args.id}', dict(changes, expected_revision=revision))
        flags = ('onebot','send','manage','chat','chat_images','chat_sticker_send','chat_sticker_collect','chat_reactions','media','prepare','voice',*QQ_FLAGS)
        body = dict(name=args.name, platforms=['qq'], conversations=[['qq', group] for group in (args.group or [])],all_conversations=args.all_conversations,
                    start=args.start, end=args.end, **{name:getattr(args,name) for name in flags})
        output = Path(args.output).expanduser() if args.output else None
        stream = None
        if output:
            # Reserve the destination before creating a grant with a one-time token.
            stream = os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8')
        try:
            result = client.post('/grants', body)
            if stream:
                json.dump(result['client_config'], stream, ensure_ascii=False, indent=2)
                stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
                return dict(id=result['id'], mcp_url=result['mcp_url'], config_file=str(output))
            return result  # Credential shown only for this explicit creation operation.
        except Exception:
            if stream:
                stream.close(); output.unlink(missing_ok=True)
            raise
        finally:
            if stream: stream.close()
    if command == 'sessions':
        if args.action == 'list': return client.get('/sessions')
        if args.action == 'start':
            return client.post('/sessions/start', dict(connection_id=args.connection, conversation_id=args.group,
                persona_preset=args.persona, idempotency_key=args.key or uuid.uuid4().hex))
        if args.action == 'stop': return client.post(f'/sessions/{args.id}/stop')
        path = f'/sessions/{args.id}/learning'
        if args.enabled is None: return client.get(path)
        return client.put(path, dict(enabled=args.enabled == 'yes'))
    raise TerminalError('未知命令，请运行 ./tulpa --help。')


def prepare_tui(args):
    """Child shortcuts still open the single hub UI, preselecting that child."""
    from .config import ROOT
    from .terminal_instances import metadata
    from .terminal_launcher import ensure_backend
    from .terminal_client import DEFAULT_URL
    notify = lambda text: print(text, file=sys.stderr, flush=True)
    instance = metadata(ROOT)
    if instance and not args.url and not args.token_file:
        hub_root = Path(instance['hub_root'])
        hub_url = instance.get('hub_url') or DEFAULT_URL
        hub_token = hub_root / 'data/terminal-admin.token'
        ensure_backend(hub_url, hub_token, root=hub_root, notify=notify)
        hub = TerminalClient(hub_url, hub_token)
        try:
            selected = next((r for r in hub.get('/instances')['items'] if r['id'] == instance['id']), None)
            if selected is None or Path(selected.get('root', '')).resolve() != ROOT:
                raise TerminalError('主后台没有此实例的有效登记，请从主目录打开实例管理。')
            if not selected['running']:
                selected = hub.post('/instances/'+instance['id']+'/start')
            return dict(base_url=hub_url, token_file=hub_token, initial_instance=selected)
        finally:
            hub.close()
    ensure_backend(args.url, args.token_file, notify=notify)
    return dict(base_url=args.url, token_file=args.token_file)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command in (None, 'tui', 'start'):
            from .terminal_environment import prepare_environment
            prepare_environment()
        if args.command in (None, 'tui'):
            options = prepare_tui(args)
            try:
                from .tui import run
            except ImportError:
                raise TerminalError('终端界面依赖未就绪，请运行 ./setup-linux.sh。') from None
            run(**options)
            return 0
        if args.command == 'serve':
            from .terminal_server import run
            run(args.port, args.token_file)
            return 0
        if args.command == 'chat-bridge':
            from .mcp_chat_bridge import run
            try:run(args)
            except ValueError:raise
            except Exception:raise TerminalError('Tulpa 桥接连接失败，请检查后台及 MCP 凭据文件。') from None
            return 0
        client = TerminalClient(args.url, args.token_file)
        try:
            result = execute(args, client)
        finally:
            client.close()
        if not args.json:
            try:
                from rich.console import Console
                Console().print_json(json.dumps(result, ensure_ascii=False))
            except ImportError:
                print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except (TerminalError, ValueError, OSError) as error:
        # OSError text can include user filenames; never serialize HTTP exceptions.
        message = str(error) if isinstance(error, ValueError) else '本机文件或端口不可用，请检查路径、权限和占用情况。'
        if args.json: print(json.dumps(dict(error=message), ensure_ascii=False), file=sys.stderr)
        else: print('Tulpa: '+message, file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
