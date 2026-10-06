![Tulpa](doc/assets/title.png)

# Tulpa

**在本机查询 QQ / 微信聊天，整理资料、持续调查，并起草符合当前语境的回复。**

[下载 Windows 版](https://github.com/fumingyang2004/Tulpa/releases/latest) · [功能清单](doc/FEATURES.md) · [OneBot 配置教程](doc/SNOWLUMA_SETUP.md) · [参与开发](doc/CONTRIBUTING.md)

Tulpa 是面向 Windows 的个人通信工作台。聊天和资料保存在本机，Agent 在你选择的平台、会话和时间范围内检索，按需读取图片、语音和文件。桌面版解压即用；浏览器入口保留用于开发与测试。

**MCP 轻量版：把 QQ / 微信资料接入你正在使用的 Agent。**

如果你已经在使用 Codex、DeepSeek Harness、Antigravity 或 WorkBuddy，可以选择 MCP 轻量版。Tulpa 负责导入与实时读取、检索、原文件下载和访问授权，外部 Agent 负责理解、分析与执行任务。轻量版不包含内置 Harness，无需在 Tulpa 中另配模型 API Key；模型由外部 Agent 提供。完整版也包含同样的 MCP 能力。

目前已在以下客户端完成实际接入测试：

| Agent | 已实测能力 |
| --- | --- |
| <img src="doc/assets/agents/openai.png" width="28" height="28" alt="OpenAI"> **[Codex](https://openai.com/codex/)** | QQ / 微信查询、图片读取、文件下载、PDF 与语音内容读取；授权测试群发言、修改群名并恢复 |
| <img src="doc/assets/agents/deepseek.png" width="28" height="28" alt="DeepSeek"> **[DeepSeek Harness](doc/DEEPSEEK_MCP.md)** | QQ / 微信查询、图片与文件读取、OneBot 群资料；授权测试群发言、修改群名并恢复 |
| <img src="doc/assets/agents/antigravity.svg" width="28" height="28" alt="Google Antigravity"> **[Google Antigravity](https://antigravity.google/)** | 工具发现、会话与消息查询、图片理解；本次验收仅开放只读权限，尚未验证持续群聊、发送与群管理（理论上可以） |
| <img src="doc/assets/agents/workbuddy.png" width="28" height="28" alt="WorkBuddy"> **[WorkBuddy](doc/AGENT_SETUP.md#workbuddy)** | 已接入并由用户确认功能跑通；本机调用记录覆盖消息与图片读取、文件下载、OneBot 群资料、群聊目录与人格发现 |

连接 OneBot 并授予相应权限后，MCP 还可提供 QQ 发送、群管理，以及使用自定义人格的持续群聊和表情包互动。不同客户端的工具审批与持续运行机制各有差异，接入实测范围以上表为准。

[四款 Agent 配置教程](doc/AGENT_SETUP.md) · [MCP 能力与权限](doc/MCP.md) · [轻量版说明](doc/MCP_LITE.md) · [持续群聊与人格](doc/MCP_CHAT.md) · [验证记录](doc/VALIDATION.md)

## 开始使用

0.5.2 同时提供两个版本，使用相同的 QQ / 微信读取器和 MCP 权限机制：

| 下载包 | 适合谁 | 包含内容 |
| --- | --- | --- |
| `Tulpa-0.5.2-win-x64.zip` 完整版 | 在 Tulpa 内直接聊天、回复、使用记忆和工作区 | 内置 Harness、MCP、本地 OCR / 语音 / Office 解析和固定 WebView2 |
| `Tulpa-MCP-0.5.2-win-x64.zip` 轻量版 | 使用 Codex、DeepSeek Harness、Antigravity、WorkBuddy 等外部 Agent | 导入、实时读取、聊天浏览、原文件下载、OneBot 和 MCP；语音模型按需安装，使用系统 WebView2 |

1. 在 [Releases](https://github.com/fumingyang2004/Tulpa/releases/latest) 选择一个 ZIP。GitHub 自动提供的 `Source code` 不是桌面安装包。两个版本请放在各自的文件夹中，不要混合覆盖程序文件。
2. **完整解压**到有写入权限的本地目录，打开 `Tulpa/Tulpa.exe`。不要单独取出 EXE，也不要直接从压缩包内运行。
3. 首次选择 **使用内置聊天** 或 **连接外部 Agent**。内置聊天在 **模型与连接**填写自己的 API 地址、API Key 和模型名称；只使用 MCP 无需模型配置。
4. 登录本机 QQ / 微信，在 **数据与同步**选择平台、会话和日期，点击读取。可以分批读取整个所选时间范围，并查看进度。
5. 回到对话页面提问，或打开 **浏览聊天记录**查看原文。没有 OneBot 也可以使用本地导入、检索和回复草稿。

运行环境：**Windows 10 / 11 x64、.NET Framework 4.8**。两版均自带 Python；完整版自带 WebView2，轻量版使用系统 WebView2 Evergreen Runtime。QQ / 微信客户端、模型服务及可选 OneBot 服务需用户自行准备。客户端读取是否可用，受版本、登录账号、权限和本机缓存影响。

本机留有多个 QQ / 微信账号目录时，先在 **数据与同步 → 本机账号** 选择要读取的账号，再更新会话列表或导入消息。只有一个候选时自动选中；读取后会记住选择。列表反映本地数据目录，不代表账号已登录。每个程序数据目录、每个平台使用一个账号；已有来源不会自动切换，其他账号请使用独立的 Tulpa 文件夹。

## 能做什么

| 功能 | 使用方式 |
| --- | --- |
| 聊天查询 | 自然语言检索、上下文展开、多轮追问、人物账号解析及受限 SQL 统计；可跳回原文 |
| 实时聊天浏览 | 左侧会话列表显示最后一条消息，右侧展示聊天，新入库消息自动刷新 |
| 图片、语音、文件 | 图片本地 OCR 或原生识图；语音本地转写；PDF、Office 与文本文件按需解析 |
| 帮我回复 | 选择真实消息，填写要求，生成并编辑草稿；从已导入聊天中寻找相似真实互动 |
| 记忆 | 按会话维护行为记忆和人工修正；可关闭，历史回复案例检索不要求先积累成长进度 |
| 关注卡 | 保存一个问题，按时间或新增消息数量重新询问，也可手动检查 |
| 工作区 | 围绕一个目标持续调查，产出 Markdown / CSV，查看差异、审批修改、保留版本并回退 |
| 证据集合 | 组织消息、媒体、文件片段及群资料的引用，保留来源 |
| QQ 扩展 | 连接 OneBot 后按需读公告、精华和群文件；批准回复发送；按账号权限管理群 |
| 持续群聊与表情包（MCP） | 小鲸鱼预设或自定义 Markdown 人格，热发现；独立接收 OneBot 消息，按语境接话、看图和选择表情，可随时停止 |
| 外部 Agent / MCP | 让 Codex 等客户端直接查询授权的聊天与文件，复用本地检索；无需 Tulpa 模型配置；可为连接持续授权 QQ 发送和群管理 |

完整行为和限制见 [FEATURES.md](doc/FEATURES.md)。

两个版本均包含 MCP。在侧栏 **外部 Agent / MCP** 或 **连接外部 Agent** 导入资料、选择范围，再点击创建连接（可一并开启服务）；随后检测连接、写入本机 Codex 配置或复制到其他客户端。OneBot 设置可独立保存，不要求模型 API Key。操作步骤见 [MCP 教程](doc/MCP.md)。

需要后台提供 MCP 时，可勾选关闭窗口后留在托盘。托盘菜单支持打开窗口、开机启动（默认关闭）和彻底退出。只有彻底退出才会停止托盘模式中的服务；移动程序目录后应重新设置开机启动。

轻量版的文件链路是 **OneBot 下载原文件 → 返回本机路径 → 外部 Agent 用自己的工具读取**，不要求先解析 Office 文件。使用方式见 [MCP 轻量版](doc/MCP_LITE.md)，版本变化见 [0.5.2 发布说明](doc/releases/0.5.2.md)。

持续群聊可直接对外部 Agent 说：**“用小鲸鱼预设在测试群持续聊天，直到我停止。”** 先在连接中授予持续群聊与发送权限，并配置 OneBot 实时事件。自定义人格放入当前安装目录的 `chatlocal/prompts/mcp_chat/`；新增或修改后让 Agent 列出人格即可，无需重启。具体步骤见 [水群人格与接话](doc/MCP_CHAT.md)。

## 发送和群管理

**帮我回复不会自动发送。** 底部面板先显示草稿，用户可以编辑；点击 **批准** 后才发送当前文字，点击 **拒绝** 不发送。当前支持 QQ 文本发送，微信仅支持草稿和复制。

群管理默认每项操作询问，也可为当前普通对话明确选择“默认允许”或“默认拒绝”。权限检查、目标确认及操作记录始终保留。工作区和关注卡不会在后台执行群管理写操作。

MCP 连接的发送与群管理使用独立授权：界面勾选后为持续允许，外部 Agent 直接执行，不用返回 Tulpa 逐项批准；默认关闭，可以撤销，记录保存在本机。

这些扩展需要独立运行的本机 OneBot HTTP 服务。Tulpa 不附带 SnowLuma，不代填账号或 Token；按 [从零取得 URL 和 Token](doc/SNOWLUMA_SETUP.md) 操作。

## 数据与模型调用

- 聊天、索引、记忆、成果及配置保存在程序目录。`data/`、`imports/`、`.env` 和私人诊断不进入源码仓库或标准发布包。
- **本地保存不等于完全离线。** 提问时，必要的消息片段和上下文会发送给你配置的模型服务；启用原生识图时还会发送选中的图片。语音在本地转写，转写文字可用于模型查询。
- 记忆默认开启，达到成长门槛会进行有限模型整理；可在侧栏关闭。工作区自动维护默认关闭；定时任务仅在应用运行时执行。
- API Key / OneBot Token 保存在本机配置中，请勿共享整个使用过的程序目录。提交问题时先移除聊天内容、账号、密钥和个人路径。
- 检索结果只覆盖已经取得的本地资料。引用有效不保证模型解释正确；未通过引用检查的文字会标注原因。

**从旧版本升级无需重新导入。** 按 [升级教程](doc/UPGRADE.md) 将新版解压到临时目录，用随包升级工具检查、备份并更新原安装目录；聊天、附件、授权、账号选择和实时读取进度保留。教程附有可交给 Codex / DSH 的执行指令。升级后仍打开原目录 EXE，重新连接 MCP。

## 从源码运行

需要 Windows x64、Python 3.13 和 Git。在 PowerShell 中执行：

```powershell
git clone https://github.com/fumingyang2004/Tulpa.git
cd Tulpa
.\setup.ps1
.\start.ps1
```

打开 <http://127.0.0.1:7860/?desktop=1>，在界面中配置模型。首次 `setup.ps1` 会联网安装固定依赖和读取模块。图片 / 语音的开发依赖及桌面构建步骤见 [DESKTOP.md](doc/DESKTOP.md)。不需要把个人 `.env` 或数据库提交到 Git。

## 文档

| 主题 | 文档 |
| --- | --- |
| 功能与边界 | [功能清单](doc/FEATURES.md) |
| 保留数据升级 | [升级教程与 Agent 执行指令](doc/UPGRADE.md) |
| 桌面运行、开发与打包 | [DESKTOP.md](doc/DESKTOP.md) |
| 数据读取、进度和诊断 | [导入](doc/IMPORTS.md)、[实时摄取](doc/LIVE_INGESTION.md) |
| 查询与多模态资料 | [调查和证据](doc/INVESTIGATION.md)、[文件](doc/ARTIFACTS.md)、[语音](doc/VOICE.md) |
| 持续工作 | [工作区](doc/WORKSPACES.md)、[行为记忆](doc/TULPA.md) |
| QQ 扩展 | [回复助手](doc/REPLY_COPILOT.md)、[群管理](doc/GROUP_MANAGEMENT.md)、[OneBot 教程](doc/SNOWLUMA_SETUP.md) |
| 外部 Agent 接入 | [Codex / DSH / Antigravity / WorkBuddy 配置教程](doc/AGENT_SETUP.md)、[MCP 能力与权限](doc/MCP.md)、[轻量版](doc/MCP_LITE.md) |
| 开发与验证 | [贡献指南](doc/CONTRIBUTING.md)、[验证说明](doc/VALIDATION.md)、[安全报告](doc/SECURITY.md) |

## 开源许可

Tulpa 自有代码采用 [MIT License](LICENSE)。第三方组件、模型和客户端遵循各自许可证，详见 [THIRD_PARTY.md](doc/THIRD_PARTY.md)。本项目不是腾讯官方产品，与 QQ、微信及所用模型服务商没有隶属关系。

## 致谢

- 感谢 [qq-bridge](https://github.com/Derpyu520/qq-bridge) 对群聊交互、提示词组织和表情包使用的探索。Tulpa 的 MCP 持续群聊借鉴了其设计，小鲸鱼人格基于其角色卡并由 Tulpa 维护者按需修改；来源与 MIT 许可保留在项目中。
- 感谢 [SnowLuma](https://github.com/SnowLuma/SnowLuma) 提供 QQ / OneBot 接入能力，支持 Tulpa 的实时事件、消息发送和群资料功能。SnowLuma 是独立项目，需另行安装并遵循其许可。
