# 专用聊天连接：减少空闲调用和重复人设

普通 HTTP MCP 仍可使用。长时间群聊可改用 `tulpa chat-bridge`：宿主通过 stdio 连接桥接进程，桥接使用原凭据连接 Tulpa 的 HTTP MCP。它保持连接、合并空轮询、精简成功回执，不调用任何模型，也不自动生成群回复。

## 接入

先按 [Linux Agent 接入](LINUX_AGENT_SETUP.md) 保存一个带持续聊天和发送权限的 HTTP MCP 配置，例如 `/home/me/tulpa-agent.json`。它必须是含原 HTTP 地址和凭据的配置，不能指向桥接自身的配置。多连接文件用 `--server` 指定名称。

在宿主的**独立群聊对话**中安装一个 stdio MCP 连接。例如支持 `mcpServers` 格式的客户端可使用：

```json
{
  "mcpServers": {
    "tulpa-chat": {
      "command": "/absolute/path/Tulpa/tulpa",
      "args": [
        "chat-bridge",
        "--config", "/home/me/tulpa-agent.json",
        "--conversation", "ACCOUNT:group:GROUP",
        "--idle-wait-seconds", "1800",
        "--duration-seconds", "1800"
      ]
    }
  }
}
```

替换程序路径及完整会话编号。路径中有空格时，JSON 字符串原样填写，勿在字符串内再加 shell 引号。好友私聊用 `ACCOUNT:direct:QQ`。凭据只从配置文件读取，不放命令行中。远程 Tulpa 需先建本地 SSH 转发，配置读取器只接受本机 `/mcp` 地址。

这条命令由 MCP 宿主启动，标准输入/输出用于协议，不会打开 TUI。在普通终端直接运行时会等待 MCP 请求。可先用 `./tulpa chat-bridge --help` 查看选项。

| 选项 | 行为 |
| --- | --- |
| `--idle-wait-seconds` | 单次模型工具调用合并空轮询的最长秒数，默认 1800，范围 1–43200；到期返回 `idle_window_elapsed`，会话仍可继续等待 |
| `--duration-seconds` | 从第一次启动或接续该会话起计时，进程内独立定时停止；不因后续工具调用续期。省略则不限制总时长 |
| `--conversation` | 此桥接只提供这个会话；换群需新桥接、新宿主对话，建议使用独立授权 |

**宿主的工具超时要长于空闲等待上限**，例如等待 1800 秒时预留至少 1900 秒；字段名和单位按各宿主配置。若宿主只能等 240 秒，将 `--idle-wait-seconds` 设为 180。`wait_chat_messages.timeout_seconds` 在桥接里仅控制一次后台轮询，实际每次最多 30 秒。桥接进程合并轮询不代表后台完全没有 HTTP 请求。

同一个群不要同时从 HTTP 连接和桥接运行两套回复循环。切换时先结束原循环，再在新宿主对话恢复人设；桥接不能删除宿主已有的工程历史，也不能把一个现有对话自动拆成多个独立模型上下文。

## 人设与返回内容

- `list_chat_personas` 默认只返回名称、ID 和摘要；需要全部全文时用 `include_content=true`。
- `start_chat_session` 和不带 `known_prompt_version` 的 `get_chat_session` 完整返回所选人格、行为和说明，全文只在 `chat_prompt.persona` 出现一次。
- 后续 `get_chat_session` / `wait_chat_messages` 带上仍保留在上下文中的 `known_prompt_version`。版本相同仅返回动态提示；版本不同自动补完整提示。服务不会凭“之前曾返回过”就假定新的宿主仍记得。
- 新对话、上下文压缩后丢失人设，或需要恢复完整说明时，省略 `known_prompt_version`，或用 `get_chat_session(response_format="full")`。
- 默认 `response_format="compact"` 保留原始消息、看图入口、权限、回应通知和必要的学习状态；省略固定示例与重复说明。`full` 可以读取完整诊断。
- 桥接对成功发送只返回气泡状态、消息/操作 ID 和详情查询入口；`REVIEW_REQUIRED` 的草稿及 `UNKNOWN` 等异常回执保持完整。原操作仍可用 `get_qq_operation` 查询，最近计划可用完整 `get_chat_session` 查看。

省略重复返回并不等于首次加载的人格从模型历史中消失。模型的历史计费、缓存和上下文压缩由宿主决定；此功能减少的是新增的重复工具内容及空闲模型往返。

## 新消息与回复

默认 `new_message_policy="review"` 使用以下流程：

1. 根据已交付的 `input_batch` 规划并组织草稿。
2. `send_chat_reply` 若发现相关新消息，返回 `REVIEW_REQUIRED`，原草稿保存在计划中，没有发送任何气泡。
3. 阅读 `review.messages`，沿 `next_call` 调用 `review_chat_reply`：`keep` 保留、`replace` 替换完整气泡数组、`cancel` 取消。保留或替换会直接发送，无需再调用 send。若要换目标或表达通道，取消后重新规划。
4. 每份计划最多复核一次。复核后新到的普通消息，以及气泡发送间隙的普通插话，留到下一轮；不会让同一回复无限退回重写。未遇新消息的计划直接发送。

复核最多返回 50 条新增事件，包括同一水位内的其他话题，避免断章取义。超过上限返回 `needs_refresh`，分页补读后重新规划。复核不自动确认这些新消息的处理进度，下一次 wait 仍会交付，避免吞掉新话题。程序只判断原生引用、@、参与者连续补充等结构相关性，不替模型判断更正是否改变答案。

停止会话、撤权、目标撤回、断线或缓存缺口、计划过期和客户端取消仍是硬中断；每次实际派发前继续检查。已交给 QQ 的请求无法撤回。一个已开始的短回复可能晚于对方的补充，这是一次复核上限的明确取舍。需要原有行为时在 plan 指定 `new_message_policy="strict"`，相关插话仍返回 `stale` 并取消旧尾部。

相同 `review_id` 和决定只返回原结果；更改已提交决定会被拒绝。`UNKNOWN` 不自动重试或换编号补发。学习表达选择仍必须完成，复核不能绕过这一步。

## 停止与更新

桥接不会在工具调用外发送聊天内容。设置总时长后，即使模型还在生成，定时器也会请求停止会话并阻止新的写入；若后台失联，返回停止结果未知，不宣称远端已经停止。定时器依赖桥接进程存活，进程被强杀或机器关机时不能保证远端会话标志立即关闭。

正常退出桥接会尝试停止通过它显式 start 的会话。只读查看已有会话不会使退出操作停止他人的会话。取消一次 wait 仅取消等待；结束聊天应显式调用 `stop_chat_session`。

更新源码后需重启 Tulpa 后台并重连 MCP，才能加载新协议；已经运行的旧进程不会热加载 Python 文件。Docker 托管会随当前后台的正常退出停止，重启可能需要重新扫码。先结束正在运行的群聊，再选择合适时间更新。无需重建 MCP 授权或更改人格卡。

隔离验证：

```sh
.venv/bin/python scripts/check_mcp_chat_bridge.py
.venv/bin/python scripts/check_mcp_chat_turns.py
.venv/bin/python scripts/check_mcp_chat.py
```

测试使用临时数据库、本机模拟 OneBot 和真实 MCP stdio/HTTP 传输，不调用真实模型、不向真实 QQ 写入。它验证协议和取消/重试边界，不代表所有宿主都会使用更少的计费 token，或真实群聊里的每次回复都更自然。
