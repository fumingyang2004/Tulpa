# 验证说明

## 外部客户端接入补充（2026-10-06）

- Claude Code（VS Code 扩展 `2.1.289`）：用户自行配置后确认功能正常。本轮只读核对 `.claude.json` 中项目作用域的 HTTP / Bearer 配置，凭据对应有效的 Tulpa 授权，并对照官方文档整理跨项目配置方法。当前授权与另一客户端共用，服务端调用记录不能独立归因给 Claude Code；本轮未重复执行发送或群管理验收。
- WorkBuddy `5.6.2`：用户按 `streamableHttp`、`url`、Bearer 请求头配置后确认功能跑通。只读核对服务端访问记录，`get_data_status`、`list_conversations`、`read_conversation`、`read_image`、`search_files`、`download_file`、`read_qq_group`、`list_chat_groups`、`list_chat_personas`、`list_chat_sessions` 均有成功调用。用户整体使用反馈与这些可核对的调用记录分别保留；本次文档更新没有另行发送消息或执行群管理，也不把未记录的写操作列为逐项验收通过。
- Antigravity `2.19.1`：此前在实际应用中完成工具发现、资料状态、会话、消息和图片理解验收。独立只读授权未开放发送、群管理和持续群聊，因此不将其列为该客户端已验收能力。
- [Agent 配置教程](AGENT_SETUP.md) 提供配置文件、重连与只读验证步骤。示例仅含占位凭据，不公开本机配置、账号、聊天或原始调用回执。

## 持续群聊表情包子功能（2026-10-05，本机覆盖验收）

- `check_mcp_chat_media.py`：实际 MCP HTTP 图片块、动图三帧采样与原字节发送、模拟 QQ 收藏和笔记检索；验证独立授权、跨连接/会话隔离、无原生数据库访问、私有网络/任意路径拒绝、代理虚拟 DNS 回退、坏图/尺寸限制、并发幂等、发送与收藏 UNKNOWN 不重试、看图及发送准备阶段停止、撤销、独立额度和并发通道。
- 外部官方 DeepSeek Harness 真模型通过 MCP 看实际 GIF 像素，识别出白底圆形的红→绿→蓝变化及英文标记，保存笔记、模拟发送一次、收藏一次并停止。本次模型验收接的是隔离 OneBot，不是对真实群自动聊天的试验。
- 用户授权的真实测试群通过 SnowLuma 收到 GIF 事件，经 MCP 下载和读取三帧，再成功发送原动画并观察到同消息编号的 WebSocket 回传；成功收藏到 QQ，并从刷新后的 QQ 收藏目录再次读取该动图。全程禁止访问原生聊天 Store。真实来信使用本人发送的回传，其他账号消息由夹具覆盖。
- 原有 MCP 协议、文字发送/管理、持续聊天及 Edge 设置页面回归通过；表情权限取消会联动清除子权限，已有连接不升级。没有新增依赖，不修改内置 Harness、关注卡和工作区工具。
- 私人回执、测试图片和模型输出仅保留在忽略目录。不重新发布公共版本；本轮目标为覆盖本机现用 MCP 目录。

## SnowLuma 实时群聊验收（2026-10-04，本机 0.5.0 MCP 覆盖版）

- 使用真实 WebSocket 服务夹具和 MCP HTTP：零历史导入、群与账号/日期隔离、消息去重、断线重连及缺口提示、持久分页确认、六个并发等待、普通工具独立使用、停止与发送竞争、撤销、幂等发送及 UNKNOWN 不重试通过。
- 在禁止调用历史 Store 的条件下，通过 MCP HTTP 完成等待、读取实时事件、发送与停止；原生历史导入不会进入持续聊天队列。普通 MCP 数据检索与直接写操作回归通过。
- 外部 DSH 真模型使用隔离 QQ 事件：至少两次空闲后继续等，两个新话题触发两次人格回复，停止后结束；没有调用内置 Harness。该模型试验使用模拟群和模拟发送。
- 用户授权的真实小号测试群：发出一条明确标注的验收消息，取得 QQ 成功回执、独立观察到同编号的原始 SnowLuma WebSocket 回传，并经 MCP 读取后停止。发送/等待/停止期间历史数据库访问被禁止。真实验收使用本人发送回传，其他账号来信由隔离事件验证，不能混称真实他人来信验收。
- 本机事件节点仅监听 loopback，配置与回执保存在忽略的私人目录。不上传 GitHub、不重新发布公共 ZIP；覆盖目录保留个人配置、数据和授权，并留有回滚备份。


公开仓库只保留可复现的测试方法与不含私人聊天的夹具。个人账号、真实会话、原文截图、模型私有回答及开发机器的诊断记录不作为公开验收材料。

## 持续群聊开发版验收（2026-10-04，未打包）

- `check_mcp_chat.py` 通过真实 MCP HTTP 和隔离 OneBot，验证人格输入、无总时长、空闲继续、分页确认、进程重建后保留游标、旧补导/本人消息标记、跨连接和日期边界、同群重复运行拒绝、发送幂等及 UNKNOWN 不重发。
- 6 个群同时等待期间，普通查询、独立发送及停止仍成功；测试了网络等待取消、撤销权限、发送准备阶段同时停止、达到调用配额后停止，以及停止不依赖 OneBot 在线探测。没有真实 QQ 发言。
- 官方外部 DeepSeek Harness 使用真实模型接入隔离 MCP：完成开场、3 次空闲等待、连续两条新话题回复，最后收到停止事件并结束。人格中的轻松短句和“摸鱼”表达进入了实际发出的模拟文本。等待期间独立资料查询约 0.07 秒；本地停止写入约 0.02 秒。这是单次本机观察，不是性能保证；Tulpa 内置模型没有参与。
- Edge 前端夹具通过原有服务配置/连接创建回归，以及新增授权联动、人格纯文本显示、刷新保留展开状态、单群/全部停止。内置浏览器另以真实本地设置 API 操作单群停止，确认状态变为“已停止”。页面和 OneBot 数据均为隔离夹具。
- 原 `check_mcp.py`、`check_mcp_actions.py`、`check_mcp_onebot.py`、`check_mcp_downloads.py` 回归通过。未运行长期真实 QQ 水群、没有本轮新的 Codex 模型长会话验收，也没有构建或发布 EXE / ZIP。
- 已接入完整版与轻量版共用的 MCP 路由和前端资源；未来打包会运行新增后端验收。外部 Harness 的运行时间、预算、断网恢复仍由该宿主负责，协议空闲可继续不等于所有宿主永不结束任务。

## 0.5.0 MCP 轻量版本机验收

- 使用系统 WebView2 启动实际 EXE；随包 Python 通过原有 QQ 大库锁页、微信密钥/增量、名字恢复、MCP HTTP 范围隔离和 OneBot 回归。它们使用隔离数据库，不代表第二台电脑的客户端兼容性验收。
- 真实 Codex CLI 与官方 DeepSeek Harness 各完成一条授权测试群发言、临时改群名并恢复，约 86 秒与 37 秒；独立查询核对消息均存在、群名已恢复。没有踢人，也没有改变成员原有禁言。
- 两个模型客户端均实际读取 QQ / 微信、图片像素、PDF 原文块和本地语音转写。另由官方 DSH 工具桥完成原有 23 个资料工具的 31 次调用。新增原文件下载工具已在两客户端调用成功。
- `download_file` 将本机不存在的真实 QQ 群 PDF 下载到隔离缓存，2.2 MB、约 3.8 秒；下载后仍为未解析状态。外部 Harness 的本地阅读与 MCP 下载分别验收：本轮独立 CLI 的 PDF 命令处理被其执行策略/Windows 沙箱授权挡住，不宣称外部 PDF 处理已通过。
- 在实际页面点击安装可选语音模型，观察下载进度，校验通过后完成一条新的本地转写，约 5.1 秒；正式主包仍不包含这个约 181 MiB 的模型。
- `check_mcp_actions.py` 覆盖默认无写权限、范围/账号/角色限制、并发幂等、撤销和未知结果不重试。`check_mcp_downloads.py` 覆盖原文件字节、不解析、下载权限和配额，以及可选模型安装的校验、取消、失败保护和同源限制。
- 本机私人报告保留真实回执、用量和失败过程。公开代码与 ZIP 不包含测试聊天、账号配置和模型凭据。未在第二台电脑验证这次精简包；真实踢人和入群申请处置未执行。

## 0.4.0 桌面 MCP 本机验收

- 创建连接流程修正：默认不再要求先勾选启用、再保存服务；未运行时提供「开启服务并创建连接」，高级服务设置默认折叠。`check_mcp_ui.cjs` 使用真实 Edge 和实际前端脚本、隔离 API 响应，覆盖未启动、页面状态过期、端口冲突及修改、范围保留、重复提交和创建后列表刷新失败。不会创建用户的真实授权或修改 Codex 配置。

- 真实 WinForms + WebView2 测试构建：首次模式选择；无模型 API Key 保存 OneBot；授权创建、Token 关闭清除和真实 SDK 检测。浏览器脚本未报错。
- 桌面关闭进入托盘后 MCP 仍可读取隔离消息；恢复窗口复用同一后端进程；彻底退出回收服务和端口。重启后原 MCP 地址、Token 和范围仍有效，来源链接使用本轮新桌面端口。
- 使用已运行的真实本机 OneBot，桌面包保存配置后通过 MCP 读取实际群信息及一条群公告。未发送消息，未调用模型；测试凭据已撤销，私人资料不进入发布包。
- `check_mcp_onebot.py` 使用隔离 HTTP OneBot 服务，验证 EXE 设置保存 → MCP 工具出现 → 公告读取，以及 Token 更换立即生效、不依赖模型 Key。
- `check_mcp.py` 增加连接检测 API、端口冲突、Codex 配置备份/合并/同名冲突拒绝；配置写入仅测试隔离目录，不修改开发者的真实 Codex 配置。
- 构建阶段用随包 Python 执行上述 MCP 检查，确认不依赖开发虚拟环境。正式 ZIP 仍按文件清单生成，并执行隐私边界和 CRC 检查。
- 限制：本轮不是另一台干净 Windows 机器或实际重启 Windows 的验收；开机启动菜单已实现，未修改开发机的登录启动项。其他外部 Agent 客户端仍需分别测试。

## 隔离回归

安装开发依赖后，在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe scripts/check_publication.py
.\.venv\Scripts\python.exe scripts/check_client_accounts.py
.\.venv\Scripts\python.exe scripts/check_data_controls.py
.\.venv\Scripts\python.exe scripts/check_desktop.py
.\.venv\Scripts\python.exe scripts/check_mcp.py
.\.venv\Scripts\python.exe scripts/check_mcp_onebot.py
.\.venv\Scripts\python.exe scripts/check_mcp_chat.py
.\.venv\Scripts\python.exe scripts/check_import_batches.py
.\.venv\Scripts\python.exe scripts/check_chat_browser.py
.\.venv\Scripts\python.exe scripts/check_reply_history.py
.\.venv\Scripts\python.exe scripts/check_reply_copilot.py
.\.venv\Scripts\python.exe scripts/check_group_admin.py
.\.venv\Scripts\python.exe scripts/check_workspace_ide.py
.\.venv\Scripts\python.exe scripts/check_tulpa.py
.\.venv\Scripts\python.exe scripts/check_tulpa_api.py
.\.venv\Scripts\python.exe scripts/check_withheld_claims.py
.\.venv\Scripts\python.exe scripts/agent_smoke.py
node scripts/check_chat_browser_ui.cjs
node scripts/check_reply_ui.cjs
node scripts/check_workspace_ui.cjs
node scripts/check_refresh_ui.cjs
```

这些检查使用临时库、模拟提供方或模拟接口，不发送真实 QQ 消息，不调用付费模型。`agent_smoke.py` 使用实际安装的 Harness 配合本地假 Provider。CI 在 Windows 上运行选定的隔离检查。

`check_mcp.py` 启动真实 localhost MCP 服务，并使用官方 Python MCP 客户端完成初始化、工具发现与调用。隔离数据覆盖两个授权范围、消息和身份目录、受限 SQL、同 SHA 文件、520 条连续分页、密集长消息分页、GIF 图像内容、模拟 OneBot 公告/成员、取消与撤销、来源账号变化和持久限流。OneBot 分支是夹具，不代表真实 QQ 远端兼容性；协议检查也不替代外部 Agent 模型的任务效果验收。

原生读取器的版本、SQLite 特殊锁页、快照恢复和媒体分支另有 `check_reader_runtime.py`、`check_live_runtime.py`、`check_qq_history.py` 等测试，桌面构建会运行相关门禁。

## 发布前

1. 在干净提交上构建，核对 `build-manifest.json` 中的源码提交与版本。
2. 对实际 ZIP 运行 `check_publication.py --archive ...`，检查文件清单、私密文件排除、可疑凭据、工作区路径和 QA 程序排除。
3. 解压到新的可写目录，使用随包运行时启动真正的 EXE；首次配置应为空。
4. 浏览器或实际桌面检查首页、模型设置、数据入口、聊天与回复面板；源码语法或 HTTP 测试不能替代视觉验收。
5. 核对 SHA256，发布后验证远程仓库、标签、CI、Release 附件大小与摘要。

## 验证边界

0.3.1 的账号选择修复通过合成双账号目录验证实际目录发现与导出器选择、HTTP 账号参数传递、持久化、增量刷新、账号失效和跨账号进度保护。浏览器使用实际页面与隔离数据验证选择第二个账号、读取会话列表、模拟导入及刷新页面后保留选择。本机单账号目录检测已验证；没有真实多账号电脑，因此不声明真实多账号解密与导入已通过。每个平台仍使用一个活动来源，不支持在同一数据目录并行同步多个账号。

隔离夹具不证明真实账号兼容性、识别准确率或实际发送成功。真实读取需在目标机器、对应客户端版本和账号上另行检查；真实发送和群管理只能在明确授权的目标与操作范围内执行。

普通测试不应自动启用个人 API、导入真实数据或发布原始日志。需要真实验证时，结果保存在被 Git 忽略的私人目录，并明确记录验证范围。


## 0.5.1 升级验证

新增 `scripts/check_upgrade.py`，使用真实 SQLite / WAL、模拟版本程序和真实 Windows 进程检查：只检查不更新程序、保留聊天编号/数据源身份/游标/Token 哈希/端口、锁住写入、保存匹配的数据库与 WAL 备份、更新失败恢复程序、校验下载文件、拒绝不同版本类型和降级、拒绝越界路径、重复升级不重复修改。两种安装包构建时都用随包 Python 执行此测试。

`build-manifest.json` 从本版开始带每个程序文件的 SHA256，升级和压缩包发布检查均核对；私人文件不进入清单。新版启动器发现升级锁时不启动后端。正常升级原目录不移动数据，不改历史编号与 MCP 授权，因此没有重新导入步骤。

发布时还需在本机关闭旧 EXE，实际升级并启动新程序，核对已导入数量、MCP 重连及新消息。持续聊天的 OneBot 实时事件与 QQ/微信数据库实时入库分别验证，不能互相替代。具体操作见 [升级教程](UPGRADE.md)。
