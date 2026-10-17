# Linux 终端版：Tulpa + SnowLuma

终端版保留 Tulpa 的 MCP、人格卡、持续群聊与好友私聊、回复队列、语言学习和资料索引。
SnowLuma（SL）作为独立的 QQ 接入服务，通过 OneBot HTTP 与正向 WebSocket 连接。
交互前端由 Claude Code 实现，Python 后端与 Linux 适配由 Codex 实现。

## 环境与安装

使用 Python 3.13。当前本机验证环境为 Arch Linux ARM64；依赖放在项目的
`.venv/`，不修改系统 Python。已有 Python 3.13 或 uv 时，只需执行：

```sh
./tulpa
```

首次运行会自动创建虚拟环境、安装依赖、启动后台并打开终端界面；安装中断后可重新
运行同一命令继续。以后会检查运行依赖并复用已运行的后台，不需要开两个终端。
只安装依赖而不启动时，仍可运行 `./setup-linux.sh`。
只需要可脚本化 CLI 时，可在项目虚拟环境安装
`requirements/linux.txt`，省略 `requirements/tui.txt`。

Linux 发行包以 `./tulpa` 为统一入口。终端服务提供原版完整业务 API，
并带有独立的模型对话入口；无需 Gradio。
模型只在提交问题、生成草稿或启用对应自动任务时调用。

安装全部可选组件（原生读取、OCR、Office、CPU 语音程序和约 190 MB 模型）：

```sh
./setup-linux.sh --full
```

如果没有 C/C++ 编译器，语音安装器在虚拟环境内安装固定版本的 Zig、CMake、Ninja，
构建固定提交的 whisper.cpp。无需修改系统软件；源压缩包与模型均检查 SHA-256。
OCR 使用 `opencv-python-headless`，无需 X11/Qt。uv 安装会排除同名桌面 cv2
提供包；pip 安装之后，OCR 准备脚本会移除冲突包并重新安装唯一的 headless 提供包。
RapidOCR 的发行元数据仍声明桌面包，因此直接执行 `pip check` 可能提示该包未安装；
实际 `cv2` 导入和 OCR 推理由组件验收检查。不要混装多个 OpenCV 发行包。

## 启动

日常启动使用同一个入口：

```sh
./tulpa
```

后台未运行时自动启动，日志位于 `.tmp/terminal-service.log`；若已安装本实例的
systemd 服务，则启动原服务，不会重复创建后台。退出 TUI 不会关闭后台，因此重新
打开界面不会重启 QQ。同一项目目录只允许一个终端服务，并发打开也会复用同一个后台。
端口被其他应用或其他实例占用时会明确报错，不会连接或停止对方。

只启动后台或停止本目录后台：

```sh
./tulpa start
./tulpa stop
```

`./tulpa serve` 保留为前台服务入口，适合 tmux 或进程管理器；Ctrl+C 可正常关闭。
停止后台也会停止本实例托管的 SL；关闭 TUI 则不会。

界面共 19 页，涵盖概览、实例管理、SL 连接与托管、MCP 授权、人设、群聊、搜索、工具调用、
Agent 对话、聊天记录、本机数据、记忆、关注卡、材料、集合、群资料、工作区及组件服务。
按 `1`–`7` 切换原页面，`8` 打开模型对话，`9` 打开聊天记录，`0` 打开本机数据；
其余页面通过可滚动侧栏进入。`Tab` 切换焦点，`Esc` 返回菜单，`r` 刷新，`q` 退出。
页面底部显示当前可用快捷键；输入框内可正常输入文字。
完整功能使用扩展侧栏入口；原 1–7 快捷键保留。CLI 也可操作模型、原生来源、
组件任务和完整业务 API。

## 同时运行多个 QQ / SL 实例

在主目录运行 `./tulpa`，按 `F2` 或选择侧栏「实例管理」。新建实例，填写 ID
（例如 `deepseek`、`claude`）和显示名称，再启动并切换。顶部显示当前实例名称；
切换后所有 SL 托管、MCP 授权、人设、会话和模型设置都属于所选实例。

每个实例在「SL 托管」分别启用 Docker 托管、确认协议、扫码登录自己的 QQ；
随后在「MCP 授权」启用 MCP 并创建连接，将对应的 MCP 地址和 Token 交给相应 Agent。
例如白龙娘、大肥鱼、Claude 大小姐各使用一个 QQ、一个 Tulpa 实例和一份 MCP 授权。
创建实例不会自动启动模型；外部 Agent 仍须在自己的客户端连接 MCP。

默认分配如下；端口被占用时顺延，也可在创建时指定：

| 实例 | 管理端口 | MCP 端口 |
| --- | --- | --- |
| 原主实例 | 保留原配置（通常 7861） | 保留原配置（通常 18777） |
| 新实例 1 | 7862 | 18778 |
| 新实例 2 | 7863 | 18779 |

SL 的 WebUI、OneBot HTTP/WS、noVNC 端口另行自动分配。容器名、Docker 数据卷、
管理 Token、SL/VNC 密码、数据库、聊天记录、人格与模型配置均各自独立。
新实例的 MCP 与 SL 初始关闭，未复制主实例的配置或授权。

CLI 等价操作（在主目录执行）：

```sh
./tulpa instances create deepseek --name 大肥鱼
./tulpa instances create claude --name Claude大小姐
./tulpa instances start deepseek
./tulpa instances start claude
./tulpa instances list
./tulpa instances rename default --name 白龙娘
./tulpa instances stop claude
```

实例位于 `instances/<ID>/`，共享主目录的 `.venv` 依赖，但配置和运行数据独立；
在该目录运行 `./tulpa status`、`./tulpa mcp enable` 等命令，会使用它自己的端口。
在子目录直接运行 `./tulpa` 也会打开同一个主界面，并自动选中该实例。
子实例 MCP 使用创建时预留的端口，避免占用其他实例；`instances/` 不进入 Git 和发行包。
升级主目录代码后，在「实例管理」停止并重新启动子实例，会同步公开源码并保留其数据。
同步仅在子实例停止时执行；复用正在运行的实例不会更新源码或重启 QQ。
各实例的人格卡位于自己的 `chatlocal/prompts/mcp_chat/`；已有的人格 Markdown 在同步时保留，
不会覆盖该实例的人设修改（通用行为规则 `behavior.md` 随程序更新）。

关闭终端界面不会停止任何后台。停止某个子实例会同时停止它托管的 SL，其他实例继续运行。
子实例离线时仍可按 `F2` 返回实例管理。可用的用户 systemd 会为每个子实例建立独立服务，
主实例重启不影响它们；此操作默认不启用开机启动。需要随用户登录启动时，进入子目录执行
`./tulpa service install`。没有用户 systemd 时使用独立后台进程；若主实例由其他进程管理器
运行，应确认其停止策略不会同时结束派生的子进程。

多个实例仍运行在同一 Linux 用户下，这是账号与数据的逻辑隔离，并非操作系统安全沙箱。

隐藏图片彩蛋：在普通页面依次按 `↑ ↑ ↓ ↓ ← → ← → b a b a`，
会并排显示两张插画，`1.png` 在左、`2.png` 在右；按 `Esc` 返回原页面。
输入框和其他弹窗内不识别此序列。图片随源码包保存在 `web/easter-egg/`，
运行时直接读取本地资源。支持图片协议的终端显示像素图片，其余终端显示彩色字符预览；
实际效果取决于终端及 SSH/tmux 链路，参见 [textual-image 兼容说明](https://github.com/lnqs/textual-image#supported-terminals)。

默认管理地址 `http://127.0.0.1:7861`。新实例需要使用其他端口时：

```sh
./tulpa --url http://127.0.0.1:7862
```

管理接口凭据自动创建于 `data/terminal-admin.token`，Unix 文件权限为 600。
TUI/CLI 在本机读取它；它与 SL 节点 Token、MCP 连接 Token 各自独立。
`--token-file` 或 `TULPA_TOKEN_FILE` 可选择明确的凭据文件，`TULPA_URL` 可指定地址。
接口仅接受本机连接；SSH 使用时，在远端终端运行 TUI。
已有用户服务时使用安装该服务时选择的端口；停止自选端口后台时也需传同一 `--url`。

用户级常驻服务与桌面终端入口：

```sh
./tulpa service install --start
./tulpa service status
./tulpa service restart
./tulpa desktop-install
```

主实例服务安装到 `~/.config/systemd/user/tulpa.service`，子实例使用
`tulpa-<ID>-<路径摘要>.service`，不会覆盖主服务。手动 `service install` 后登录时自动启动；是否在注销后
继续运行取决于系统已有的 user lingering 设置。`service uninstall` 停止并删除
本实例服务，保留聊天数据。含空格的项目路径可用，迁移后重新安装服务即可。

## 连接已有 SL

独立启动适合本机架构的 SnowLuma，并在它的界面完成 QQ 登录。
为同一账号开启 HTTP 服务和正向 WebSocket 服务（Event 或 Universal）。
通过 TUI 的 SL 连接页面，或者 `./tulpa setup` 输入实际地址与各自 Token：

```sh
./tulpa setup
./tulpa sl test
./tulpa groups
```

地址必须能从 Tulpa 通过 localhost 访问。SL 在容器内运行时，把这两个节点
映射到宿主机的回环地址。管理界面的端口不是 OneBot API 端口。
HTTP 和 WS 可以使用不同 Token；空 Token 输入保留原值。
保存配置不会启动或重配 SL；改变连接设置会停止已有群聊，待核对账号后重新开始。
“检测连接”只检查身份与事件通道，不发送消息。

也可使用完整前端中的 SL 托管设置：选择本机官方 v1.14.22 完整包，核对目录和
协议后授权初始化。支持 Linux x64/ARM64，必须与本机架构相符。Tulpa 不分发 SL。
托管保留原版的协议确认、账号核对、实例归属、配置备份和已有实例保护。
Linux 凭据以 AES-GCM 保存，本机密钥与密文文件均为 600；备份时两者一起保存。
SL 自己使用的 Token 仍存于其配置目录；该目录设置为仅当前用户可访问。

### 无桌面环境：Docker 托管

侧栏的「SL 托管」位于「SL 连接」旁；也可在「SL 连接」页点击「SL Docker 托管」。
从侧栏按 Enter 进入正文后，顶部提供可点击的启停、扫码和日志按钮：

1. 点击「启用 Docker 托管」（或按 `d` 检测），阅读本次显示的 SnowLuma 协议和授权范围，勾选后启动。
2. 等待固定版本镜像和 QQ 启动。点击「扫码登录」（或按 `b`）查看 noVNC 地址和密码，在浏览器中打开
   虚拟桌面，用手机 QQ 扫码。远程服务器按弹窗给出的 `ssh -N -L ...` 命令转发端口。
3. 登录后 Tulpa 自动配置 HTTP/正向 WS，核对唯一账号和实时事件，显示「QQ 已就绪」。
4. 点击「查看日志」读取脱敏日志，点击「停止 Docker 托管」停止容器；再次点击「启用 Docker 托管」
   恢复原实例。快捷键 `l` / `x` / `t` 仍可用；`R` 撤销授权。

更新终端前端代码后，需要按 `q` 退出并重新运行 `./tulpa` 才能载入新界面；`r` 只刷新服务状态。
退出终端界面不会停止后台服务。

停止和撤销均保留三个命名卷（SL 配置、QQ 配置、QQ 数据），不会删除登录状态。
再次登录是否需要扫码由 QQ 决定。托管只恢复原绑定账号，出现其他账号会停止访问。
宿主机的四个端口均绑定 `127.0.0.1`，自动分配以避免占用常用端口；不发布原生 VNC 5900。
密码只在主动打开登录弹窗时读取，不在状态和日志中显示。
托管会关闭虚拟桌面的空闲屏保，并在打开扫码入口时唤醒画面，避免 VNC 已连接但桌面全黑。
扫码链接自带 `?resize=scale`，使整个桌面适应浏览器窗口。手机打开时请保留这个参数；
旧的无参数链接可能只显示桌面左上角的黑色背景，QQ 窗口位于屏幕外。
重新点击「扫码登录」即可获取新链接，手机横屏可放大显示。

镜像为官方 `motricseven7/snowluma:v1.14.22`，按本机 `linux/arm64` 或 `linux/amd64`
使用 manifest 中固定的 SHA256 摘要。镜像包含 QQ、虚拟显示和 noVNC；Tulpa 源码包不包含镜像。
Docker 需由操作者安装、启动并授予当前用户访问权限。支持普通 Docker 与 rootless Docker；
优先使用 `/run/user/<uid>/docker.sock`，否则使用 `/var/run/docker.sock`。
显式 `DOCKER_HOST` 只接受本机 `unix:///...`，不连接远程 Docker context。

Android 宿主下的 Arch/Droidspaces 可能共享 Android 防火墙与策略路由，普通 Docker 桥接
出网会被 Android 的转发规则拦截。这种环境建议使用 rootless Docker 的用户态网络，
并先核对 `newuidmap/newgidmap`、subuid/subgid 和用户服务是否可用。
本机环境与验证结果见 [DOCKER_ACCEPTANCE.md](DOCKER_ACCEPTANCE.md)。

## MCP 授权与人格聊天

在界面中启用 MCP（默认端口 18777），选择明确的群、好友私聊与权限，再创建连接。
新连接的凭据仅在创建时返回；在弹窗中保存配置，或使用 CLI 输出到新文件：

```sh
./tulpa mcp enable
./tulpa connections create --name my-agent --group ACCOUNT:group:GROUP \
  --chat --send --onebot --output ./my-agent-mcp.json
```

把 ACCOUNT 与 GROUP 替换为 `tulpa groups` 的实际编号。
示例明确开启持续群聊和发送；图片、表情发送/收藏、回应和群管理由用户另行选择。
`--output` 以 600 权限新建文件，不覆盖已有文件。这个文件含新连接 Token，请妥善保存。
JSON 配置示例使用 Claude Code 的 `type: http` 格式；合并其中的 `tulpa` 条目，
保留客户端已有配置。其他支持 Streamable HTTP MCP 的 Agent 使用相同地址和
Bearer 凭据，按各自的字段格式配置。

新建连接表单还提供语音、视频、文件、消息操作、互动、好友管理、个人资料、空间和群相册的
独立开关，默认关闭。好友管理、个人资料、空间是账号级权限，不局限于所选会话。
完整说明及文件发送示例见 [SL_TOOLS.md](SL_TOOLS.md)。旧连接保持原授权；
要启用新增权限，在「MCP 授权」列表选中连接，按 `e` 修改，`Ctrl+S` 保存。
原凭证继续有效，无需替换 Agent 配置；权限变化会停止该连接的旧会话，需要重新开始聊天。
也可用 `./tulpa connections edit CONNECTION_ID --chat-voice` 开启权限，
或 `--no-qq-qzone` 关闭权限，未指定的开关保持原值。

可在 TUI 选择连接、群或好友，以及人格来准备会话，或者：

```sh
./tulpa personas
./tulpa sessions start --connection CONNECTION_ID --group ACCOUNT:group:GROUP \
  --persona little_whale_v2
./tulpa sessions list
```

私聊使用 `./tulpa friends` 返回的完整编号，命令中将 `--group ACCOUNT:group:GROUP`
替换为 `--conversation ACCOUNT:direct:FRIEND`；新建连接时也须选中该好友。

准备会话后显示“等待 Agent”。外部 Agent 需要连接 MCP 并持续执行聊天工具；
此流程不会自行启动外部 Agent。人格快照、回复规划、发送幂等、表情与学习仍由原有
Tulpa 核心执行。停止和学习控制：

```sh
./tulpa sessions learning SESSION_ID --enabled no
./tulpa sessions stop SESSION_ID
./tulpa connections revoke CONNECTION_ID
```

停止会话会停止该会话的持续聊天；连接中单独授予的发送等权限仍然有效。
撤销授权会停止该连接的会话和后续调用。每个新群的授权仍需明确建立。

## 导入、检索与脚本

将统一格式 JSON/JSONL 放到本实例的 `imports/` 后导入：

```sh
./tulpa import imports/messages.json --platform qq
./tulpa search '课程设计' --limit 20
./tulpa --json status
./tulpa --json events
```

CLI 的全局参数（`--json`、`--url`、`--token-file`）位于子命令之前。
成功返回 0，操作失败返回 1，终端中断返回 130；错误输出到 stderr。
`python -m chatlocal` 原有导入/检索入口保持可用。

## QQ / 微信聊天数据

### SL Docker 托管的 QQ

QQ 运行在 Tulpa 托管的 SL Docker 容器时，进入「本机数据」，刷新后可看到
SL 绑定的 QQ 账号，来源显示「SL Docker 历史消息」。首次使用时登录 QQ，然后选择
账号、读取会话目录、勾选群或好友、设置日期及每会话条数，执行历史导入。
这条路径通过 SL 的历史接口读取，无需配置宿主机 QQ 路径、安装原生读取器或提取数据库密钥。
“我的电脑／手机／Pad”属于文件传输设备，不出现在聊天记录目录中。

未设置本机 QQ 路径且未发现宿主机 QQ 数据库时，自动选择 Docker 来源；
已有本机路径设置会保留。需要切换时，按 `p` 打开数据来源设置，明确选择
「SL Docker 历史消息」或「本机数据库」。切换来源后，先按范围导入，再开启监听。

导入后可按 `s` 增量同步；重复消息按会话和 SL 消息编号去重。
开启监听后，新消息通过 OneBot 进入本地索引，关闭 TUI 不影响后台监听。
启动前或断线期间的记录需要增量同步补读；监听提示不代表历史完整。
此数据监听只负责入库，不会启动 MCP 人格回复。
监听期间新加的好友需关闭再开启数据监听，以刷新好友目录；缺失时段可增量补读。

历史范围由 SL / QQ 当时能够返回的记录决定，不能保证包含全部云端或已删除记录。
图片可尝试缓存和补载；过期图片保留占位。语音、视频、文件、卡片和合并转发当前记录
类型占位或文件名，不通过这条路径下载正文或转写。未登录时界面提示恢复 SL 登录，
不会将容器内数据误报为“本机没有 QQ 聊天记录”。

### 本机 QQ / 微信数据库

Linux 读取器使用固定提交的 chatlog-keeper 目录发现及取钥实现，复用 Tulpa 的
快照、WAL 验证、增量游标、分批入库和媒体恢复。支持官方原生客户端的数据布局，
也可显式指定 XDG/Flatpak 或自定义路径。多个账号必须明确选择，不自动换号。

```sh
./tulpa native install
./tulpa native configure qq /home/YOU/.config/QQ
./tulpa native configure wechat /home/YOU/Documents/xwechat_files --keys-file /home/YOU/wechat-keys.json
./tulpa native extract qq --account ACCOUNT --method passive
```

密钥文件必须为当前用户所有、权限 600 的 JSON。QQ 支持
`{"ACCOUNT":"16或32字符口令"}`；微信支持 `{"master_key":"64位十六进制"}` 或
`{"message/message_0.db":{"enc_key":"64位十六进制"}}`，也可再按账号嵌套。
微信图片可提供 `image_key`（16 字符）。所有数据库密钥都需通过当前源数据库
页 HMAC 验证，不会仅因为 JSON 格式正确而接受。

配置路径变更会关闭该平台实时监听。先在聊天数据页选择账号读取历史，形成新基线，
再显式开启实时监听。实时同步和历史读取保持独立范围，不绕过系统内存读取权限。

Linux 自动启动取钥仅开放上游已验证的 x86_64 路径；部分微信版本需要带 Python
支持的 GDB。该操作须正常退出原客户端，再明确使用
`native extract PLATFORM --method active --confirm-start-client`。
ARM64 可使用已有密钥、缓存及操作系统允许的被动读取；不能保证任意版本自动取钥。

## 模型及完整业务 CLI

```sh
./tulpa model configure --base https://api.deepseek.com --model deepseek-flash --key-stdin
./tulpa agent create
./tulpa agent ask SESSION_ID '最近讨论了什么？'
./tulpa agent history SESSION_ID
./tulpa agent stop TURN_ID
./tulpa components status
./tulpa components install voice
./tulpa components job JOB_ID
./tulpa api GET /api/workspaces
./tulpa api POST /api/collections --body-file request.json
```

`--key-stdin` 在交互终端使用隐藏输入，脚本可通过标准输入传入；不将密钥写在命令行。
回复发送仍必须使用原版预览令牌并确认；记忆编辑、workspace 提案应用保留版本与审批。
`api` 命令可操作文档中的完整原生接口；`download /api/... NEW_FILE` 保存为新的 600 文件。

`./tulpa package /path/to/Tulpa-linux-clean.tar.gz` 创建可重建源码包，包含运行代码、
提示词、前端、固定依赖、Linux 安装脚本、文档和许可，不包含 `.env`、账号数据、
工具下载、缓存或其他平台的启动及构建文件。详见 [开发与打包](LINUX_CONTRIBUTING.md)。

## 当前范围

- Linux 同时支持已有 SL 连接与官方 Linux 完整包托管。
- 检索覆盖已导入的索引。OneBot 实时群聊不代表自动获得全部历史聊天。
- 原生客户端版本、登录状态、已有数据库密钥及系统读取权限决定真实数据是否可读。
- 图片、文件、表情和学习沿用原核心；OCR、Office、语音可独立安装，缺失时报告真实状态。
- 本地测试使用合成聊天及隔离 OneBot HTTP/WS；真实账号登录稳定性、真实发送和
  长时间群聊需在指定账号/测试群另外验收。

## 验证

```sh
.venv/bin/python scripts/check_linux_full.py
.venv/bin/python scripts/check_linux_suite.py
.venv/bin/python scripts/check_linux_components.py
```

测试不会调用付费模型或向真实 QQ 发消息。
`check_linux_suite.py` 汇总适用于 Linux 终端的回归；当前清单见脚本，单项日志位于
`.tmp/linux-acceptance/`。组件检查需要先执行完整安装，使用合成文档、图片及静音音频。
