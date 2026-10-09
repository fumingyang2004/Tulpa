# 参与开发

使用 Windows x64 和 Python 3.13。安装、运行和构建步骤见 [DESKTOP.md](DESKTOP.md)，测试入口见 [VALIDATION.md](VALIDATION.md)。

## 目录与开发入口

根目录保留 README、许可证、配置示例、`requirements.txt` 和安装、启动、停止入口。`setup.ps1`、`start.ps1`、`stop.ps1` 的用法不变。

- `requirements/`：读取器、MCP 轻量版、图片、文件解析和语音的分类依赖，版本与移动前相同；`readers.txt` 包含根目录的基础依赖。
- `scripts/`：辅助脚本和检查。手动刷新数据使用 `scripts/refresh-data.ps1`；本地 OneBot 的启停辅助脚本为 `scripts/start-reply-sender.ps1` 和 `scripts/stop-reply-sender.ps1`。
- `desktop/`：桌面入口和打包流程；`chatlocal/`、`web/`、`harness/`：应用实现；`doc/`：教程与验证记录。

## 开发约定

- 先改源码、运行对应检查，再从干净提交生成发布包；不要只修改忽略目录里的 release 副本。
- 保留现有数据迁移、范围校验、审批和原文来源检查。模型不能自行批准发送或群管理操作。
- 测试优先使用临时库与合成夹具。真实客户端、真实发送、云端模型调用与浏览器实测应分别说明，不能互相替代。
- 新的读取兼容修改通过 `scripts/reader_patches.py` 或本项目适配层实现，不只修改下载到 `tools/` 的副本。
- 前端没有独立构建步骤。交互修改需要检查实际浏览器；DOM 夹具只验证脚本行为。
- 不提交 `.env`、数据库、聊天、个人截图、真实模型回答、原始日志或发布包。提交前运行 `scripts/check_publication.py`。

## 提交问题或改动

说明复现步骤、版本、期望与实际行为。诊断先脱敏；安全问题按 [SECURITY.md](SECURITY.md) 私下报告。PR 说明实际测试、限制及是否涉及迁移，不需要附私人聊天证明。

提交贡献表示你有权以仓库的 MIT 许可提供该部分代码；第三方内容需要保留原许可与来源。不得将受限制的客户端或服务直接并入发布包。
