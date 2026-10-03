# Windows 桌面版与构建

## 运行

完整解压 Release ZIP 后运行 `Tulpa/Tulpa.exe`。程序启动一个仅监听 `127.0.0.1` 随机端口的服务，并使用随包 WebView2 显示界面。默认关闭窗口会停止自己的后台服务；开启托盘运行后，关闭窗口继续提供服务，通过托盘菜单「彻底退出」才停止。始终不关闭 QQ / 微信或独立 OneBot 服务。

要求 Windows 10 / 11 x64、.NET Framework 4.8、有写入权限的本地目录。当前 EXE 未做商业代码签名。首次可选内置聊天或外部 Agent；只使用 MCP 无需模型 API Key。OneBot 可独立配置，发布包不带作者的配置。

托盘菜单可显式开启当前用户开机启动（默认关闭），启动命令为当前 EXE 的 `--background`。移动目录后请重新设置；未使用 Windows 服务或要求管理员权限。

数据在 `data/`，导出文件在 `imports/`，配置在 `.env`。升级前退出程序、备份整个使用目录，将新包程序文件合并过去并保留这些个人文件。原名 ChatWeave 的内部配置键与数据库兼容，不要求重新导入。

`Tulpa.Support.exe` 是可选诊断助手，可导出脱敏摘要。临时只读连接由用户主动开启，十分钟有效，只提供诊断摘要；它不能执行命令或读取聊天。排障信息仍应在提交前检查。

## 源码环境

使用 Python 3.13 x64，在仓库根目录执行：

```powershell
.\setup.ps1
.\.venv\Scripts\python.exe -m pip install -r requirements-media.txt -r requirements-voice.txt
.\.venv\Scripts\python.exe scripts/setup_voice.py
.\start.ps1
```

普通页面是 <http://127.0.0.1:7860>；桌面样式为 <http://127.0.0.1:7860/?desktop=1>；兼容界面为 `/legacy`。开发入口使用仓库内的数据，便携包使用其自身目录的数据，两者不会自动共享。

可让桌面壳连接已启动的开发服务：

```powershell
.\release\0.4.0\Tulpa\Tulpa.exe --connect http://127.0.0.1:7860
```

连接模式仅允许本机 HTTP，关闭桌面窗口不会关闭外部开发服务。

## 构建便携包

除了上述环境，还需要 Visual Studio Build Tools 的 C++ x64 工具链，以及 Windows 自带的 .NET Framework C# 编译器。

```powershell
.\.venv\Scripts\python.exe desktop/fetch_runtime.py
.\.venv\Scripts\python.exe scripts/setup_voice.py
.\.venv\Scripts\python.exe -c "from rapidocr import RapidOCR; RapidOCR(params={'Global.model_root_dir': '.cache/rapidocr'})"
# 检查并提交源码后再构建
.\.venv\Scripts\python.exe desktop/build.py
.\.venv\Scripts\python.exe scripts/check_publication.py --archive release/Tulpa-0.4.0-win-x64.zip
```

OCR 初始化只下载固定依赖版本的默认模型。构建会校验三份 OCR 模型、语音模型、官方 Python / WebView2 / SQLite 下载的 SHA256，哈希不匹配时停止。

输出：`release/0.4.0/Tulpa/`、`release/Tulpa-0.4.0-win-x64.zip` 和 `release/SHA256SUMS.txt`。`--no-zip` 只生成目录；`--qa --no-zip` 用于本机测试构建，不可作为正式 Release。

构建使用固定组件、已跟踪脚本和文件清单，并用随包 Python 运行真实 HTTP MCP 隔离回归，检查协议依赖及范围控制。ZIP 根据本次清单逐项生成，禁止把使用过的程序目录整体压缩上传。`build-manifest.json` 记录版本、源码提交、依赖和文件列表。正式包不包含 QA 调试端点、用户配置、数据库、私人报告、旧预览包或 SnowLuma 服务。

## 发布验证

0.5.0 起可用 `python desktop/build.py --mcp-only` 构建独立轻量版，输出 `release/0.5.0-mcp/Tulpa/` 与 `release/Tulpa-MCP-0.5.0-win-x64.zip`。轻量版使用系统 WebView2，移除内置 Harness、Gradio、OCR 和 Office 解析依赖；语音模型按需安装。使用与验收见 [MCP_LITE.md](MCP_LITE.md)。

按 [VALIDATION.md](VALIDATION.md) 运行隔离回归，并在空白目录启动实际 EXE。检查首次配置、Logo、数据入口、关闭后的服务回收及压缩包哈希。一次本机启动成功不代表所有 QQ / 微信版本均兼容。
