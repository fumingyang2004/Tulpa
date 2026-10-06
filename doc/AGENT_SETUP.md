# 将 Tulpa 接入你的 Agent

适用于 Windows 上的 Tulpa 完整版和 MCP 轻量版。两版使用同一个 MCP 服务；只连接外部 Agent，不需要在 Tulpa 里填写模型 API Key。

你可以自己按步骤配置，也可以把[文末指令](#交给-agent-配置)交给本机 Agent。本文示例只含占位值，不包含可直接使用的 Token。

## 选择客户端

| 客户端 | 配置教程 | 已有验证 |
| --- | --- | --- |
| Codex | [打开教程](#codex) | 真实资料查询、图片、文件、语音；测试群发言、改名与恢复 |
| Claude Code | [打开教程](#claude-code) | 用户确认接入后功能正常；已核对 VS Code 扩展 `2.1.289` 的本机 HTTP / Bearer 配置 |
| DeepSeek Harness | [打开教程](#deepseek-harness) | 官方 DSH `0.2.0-rc.2`；真实资料和 OneBot 调用；持续群聊与表情包另有隔离模型测试 |
| Google Antigravity | [打开教程](#google-antigravity) | `2.19.1`；实际应用中完成消息查询与图片理解，当次未授予发送、群管理和持续群聊权限 |
| WorkBuddy | [打开教程](#workbuddy) | `5.6.2`；用户确认功能跑通，服务端记录确认消息、图片、文件下载、群资料、群聊目录与人格发现 |

配置与验证信息更新于 **2026-10-06**。工具是否出现还取决于连接权限和 OneBot 状态；不以固定工具数量判断成功。详细记录见 [VALIDATION.md](VALIDATION.md)。

## 共同准备

1. 在这台电脑打开 `Tulpa.exe`，进入 **外部 Agent / MCP**，轻量版也可能显示为 **连接外部 Agent**。
2. 要查历史聊天，先到 **数据与同步** 选择 QQ / 微信账号并导入需要的范围。只用 OneBot 实时群聊时，可从 OneBot 选择群，无需导入历史。
3. 为要连接的客户端创建一个独立授权，名称可填 `codex`、`claude-code`、`deepseek`、`antigravity` 或 `workbuddy`。选择平台、会话、日期和所需权限；持续接收新消息时，截止日期留空。
4. 点击 **创建连接凭据** 或 **开启服务并创建连接**，再点 **检测连接与工具**。先确认 Tulpa 这一侧检测成功。
5. 保存页面显示的 **MCP 地址** 和 **MCP Token**。Token 只显示一次；遗失时撤销旧连接再创建。不同客户端单独建连接，后续可分别撤销。
6. 保持 Tulpa 运行。勾选关闭窗口后留在托盘，窗口关闭后仍可提供服务。

后文以 `http://127.0.0.1:18777/mcp` 为例。如果你改过端口，所有示例都换成 Tulpa 实际显示的地址。

| 要填写的内容 | 从哪里取得 |
| --- | --- |
| MCP 地址 | Tulpa 的外部 Agent / MCP 页面，保留末尾 `/mcp` |
| MCP Token | Tulpa 为该连接新生成的凭据；替换示例中的 `YOUR_TULPA_MCP_TOKEN` |
| 模型登录或 API Key | 在外部客户端配置；DSH 的模型 Key 与 MCP Token 是两项不同配置 |
| OneBot HTTP / WebSocket 和 Token | 只在 Tulpa 的 OneBot 设置中配置，按 [OneBot 教程](SNOWLUMA_SETUP.md) 获取；不用再填进外部 Agent 的 MCP 配置 |

这里的 `127.0.0.1` 指运行 Tulpa 的电脑，适用于同机桌面或本机命令行任务。云端 Agent、另一台电脑或隔离容器里的同一地址，不会指向这台 Tulpa。

## Codex

### 在 Tulpa 中写入配置

1. 在刚创建的连接页面，审阅 Tulpa 显示的 Codex 配置。
2. 点击 **写入本机 Codex 配置**。Tulpa 会备份原文件、保留其他配置；若已有不同的 `tulpa` 条目，会提示冲突，不会替你覆盖。
3. 回到 Codex 的 MCP 设置，重新连接 `tulpa`；必要时重启 Codex，然后新建对话。
4. 执行下文的[共同验证](#连接后先这样验证)。

### 手动配置

Windows 默认文件是 `%USERPROFILE%\.codex\config.toml`。如果你设置过 `CODEX_HOME`，使用该目录中的 `config.toml`。先备份，在原文件中加入以下内容；已有 `[mcp_servers.tulpa]` 时修改那一段，不要重复追加。

```toml
[mcp_servers.tulpa]
url = "http://127.0.0.1:18777/mcp"
http_headers = { Authorization = "Bearer YOUR_TULPA_MCP_TOKEN" }
tool_timeout_sec = 240
```

保存后重新连接。`tool_timeout_sec` 的单位是秒，240 秒为按需文件处理、语音和群聊等待留出时间。安装了 Codex CLI 时，可用 `codex mcp list` 查看是否加载配置；仍需实际工具调用验证连通性。[Codex 官方 MCP 配置说明](https://learn.chatgpt.com/docs/extend/mcp)

## Claude Code

本节适用于同机运行的 Claude Code CLI 和 VS Code 扩展。本次用户已自行配置并确认功能正常；核对的扩展版本为 **2.1.289**。Claude Code 原生支持 Tulpa 的 HTTP MCP 和 Bearer 请求头，无需安装 stdio 转接器。[Claude Code 官方 MCP 教程](https://code.claude.com/docs/en/mcp)

### 1. 取得 Tulpa 连接信息

先完成[共同准备](#共同准备)，在 Tulpa 中给 Claude Code 创建连接，保存 MCP 地址和 Token。连接名称可写 `claude-code`；下面在 Claude Code 中将服务命名为 `tulpa`，两个名称不要求相同。

### 2. 添加服务

**已安装 Claude Code CLI：** 在 PowerShell 中执行下面这一行，把地址和 Token 换成自己的值：

```powershell
claude mcp add --transport http --scope user tulpa http://127.0.0.1:18777/mcp --header "Authorization: Bearer YOUR_TULPA_MCP_TOKEN"
```

`--scope user` 让这项个人配置在不同项目中都可用。省略它时默认是 `local`，只在运行命令时所在的项目生效；本机已跑通的配置就是这种项目限定方式。已有同名服务时，先在 `/mcp` 中检查并修改实际生效的条目，不要重复添加。

**只有 VS Code 扩展，或希望手动编辑：** 默认文件是 `%USERPROFILE%\.claude.json`，不是 `.claude/settings.json`。先备份原文件，在 JSON **根对象**的 `mcpServers` 中合并以下 `tulpa` 条目；文件有其他设置或服务时保留它们，不要用示例整份覆盖。如果设置过 `CLAUDE_CONFIG_DIR`，请使用对应配置目录。

```json
{
  "mcpServers": {
    "tulpa": {
      "type": "http",
      "url": "http://127.0.0.1:18777/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TULPA_MCP_TOKEN"
      },
      "timeout": 240000
    }
  }
}
```

这里必须有 **`type: "http"`**，地址字段为 **`url`**，`Bearer` 后保留一个空格。`timeout` 单位为毫秒，是单次工具调用的超时；240000 为文件处理、语音和群聊等待预留时间，不代表模型任务无限运行。CLI 添加的条目也可按需补上这一字段。

根级 `mcpServers` 是个人跨项目配置；`projects[项目路径].mcpServers` 是仅当前项目的配置。同名项目条目可能优先于根级条目：如果更换 Token 后仍失败，检查是否改错了作用域。不要把含真实 Token 的配置复制进仓库的 `.mcp.json`。

VS Code 扩展本身不会把 `claude` 命令加入终端 PATH，所以“找不到命令”不代表扩展不能用 MCP。可以采用上面的手动方式；扩展 **2.1.261 及以后**也可以在聊天面板输入 `/mcp`，通过服务管理窗口添加 HTTP 服务、填写地址和请求头，并选择个人作用域。[Claude Code VS Code 扩展说明](https://code.claude.com/docs/en/vs-code)

### 3. 重连并验证

保存后在 Claude Code 会话中输入 `/mcp`，确认 `tulpa` 已启用并显示 **Connected**；必要时重新连接，然后新建对话。有 CLI 时也可运行 `claude mcp list` 检查连接状态。仅显示“添加成功”只代表配置已保存，还没有验证 Token。

在新对话中执行下文的[共同验证](#连接后先这样验证)，确认 `get_data_status` 和 `list_conversations` 真正返回结果。Claude Code 自己的工具权限弹窗由用户选择放行；Tulpa 的访问范围与发送授权仍独立生效。

## DeepSeek Harness

以下使用已验证的官方 DSH `0.2.0-rc.2` 和 Node.js `24.19.0`。**如果已有可用 DSH，保留它的模型配置和安装目录，只合并 Tulpa MCP 配置**；已用本项目旧教程配置过的 `connection.env`，通常只需更新地址和 Token 后重启 DSH。

### 1. 准备独立目录

未安装时，先安装 Node.js 24，在一个新建的 DSH 安装目录打开 PowerShell：

```powershell
npm.cmd init -y
npm.cmd install --save-exact @deepseek-ai/dsh@0.2.0-rc.2
New-Item -ItemType Directory -Force home, workspace | Out-Null
```

安装完成后，目录中应有 `node_modules`、`home` 和 `workspace`。若 npm 提示某依赖安装脚本被拦截，按具体提示处理；旧版验收使用过的依赖见 [DSH 部署记录](DEEPSEEK_MCP.md#在其他机器复现)。

### 2. 填写连接信息

在安装目录新建 `connection.env`，不要放到 `home/.env`。填写你自己的模型 Key 和 Tulpa Token：

```dotenv
DEEPSEEK_API_KEY=YOUR_DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL=https://api.deepseek.com/anthropic
TULPA_MCP_URL=http://127.0.0.1:18777/mcp
TULPA_MCP_TOKEN=YOUR_TULPA_MCP_TOKEN
DSH_TELEMETRY_DISABLED=1
DSH_TELEMETRY_MODE=DISABLED
```

此版本使用 DeepSeek Messages API；不要把模型地址改成 Tulpa MCP 地址。已有其他可用模型配置时沿用原值。`connection.env` 是本机凭据文件，不提交到 Git。

### 3. 添加 MCP 插件

新安装时创建 `home/cordis.patch.yml`，填写下面内容。已有该文件时合并相应项目，保留原有插件和模型设置，不再插入第二个 `mcp-tulpa`。

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

MCP 工具名会带 `mcp__tulpa__` 前缀；`toolCallTimeoutMs` 的单位是毫秒。这里关闭额外会话日志上传与遥测，任务所需的聊天内容仍会随模型请求发给模型服务。[DSH 官方 MCP 插件说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/mcp-client/README.md)

### 4. 启动并检查

在刚才的安装目录运行：

```powershell
$dshRoot = (Get-Location).Path
$env:DSH_HOME = Join-Path $dshRoot 'home'
Set-Location (Join-Path $dshRoot 'workspace')
node --env-file="$dshRoot/connection.env" "$dshRoot/node_modules/@deepseek-ai/dsh/lib/bin.js" web --host 127.0.0.1 --port 3080
```

使用启动器打开的本机网页，新建对话并执行[共同验证](#连接后先这样验证)。网页要求本机登录凭据时，使用启动输出提供的入口；不要公开分享带凭据的链接。3080 若已被其他程序占用，可换成空闲端口，它与 Tulpa MCP 的 18777 无关。

修改 `connection.env` 后要停止旧 DSH 进程，再从安装目录重启；只刷新网页不会更新进程环境变量。自己用前台命令启动的实例可在对应终端按 `Ctrl+C` 停止。

## Google Antigravity

### 1. 找到配置文件

本次验证的 Antigravity `2.19.1` 使用 `%USERPROFILE%\.gemini\config\mcp_config.json`。先备份已有文件；如果界面打开的是其他路径，以应用实际使用的文件为准。

- Antigravity 2.x：打开 **Settings → Customizations → Installed MCP Servers** 管理连接，编辑上述 JSON 后在这里刷新。
- Antigravity IDE：Agent 面板右上角 **… → MCP Servers → Manage MCP Servers → View raw config**，打开应用正在使用的配置文件。

### 2. 合并配置

```json
{
  "mcpServers": {
    "tulpa": {
      "serverUrl": "http://127.0.0.1:18777/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TULPA_MCP_TOKEN"
      }
    }
  }
}
```

Antigravity 的字段是 **`serverUrl`**。不要直接照搬其他客户端的 `url`。已有其他服务时，只将 `tulpa` 合并到现有 `mcpServers` 中。

### 3. 刷新并检查

回到 MCP 管理页刷新，确认 `tulpa` 已启用、显示工具；必要时重启应用。新建对话执行[共同验证](#连接后先这样验证)。遇到客户端自己的工具权限弹窗，由你选择允许本次调用或本次对话；这与 Tulpa 的授权是两层独立设置。[Antigravity 官方 MCP 说明](https://antigravity.google/docs/mcp)

本项目已验证该客户端的真实消息查询与图片理解。当次使用只读授权，没有验证它的持续群聊、发送或群管理行为。

## WorkBuddy

以下步骤已在 WorkBuddy **5.6.2** 上由用户配置并确认跑通。

### 1. 打开配置入口

左侧 **专家·技能·连接器 → 连接器 → 自定义连接器 → 配置 MCP**。部分版本显示为 **插件 → MCP 服务器 → 配置 MCP**。用户级配置文件为 `%USERPROFILE%\.workbuddy\mcp.json`，可跨项目使用。[WorkBuddy 连接器入口](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Connector) · [官方 MCP 配置教程](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/MCP-Guide)

### 2. 填入配置

```json
{
  "mcpServers": {
    "tulpa": {
      "type": "streamableHttp",
      "url": "http://127.0.0.1:18777/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TULPA_MCP_TOKEN"
      },
      "timeout": 240000
    }
  }
}
```

将 Token 占位符替换为你在 Tulpa 给 WorkBuddy 创建的凭据，保留 `Bearer` 后的空格。原文件有其他 MCP 时，只合并 `tulpa` 项，不要整份覆盖。这里使用 **`type: "streamableHttp"` 与 `url`**，`timeout` 按毫秒填写；它不代表 Agent 可以无限运行。[WorkBuddy 官方字段说明](https://open.workbuddy.cn/docs/connector)

### 3. 保存、启用并检查

保存后查看连接状态，确认 `tulpa` 成功连接。新建任务，在输入框旁 **+ → 连接器** 中确认它对当前任务启用，再执行[共同验证](#连接后先这样验证)。如果未加载，重新连接或重启 WorkBuddy；也检查客户端安全中心是否关闭了自定义 MCP。

Tulpa 里给连接取名 `workbuddy`，配置里写 `tulpa` 没有冲突：前者是方便你管理授权的备注，后者是 WorkBuddy 给工具使用的服务名称。

## 连接后先这样验证

在客户端的新对话里发送：

> 请使用 Tulpa MCP，先调用 get_data_status 检查当前授权范围和数据时点，再用 list_conversations 列出最多 5 个可访问的会话。本次只验证读取，不发送消息、不改群设置；如果失败，告诉我实际失败的工具和错误，不要猜测成功。

应该看到真实工具调用和返回结果。仅出现在 MCP 列表里，或模型说“我能使用”，都不能代替这一步。

随后可以按需要让 Agent 读取某个授权会话的最近几条消息、打开一张图片，或下载并阅读指定群文件。文件会下载到 **Tulpa 所在电脑**，由同机 Agent 的文件工具继续读取；轻量版不需要额外安装 Office 解析框架。

### 使用持续群聊

先在 Tulpa 配好 **OneBot HTTP 和实时 WebSocket**，并在该客户端连接上开放持续群聊、发送，以及需要的图片 / 表情包子权限。新增权限后使用新凭据重新连接。然后向 Agent 说明目标群，例如：

> 先列出当前可用人格和允许的 QQ 群。核对目标群为“我的测试群”后，使用小鲸鱼人格持续聊天，直到我停止。空闲时继续等待，有适合接话的内容再回复。

在 Tulpa 的 **停止聊天 / 停止全部群聊** 或通过 Agent 调用 `stop_chat_session` 可以中止。外部 Agent 必须保持执行；宿主结束任务、达到预算、退出或休眠后，Tulpa 不会替它继续推理。原理和详细步骤见 [持续群聊](MCP.md#持续群聊snowluma-实时事件) 与 [人格教程](MCP_CHAT.md)。

Tulpa 的发送 / 群管理勾选是持续授权，调用会直接执行；外部客户端仍可能有自己的权限弹窗。本文配置不自动修改任何客户端的审批策略。

## 重启与常见问题

| 现象 | 处理方法 |
| --- | --- |
| 电脑重启后连不上 | 先启动 Tulpa，再重连外部 Agent；需要 QQ 实时事件或操作时同时启动 QQ 和 SnowLuma。普通历史查询不依赖 SnowLuma |
| Connection refused / 连接被拒绝 | 检查 Tulpa 的 MCP 服务是否开启，核对当前端口和 `/mcp`。不要误填 Tulpa 网页地址或 SnowLuma 面板地址 |
| HTTP 401 | 核对本次连接的 Tulpa Token；不能使用 OneBot Token 或模型 API Key。凭据遗失、撤销或换了独立数据目录时，需要重新创建并替换 |
| HTTP 403 / 范围不符 | 查看具体错误及授权的平台、会话、日期、源账号是否一致；客户端勾选不能扩张 Tulpa 授权 |
| 连接成功但新消息查不到 | 历史查询先检查数据库实时读取；持续群聊检查 OneBot 事件连接和活跃会话。截止日期留空才允许未来消息 |
| 看不到发言、群管理或表情包工具 | 检查该连接是否授予对应权限、OneBot 是否有效，再重连客户端；原有只读连接不会自动获得新权限 |
| 文件、语音或等待超时 | 按本文设置客户端的每工具超时；Claude Code 的 `timeout` 按毫秒填写。连接超时和模型任务总时限不是同一件事 |
| Claude Code 换个项目就没有 Tulpa | 原配置可能是默认 `local` 作用域；使用 `--scope user` 或根级 `mcpServers` 配置，并检查同名项目条目是否覆盖它 |
| VS Code 中能用 Claude Code，终端却找不到 `claude` | 扩展不向 PATH 安装独立 CLI；使用扩展的 `/mcp` 管理窗口或按本文合并 `.claude.json` 即可 |
| DSH 改过 Token 仍报错 | 停止旧进程，重新加载 `connection.env` 再启动；刷新网页不更新环境变量 |
| Antigravity 找不到远程地址 | 检查是否用了 `serverUrl`，并确认编辑的是应用实际加载的配置文件 |
| WorkBuddy 显示连接成功但任务不可用 | 检查当前任务的连接器开关、安全中心自定义 MCP 开关；再新建对话验证 |

同一 Tulpa 数据目录、端口和授权未变时，重启后可复用原地址和 Token；保留数据升级见 [UPGRADE.md](UPGRADE.md)。不需要每次重新导入或重新授权。

## 交给 Agent 配置

复制下面这段话给本机 Agent，并把方括号内的内容换成自己的情况。Token 在本机配置界面或凭据文件中填写，不需要贴到公共聊天、Issue 或截图中。

```text
请按 Tulpa 仓库的 doc/AGENT_SETUP.md，为我配置 [Codex / Claude Code / DeepSeek Harness / Antigravity / WorkBuddy] 的 MCP。
教程：https://github.com/fumingyang2004/Tulpa/blob/main/doc/AGENT_SETUP.md

Tulpa 安装目录：[我的安装目录]
MCP 地址：[Tulpa 页面显示的地址]
我已经在 Tulpa 为该客户端创建了独立连接，Token 由我填入本机配置，或从我指定的本机凭据文件读取。

先检查客户端版本与实际配置路径，备份原配置，只合并 Tulpa 条目，保留其他 MCP、模型设置和会话。
不要把 Token 输出到终端、回复或日志，不要上传私人配置。
不要替我扩大 Tulpa 授权，也不要为了测试而自动放开所有工具。
完成后重新连接，并实际调用 get_data_status 和 list_conversations 验证；本次不发送消息、不改群设置。
若需要我处理客户端权限弹窗，请说明点击哪个按钮。
最后告诉我修改了哪个文件、哪些调用成功，以及还有什么未验证。
```
