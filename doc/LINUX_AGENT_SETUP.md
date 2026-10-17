# Linux：将 Tulpa 接入外部 Agent

Tulpa 提供 Streamable HTTP MCP 服务。外部 Agent 负责模型推理，Tulpa 负责资料、
权限、人格和 QQ 接入；仅使用 MCP 时，无需在 Tulpa 配置模型 API Key。

## 创建连接

1. 运行 `./tulpa`，按 [Linux 使用说明](LINUX_TERMINAL.md) 接通 SL，或先导入需要查询的历史资料。
2. 打开「MCP 授权」，启用 MCP，并为这个 Agent 创建独立连接。
3. 选择明确的平台、会话和日期。实时群聊的截止日期留空；持续聊天和发送需分别授权。
4. 保存创建结果中的 MCP 地址、Token 和客户端配置。Token 仅在创建时显示，遗失后需撤销旧连接并新建。

已有连接的能力权限可在终端「MCP 授权」中选中后按 `e` 修改，或使用
`./tulpa connections edit CONNECTION_ID --chat-voice`。原 Token 保持有效；
保存实际变更后重新开始该连接的聊天会话，客户端有缓存时刷新或重连 MCP。

只读取资料时不要开启发送和群管理。图片、表情发送/收藏、回应等能力也各自授权。
MCP 写操作一旦授予即持续有效，调用时不会再回到 Tulpa 逐条审批。

## CLI 示例

先运行 `./tulpa start`。以下示例明确授予一个 QQ 群的持续聊天和发送权限：

```sh
./tulpa groups
./tulpa mcp enable
./tulpa connections create --name my-agent --group ACCOUNT:group:GROUP \
  --onebot --chat --send --output ./my-agent-mcp.json
```

将 `ACCOUNT:group:GROUP` 替换为 `groups` 返回的完整群编号。多个群可重复传入 `--group`。
`--output` 以权限 600 新建文件，不覆盖已有文件。它包含真实 Token，应保存在自己的配置中。

Tulpa 输出的 JSON 使用 Claude Code 的 HTTP MCP 字段格式，结构如下；真实内容以创建结果为准：

```json
{
  "mcpServers": {
    "tulpa": {
      "type": "http",
      "url": "http://127.0.0.1:18777/mcp",
      "headers": {"Authorization": "Bearer YOUR_TULPA_MCP_TOKEN"},
      "timeout": 240000
    }
  }
}
```

把 `tulpa` 条目合并到客户端自己的 MCP 配置，保留其他设置；同名条目应修改原条目。
其他 Agent 按其配置格式填写下表中的连接信息，不能假定它们使用同一种 JSON 文件。

| 字段 | 内容 |
| --- | --- |
| 传输方式 | Streamable HTTP |
| URL | 创建连接时显示的地址，保留末尾 `/mcp`；默认端口 18777 |
| 请求头 | `Authorization: Bearer <本连接的 Token>` |
| 工具超时 | 建议预留 240 秒，具体单位按客户端字段定义填写 |

这里的 Token 是 MCP 连接凭据，与模型 API Key、SL HTTP/WS Token、终端管理凭据及 VNC 密码不同。
管理端口 7861 也不是 MCP 端口。`127.0.0.1` 指客户端所在机器；远程使用时应先建立 SSH
本地端口转发，再按转发后的地址配置。不要直接将管理或 MCP 端口暴露到公网。

## 验证与群聊

长时间群聊可使用 [专用 stdio 聊天桥接](MCP_CHAT_BRIDGE.md)：合并空闲轮询，按版本返回人设，并支持到期停止。它复用 HTTP 凭据；为聊天单独开宿主对话，并将工具超时设得比合并等待上限更长。

保存后重新连接客户端 MCP，在专用对话中让 Agent：

> 检查 Tulpa 的资料状态，列出我授权的会话和可用人格，先不要发消息。

工具实际返回结果后，再选择目标群和人格开始。仅显示“配置已保存”不代表认证成功。
TUI「工具调用」或 `./tulpa events` 可查看调用状态。

Tulpa 可以预先准备会话：

```sh
./tulpa connections list
./tulpa personas
./tulpa sessions start --connection CONNECTION_ID --group ACCOUNT:group:GROUP \
  --persona little_whale_v2
./tulpa sessions list
```

替换连接编号和群编号。显示「等待 Agent」时，需要外部 Agent 连接并持续调用聊天工具；
Tulpa 不会自动启动外部 Agent，也不会在它停止执行后继续生成回复。
自定义人格放进本实例 `chatlocal/prompts/mcp_chat/`，重新列出即可发现；已有会话保留创建时的人格快照。

```sh
./tulpa sessions stop SESSION_ID
./tulpa connections revoke CONNECTION_ID
```

停止会话只停止该会话；撤销连接会同时取消它的其他权限。已发送的消息不会因此撤回。

## 发送生成的图片

外部 Agent 生图后，可以通过 **导入图片 → 规划回复 → SL 发送到 QQ** 的流程发图。
生图由外部工具完成；Tulpa 接收原图并沿用当前会话的群权限、发送队列和回执，不需要另外连接 SL 的 MCP。
连接需具备持续聊天、发送、看图和发送表情权限；已有这些权限的连接可以直接使用新接口。

先通过 `start_chat_session` 获取目标群的 `session_id`，再调用
`import_chat_image(session_id, data_base64)`。`data_base64` 是文件原始字节的纯 Base64，
不带 `data:` 或 `base64://` 前缀；不接受文件路径或 URL。支持 PNG、JPEG、GIF、WebP，
单图最多 6 MiB，并沿用图片尺寸和动画帧数限制。接口返回预览像素、`sha256` 和本会话的 `sticker_id`，**导入本身不发送消息**。

图片在 Agent 所在机器上时，建议用随包脚本读取并上传，避免把大量 Base64 放进对话：

```sh
.venv/bin/python scripts/import_chat_image.py \
  --config /path/to/tulpa-client.json \
  --session SESSION_ID \
  "/path/to/generated image.png"
```

`--config` 支持 Tulpa 导出的连接 JSON、Claude Code 格式 JSON 和 Codex 格式 TOML。
多个连接时用 `--server tulpa` 指定名称；Token 从配置或其指定的环境变量读取，不放进命令参数。
脚本只上传，不自动开启会话或发消息；终端输出图片编号和元数据，不输出原图 Base64 或凭据。
远程 Tulpa 先做 SSH 本地端口转发，文件由脚本所在机器读取，无需复制进 SL 容器。

核对生成图或用 `read_chat_sticker(session_id, sticker_id)` 查看导入后的预览，然后：

1. `get_chat_session` 读取最新的 `input_batch.id`。
2. `plan_chat_reply` 使用该批次，选择 `action="reply"`、`expressions=["sticker"]`，填写本次发送意图。
3. 取得 `READY` 的 `plan_id` 后，调用 `send_chat_reply`，其中 `bubbles` 为
   `[{"kind":"sticker","sticker_id":"上一步返回的图片编号"}]`；也可用带 `plan_id` 的 `send_chat_sticker`。

这里的 `sticker` 表达通道会发送普通 QQ 图片段，保留原图字节和动画，不要求先收藏到 QQ。
发送和导入是两个独立步骤：同一会话重复导入相同内容返回同一编号；发送重试沿用原计划和幂等编号，
`UNKNOWN` 不换编号重发。会话停止后原编号不可继续使用；缓存被清理时可重新上传原文件。
导入每个连接每小时最多 60 次，使用独立媒体通道；不消耗普通聊天的调用额度。

## 语音、文件、私聊与 SL 扩展

好友目录可用 `./tulpa friends` 或 MCP `list_chat_friends` 查询；授权时使用
`--conversation ACCOUNT:direct:FRIEND`，开启会话后沿用原有的人格、等待和发送计划。
新增附件导入脚本为 `scripts/import_qq_media.py`，支持语音、视频及文件。
权限选项用 `./tulpa connections permissions` 查询；旧连接不会自动获得新增独立权限。
空间、戳戳、撤回、转发、好友、个人资料及群管理的具体工具、参数与限制见
[SL 扩展工具](SL_TOOLS.md)。

## 常见问题

- **连接拒绝**：确认 `./tulpa status` 显示后台运行、MCP 已启用，并核对端口和 SSH 转发。
- **认证失败**：核对 Token 对应的连接是否被撤销，以及请求头是否保留 `Bearer` 后的空格。
- **群为空或缺工具**：核对 SL 登录账号、HTTP/正向 WS、连接授权范围及日期；工具随权限提供。
- **查不到历史**：实时群聊不自动导入整库。先导入可读的本机资料，再授权对应会话。
- **没有继续回复**：确认外部 Agent 仍在执行，未因预算、超时或宿主退出而停止。
- **关闭界面后还能连接**：这是正常行为；`q` 只退出界面，`./tulpa stop` 才停止后台。
