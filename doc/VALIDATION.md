# 验证说明

公开仓库只保留可复现的测试方法与不含私人聊天的夹具。个人账号、真实会话、原文截图、模型私有回答及开发机器的诊断记录不作为公开验收材料。

## 0.4.0 桌面 MCP 本机验收

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
