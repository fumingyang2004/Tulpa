# 持续群聊：先决定，再表达

此协议只作用于 MCP 持续 QQ 群聊。完整版的 MCP 与轻量版共用实现；普通问答、回复助手、记忆和历史检索不调用新模型。Planner 和 Replyer 是同一个外部 Agent 的两个工作阶段，不是两个模型服务。

## 使用顺序

1. 更新并启动实际使用目录的 Tulpa，重连 MCP，确认可发现 `plan_chat_reply`、`send_chat_reply`。原连接的账号、群范围和媒体权限继续有效，不自动扩权。
2. 调用 `list_chat_personas`。新增 `little_whale_v2` 显示为“小鲸鱼2号”；原 `little_whale` 和自定义卡仍可选。
3. 停止原群聊会话，用新开始编号 `start_chat_session(persona_preset="little_whale_v2", ...)`。返回的 `session.persona_preset` 和 `chat_prompt.persona` 应为新卡；重复使用旧开始编号仍返回旧快照，不能用它切人格。
4. `get_chat_session` / `wait_chat_messages` 返回 `input_batch`，包含批次 ID、水位、收集原因及同人短连发的 ID 分组。单条时间、来源和消息 ID 不丢失。
5. Planner 调用 `plan_chat_reply`，传批次、`wait / silence / reply`、简短沟通意图；参与时选目标、相关话题消息及 `text / sticker / reaction`。可附少量主题词。这里只记决定，不发送推理或规划文本。
6. `READY` 后同一个 Agent 组织内容，调用 `send_chat_reply` 提交 1–3 个完整语义气泡。程序先检查，再依次发送，后续气泡有间隔。一个长解释可以仍是一条；不按标点切句。
7. 看回执，确认已处理消息水位后继续等待。沉默、只用图或小回应同样是完整参与；程序不补发说明文字。

```json
{
  "session_id": "来自开始会话",
  "batch_id": "来自 input_batch.id",
  "idempotency_key": "turn-plan-001",
  "action": "reply",
  "target_event_id": 123,
  "expressions": ["text"],
  "intent": "接住对方刚说完的玩笑"
}
```

```json
{
  "session_id": "同一会话",
  "plan_id": "来自 READY 计划",
  "idempotency_key": "turn-send-001",
  "bubbles": [
    {"kind": "text", "text": "第一句完整的话"},
    {"kind": "text", "text": "确实需要时才补充第二句"}
  ]
}
```

单条兼容工具 `send_chat_message`、`send_chat_sticker`、`react_to_chat_message` 保留原参数，新增 `plan_id`；缺少计划返回 `plan_required`。一份计划只提交一个完整队列，组合表达应一次提交。文字首条可选 `quote` / `mention_user_ids`；回应目标来自计划，后续不重复提醒。图片使用既有 `sticker_id` 和已看原图校验。小回应使用既有 `event_id / reaction_id / operation`，原生协议与授权不变。不能通过普通 `send_qq_message` 绕开正在运行的持续聊天队列。

## 等待与新鲜度

沿用尾部静默，默认 2 秒。等待的首个未处理发言者作为本次收集锚点；同一人继续发言、引用该消息或 @ 机器人会延长它，无关热聊不会无限重置。最多收集 8 秒，分页仍最多 50 条。超时/达到页上限只返回材料重新评估，不授权抢答；Planner 会重新核对所选目标的静默。

相关性使用可解释的有限规则：所选目标与话题消息的发言者、原生引用关系、明确 @ 本人和 Agent 提供的少量主题词。未指定目标的主动发言保守地把全部新群友输入视为相关。第二条前还会识别别人引用第一条的插话。这不是完美语义理解：换说话者、无引用、无共同关键词的含蓄纠正可能漏判，Agent 仍应读上下文并谨慎选目标。普通 get 不绕过静默。

`stale`、`needs_refresh`、`plan_expired`、`target_expired` 表示补读后重新规划；`needs_wait` 表示继续等。发送中的相关插话、停止、撤权会取消尚未发送的尾部；已发内容保留。返回每个气泡的 `SUCCEEDED / NOOP / FAILED / UNKNOWN / CANCELLED` 和原操作回执。`UNKNOWN` 不自动重试；重复提交同一计划不会再次发送成功气泡，也不能更改其队列。

不能强制取消所有外部宿主正在生成的文字。可保证的是进入发送接口及每次派发前的检查，不能撤销已交给 QQ 的请求；网络派发与收到新消息仍存在不可消除的极短竞争窗口。服务重启使旧读取批次和未发送计划失效，旧队列不会在后台续发。中断时已派发但没取得回执的气泡保留 UNKNOWN。

## 本地配置与状态

可选文件 `data/mcp-chat-policy.json`，默认不需要创建。保存后下次检查生效；非法字段或数值阻止本功能，不静默放宽限制。现有 wait 的 `quiet_seconds` 只控制读取返回节奏，发送至少遵守本地策略。

| 字段 | 默认 | 允许范围 |
| --- | ---: | ---: |
| quiet_seconds | 2 | 0.1–5 |
| max_collect_seconds | 8 | 1–30 |
| reply_cooldown_seconds | 3 | 0–60 |
| batch_ttl_seconds | 120 | 10–300 |
| plan_ttl_seconds | 90 | 5–180 |
| bubble_min_seconds | 1.2 | 0.1–5 |
| bubble_max_seconds | 4 | 0.1–10，不能小于最小间隔 |
| bubble_seconds_per_char | 0.035 | 0–0.2 |

间隔为上一条字数 × 系数 + 最小间隔，封顶；只加在真实的两次发送之间。不同轮默认冷却 3 秒；小回应原有 10 秒冷却及各工具全局限流仍生效。混合队列不会绕过表情发送额度。

`session.turn_process` 显示 COLLECTING、WAITING、DECIDING、REPLYING、SENDING、STOPPED 及最近计划结果。每会话保留最多 64 个读取批次、近期 200 个普通闭合计划；未解决 UNKNOWN 和底层操作回执不按此清理。计划仅保存在本机 MCP 数据库，不形成长期记忆/群友画像，也不新增模型调用。

## 校验与回滚

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp_chat_turns.py
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp_chat.py
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp_chat_targets.py
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp_reactions.py
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp_chat_media.py
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp_sticker_library.py
```

第一项不替换规划或发送入口，贯通真实 MCP HTTP 与隔离 HTTP/WS OneBot fixture。旧聊天/引用/回应/媒体测试通过明确的测试夹具单独验证适配层，刻意测试未读、伪造目标及派发竞争；它们不代替新协议集成测试。全部自动测试无真实 QQ 写入。真实测试的时间与消息 ID 仅记录在本机私有验收目录，不能用 mock 宣称已经改善所有群的自然程度。

安装版可使用它的 `runtime/python.exe` 调用同一测试脚本，加 `--package <安装目录>`。更新是新增两张独立表，不改授权、原卡或用户数据。备份位置见当次本地更新记录；完全退出 EXE 和托盘后恢复备份程序，保留当前数据库中的兼容表和原操作回执。源码可从开发前检查点另建分支恢复，不 reset/clean 用户工作区。

人工 A/B：分别以新开始编号启动旧卡和2号，确认返回的人格名称及全文；在授权测试群分三条讲一件事，在回复间隙更正它；检查只回应、单条及两条表达是否自然。每次切换先停止旧会话。文件热发现不等于运行中角色会自动更换。

## 参考机制

阅读了 MaiBot 固定提交 `f9ec3670a3638d6a4708ee5253f7ffe2838c9415` 的 [personality 配置](https://github.com/Mai-with-u/MaiBot/blob/f9ec3670a3638d6a4708ee5253f7ffe2838c9415/src/config/official_configs.py)、[决策提示](https://github.com/Mai-with-u/MaiBot/blob/f9ec3670a3638d6a4708ee5253f7ffe2838c9415/prompts/zh-CN/maisaka_chat.prompt)、[表达提示](https://github.com/Mai-with-u/MaiBot/blob/f9ec3670a3638d6a4708ee5253f7ffe2838c9415/prompts/zh-CN/maisaka_replyer.prompt)、[reply 工具](https://github.com/Mai-with-u/MaiBot/blob/f9ec3670a3638d6a4708ee5253f7ffe2838c9415/src/maisaka/builtin_tool/reply.py)及[文本处理](https://github.com/Mai-with-u/MaiBot/blob/f9ec3670a3638d6a4708ee5253f7ffe2838c9415/src/chat/utils/utils.py)。借鉴决策、表达、人格、执行分层的机制；该版本为 GPLv3，本实现没有复制其源码或提示词。保留 Tulpa 的外部 MCP Agent 架构，不引入其第二次回复生成、人类身份或概率/标点拆句。
