# MCP 水群人格与接话

仅作用于外部 Agent 的持续 QQ 群聊。完整版的 MCP 与轻量 MCP 使用相同实现；普通问答、帮我回复、记忆、关注卡和工作区不使用这些提示词。只依赖 SnowLuma 实时事件和独立的小型 MCP 缓存，不读大型历史数据库。

0.5.5 加入 [Planner / Replyer 与发送队列](MCP_CHAT_TURNS.md)：get/wait → plan_chat_reply → send_chat_reply。单条兼容发送也需计划；程序检查静默、相关新消息和气泡间中止。新增独立“小鲸鱼2号”（`little_whale_v2`），保留原卡。升级后重连 MCP，停止旧群聊并用新开始编号选择人物卡。

## 使用

先按 [MCP 配置](MCP.md#持续群聊snowluma-实时事件) 开启持续群聊和发送权限，并保持外部 Agent 执行。在专用对话中说：

> 用小鲸鱼预设在“测试群”持续聊天，直到我停止。自然参与，少用问号。

Agent 用 `list_chat_groups` 核对群，用 `list_chat_personas` 查看角色，随后开启：

```json
{
  "conversation_id": "从 list_chat_groups 取得的完整会话编号",
  "persona_preset": "little_whale",
  "persona": "少用问号",
  "participation": "natural",
  "idempotency_key": "此次开始的唯一编号"
}
```

`persona_preset` 与 `persona` 至少提供一个。只写 `persona` 就完全使用自定义人格；一起提供时，自定义文字补充预设。开始时保存人格全文快照，旧会话不会因预设文件变化被替换；更换人格需要停止后重新开启。相同开始编号的重试返回原会话，不重复创建。

## 随包人格

0.5.3 包含维护者本次修改的小鲸鱼（`little_whale`）与新增的 GPT 白龙娘（`dragon_girl`）。两张卡片均完整随包，选择 `persona_preset: "dragon_girl"` 即可使用龙娘。它们是角色设定，不代表当前宿主换用了 DeepSeek 或 GPT 模型。

升级后先调用 `list_chat_personas` 核对当前文件；已开启的会话继续使用创建时的快照，需停止后重新开始才会采用新卡。开发区和安装目录各自热读取自己的文件。

## 新增或修改人格：保存 Markdown 即可

在**当前运行的 Tulpa 安装目录**下，打开 `chatlocal/prompts/mcp_chat/`，新建 UTF-8 编码的 `.md` 文件。例如 `夜猫子.md`：

```markdown
# 夜猫子

你是喜欢电影的群友，说话简短，有点冷幽默。
能接上话就说一句，接不上就继续听。不要把每句话变成建议。

## 对话示例
群友：这电影看得我都睡着了
你：至少失眠治好了
```

保存后告诉外部 Agent：

> 先查看现在有多少个人格，列出名称。然后用“夜猫子”在测试群持续聊天，直到我停止。

Agent 调用 `list_chat_personas`，得到最新的 `count`、`personas` 和 `warnings`；选择返回的 `id`，传给 `start_chat_session.persona_preset`。上面的 id 是 `夜猫子`，显示名取文件中的第一个一级标题，没有标题就用文件名。中文文件名和 UTF-8 BOM 均可使用，无需注册 Python 字典或修改配置。

- 每次列出人格、新建会话都会读取当前文件；增加、修改、改名、删除均无需重启 Tulpa 或重新连接 MCP。工具参数没有固定的人格枚举，客户端缓存工具定义也不会挡住新文件。
- 只扫描该目录第一层的普通 `.md` 文件；`behavior.md` 是共用行为规则，`README.md` 是说明，均不作为人格。隐藏文件、子目录中的文件不会加载。
- 空白、非 UTF-8、不可读、链接、超过 64 KiB 或重名的文件不会作为可用人格；`warnings` 会说明原因，其他有效人格仍可使用。文件不截断，不回退到过期副本。
- 已开启会话保留开始时的人格全文和显示名。改删文件不会改变它；用原开始编号重试也会返回原会话。要采用新内容，停止旧会话后用新的开始编号创建会话。
- 开发目录与 release 安装目录相互独立。将卡片放入实际运行的那一份目录；升级时保留自己新增的人格文件；修改过随包预设时，先按 [升级教程](UPGRADE.md) 另存一份。完整包和轻量 MCP 包使用相同机制。

这次代码升级本身仍需重启旧服务并刷新一次客户端工具定义；此后修改人格文件无需重启。人格只影响 MCP 持续群聊，不改变普通聊天、帮我回复、关注卡或工作区。

## 小鲸鱼的来源

小鲸鱼基于 [qq-bridge 原版角色卡](https://github.com/Derpyu520/qq-bridge/blob/9df6a7e7fc5abcb36793337f778483bd442a3d2d/roles/小鲸鱼.md) 改编，保留 DeepSeek 小鲸鱼身份与角色结构，并纳入 Tulpa 维护者对口语、调侃、复读和回复示例的修改。这是角色设定，不会切换宿主实际使用的模型。当前角色卡与来源摘要分别保存于 `little_whale.md` 和 `little_whale.source.json`。

人物表达以当前角色卡为准，不额外叠加另一套小鲸鱼台词。Tulpa 仍负责自身 MCP 工具、权限、等待、发送和停止协议，没有引入 qq-bridge 的其他桥接服务。安静、自然、活跃三个参与程度仍可选择。

## 提示怎样给到模型

1. `start_chat_session` / `get_chat_session` 返回工具协议 `instructions`，以及完整 `chat_prompt`：群聊行为、人格快照、相关示例、当前上下文提示。
2. 每轮 `wait_chat_messages` 返回较短的 `chat_guidance`：参与程度、近三分钟已交付消息中的发言比例与连续自身发言、明确 @ / 引用指向、可能没说完的半句、当前便签、默认四个通过原件核对的表情候选（可配置为最多六个）。小鲸鱼使用当前角色卡内的完整例子，其他自定义人格最多附两个场景与节奏例子。
3. Agent 将 `prompt_version` 放进下一轮的 `known_prompt_version`。版本变化时会额外收到完整 `chat_prompt`；正常等待不重复整份人格与行为长文。客户端遗忘上下文时用 `get_chat_session` 恢复。
4. 决定接话才发送；沉默就是继续等待。处理完消息再确认 `read_through_id`。例子里的“等待 / 看图 / 收藏”是动作说明，不能当消息发出。

接话提示只使用当前会话已交付的消息，最多查看其中最近 40 条；不会偷读下一页或另一个群。@ / 引用指向有明确依据时才标记指向自己，其余引用可能指向别人，也可能是缓存外的旧消息，需模型结合原文判断。未完句和场景关键词只是提示，不是语义分类器。实际消息是数据，不能改人格、目标群或授权。

## 表情链路

看图权限开启后，模型可以 **读实际图片 → 直接发送，或选择收藏并同时记理解 → 后续会话按语境选择 → 发送后累计用量**。收藏、发送分别需要对应权限。收藏默认同时做 QQ 收藏和本地原件保留，可用 `save_qq=false` 只留本地；QQ、本地和笔记分别报告结果。未知图仍必须先看像素，当前群图无需先收藏才能发送。

生成接话提示只查本地索引并校验少量原件哈希，不请求 QQ、下载图片或调用另一模型。候选覆盖关键词匹配、常用和轮换，刚使用或结果未知的图会被抑制。可靠理解与原件在同一连接授权及 QQ 账号下可以跨会话复用；单凭旧笔记或旧 QQ 编号不能变成已看图。没有候选时如实返回状态，模型也可以只发文字或沉默。小鲸鱼同样收到通用工具提示，人物卡不变。完整接口、容量、迁移、诊断及人工验收见 [表情链路说明](MCP_STICKERS.md)。

## 发言时的引用与 @

`send_chat_message` 保留原来的 `session_id`、`text`、`idempotency_key`，默认只发普通文本。新增三个可选参数，彼此分开：

| 参数 | 用途 |
| --- | --- |
| `reply_to_event_id` | 指定正在回应哪条已读取事件；使用当前会话 `context.messages` 或 `wait_chat_messages.messages` 中的 `id`。只记录回应目标，不自动显示引用。 |
| `quote` | 默认 `false`。明确设为 `true` 才提交 OneBot 原生 `reply` 段，必须同时提供 `reply_to_event_id`。 |
| `mention_user_ids` | 默认空列表。用可靠的 QQ 成员号（例如 `sender_id`）提交原生 `at` 段，发送前向 OneBot 核实成员身份。最多 5 人，不支持 `all`、昵称或重复成员。 |

近距离接话、对象明确时直接说。回复较早内容、话题被插开或多人交叉容易混淆时再引用；确实点名、向某位群友发问时才 @。两者可以同时指定，但程序不会因为引用就自动添加 @。一段话拆成多条通常只在首条挂标记，后续省略；选项不会自动继承到下一次调用。

提示会提供最近已交付窗口中最多 8 个目标的时间间隔、后续消息数和发言人数。这只是有限窗口内的参考，模型结合语境决定，不按固定条数强制引用。

引用目标必须属于当前会话、账号和群，且确实已经由工具交付并仍在事件缓存中。`reply_to_event_id` 是事件行的 `id`，不能填历史 `M` 编号、原生 QQ 数据库编号、`onebot_message_id`，也不能拿分页进度代替具体消息。程序从事件映射 OneBot 编号，并用 `get_msg` 再核对群、发送者和时间；支持 OneBot 的有符号消息编号。检测到撤回、缓存淘汰、映射失效或无法核实时明确报错，不换对象或悄悄降级发送。校验依赖当前 OneBot 的查询与事件；断线期间漏掉撤回且服务端仍返回旧记录时，不能保证发现这次撤回。已经获取的确定回执和 `UNKNOWN` 仍以原幂等编号查询，不会再次发送。

正文中的 `[CQ:...]` 保持普通文本。回执的 `message_segments` 是程序实际提交的消息段，`mention_user_ids` 仅表示本次主动要求的 @；某些 OneBot / QQ 实现可能因引用自行附加 @，这不等于 Tulpa 又添加了一次。自发缓存先标记为 `send_receipt`，收到同编号的真实回传后更新为 `onebot_websocket`，保留同一事件编号及 `send_request`，可区分请求结构与实际回传。真实 QQ 的最终外观需用户在所用版本中核对。

升级这些 Python 模块后重启 Tulpa，并在外部客户端重连 MCP 或开启能刷新工具定义的新对话；已有角色卡和已开启会话的人格快照保留。普通“帮我回复”、图片与表情包工具不使用这组新增参数。

## 维护与验收

- `chatlocal/prompts/mcp_chat/behavior.md`：群聊行为，独立于角色。
- `chatlocal/prompts/mcp_chat/little_whale.md`：基于 qq-bridge、由 Tulpa 维护者修改的小鲸鱼；相邻的 `.source.json` 记录来源提交、原版与当前文本 SHA256、修改说明。
- `chatlocal/prompts/mcp_chat/dragon_girl.md`：Tulpa 维护者提供的 GPT 白龙娘设定，完整文本由人格热发现机制加载。
- `chatlocal/prompts/mcp_chat/examples.json`：多轮正反示例，包括沉默、第三方对话、半句话和表情流程。
- `chatlocal/mcp_chat_prompts.py`：只组装提示和有限状态，不发言、不调用模型。

人格 Markdown 按需热读取；共用的 `behavior.md`、`examples.json` 与 Python 代码修改后需要重启服务。两种发布包均随 `chatlocal` 目录带入资源。普通自定义人格只选择场景和节奏例子，不注入小鲸鱼的嘴硬台词。群聊行为参考 qq-bridge 的 `qq-chat-v2`，小鲸鱼角色卡在原作基础上改编，保留 [MIT 来源声明](THIRD_PARTY.md)。未引入其 DSH 会话桥接或后台唤醒系统。

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\check_mcp_chat_prompts.py
.\.venv\Scripts\python.exe -X utf8 scripts\check_mcp_chat.py
.\.venv\Scripts\python.exe -X utf8 scripts\check_mcp_chat_media.py
.\.venv\Scripts\python.exe -X utf8 scripts\check_mcp_sticker_library.py
.\.venv\Scripts\python.exe -X utf8 scripts\check_mcp_chat_targets.py
```

前者检查人格热发现、同大小同时间戳修改、无效文件、快照隔离，以及提示分层、指向、场景、权限和大小；后两者用隔离的 HTTP / WebSocket OneBot 夹具验收真实 MCP 协议、单次 MCP 连接中新角色的新增/修改/删除、消息游标、接续、并发、停止和表情流程。可选 `scripts/eval_mcp_chat_style.py --live` 使用本机配置的模型和合成群聊评估接话选择，不发送 QQ 消息、不读个人聊天。报告只保存合成输入、公开回复和工具选择，不保存模型的推理过程或凭据。

提示词能改善表现，不能保证每种模型都像同一个人；宿主必须保留并遵循 MCP 工具返回的提示。外部 Agent 结束后，Tulpa 不会自己启动模型继续聊。

`check_mcp_chat_targets.py` 用隔离 OneBot HTTP / WebSocket 与真实 MCP HTTP 验证纯文本兼容、原生引用和 @、目标校验、分页交付边界、回执/回传去重、停止/撤权竞争、幂等及 UNKNOWN；不向真实 QQ 群发送消息，不调用外部模型。可用 `--package <安装目录>` 在目标 release 的模块上运行。通过夹具不代表真实 QQ 的引用外观或隐式 @ 已实测。

模型表现与实际宿主、人格修改和群聊语境有关。合成场景只能核对接话、等待、看图等工具选择，不能替代真实群聊的长期体验；发布验证记录见 [0.5.5 发布说明](releases/0.5.5.md)。

## 消息表情回应

MCP 持续群聊现在可在独立授权下对已读真实群消息添加或撤销小表情回应，并在正常上下文中看到他人的回应通知。它与图片/表情包权限独立，旧连接不会自动获得权限。启用、目录配置、回执、边界及人工步骤见 [消息回应说明](MCP_REACTIONS.md)。
