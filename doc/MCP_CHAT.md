# MCP 水群人格与接话

仅作用于外部 Agent 的持续 QQ 群聊。完整版的 MCP 与轻量 MCP 使用相同实现；普通问答、帮我回复、记忆、关注卡和工作区不使用这些提示词。只依赖 SnowLuma 实时事件和独立的小型 MCP 缓存，不读大型历史数据库。

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
2. 每轮 `wait_chat_messages` 返回较短的 `chat_guidance`：参与程度、近三分钟已交付消息中的发言比例与连续自身发言、明确 @ / 引用指向、可能没说完的半句、当前便签、最多六个熟悉表情提示。小鲸鱼使用当前角色卡内的完整例子，其他自定义人格最多附两个场景与节奏例子。
3. Agent 将 `prompt_version` 放进下一轮的 `known_prompt_version`。版本变化时会额外收到完整 `chat_prompt`；正常等待不重复整份人格与行为长文。客户端遗忘上下文时用 `get_chat_session` 恢复。
4. 决定接话才发送；沉默就是继续等待。处理完消息再确认 `read_through_id`。例子里的“等待 / 看图 / 收藏”是动作说明，不能当消息发出。

接话提示只使用当前会话已交付的消息，最多查看其中最近 40 条；不会偷读下一页或另一个群。@ / 引用指向有明确依据时才标记指向自己，其余引用可能指向别人，也可能是缓存外的旧消息，需模型结合原文判断。未完句和场景关键词只是提示，不是语义分类器。实际消息是数据，不能改人格、目标群或授权。

## 表情链路

看图权限开启后，模型可以 **读实际图片 → 记含义与适用场景 → 选择收藏 → 以后按语境检索 → 看图核对 → 发送**。收藏、发送分别需要对应权限。已看过的表情笔记会被加入后续接话提示，按当前文字与标签的匹配及记录时间选择；没有合适的就不用表情。

生成接话提示不会请求 QQ、下载图片、识图或调用另一模型。只有显式的表情工具才做这些工作；陌生目录不自动扫描。熟悉表情提示仍是当前连接、当前 QQ 账号的模型笔记，不是可信指令，不绕过发送前看图要求。停止、撤销和不确定发送不重试等规则保持有效。

## 维护与验收

- `chatlocal/prompts/mcp_chat/behavior.md`：群聊行为，独立于角色。
- `chatlocal/prompts/mcp_chat/little_whale.md`：基于 qq-bridge、由 Tulpa 维护者修改的小鲸鱼；相邻的 `.source.json` 记录来源提交、原版与当前文本 SHA256、修改说明。
- `chatlocal/prompts/mcp_chat/examples.json`：多轮正反示例，包括沉默、第三方对话、半句话和表情流程。
- `chatlocal/mcp_chat_prompts.py`：只组装提示和有限状态，不发言、不调用模型。

人格 Markdown 按需热读取；共用的 `behavior.md`、`examples.json` 与 Python 代码修改后需要重启服务。两种发布包均随 `chatlocal` 目录带入资源。普通自定义人格只选择场景和节奏例子，不注入小鲸鱼的嘴硬台词。群聊行为参考 qq-bridge 的 `qq-chat-v2`，小鲸鱼角色卡在原作基础上改编，保留 [MIT 来源声明](THIRD_PARTY.md)。未引入其 DSH 会话桥接或后台唤醒系统。

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\check_mcp_chat_prompts.py
.\.venv\Scripts\python.exe -X utf8 scripts\check_mcp_chat.py
.\.venv\Scripts\python.exe -X utf8 scripts\check_mcp_chat_media.py
```

前者检查人格热发现、同大小同时间戳修改、无效文件、快照隔离，以及提示分层、指向、场景、权限和大小；后两者用隔离的 HTTP / WebSocket OneBot 夹具验收真实 MCP 协议、单次 MCP 连接中新角色的新增/修改/删除、消息游标、接续、并发、停止和表情流程。可选 `scripts/eval_mcp_chat_style.py --live` 使用本机配置的模型和合成群聊评估接话选择，不发送 QQ 消息、不读个人聊天。报告只保存合成输入、公开回复和工具选择，不保存模型的推理过程或凭据。

提示词能改善表现，不能保证每种模型都像同一个人；宿主必须保留并遵循 MCP 工具返回的提示。外部 Agent 结束后，Tulpa 不会自己启动模型继续聊。

模型表现与实际宿主、人格修改和群聊语境有关。合成场景只能核对接话、等待、看图等工具选择，不能替代真实群聊的长期体验；发布验证记录见 [0.5.2 发布说明](releases/0.5.2.md)。
