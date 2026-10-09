# 七项消息回应：真群验收补充

日期：2026-10-09。源码基线 `c4d76fdb8675d5db0a907e41cfe2123181355705`，工作分支 `codex/mcp-chat-reactions`。这是本地覆盖，不发布 GitHub release。保留原 4 项，新增用户截图中的 7 项，共 11 项；没有改变人物卡、聊天架构或既有授权。

## 编号核对

按照本机 SnowLuma 1.14.20 的 `sys-face-catalog.json` 核对 `qSid` 与 `qDes`。七项均为 Unicode，调用时使用字符的**十进制码点字符串**。不使用外观相似的 QQ face 编号，也不使用 `emCode`。候选及调用标识见[配置表](MCP_REACTIONS.md#本地候选配置)。

## 真实链路

用户明确允许在指定测试群的任意消息上，对这七种回应各添加一次、随后各撤销一次。测试监听连接后，用户发送一条专用标记消息；只把这条新消息交给隔离的 MCP 会话。没有发送聊天文字，没有测试群管理，没有修改实际安装的 grant 或配置。

路径：真实 OneBot WS 收到标记 → `wait_chat_messages` 交付 `event_id` → MCP SDK 的 Streamable HTTP 调用 `react_to_chat_message` → 权限、目标、实例及原生 sequence 预检 → OneBot HTTP `set_msg_emoji_like` → 收到对应实时通知 → 下次正常 `get_chat_session` 显示关联通知。

| 表情 | 下游 `emoji_id` | 添加 `set=true` | 撤销 `set=false` | 实时通知 |
| --- | --- | --- | --- | --- |
| 👍 | `128077` | SUCCEEDED / 0 | SUCCEEDED / 0 | add + remove |
| 😰 | `128560` | SUCCEEDED / 0 | SUCCEEDED / 0 | add + remove |
| 💪 | `128170` | SUCCEEDED / 0 | SUCCEEDED / 0 | add + remove |
| 🐳 | `128051` | SUCCEEDED / 0 | SUCCEEDED / 0 | add + remove |
| ❔ | `10068` | SUCCEEDED / 0 | SUCCEEDED / 0 | add + remove |
| 👊 | `128074` | SUCCEEDED / 0 | SUCCEEDED / 0 | add + remove |
| 😭 | `128557` | SUCCEEDED / 0 | SUCCEEDED / 0 | add + remove |

表中的 0 是原始 `retcode`。共 **14 次真实 reaction 写入，0 次文字发送**，没有重试。维持默认 10 秒冷却，各次间隔至少 10.3 秒；七项添加后保留 60 秒观察，再逐项撤销。每次使用独立固定幂等编号，并在派发前写本地验收账本；未重跑该真实脚本。

收到 14 条与同一目标关联的自身 add/remove 回声；最终上下文按原上限呈现最近 12 条。SnowLuma 在这次撤销通知中仍报告 `count=1`，因此保持 `reported_count=1`，**不解释成撤销后还剩一人**，也不累加成总人数。自己的通知没有触发新发送。

用户随后确认“都正常显示并撤销”。这项人工观察与协议成功、WS 通知分别记录；工具回执仍保留 `client_display_confirmed=false`，因为工具本身不能观察 QQ 界面。七项候选标记 `live_tested`，只代表本次环境的样本验收。

## 离线回归与复现

`scripts/check_mcp_reactions.py` 补充七项精确编号、逐项 add/remove 参数、11 项目录发现，以及冒用 `live_tested` 或更换下游编号的拒绝测试。保留原有负数 ID、空 data、跨群/跨 grant、过期、冻结、撤权、并发、冷却、UNKNOWN、通知去重和纯 reaction 完成本轮等回归。

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp_reactions.py
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp_chat_targets.py
```

实际安装可用其 `runtime\python.exe` 执行同一脚本，加 `--package <实际 Tulpa 目录>`。这些自动回归只用隔离 fixture 和 loopback OneBot，不连接真实 QQ。

复现真实体验需要用户另行授权目标及动作：读取一条新群消息后使用目录中的 `reaction_id` 添加，等冷却后用新的幂等编号撤销同一目标。不把本文示例当作自动发送许可。私有验收脚本、凭据、群号、原始消息 ID、聊天内容和原始日志均不进入源码或发布文件。

## 更新与边界

本次实际覆盖目标由用户确认：`C:\Lab0921\release\0.5.2-mcp\Tulpa-Bot`，保留安装版本号 0.5.2，构建清单记录本地更新 `mcp-reaction-catalog-seven`。程序覆盖前备份到该目录的 `reports\private\updates`，仅恢复被替换的程序/文档/清单即可回退；不要用旧数据库覆盖新的操作回执。

完全退出并重新打开该目录的 `Tulpa.exe`，外部 Agent 刷新或重连 MCP，即可取得新目录。已有回应授权继续有效；未授权连接仍不开放工具。保留用户自定义候选配置、配置、人物卡和数据，不强制改成默认目录。

仅针对一个 SnowLuma 实例、账号和新群消息完成真实验证，不代表所有 QQ 版本、历史消息或其他适配器均支持。本轮真实通知来自机器人自身；他人的添加/撤销、故障和竞态由原有 mock 回归覆盖，未伪称新增真机覆盖。原 4 项仍只有来源/契约验证。
