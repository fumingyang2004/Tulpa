# DeepSeek Harness 连接 Tulpa MCP

从旧版本升级：先看 [保留数据升级教程](UPGRADE.md)，可将其中的指令交给本机 Codex / DSH。原目录更新会保留 MCP 端口、凭据和读取进度，无需重新导入；新增能力仍需主动授权。

Tulpa 的 MCP 是通用资料接口，不依赖 Codex。本次使用独立安装的 DeepSeek 官方 Harness，通过它自带的 MCP 客户端连接桌面版 Tulpa，调用真实模型完成资料任务。

新用户先按 [Agent 配置教程中的 DeepSeek Harness 部分](AGENT_SETUP.md#deepseek-harness) 操作。下文的 `tools/deepseek-harness/` 是维护者本机验收目录，不随 Tulpa 发布包提供；旧版本实测记录保留其当时的能力范围，当前 MCP 能力见 [MCP.md](MCP.md)。

## 本机使用

目录为 `tools/deepseek-harness/`，安装 `@deepseek-ai/dsh@0.2.0-rc.2`，模型为 `deepseek-flash`。

1. 打开自己使用的 Tulpa EXE（例如 `release/0.5.0/Tulpa/Tulpa.exe` 或 `release/0.5.0-mcp/Tulpa/Tulpa.exe`），保持 MCP 服务开启。
2. 要查询当前 QQ 群公告、精华、成员或群文件，再开启已经配置的 SnowLuma。普通本地聊天查询不需要 OneBot。
3. 双击 `tools/deepseek-harness/start.cmd`，启动 Harness 并打开网页。已运行时复用现有进程。
4. 在网页交任务，例如：“通过 Tulpa MCP 检查授权范围，再核对某课程群最近的作业要求，保留引用，区分已确认与不确定的内容。”
5. 关闭网页不会停止服务。同目录 `stop.cmd` 停止 Harness，不停止 Tulpa 或 SnowLuma。

网页地址为 `http://127.0.0.1:3080`。首次需要启动器携带的本机登录凭据；裸地址出现 `Unauthorized` 时，重新运行 `start.cmd`。

`connection.env` 保存现有模型配置与独立 MCP Token；`home/` 保存会话，`workspace/` 是工作目录，`logs/` 是私人日志。整个安装目录和测试报告均被 Git 忽略。不要分享凭据、会话或日志。Tulpa 中授权名称为 **DeepSeek Harness**，可单独撤销；不依赖 Codex 配置。

### 重启或切换版本后连接

同一数据目录、端口和授权未变时，可继续用原 MCP URL / Token。切换到新的独立程序目录或重新创建连接后，将新值填入 `connection.env` 的 `TULPA_MCP_URL` 和 `TULPA_MCP_TOKEN`，保留模型配置；先运行 `stop.cmd`，再运行 `start.cmd`。仅刷新网页或对已运行的服务重复点 `start.cmd` 不会重新加载配置。HTTP 401 表示当前 Token 未获该 Tulpa 服务授权，不是模型 API Key 有误。Token 遗失时在 Tulpa 撤销旧连接并新建，不要贴到公开 Issue。

新建对话后，可先让模型调用 `get_data_status` 检查连接和范围。0.5.0 在原资料工具基础上增加原文件下载；勾选发送和群管理权限后，对应工具直接执行。具体权限、文件阅读边界和本轮验收见 [轻量版说明](MCP_LITE.md) 与 [验证说明](VALIDATION.md)。

## 在其他机器复现

以下为本次验证过的版本组合，不代表所有未来版本或所有 MCP 客户端已经验收。

先按 [MCP 教程](MCP.md) 导入资料、选择范围、创建独立连接。保存 URL 与 Token；图片、文件准备、转写、OneBot 分别需要对应权限，OneBot 还需有效服务配置。

本次使用 Windows x64、Node.js 24.19.0。在新建的安装目录执行：

```powershell
npm.cmd install --save-exact @deepseek-ai/dsh@0.2.0-rc.2
```

若 npm 拦截依赖安装脚本，应查看提示并明确批准所需包，再重建。本次批准并重建 `@deepseek-ai/dsh-subprocess-local`、`koffi`、`node-pty`、`protobufjs`，没有全局关闭安装脚本限制。

创建 `connection.env`，自行填写凭据：

```dotenv
DEEPSEEK_API_KEY=填写DeepSeek模型APIKey
DEEPSEEK_BASE_URL=https://api.deepseek.com/anthropic
TULPA_MCP_URL=http://127.0.0.1:18777/mcp
TULPA_MCP_TOKEN=填写Tulpa界面生成的MCPToken
DSH_TELEMETRY_DISABLED=1
DSH_TELEMETRY_MODE=DISABLED
```

这里使用原生 DeepSeek Messages API，基础地址与 Tulpa 内置模型的 OpenAI 兼容地址可能不同。MCP Token 和 OneBot Token 不能混用。

创建 `home/cordis.patch.yml`：

```yaml
- id: session-log-deepseek
  config:
    enabled: false
- id: otel
  disabled: true
- id: llm-deepseek
  config:
    apiKeyEnv: DEEPSEEK_API_KEY
    maxTokens: 8192
    reasoningEffort: low
    streamIdleTimeoutMs: 120000
- insert:
    - id: mcp-tulpa
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: tulpa
        transport: streamable-http
        url: !!js process.env.TULPA_MCP_URL
        headers:
          Authorization: !!js '`Bearer ${process.env.TULPA_MCP_TOKEN}`'
        toolCallTimeoutMs: 240000
        failOnStartupError: true
```

额外会话日志上传和遥测已关闭；任务所需内容仍会通过模型请求发送给 DeepSeek。不要将 `connection.env` 放成 `home/.env`：此版本对自动加载的 `.env` 有变量限制，`DEEPSEEK_BASE_URL` 会被拒绝。用 Node 的 `--env-file` 加载独立文件。

在安装目录启动：

```powershell
$dshRoot = (Get-Location).Path
$env:DSH_HOME = Join-Path $dshRoot 'home'
New-Item -ItemType Directory -Force workspace | Out-Null
Set-Location workspace
node --env-file="$dshRoot/connection.env" "$dshRoot/node_modules/@deepseek-ai/dsh/lib/bin.js" web --host 127.0.0.1 --port 3080
```

官方启动器会打开网页。让模型先调用 `mcp__tulpa__get_data_status` 检查范围，再布置任务。默认工作目录由启动位置决定，不要把凭据目录当作任务目录。

工具名由 Harness 添加 `mcp__tulpa__` 前缀。更换 Token 后重启 Harness。授权撤销或源账号变化时，在 Tulpa 创建新连接。其他客户端需要支持 **Streamable HTTP、Bearer 请求头和 MCP 图像内容**；文件、语音建议允许至少 240 秒工具超时。

## 2026-10-03 实测

连接的是当前桌面 release MCP，使用独立授权；开发环境的 Codex MCP 配置没有重新启用。

| 验证 | 实际结果 |
| --- | --- |
| 官方 Harness MCP 插件逐项调用 | 23 个已开放工具全部实际调用成功；没有用假 Provider 或模拟 OneBot 替代 |
| QQ / 微信 / 人物 / 上下文 / SQL | 真实授权资料查询，包含分页工具与交互候选 |
| 图片 | MCP 返回真实图片，DeepSeek 读图并给出描述 |
| 语音 | 既有微信语音完成本地 whisper.cpp 转写，约 8.4 秒 |
| 文件 | 聊天 PDF 按需准备、搜索、读取；另从 QQ 群目录下载并解析一份 PDF |
| OneBot | 真实群信息、成员、文件目录、公告及非空精华均返回 |
| 真实模型任务一 | 自主查 QQ、微信、公告、PDF，8 次工具调用、3 次模型请求，约 12.18 秒 |
| 真实模型任务二 | 取图、准备群文件、读取正文，3 次工具调用、3 次模型请求，约 10.43 秒 |
| Web 与启动 | 本机登录返回 HTTP 200；停止、重启、再次启动复用同一服务已验证 |

两次任务累计用量分别 **49,037** / **43,194 tokens**，含缓存读取与重复上下文，不是独立新资料量。逐项工具验收本身不调用云端模型。私人结果和用量在 `reports/private/deepseek-mcp/`，不随源码发布。

模型的图片描述少计了一只鸟；传图链路可用，不表示答案经过事实正确性认证。

**浏览器交互未验收**：内置浏览器返回 `ERR_BLOCKED_BY_CLIENT`。HTTP 登录和真实 SDK 模型任务不能替代网页渲染、点击验证；用户可通过 `start.cmd` 在自己的浏览器审阅。

## 2026-10-03 验收边界（0.4.0）

- 验证的是 **MCP 已开放的 23 个资料工具**。发送消息、群管理、审批、工作区写入尚未开放为 MCP 工具。
- 权限不全或 OneBot 不可用时，可见工具会减少。
- 实时消息需先由 Tulpa 入库，再由 Harness 查询；MCP 不替客户端自动发起持续任务。
- Harness 自带终端和文件工具。MCP 范围不等于 Windows 系统沙箱；本次模型任务限定使用 Tulpa 工具，调用记录也只有这些工具。
- 没有发送 QQ / 微信消息，没有重新分发 SnowLuma，没有修改 Codex 配置，没有上传 GitHub。

官方参考：[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)、[MCP 客户端插件](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/mcp-client/README.md)。
