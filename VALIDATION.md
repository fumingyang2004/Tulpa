# 验证说明

公开仓库只保留可复现的测试方法与不含私人聊天的夹具。个人账号、真实会话、原文截图、模型私有回答及开发机器的诊断记录不作为公开验收材料。

## 隔离回归

安装开发依赖后，在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe scripts/check_publication.py
.\.venv\Scripts\python.exe scripts/check_desktop.py
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

原生读取器的版本、SQLite 特殊锁页、快照恢复和媒体分支另有 `check_reader_runtime.py`、`check_live_runtime.py`、`check_qq_history.py` 等测试，桌面构建会运行相关门禁。

## 发布前

1. 在干净提交上构建，核对 `build-manifest.json` 中的源码提交与版本。
2. 对实际 ZIP 运行 `check_publication.py --archive ...`，检查文件清单、私密文件排除、可疑凭据、工作区路径和 QA 程序排除。
3. 解压到新的可写目录，使用随包运行时启动真正的 EXE；首次配置应为空。
4. 浏览器或实际桌面检查首页、模型设置、数据入口、聊天与回复面板；源码语法或 HTTP 测试不能替代视觉验收。
5. 核对 SHA256，发布后验证远程仓库、标签、CI、Release 附件大小与摘要。

## 验证边界

隔离夹具不证明真实账号兼容性、识别准确率或实际发送成功。真实读取需在目标机器、对应客户端版本和账号上另行检查；真实发送和群管理只能在明确授权的目标与操作范围内执行。

普通测试不应自动启用个人 API、导入真实数据或发布原始日志。需要真实验证时，结果保存在被 Git 忽略的私人目录，并明确记录验证范围。
