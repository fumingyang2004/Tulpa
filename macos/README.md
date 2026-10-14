# Tulpa macOS 预览版

macOS 源码运行版使用 Cocoa 原生窗口和本机 Python 服务，支持 QQ、微信本地聊天导入、检索、增量监听及外部 Agent MCP。Windows 安装方式保持不变。

## 安装与启动

需要 macOS 12 或以上、Git 和 uv。当前已在 Apple Silicon、Python 3.13 环境验证；Intel Mac 尚未实机验证。

```sh
git clone https://github.com/fumingyang2004/Tulpa.git
cd Tulpa
./setup-macos.sh
./start-macos.sh
```

首次安装联网下载固定版本依赖及带许可证的只读数据库模块。可在界面导入 JSON/JSONL 资料；直接读取客户端需要有效的本机账号数据库密钥。

可选创建应用启动器：

```sh
.venv/bin/python macos/build_app.py "$HOME/Applications/Tulpa.app"
```

目标目录须不存在。这个 `.app` 引用当前项目的源码和 `.venv`，请保留项目目录；它不是独立的可移植安装包，也未进行 Apple 公证。更新源码后正常退出并重开应用。圆角透明图标由 `macos/assets/Tulpa.icns` 提供。

## QQ / 微信本机读取

解析器只读本机数据库，密钥缓存按账号绑定并重新验证；实时读取不自动探测进程密钥或切换账号。客户端版本、权限和本机缓存会影响可用性。普通群聊与私聊解析不代表所有公众号、服务会话或特殊原始消息都已支持；缺失附件无法从消息占位恢复。

macOS 若显示 Python 访问“文稿”或“其他 App 数据”的系统提示，需要用户决定是否允许。应用数据权限与文稿权限不同；拒绝或未处理可能造成账号目录检测超时。无需关闭 SIP。

如需从自己账号恢复密钥，先单独安装固定源码：

```sh
.venv/bin/python macos/install_recovery_source.py
.venv/bin/python macos/reader_recovery.py
```

第二条命令仅检查已安装客户端签名。恢复过程必须另行明确启动，并在退出原客户端后使用隔离副本；用户自行完成官方登录，不修改原客户端，不关闭 SIP。`--recover-qq` 与 `--recover-wechat` 会启动该流程，`--preflight-wechat` 检查版本策略并编译辅助组件。当前微信恢复策略仅接受已校验的官方 4.1.13 build269602，其他版本默认拒绝。恢复组件源自 [chatlog-keeper](https://github.com/labazhou2024/chatlog-keeper/tree/2225294fd0761e10ce15540f21e7dbb2ac304c3c)，固定清单与 SHA-256 验证，保留上游 MIT 许可证。

“数据与同步 → 实时摄取”可独立关闭“读取微信图片”。关闭后历史读取、手动/定时刷新和实时监听均不解码图片、探测图片密钥、遍历附件或重试图片；保留文字、文件信息、引用及图片占位，已有缓存保留。关闭表情包开关本身不等于关闭普通图片。

## 连接外部 Agent

在“外部 Agent / MCP”中选择平台、会话、时间与所需权限，创建连接并使用界面的客户端配置。可在资料首次导入前明确授权全部会话的只读连接；首次导入后固定来源账号，之后账号变化仍须重新授权。发送及群管理权限由用户另外开启。

保持 Tulpa 运行，在已加载连接的 Codex 新聊天中说“用 Tulpa MCP 查询我的聊天……”即可。关闭 Tulpa 会停止本机服务，当前没有菜单栏后台模式或开机启动。外部模型、语音/OCR/Office 可选组件及 OneBot 需要另外配置。

## 验证

合成加密数据库覆盖 Mac QQ 大文件、溢出页、WAL、只读约束、源变化及损坏页拒绝；微信测试覆盖账号隔离、缓存验证、增量游标和关闭图片处理。原生桌面、真实本机 QQ/微信导入、文字增量监听及 Codex MCP 查询在 0.5.2 基础上验证；当前源码已适配 0.5.5，并在该版本重新运行隔离检查。未验证 Intel Mac 和独立可分发安装包。

```sh
.venv/bin/python macos/check_readers.py
.venv/bin/python scripts/check_wechat_macos.py
.venv/bin/python scripts/check_wechat_no_images.py
.venv/bin/python scripts/check_wechat_read_preferences.py
.venv/bin/python macos/check_pending_mcp.py
node macos/check_ui.cjs
```

所有聊天资料、账号密钥、连接凭据及临时客户端均留在被 Git 忽略的本机目录，不能随源码提交。
