![Tulpa](doc/assets/title.png)

# Tulpa

**在本机查询 QQ / 微信聊天，整理资料、持续调查，并起草符合当前语境的回复。**

[下载 Windows 版](https://github.com/fumingyang2004/Tulpa/releases/latest) · [功能清单](doc/FEATURES.md) · [OneBot 配置教程](doc/SNOWLUMA_SETUP.md) · [参与开发](doc/CONTRIBUTING.md)

Tulpa 是面向 Windows 的个人通信工作台。聊天和资料保存在本机，Agent 在你选择的平台、会话和时间范围内检索，按需读取图片、语音和文件。桌面版解压即用；浏览器入口保留用于开发与测试。

## 开始使用

1. 在 [Releases](https://github.com/fumingyang2004/Tulpa/releases) 下载 `Tulpa-0.3.0-win-x64.zip`。GitHub 自动提供的 `Source code` 不是桌面安装包。
2. **完整解压**到有写入权限的本地目录，打开 `Tulpa/Tulpa.exe`。不要单独取出 EXE，也不要直接从压缩包内运行。
3. 在 **模型设置**填写自己的 API 地址、API Key 和模型名称。接口需要支持流式 Chat Completions 和工具调用；当前主要验证 DeepSeek。
4. 登录本机 QQ / 微信，在 **数据与同步**选择平台、会话和日期，点击读取。可以分批读取整个所选时间范围，并查看进度。
5. 回到对话页面提问，或打开 **浏览聊天记录**查看原文。没有 OneBot 也可以使用本地导入、检索和回复草稿。

运行环境：**Windows 10 / 11 x64、.NET Framework 4.8**。便携包自带 Python、WebView2 和所需运行组件。QQ / 微信客户端、模型服务及可选 OneBot 服务需用户自行准备。客户端读取是否可用，受版本、登录账号、权限和本机缓存影响。

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

完整行为和限制见 [FEATURES.md](doc/FEATURES.md)。

## 发送和群管理

**帮我回复不会自动发送。** 底部面板先显示草稿，用户可以编辑；点击 **批准** 后才发送当前文字，点击 **拒绝** 不发送。当前支持 QQ 文本发送，微信仅支持草稿和复制。

群管理默认每项操作询问，也可为当前普通对话明确选择“默认允许”或“默认拒绝”。权限检查、目标确认及操作记录始终保留。工作区和关注卡不会在后台执行群管理写操作。

这些扩展需要独立运行的本机 OneBot HTTP 服务。Tulpa 不附带 SnowLuma，不代填账号或 Token；按 [从零取得 URL 和 Token](doc/SNOWLUMA_SETUP.md) 操作。

## 数据与模型调用

- 聊天、索引、记忆、成果及配置保存在程序目录。`data/`、`imports/`、`.env` 和私人诊断不进入源码仓库或标准发布包。
- **本地保存不等于完全离线。** 提问时，必要的消息片段和上下文会发送给你配置的模型服务；启用原生识图时还会发送选中的图片。语音在本地转写，转写文字可用于模型查询。
- 记忆默认开启，达到成长门槛会进行有限模型整理；可在侧栏关闭。工作区自动维护默认关闭；定时任务仅在应用运行时执行。
- API Key / OneBot Token 保存在本机配置中，请勿共享整个使用过的程序目录。提交问题时先移除聊天内容、账号、密钥和个人路径。
- 检索结果只覆盖已经取得的本地资料。引用有效不保证模型解释正确；未通过引用检查的文字会标注原因。

升级前退出程序并备份。将新版本程序文件合并到原目录时，保留自己的 `data/`、`imports/` 和 `.env`。不要让两个版本同时使用同一数据目录。更多说明见 [DESKTOP.md](doc/DESKTOP.md)。

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
| 桌面运行、开发与打包 | [DESKTOP.md](doc/DESKTOP.md) |
| 数据读取、进度和诊断 | [导入](doc/IMPORTS.md)、[实时摄取](doc/LIVE_INGESTION.md) |
| 查询与多模态资料 | [调查和证据](doc/INVESTIGATION.md)、[文件](doc/ARTIFACTS.md)、[语音](doc/VOICE.md) |
| 持续工作 | [工作区](doc/WORKSPACES.md)、[行为记忆](doc/TULPA.md) |
| QQ 扩展 | [回复助手](doc/REPLY_COPILOT.md)、[群管理](doc/GROUP_MANAGEMENT.md)、[OneBot 教程](doc/SNOWLUMA_SETUP.md) |
| 开发与验证 | [贡献指南](doc/CONTRIBUTING.md)、[验证说明](doc/VALIDATION.md)、[安全报告](doc/SECURITY.md) |

## 开源许可

Tulpa 自有代码采用 [MIT License](LICENSE)。第三方组件、模型和客户端遵循各自许可证，详见 [THIRD_PARTY.md](doc/THIRD_PARTY.md)。本项目不是腾讯官方产品，与 QQ、微信及所用模型服务商没有隶属关系。
