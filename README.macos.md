# Tulpa · macOS（Intel 预览）

CLI / TUI 与 Cocoa 桌面窗口共用一个本机后台、账号库及 MCP 权限。关闭窗口或退出 TUI 后，后台继续运行；`./tulpa stop` 显式停止后台。

## 安装和启动

需要 macOS 13+、uv 及 Xcode Command Line Tools。安装脚本通过 uv 准备 Python 3.13，支持目录中有空格。先将源码放到最终目录，再运行：

```sh
./setup-macos.sh
./tulpa                  # 自动启动后台并打开 TUI
./start-macos.sh          # 构建并打开 Cocoa 窗口
./start-macos.sh --verify # 同时检查实际窗口和页面已渲染
```

Python、安装缓存、编译工具及运行数据保存在项目目录。Intel 上 cryptography 49 起没有官方二进制包；安装脚本使用独立 Rust 与静态 OpenSSL 4.0.3 编译项目固定版本，不修改系统 Python。

桌面启动器位于 `~/Applications/Tulpa.app`，引用当前源码和 `.venv`，并非独立可搬运应用。搬动项目后请重建虚拟环境、后台服务和启动器。后台日志在 `.tmp/terminal-service.log`，窗口日志在 `.tmp/macos-launch.log`。

「设置 → 外观」可以选择跟随系统、浅色或深色（夜间）；侧栏底部可以快速切换浅色和深色。Mac 窗口和标题栏同步切换；选择保存在项目的 `data/macos-appearance.json`，关闭再打开仍有效。普通浏览器窗口单独保存自己的外观选择。

安装用户登录后自动启动的后台：

```sh
./tulpa service install --start
./tulpa service status
./tulpa service restart
./tulpa service uninstall
```

macOS 使用 launchd；不同目录的实例使用独立服务名称。普通窗口启动可复用已有后台，不重复启动 MCP。服务只监听本机，桌面窗口通过一次性凭证获取 HttpOnly 会话，永久管理凭据不放入地址栏。

## SL / QQ

SnowLuma 没有原生 macOS 包。先准备 Colima 或 Docker Desktop 的 Linux 虚拟机，然后在 TUI「SL 托管 → 检测 Docker」或桌面「设置 → SL Docker」中检测、阅读协议并启动。首次登录需用户扫码。

桌面「打开扫码登录」会打开系统浏览器中的 VNC 页面，密码默认隐藏在 Tulpa 的登录面板内。面板提供「在浏览器中打开 QQ 桌面」和「复制地址」；启动浏览器失败时保留地址、密码及错误提示，可重试或手动粘贴地址打开。普通浏览器需要点击该链接打开页面。

本机部署采用 Colima 的独立 `tulpa` profile（Intel x86_64、2 CPU、3 GiB 内存、25 GiB 数据盘）。不切换现有 Docker context，不挂载个人主目录。Tulpa 自动识别 `~/.colima/tulpa/docker.sock`，同时支持默认 Colima 和 Docker Desktop 本机 socket。

```sh
./macos/install_container_tools.sh # 首次安装官方 Intel 运行工具
./macos/colima.sh start
./macos/colima.sh status
./macos/colima.sh stop
```

容器镜像、账号及聊天数据保存在 VM 持久盘；停止托管不会删除数据。机械盘部署可将 `~/.colima` 和 Colima 下载缓存迁移后用软链接保留原路径；必须先停止虚拟机。请勿在 VM 运行时移动其磁盘。

在「数据与同步 → 读取聊天 → QQ 历史来源」中选择自动、本机数据库或 SL Docker；只有点击保存才会改源。Mac 已有原生 QQ 数据库时，自动模式可能继续使用本机数据库，使用 SL 历史请明确选择 SL Docker。来源切换会重置旧的实时读取器，应先按范围补读历史，再重新开启实时摄取。

SL 历史读取、私聊/群聊、图片等权限和减少重复人设的会话协议沿用 Linux 后端。选择 SL 历史源后不要求寻找宿主 macOS 的 QQ 数据库；只能读取 QQ / SL 实际提供的历史。

## 本机数据库与可选组件

QQ / 微信解析器只读本机数据库。已有有效密钥可用于历史及增量读取。macOS 可能显示访问其他应用数据的权限提示，需要用户在本机处理。

主动恢复密钥的隔离副本流程和微信版本限制见 [macos/README.md](macos/README.md)。当前微信恢复策略只接受已校验的 4.1.13 build269602；4.1.15 不会跳过检查或宣称可主动取钥。SL 容器登录不依赖该取钥流程。

OCR、Office、语音是按需安装组件；是否能安装取决于对应依赖对 Intel macOS 的支持。默认安装完成不代表这些附加组件已验证。

## 验证与分发

```sh
.venv/bin/python scripts/check_macos_backend.py
.venv/bin/python scripts/check_macos_ui_bridge.py
.venv/bin/python macos/check_readers.py
.venv/bin/python scripts/check_wechat_macos.py
.venv/bin/python macos/check_pending_mcp.py
.venv/bin/python macos/check_processes.py
```

图形前端由 Claude Code（DeepSeek deepseek-flash[1M]）完整重构，源码位于 `web/desktop/`，采用原生 JavaScript 模块与 Hash 路由，无需安装 Node/npm 才能运行。界面包括工作台、对话、聊天记录、关注、资料、证据集合、记忆、工作区、数据与同步、外部 Agent、设置。完整界面共用 `web/desktop/`，轻量 MCP 界面继续使用 `web/` 中的组件。后端集成与实机验收由 Codex（GPT-6）完成；macOS 原生窗口及 QQ/微信读取基础沿用 thats-ok-apple 的 PR #5。前端开发检查见 [tests/frontend/README.md](tests/frontend/README.md)。

开发与验收记录见 `macos/ACCEPTANCE.md`，前端实现说明见 [doc/FRONTEND.md](doc/FRONTEND.md)。只分发公开源码，不能包含 `.env`、`data/`、`.runtime/`、`.cache/`、`.tmp/` 或登录凭据。此预览启动器没有 Apple 签名和公证。
