"""Build the portable Windows x64 folder/ZIP from the verified local environment.

No secrets/databases are copied. ZIP entries come exclusively from this build's
allowlist, never from recursively archiving a folder that someone has used.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
CACHE=ROOT/'.cache/desktop-downloads'
sys.path.insert(0,str(ROOT))
from chatlocal.version import VERSION
HASHES={
 'FFmpeg-b08d7969c5-source.tar.gz':'6f371d1ec52041a0d5a72bb2273cf1a49ed14768d1d1ef33b769478bfdf60924',
 'sqlite-amalgamation-3530400.zip':'1e71ddf93849c6a6ecf58b827c0692073d2dd7ee40196158068f7b29f422e87d',
 'sqlite-dll-win-x64-3530200.zip':'5d40de68da94cee0fbb01a7caae96c9226872549fb007e826f63cd7bb464b463',
 'python-3.13.9-embed-amd64.zip':'91d828c2da3a029b41699e918674a0cb379c02cf20dab9c501306885f837402a',
 'webview2-sdk-1.0.4191.47.zip':'f492bbf547d0da329553b6727435b677579b1e9f91cc9e4a1ad029366d5f23d0',
 'webview2-153.0.4234.48-x64.cab':'11e8240cb0bc56dcd3e4498907203c251346f65107fe35a3a13e152c7d51c79e',
}

def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def build_icon():
    """Export the supplied brand image at Windows icon sizes without stretching it."""
    from PIL import Image
    icon=ROOT/'build/tulpa.ico';icon.parent.mkdir(exist_ok=True)
    with Image.open(ROOT/'web/tulpa-logo.png') as source:
        image=source.convert('RGBA')
    image.thumbnail((256,256),Image.Resampling.LANCZOS)
    canvas=Image.new('RGBA',(256,256),'black')
    canvas.paste(image,((256-image.width)//2,(256-image.height)//2),image)
    canvas.save(icon,sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    return icon

def build(no_zip=False,qa=False,output=None,lite=False):
    if not qa and subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip():
        raise RuntimeError('正式打包前请先提交 Git 修改；测试构建可用 --qa --no-zip。')
    out=(Path(output) if output else ROOT/'release'/(VERSION+'-mcp' if lite else VERSION)/'Tulpa').resolve()
    assert out.is_relative_to((ROOT/'release').resolve()) and out.name=='Tulpa'
    out.mkdir(parents=True,exist_ok=True)
    entries=set()
    def register(rel):
        rel=Path(rel)
        if rel.is_absolute() or '..' in rel.parts:raise ValueError('Unsafe destination')
        path=out/rel
        if not path.resolve().is_relative_to(out):raise ValueError('Release path escaped through junction')
        entries.add(rel.as_posix());path.parent.mkdir(parents=True,exist_ok=True)
        return path
    def copy(source,rel):
        if source.is_symlink() or source.is_junction():raise ValueError('Source link is not distributable')
        dst=register(rel)
        if not dst.exists() or source.stat().st_size!=dst.stat().st_size or source.stat().st_mtime_ns!=dst.stat().st_mtime_ns:shutil.copy2(source,dst)
    def tree(source,rel,skip=lambda p:False):
        for path in source.rglob('*'):
            local=path.relative_to(source)
            if any(part in ('__pycache__','.git') for part in local.parts) or path.suffix in ('.pyc','.log') or skip(local):continue
            if path.is_file():copy(path,Path(rel)/local)
    def write(rel,text):register(rel).write_text(text,encoding='utf-8')
    def binary(rel,data):
        path=register(rel)
        if not path.exists() or path.read_bytes()!=data:path.write_bytes(data)
    for name,sha in HASHES.items():
        path=CACHE/name
        if not path.exists() or digest(path)!=sha:raise RuntimeError('Run desktop/fetch_runtime.py; missing or changed '+name)
    print('Assembling verified Python/runtime modules…',flush=True)
    with zipfile.ZipFile(CACHE/'python-3.13.9-embed-amd64.zip') as z:
        for info in z.infolist():
            if not info.is_dir():binary(Path('runtime')/info.filename,z.read(info))
        for info in z.infolist():
            if not info.is_dir():binary(Path('runtime/qq-sqlite')/info.filename,z.read(info))
    # QQ's shifted Windows lock-page reader pins this exact ABI. It runs only
    # in its own process; the main application's SQLite stays unchanged.
    with zipfile.ZipFile(CACHE/'sqlite-dll-win-x64-3530200.zip') as z:
        binary('runtime/qq-sqlite/sqlite3.dll',z.read('sqlite3.dll'))
    write('runtime/qq-sqlite/python313._pth','python313.zip\n.\n')
    write('runtime/python313._pth','python313.zip\n.\nLib/site-packages\n..\n../scripts\nimport site\n')
    site=ROOT/'.venv/Lib/site-packages'
    lite_deps=None
    if lite:
        from lite_dependencies import copy_dependencies
        lite_deps=copy_dependencies(ROOT,copy)
    else:tree(site,'runtime/Lib/site-packages',lambda p:p.parts[0]=='pip' or p.parts[0].startswith('pip-') or p.name in ('direct_url.json',))
    for name in (('chatlocal','web') if lite else ('chatlocal','web','harness')):tree(ROOT/name,name)
    # Only tracked scripts: omit private probes and user chats in ignored paths.
    scripts=subprocess.check_output(['git','ls-files','scripts'],cwd=ROOT,text=True).splitlines()
    source_only={'chat_adapter_fixture.py','prepare_reader_runtime.py','package_qq_source_fix.py','fix_qq_source.py',
                 'napcat_qq_probe.py','napcat_qq_fixture.py','bench_napcat_qq_fixture.py'}
    for name in scripts:
        if Path(name).suffix=='.py' and not Path(name).name.startswith(('check_','probe_')) and Path(name).name not in source_only:copy(ROOT/name,name)
    copy(ROOT/'scripts/reader_patches.py','scripts/reader_patches.py')
    copy(ROOT/'scripts/live_wechat.py','scripts/live_wechat.py')
    copy(ROOT/'scripts/qq_labels.py','scripts/qq_labels.py')
    from build_live_lock import build as build_live_lock
    copy(build_live_lock(),'_internal/qq-live-lock.dll')
    copy(ROOT/('app_mcp.py' if lite else 'app.py'),'app_mcp.py' if lite else 'app.py');copy(ROOT/'.env.example','.env.example')
    if lite:write('mcp-edition.json',json.dumps(dict(edition='mcp',version=VERSION)))
    copy(ROOT/'desktop/serve.py','desktop/serve.py')
    copy(ROOT/'desktop/diagnose.py','desktop/diagnose.py')
    for name in ('qq-reader','wechat-reader'):tree(ROOT/'tools'/name,Path('tools')/name)
    copy(ROOT/'tools/reader-manifest.json','tools/reader-manifest.json')
    probe=subprocess.run([str(out/'runtime/qq-sqlite/python.exe'),'-I','-u',str(out/'tools/qq-reader/chatlog_keeper/_qq_sqlite_helper.py'),'--runtime-probe'],capture_output=True,text=True,check=True)
    evidence=json.loads(probe.stdout)
    if not evidence.get('ok') or evidence.get('sqlite_version')!='3.53.2' or evidence.get('pending_byte')!=0x3ffffc00:raise RuntimeError('QQ isolated runtime verification failed')
    subprocess.run([sys.executable,str(ROOT/'scripts/check_reader_runtime.py'),'--runtime',str(out/'runtime/python.exe'),
                    '--proxy',str(out/'tools/qq-reader/chatlog_keeper/_qq_sqlite_proxy.py')],check=True)
    subprocess.run([str(out/'runtime/python.exe'),str(ROOT/'scripts/check_qq_history.py'),'--package',str(out)],check=True)
    subprocess.run([sys.executable,str(ROOT/'scripts/check_snapshot_recovery.py')],check=True)
    subprocess.run([sys.executable,str(ROOT/'scripts/check_live_runtime.py'),'--package',str(out)],check=True)
    subprocess.run([str(out/'runtime/python.exe'),str(ROOT/'scripts/check_qq_labels.py'),'--package',str(out)],check=True)
    subprocess.run([str(out/'runtime/python.exe'),str(ROOT/'scripts/check_mcp.py'),'--package',str(out)],check=True)
    subprocess.run([str(out/'runtime/python.exe'),str(ROOT/'scripts/check_mcp_onebot.py'),'--package',str(out)],check=True)
    subprocess.run([str(out/'runtime/python.exe'),str(ROOT/'scripts/check_mcp_actions.py'),'--package',str(out)],check=True)
    subprocess.run([str(out/'runtime/python.exe'),str(ROOT/'scripts/check_mcp_chat.py'),'--package',str(out)],check=True)
    subprocess.run([str(out/'runtime/python.exe'),str(ROOT/'scripts/check_mcp_chat_prompts.py'),'--package',str(out)],check=True)
    subprocess.run([str(out/'runtime/python.exe'),str(ROOT/'scripts/check_mcp_chat_media.py'),'--package',str(out)],check=True)
    subprocess.run([str(out/'runtime/python.exe'),str(ROOT/'scripts/check_mcp_sticker_library.py'),'--package',str(out)],check=True)
    subprocess.run([str(out/'runtime/python.exe'),str(ROOT/'scripts/check_mcp_chat_targets.py'),'--package',str(out)],check=True)
    subprocess.run([str(out/'runtime/python.exe'),str(ROOT/'scripts/check_mcp_reactions.py'),'--package',str(out)],check=True)
    subprocess.run([str(out/'runtime/python.exe'),str(ROOT/'scripts/check_mcp_chat_turns.py'),'--package',str(out)],check=True)
    subprocess.run([str(out/'runtime/python.exe'),str(ROOT/'scripts/check_mcp_language_learning.py'),'--package',str(out)],check=True)
    subprocess.run([str(out/'runtime/python.exe'),str(ROOT/'scripts/check_mcp_language_integration.py'),'--package',str(out)],check=True)
    subprocess.run([str(out/'runtime/python.exe'),str(ROOT/'scripts/check_mcp_auto_learning.py'),'--package',str(out)],check=True)
    subprocess.run([str(out/'runtime/python.exe'),str(ROOT/'scripts/check_mcp_expression_evidence.py'),'--package',str(out)],check=True)
    subprocess.run([str(out/'runtime/python.exe'),str(ROOT/'scripts/check_mcp_language_usage.py'),'--package',str(out)],check=True)
    subprocess.run([str(out/'runtime/python.exe'),str(ROOT/'scripts/check_mcp_jargon_matching.py'),'--package',str(out)],check=True)
    subprocess.run([str(out/'runtime/python.exe'),str(ROOT/'scripts/check_snowluma_managed.py'),'--package',str(out)],check=True)
    subprocess.run([str(out/'runtime/python.exe'),str(ROOT/'scripts/check_snowluma_managed_transport.py'),'--package',str(out)],check=True)
    subprocess.run([str(out/'runtime/python.exe'),str(ROOT/'scripts/check_mcp_downloads.py'),'--package',str(out)],check=True)
    subprocess.run([str(out/'runtime/python.exe'),str(ROOT/'scripts/check_upgrade.py')],check=True)
    tree(ROOT/'tools/whispercpp/Release','tools/whispercpp/Release')
    model=ROOT/'.cache/voice-models/ggml-small-q5_1.bin'
    if not lite:
        if digest(model)!='ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb':raise RuntimeError('Voice model checksum mismatch')
        copy(model,'.cache/voice-models/ggml-small-q5_1.bin')
    ocr_models={
        'ch_ppocr_mobile_v2.0_cls_mobile.onnx':'e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c',
        'PP-OCRv6_det_small.onnx':'090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f',
        'PP-OCRv6_rec_small.onnx':'6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884',
    }
    for name,sha in ([] if lite else ocr_models.items()):
        source=ROOT/'.cache/rapidocr'/name
        if digest(source)!=sha:raise RuntimeError('OCR model checksum mismatch: '+name)
        copy(source,Path('.cache/rapidocr')/name)
    print('Assembling system WebView2 loader and native runtime libraries…' if lite else 'Assembling fixed WebView2 runtime…',flush=True)
    expanded=CACHE/'webview2-expanded'
    if not list(expanded.glob('*/msedgewebview2.exe')):
        expanded.mkdir(exist_ok=True)
        subprocess.run(['expand.exe',str(CACHE/'webview2-153.0.4234.48-x64.cab'),'-F:*',str(expanded)],check=True,stdout=subprocess.DEVNULL)
    browser=next(expanded.glob('*/msedgewebview2.exe')).parent
    if not lite:tree(browser,'_internal/WebView2')
    # App-local CRT for CPU ASR / Python native extensions. Original Microsoft
    # runtime binaries come from the pinned official fixed-runtime package.
    for name in ('msvcp140.dll','msvcp140_codecvt_ids.dll','vcruntime140.dll','vcruntime140_1.dll','concrt140.dll'):
        copy(browser/name,Path('tools/whispercpp/Release')/name)
        if name not in ('vcruntime140.dll','vcruntime140_1.dll'):copy(browser/name,Path('runtime')/name)
    with zipfile.ZipFile(CACHE/'webview2-sdk-1.0.4191.47.zip') as z:
        for name in ('Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll'):
            binary('_internal/'+name,z.read('lib/net462/'+name))
        binary('WebView2Loader.dll',z.read('runtimes/win-x64/native/WebView2Loader.dll'))
        for info in z.infolist():
            if 'license' in info.filename.lower() and not info.is_dir():register('licenses/WebView2-SDK-'+Path(info.filename).name).write_bytes(z.read(info))
    icon=build_icon()
    csc=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    executable='Tulpa.QA.exe' if qa else 'Tulpa.exe'
    cmd=[str(csc),'/nologo','/target:winexe','/platform:x64','/optimize+',f'/win32icon:{icon}',f'/resource:{ROOT/"web/tulpa-logo.png"},Tulpa.Logo.png',f'/win32manifest:{ROOT/"desktop/ChatWeave.manifest"}',f'/out:{register(executable)}',
         '/r:System.dll','/r:System.Core.dll','/r:System.Drawing.dll','/r:System.Windows.Forms.dll','/r:System.Web.Extensions.dll',
         '/r:'+str(out/'_internal/Microsoft.Web.WebView2.Core.dll'),'/r:'+str(out/'_internal/Microsoft.Web.WebView2.WinForms.dll'),str(ROOT/'desktop/ChatWeave.cs')]
    if qa:cmd.insert(1,'/define:QA')
    if lite:cmd.insert(1,'/define:MCP_ONLY')
    subprocess.run(cmd,check=True)
    subprocess.run([str(csc),'/nologo','/target:winexe','/platform:x64','/optimize+',f'/win32icon:{icon}',
        f'/out:{register("Tulpa.Support.exe")}', '/r:System.dll','/r:System.Core.dll','/r:System.Drawing.dll',
        '/r:System.Windows.Forms.dll','/r:System.Web.Extensions.dll',str(ROOT/'desktop/Support.cs')],check=True)
    copy(ROOT/'desktop/ChatWeave.exe.config',executable+'.config')
    write('开始使用.txt', 'Tulpa '+VERSION+'\n\n完整解压此文件夹，双击 Tulpa.exe。无需安装 Python、Node 或运行 PowerShell。\n\n使用外部 Agent（例如 Codex）：\n1. 首次选择「连接外部 Agent」，无需模型 API Key。\n2. 在「数据与同步」登录并选择本机 QQ / 微信账号，导入需要的聊天。\n3. 打开「外部 Agent / MCP」，选择平台、会话、日期及可选权限，点击创建连接（可一并开启服务）。\n4. 点击检测连接，确认后写入本机 Codex 配置，或复制配置到其他支持 Streamable HTTP 的客户端。\n5. 在客户端重新连接 MCP。不要把 OneBot Token 当成 MCP Token。\n\n使用内置聊天：在「模型与连接」填写自己的模型 API 地址、API Key 和模型名称。\nOneBot 可独立保存并检测；MCP 与 QQ 扩展共用这份配置。SnowLuma 等接入服务仍需独立运行，本包不附带。\n\n勾选「关闭窗口后留在托盘」后，关闭窗口仍可提供 MCP 和实时读取。双击托盘图标打开，右键「彻底退出」停止服务；托盘菜单可选择开机启动（默认关闭）。不勾选时关闭窗口即退出。移动文件夹后请重新设置开机启动。\n数据和配置保存在本文件夹的 data、imports、.env 等位置。升级请先彻底退出并备份，保留个人数据；不要同时运行两个指向同一数据目录的版本。\n请放在有写入权限的本地目录，不要直接从 ZIP 内运行。Windows 10/11 x64，.NET Framework 4.8。\n本程序未签名。源码：https://github.com/fumingyang2004/Tulpa 。第三方许可见 doc/THIRD_PARTY.md，教程见 doc/MCP.md。\n')
    if lite:
        write('开始使用.txt', 'Tulpa MCP '+VERSION+'\n\n完整解压后双击 Tulpa.exe。此版只提供本机资料服务与 MCP，不含内置模型、Gradio 或 DeepSeek Harness。\n1. 在数据与同步导入 QQ / 微信资料，可开启实时读取。\n2. 在连接外部 Agent 选择范围并创建凭据，连接 Codex、DeepSeek Harness 等 Streamable HTTP 客户端。\n3. OneBot 设置与完整版本相同。发送、群管理默认关闭；勾选对应权限后 Agent 可直接执行，无需逐次审批。可撤销连接，操作记录留在本机。\n4. Windows 10/11 x64，需要系统 Microsoft Edge WebView2 Evergreen Runtime。多数系统已安装；缺少时安装微软官方运行时：https://developer.microsoft.com/microsoft-edge/webview2/ 。\n5. 主包不带语音模型，首页可一键下载约 181 MB，安装后离线转写。Office 原文件通过 download_file 下载后交给外部 Agent；不附带 Office 解析框架或本地 OCR。\n6. 此压缩包不含任何聊天或凭据。数据与完整版本各自独立；不要同时打开两个指向同一数据目录的版本。\n教程：doc/MCP_LITE.md。\n')
    with (out/'开始使用.txt').open('a',encoding='utf-8') as guide:
        guide.write('\nSnowLuma 自动配置：自行从官方页面下载并解压 Windows x64 完整版，在“连接外部 Agent → 准备资料 → 连接 QQ”选择文件夹，主动勾选协议后点击“同意并自动配置”。全新 v1.14.22 可自动初始化，已有配置可自动核对并导入；无需手抄密码、端口或 Token。本包不附带或下载 SnowLuma。完整步骤、取消与恢复说明见 doc/SNOWLUMA_MANAGED.md。\n')
        guide.write('\n从旧版本升级：先看 doc/UPGRADE.md。可将该文档交给 Codex 或 DeepSeek Harness，使用新版 scripts/upgrade_installation.py 更新原安装目录，自动保留聊天、附件、MCP 授权和实时进度，无需重新导入。升级前从托盘彻底退出 Tulpa。\n持续群聊和表情包仅向 MCP 开放：OneBot HTTP + 正向 WebSocket，按连接分别授权；完整步骤见 doc/MCP.md。\n')
    tree(ROOT/'desktop/licenses','licenses')
    for name in ('LICENSE','README.md'):
        copy(ROOT/name,name)
    docs=subprocess.check_output(['git','ls-files','doc'],cwd=ROOT,text=True).splitlines()
    for name in docs:
        if Path(name).suffix=='.md' or (name.startswith('doc/assets/') and Path(name).suffix in ('.png','.svg')):
            copy(ROOT/name,name)
    # Only aggregate versions/hashes. Never environment variables or source data.
    import importlib.metadata
    dependencies=lite_deps if lite else sorted([dict(name=d.metadata['Name'],version=d.version) for d in importlib.metadata.distributions() if d.metadata['Name']],key=lambda d:d['name'].lower())
    manifest=dict(product='Tulpa',version=VERSION,platform='windows-x64',checkpoint=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                  edition='mcp' if lite else 'full',assets=HASHES,dependencies=dependencies,files=sorted(entries),
                  file_sha256={rel:digest(out/rel) for rel in sorted(entries)})
    write('build-manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    print(f'Built {out}; {len(entries)} distributable files',flush=True)
    if not no_zip and not qa:
        archive=ROOT/'release'/f'Tulpa-{"MCP-" if lite else ""}{VERSION}-win-x64.zip'
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            for rel in sorted(entries):z.write(out/rel,'Tulpa/'+rel)
        sha=digest(archive)
        sources=archive.parent/'FFmpeg-b08d7969c5-source.tar.gz'
        shutil.copy2(CACHE/sources.name,sources)
        # A release can contain both editions; do not erase the first build's
        # checksum when building the second. Exclude archives from other commits.
        checksums={archive.name:sha}
        other=archive.parent/f'Tulpa-{"" if lite else "MCP-"}{VERSION}-win-x64.zip'
        if other.is_file():
            with zipfile.ZipFile(other) as previous:
                prior=json.loads(previous.read('Tulpa/build-manifest.json'))
            if prior.get('checkpoint')==manifest['checkpoint'] and prior.get('version')==VERSION:
                checksums[other.name]=digest(other)
        checksums[sources.name]=digest(sources)
        (archive.parent/'SHA256SUMS.txt').write_text(''.join(value+'  '+name+'\n' for name,value in sorted(checksums.items())),encoding='ascii')
        print(f'ZIP {archive.name}: {archive.stat().st_size:,} bytes; SHA256 {sha}',flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--no-zip',action='store_true');p.add_argument('--qa',action='store_true');p.add_argument('--mcp-only',action='store_true');p.add_argument('--output');args=p.parse_args();build(args.no_zip,args.qa,args.output,args.mcp_only)
