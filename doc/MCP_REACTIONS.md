# 持续群聊：消息表情回应

消息回应是贴在一条 QQ 群消息上的小表情，与文字、图片和表情包发送并行。仅提供给 MCP 持续群聊；不调用 Tulpa 内置模型，也不自动发送任何文字。模型可以只回应一次，然后继续 `wait_chat_messages`，也可以不回应。

## 开启与关闭

1. 启动实际使用目录的 `Tulpa.exe`，配置同一 QQ 实例的 OneBot HTTP 和正向 WebSocket。
2. 在「外部 Agent / MCP」创建连接时选择允许的 QQ 群，勾选「允许直接发送 QQ 消息」「允许外部 Agent 持续群聊」及新增的「允许对已读群消息添加 / 撤销小表情回应」。无需勾选看图、表情包收藏或群管理。
3. 在外部 Agent 更新该连接的配置并重连。旧 grant 的 `chat_reactions` 默认关闭，更新程序不会给已有连接扩大权限；现有 UI 修改权限的方式仍是由用户撤销旧连接并新建。
4. 选择人物卡、启动群聊、读取消息。`chat_prompt.reactions` / `chat_guidance.reactions` 及工具说明带有候选，不要求模型专门查询目录或额外调用一次模型。
5. 停止群聊会同时停止回应。撤销连接或关闭 MCP 服务会阻止添加和撤销。也可用下述本地配置关闭此子功能。

不要在真实测试前自行扩权或执行示例。只有用户批准的指定群、指定真实消息和具体添加/撤销动作可以用于真群验收。

## 调用与回执

```json
{
  "session_id": "从 start_chat_session 返回的会话编号",
  "event_id": 12,
  "reaction_id": "qq_like",
  "operation": "add",
  "idempotency_key": "my-reaction-add-001"
}
```

工具名为 `react_to_chat_message`。`event_id` 必须是这个 session 的 `context.messages` 或 `wait_chat_messages.messages` 中已读真实消息的 `id`，不是本地 M 编号，也不是 QQ 原始编号。撤销使用同一目标、`operation="remove"` 和这次撤销自己的幂等编号。每会话默认冷却 10 秒，添加与撤销共用。

| state | 含义 |
| --- | --- |
| `SUCCEEDED` | `status=ok` 且 `retcode=0`，允许 `data=null`。仅接口成功，`client_display_confirmed=false`。 |
| `NOOP` | 当前连接已有相同期望状态的成功依据，没有重复写入。 |
| `REJECTED` | 未授权、停用、目标/候选失效、冷却、取消等本地拒绝，没有派发。 |
| `FAILED` | 确定拒绝或连接未建立，提供安全的原因码及可用的 `retcode` / `http_status`。 |
| `UNKNOWN` | 已派发但超时、断线、回执不明或同一目标尚有未知写入；不得自动重试。 |

回执中的 `id` 可交给现有 `get_qq_operation`；部分去重结果使用 `operation_id` 指向先前记录。动作进入现有 `operations` / `operation_events` 和 MCP 调用日志，`kind=reaction`。没有伪造机器人聊天文本，不把没有 `send_chat_message` 误判为尚未参与。已确认成功的回执含 `turn_complete=true`，指本批参与完成，并不停止整个持续会话。

同一请求重试必须沿用幂等编号。添加后撤销是新的期望状态，不会被当成重复添加。同一实例、账号、群、真实消息身份、表情的并发重复写入也会合并。`UNKNOWN` 持久保留并阻止对同一目标表情自动重写或反向重试；不以旧成功覆盖后来的不确定状态。

## 消息边界

- 复用 native quote 的账号、群、发送者、时间及 `get_msg` 校验，保留负数 int32 QQ 消息 ID。内部事件 ID 仅用于查找授权缓存，从不直接交给 QQ。
- 仅使用 `set_msg_emoji_like(message_id, emoji_id, set)`，不再调用另一套 reaction 写接口。
- 对 `message_seq` 缺失、变化或明确不可信的记录在派发前拒绝。SnowLuma 内部额外的 authoritative-sequence 检查如果拒绝，返回确定失败；Tulpa 不猜 sequence，也不扩范围查询历史。
- 只接受当前实时连接收到的普通群消息。通知、摘要、虚拟记录、仅发送回执合成的记录、跨群引用、私聊、未交付、撤回或已淘汰记录不可执行。
- 保留现有日期范围、会话停止、撤权和缓存上限。本次额外让 reaction 引用在 WS 断线/重连或服务重启后失效；旧事件仍可作为有缺口的聊天上下文，但不能直接用于新回应。需等待并读取新真实消息，不延长旧引用寿命。
- HTTP 写入实例必须与 WS 建连时核验过的 HTTP 实例一致；切换账号或节点需重连。这个校验不证明两个由用户误配的不同接入服务一定共享相同 QQ 内部缓存，服务自身的目标校验仍可能拒绝。

## 本地候选配置

可选文件：实际安装目录的 `data/mcp-reactions.json`。不含密钥，不向模型开放修改。每次调用/上下文准备读取，保存后生效；已缓存 MCP 工具说明可通过重连刷新。文件不存在时使用 11 个内置候选；格式错误时仅停用回应，不回退成任意默认表情。

```json
{
  "enabled": true,
  "cooldown_seconds": 10,
  "candidates": [
    {
      "id": "qq_like",
      "name": "赞",
      "type": "qq_face",
      "downstream_id": "76",
      "hint": "结合原话判断是否适合表达认可。",
      "enabled": true,
      "verification": "source_checked"
    },
    {
      "id": "unicode_thumbsup",
      "name": "拇指向上（U+1F44D）",
      "type": "unicode",
      "downstream_id": "128077",
      "hint": "结合原话判断是否适合表达认可。",
      "enabled": true,
      "verification": "live_tested"
    }
  ]
}
```

提供 `candidates` 时替换目录，最多 16 项；删除条目或 `enabled=false` 即禁用。`enabled=false` 放在文件顶层可关闭整个子功能。冷却可设 0–3600 秒，仍受原有每连接全局工具限流约束。

原有 4 个稳定标识保留：`qq_like`（QQ 76 赞）、`qq_smile`（QQ 14 微笑）、`qq_cry_laugh`（QQ 182 笑哭）、`unicode_bless`（Unicode U+3297，十进制 12951）。它们标记 `source_checked`，表示来源与接口契约已核对，**不代表这四项已经完成真群显示测试**。

新增 7 项均为 Unicode 单码点；不是 QQ face 数字编号，也不是目录内部的 `emCode`：

| 表情 | `reaction_id` | 下游十进制 `emoji_id` |
| --- | --- | --- |
| 👍 点赞 | `unicode_thumbsup` | `128077` |
| 😰 紧张 | `unicode_anxious` | `128560` |
| 💪 肌肉 | `unicode_muscle` | `128170` |
| 🐳 鲸鱼 | `unicode_whale` | `128051` |
| ❔ 问号 | `unicode_question` | `10068` |
| 👊 拳头 | `unicode_fist` | `128074` |
| 😭 大哭 | `unicode_loud_cry` | `128557` |

这 7 项标记 `live_tested`：2026-10-09，经用户授权，通过真实 MCP → SnowLuma 1.14.20 在一条新群消息上逐项添加、撤销；14 次接口成功并收到对应通知，用户确认 QQ 中正常显示并全部撤销。它只说明该环境中的样本通过，不保证所有账号、QQ 版本或其他适配器均支持。原有 QQ「赞」与新增 Unicode 👍 是不同编号。详见[验收补充记录](MCP_REACTION_CATALOG_VALIDATION.md)。

新增自定义项默认 `pending`，不可执行；使用者有独立的支持证据并批准后可以标为 `operator_verified`。不能为未知条目冒用 `source_checked` 或 `live_tested`；后者只能用于上表中标识、类型、编号完全一致的条目。QQ face 与 Unicode 是不同编号体系：Unicode 必须是一个十进制码点字符串，不接受字符、组合 emoji、ZWJ 或多码点序列；即便是合法码点，也不表示 QQ 一定支持。失败不替换成其他表情。

升级不会修改现有 `candidates` 自定义目录。若之前手动限定为 4 项，请由使用者按上表增补；或者删除配置中的 `candidates` 字段以使用完整内置目录，同时保留自己的开关和冷却配置。

候选描述只是资料，不覆盖人物卡。不将“微笑”固定为友好，不建立无条件关键词规则，不规定每几条消息必须回应。

## 他人的回应与缓存

接收 SnowLuma `group_msg_emoji_like`，读取 `sub_type=add/remove`、`operator_id`（无字段才回退 `user_id`）、`message_id`、`message_seq` 和 `likes[].emoji_id/count`。通知只关联本群已缓存的真实目标；无关联时安全忽略，不回查更早历史。

如果 WebSocket 节点关闭了上报自身消息，机器人的发言只有发送回执。通知也可关联这类记录，但必须核对同一授权、群、本人账号、原始消息 ID、时间、成功发送操作和当前 HTTP/WS 实例；通知必须提供有效 sequence。这只开放接收观察，仍不能把发送回执当成添加/撤销的可写目标。未知、失败、伪造或其他授权的回执不接受。

正常 `get/wait` 的 `chat_prompt/chat_guidance.reaction_notices` 返回最近最多 12 条、目标已经交付的通知，包含操作者、原消息引用与短摘录、添加/撤销及原样报告的计数。身份/时间/计数缺失时保持 `null`。SnowLuma 解码器可能把缺失 count 补为 1，所以该字段叫 `reported_count`，不用于推断精确人数，也不累加。

每 session 最多缓存 200 条通知。有限窗口按完整字段及最近状态去重，保留 add → remove → add 的真实转换；该上游没有全局唯一通知序号，同一秒的乱序转换无法给出完整排序保证。对明确早于最新通知的时间记录忽略。重复正常查询可能再次带出同一个 `notice_id`，并不表示又发生一次回应。

通知不进入聊天正文或消息游标，不自动发起模型调用。对本人发言的新增他人回应，会按现有 `quiet_seconds` 合并后让已等待的工具返回 `event="reactions"`；即使没有新文字，也允许模型按人格与语境选择接话、贴回应或沉默。自己的回声、身份未知的通知，以及对其他人的回应只作为上下文资料，不单独唤醒等待。

`reaction_notices.new_items` 是本次新交付的他人对本人发言的回应；`items` 是有限历史。get/wait 返回时推进独立交付水位，避免同一通知反复产生参与批次，不改变消息的确认游标。水位不表示模型已经回复，也不保证网络中断时通知恰好交付一次。自己的回声标为 `is_self`，仅用于观察/使相反旧状态失效。此缓存不是完整远端 reaction 历史；重启/重连不恢复未接收通知，也不把旧状态当成当前远端依据。

## 离线验证与人工体验

源码环境执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp_reactions.py
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp_chat_targets.py
```

测试安装目录：使用该目录的 `runtime/python.exe` 调用开发区同一个测试脚本，添加 `--package <实际 Tulpa 目录>`。测试只创建临时数据库、loopback HTTP/WS 模拟 OneBot，不读取实际安装的账号、token、聊天或 grant。`--report <本地文件>` 输出合成验收记录。

人工体验需用户授权后进行：

1. 用仅包含指定测试群的新连接开启回应权限并重连外部 Agent，确认目录和工具可见；确认未授权旧连接看不到写工具。
2. 读一条用户指定真实消息，对该 `event_id` 以 `qq_like/add` 回应。在 QQ 人工核对；至少等 10 秒后以新幂等编号 `remove`，再次核对。不要把此步骤当作给 Agent 的自动发送授权。
3. 由另一位成员手动给同一消息添加、撤销回应，下一次正常 get/wait 应显示准确目标和操作者，没有新增聊天文字。自己的回声不会引发第二次动作。
4. 在目录禁用候选后再次尝试，应明确拒绝；停用会话/撤权、断线或重启后，旧引用不能写入。

## 迁移与回退

只向原有 `data/mcp-access.sqlite3` 添加三张独立 reaction 表及索引，不改旧 grants、聊天、人格或原有消息发送回执。旧 grant 没有新标志时视为未授权。状态缓存可重建，操作记录不可为“解除 UNKNOWN”随便删除。

回应接收修复另向 `chat_sessions` 添加兼容列 `reaction_seen`，记录通知已交付水位。旧代码可以保留并忽略此列，回滚无需删除它。

覆盖前备份旧程序及清单、配置、人物卡、MCP SQLite 一致性快照；运行中的目录不覆盖。回退时先完全退出 EXE 和托盘，仅恢复本次替换的程序/网页/构建清单，新模块留存也不会被旧代码调用。新增表可留着；不要恢复旧数据库覆盖新授权和操作回执，否则会损坏幂等依据。

本地覆盖记录会给出实际安装目录、备份路径、文件哈希和入口校验；界面版本号保持原安装版本，构建清单另记 `mcp-message-reactions`。无需重新导入聊天数据，无需修改人物卡或配置新模型。

## 参考与范围

- [SnowLuma 固定版本接口](https://github.com/SnowLuma/SnowLuma/blob/87527cb7641a5a42f8f0efb73cb066102e004dee/packages/onebot/src/actions/extended.ts)：本机 1.14.20 已按安装代码复核动作、通知及 sequence 检查。
- [MaiBot 表情回应插件 v0.3.1](https://github.com/cateyemizuki/cateye_set_msg_emoji_like_api_modify/tree/v0.3.1)：参考目录、负数 ID、虚拟消息过滤及冷却思路。Tulpa 独立实现，未复制其 SDK、代码或描述文案；不采用其随机/关键词自动回应或默认表情回退。

不实现完整远端状态对账、未知结果人工处理后台或全历史同步。工具不会自动证明客户端显示；验收记录中的目视确认由用户提供。真群实验仍需具体授权，其他账号/QQ 版本及他人通知的真实时序需要各自验证。
