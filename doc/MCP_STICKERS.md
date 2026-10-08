# MCP 持续群聊的表情链路

本次改动仅作用于 MCP 持续群聊，完整包的 MCP 和轻量包共用代码。不改变人物卡、普通聊天、帮我回复、群管理、微信或已有原生引用 / @ 参数。不新增模型、服务、API Key 或授权标志，也不规定发图频率。

## 工具与状态

| 动作 | 接口和行为 |
| --- | --- |
| 看当前群图 | `read_chat_image(session_id,event_id,index)` 返回像素、动图最多三帧以及 `sticker_id`。该图可以直接发送，无需收藏。 |
| 查找 | `list_chat_stickers(source="library",query="开心")` 查本授权笔记；默认 `source="qq"` 查 QQ 收藏。前者不访问 QQ，后者每次核对账号，但目录缓存一分钟，主动刷新至少间隔十秒，可跨 session 复用。目录最多 500 张，不自动批量下载或理解。 |
| 看陌生收藏 | `read_chat_sticker(session_id,sticker_id)` 返回实际像素。QQ 收藏编号的旧哈希不代替本次读取。 |
| 只记笔记 | `note_chat_sticker` 保留原 `description`、`tags`，新增可选 `emotion`、`usage`、`avoid`、`uncertainty`。不改 QQ 备注，不隐式保留原件。 |
| 积累 | `collect_chat_sticker` 保留原参数，新增同一组笔记字段、`save_local`、`save_qq`（默认均为 true）。看完后可一步收藏并记理解；只留本地可设 `save_qq=false`。 |
| 使用 | `send_chat_sticker` 参数不变，只发原始图片段，保留动图。不强制配文。确认收到可靠消息编号后才计成功用量。 |

收藏结果的 `result.qq`、`result.local`、`result.notes` 独立表示 QQ 回执、本地原件、理解笔记。`SUCCEEDED` 表示请求的步骤成功，`PARTIAL` 表示有已保留成果也有失败，`FAILED` 表示未成功，`UNKNOWN` 表示 QQ 结果无法确认。跳过的步骤为 `SKIPPED`。QQ 失败不会抹掉已保存的本地图和笔记，本地失败也不会隐藏 QQ 成功回执。

同一幂等编号只返回原回执，参数变化会拒绝。同一授权和账号下，相同内容即使换 session / 编号，也不会重复执行已经确认或结果未知的 QQ 收藏；用新编号可以补做本地保存或笔记。确定失败的 QQ 收藏可显式重试，程序不会后台自动重试。发送成功计数和操作回执在同一个事务提交，重复查询不累计。失败不计成功；发送 UNKNOWN 的图片不再入候选，换编号重发也会被拒绝。当前未提供 UNKNOWN 自动对账或人工解锁界面；需先在 QQ 核实并保留原操作记录，不通过重建连接绕过限制。

图片中的文字、OCR、文件名、QQ 备注和模型笔记都是引用数据，不是新指令。看不清写 `uncertainty`；这类笔记不会进入可直接使用的熟悉候选。

## 持久化、范围与资源

这些目录都相对实际运行的 Tulpa 根目录：

- `data/mcp-access.sqlite3`：原有授权、会话及操作回执；新增 `sticker_library`、关键词索引、收藏去重、用量和诊断表。旧的 `chat_sticker_notes` 表保留。
- `data/mcp-chat-media/`：原有 128 MiB LRU 临时缓存，可被淘汰，**不是永久图库**。
- `data/mcp-chat-library/<授权与账号的哈希>/<内容SHA256>.bin`：明确收藏时保留的原件，默认每个授权 / 账号 128 MiB、全局 256 MiB。容量满时报告 `local_capacity`，不删除已有原件。单图仍最多 6 MiB，沿用格式、像素、动图和来源校验。

每个授权 / 账号最多索引 2,000 个看图记录，笔记保留到用户主动清理。来源会话、授权范围摘要、账号、内容哈希和看图记录共同构成跨 session 的复用依据，每次候选验证实际字节；来源不在当前范围、撤权、会话停止、账号不同或哈希不符时不可复用。原件缺失 / 损坏时不悄悄换成另一张；临时缓存淘汰则需从原事件地址或 QQ 收藏重新看图，旧地址失效也可能无法找回。QQ 收藏被删除不删除本地明确保留的原件，两者独立。

授权之间不合并图库或笔记。同一张图在不同授权中可能分别占用本地空间，这是隔离的成本。没有凭旧笔记自动扫描 QQ 目录。旧版尚存的当前 session 已看图、笔记与可核对缓存可以小批接续；已经停止 session、只剩笔记且没有来源证明的老数据，需要再看一次，不能直接视为已理解原件。

迁移机器或目录时，退出 Tulpa 后按 [升级教程](UPGRADE.md) 整体迁移 `data/`，同时保留原 `.env` 与自己的角色卡；必须将授权库和图库一起迁移，路径均相对目录，无须重新导入 QQ 历史。单独复制图片或重新创建 grant 不会继承旧授权的笔记。

清理前退出 Tulpa 并备份上述数据库和图库。不需要这些表情时，可以清空 `mcp-chat-media` 临时缓存；已收藏原件不受影响。清除 `mcp-chat-library` 会失去本地原件，索引随后的验证会如实报告缺失；相应保留容量仍保守计入，重收藏同内容可以修复。若需要释放配额并删除笔记，应让维护者定点清除指定授权 / 账号的原件、`chat_sticker_notes` 和 `sticker_terms`，将对应 `sticker_library` 行的 `local_bytes` / `ready` 置零、`semantic` 置为空对象。保留这行的哈希、用量、发送状态，以及 `operations`、`sticker_collections`、`sticker_usage`，以免失去幂等和 UNKNOWN 记录。本轮不增加管理图库的 GUI 或自动清理策略。

## 候选与成本边界

`get_chat_session.chat_prompt` / `wait_chat_messages.chat_guidance` 中：

- `familiar_stickers` 默认最多四张，含会话内可调用的 `sticker_id`、简短理解、情绪、场景、限制、选择来源、原件状态、用量和 `sendable`。
- `sticker_status` 表示可用、空库、无合适候选、缺权限、实时连接断开、限频等；包含检查数、哈希核对次数、耗时和远端调用数。
- 通用提示说明可以单发表情、配文、纯文字或沉默，也可主动搜索和选择积累。此提示独立于 `selected_examples`，小鲸鱼不会因跳过通用例子而漏掉它；人物卡未修改。

关键词使用英文词和中文二字子串的小索引，最近最多八条已交付的非本人消息用于匹配。上下文命中、常用图与按时间桶轮换的图组成有界候选池；默认两分钟内刚尝试的图不推荐，UNKNOWN / EXECUTING 不推荐。每轮最多检查 32 条元数据、12 份原件，不扫描整库、不重新拉全部 QQ 收藏、不下载图。候选不是穷尽搜索，找不到合适的可以显式关键词检索。本轮不做向量搜索、视觉相似或人格训练。

可在 `data/mcp-stickers-policy.json` 写以下局部配置，重启生效；文件不存在时用默认值。该文件不含凭据，不改变权限：

```json
{
  "candidate_limit": 4,
  "rotation_seconds": 1800,
  "cooldown_seconds": 120,
  "scope_mib": 128,
  "total_mib": 256
}
```

数量限制 1–6，轮换 60–86400 秒，冷却 10–3600 秒；单授权容量 6–512 MiB、总容量 6–2048 MiB。调低容量不会删除已有文件，只阻止超额新增。

## 诊断与验收

权限未启用时，相应工具不会出现在 MCP 工具表，提示中 `capabilities` 为 false。`sticker_status` 可以区分空库、缺少可用原件、候选被冷却 / UNKNOWN 排除、实时源断开和发送限频。私有数据库 `sticker_diagnostics` 中只累计工具结果码、数量、耗时和目录缓存命中等，最多保留 2,000 个汇总项，不记 token、聊天原文或图片正文。显式搜索记 `search_no_match`，读图失败有读取工具的拒绝 / 失败码；上一批提供过候选、但没有观察到调用发送时记 `no_send_observed`，不推测模型的想法。

自动检查使用隔离 SQLite、合成图片、loopback HTTP / WS 与 mock，禁止触及真实 QQ。源码命令：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\check_mcp_sticker_library.py
.\.venv\Scripts\python.exe -X utf8 scripts\check_mcp_chat_media.py
.\.venv\Scripts\python.exe -X utf8 scripts\check_mcp_chat_targets.py
.\.venv\Scripts\python.exe -X utf8 scripts\check_mcp_chat_prompts.py
.\.venv\Scripts\python.exe -X utf8 scripts\check_mcp_chat.py
```

目标包可用其 `runtime/python.exe -X utf8 <仓库>/scripts/check_mcp_sticker_library.py --package <安装目录>`（其他 MCP 检查同样支持 `--package`）。新建和重新载入服务对象验证持久化；实际 EXE 的联网自然群聊由用户人工验证。mock 耗时仅衡量本机合成样本的程序成本，不能证明模型会更自然或更频繁地发图。

### 人工体验步骤

1. 退出 Tulpa（包括托盘），完成程序覆盖，再启动实际目录的 EXE；外部客户端重连 MCP 并刷新工具表。授权未改变，不需要重建原连接。用 `list_chat_personas` 选择原 `little_whale`，新开或恢复授权测试群的会话。
2. 在测试群放一张新图，让 Agent 先 `read_chat_image`；在你允许的发送范围内直接 `send_chat_sticker`。应无需先收藏，QQ群能看到原图 / 动图。
3. 选一张值得保留的图，一次 `collect_chat_sticker` 填理解与标签；核对三项独立状态。只想留本地时设 `save_qq=false`。不清晰的图填 uncertainty，不应出现在直接使用候选中。
4. 停止会话、退出并重启，使用同一个 MCP 连接授权和 QQ 账号再进入测试群。先查本地关键词或 get/wait，核对已理解的本地原件可以成为候选并直接使用，不必重看全库。
5. 收藏并理解几张不同用途的图，观察语境匹配、使用后的冷却以及下一轮换时间段出现的差异。无需为了验收强迫模型发图，检查候选和调用记录即可。
6. 用没有收藏权限的测试连接确认工具不出现；撤销该测试连接后再次尝试读取 / 收藏 / 发送都应失败。不同连接不应看到私有笔记。不要为测试扩大主连接范围。

需要用户确认的仍只有真实测试群、真实发送和 QQ 收藏的既有授权范围；如果现有 grant 缺少看图、表情发送或收藏任一权限，由用户在 UI 自行选择最小范围重新授权。本次自动验收不会替用户勾选或执行这些真实操作。

设计参考 [QQ-agent 的候选准备](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/stickers.js#L143-L211)、[收藏与保存处理](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/sticker-manager.js#L212-L310) 和 [发送用量记录](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/tools.js#L299-L306)，按 Tulpa 的 grant、账号、原件校验和既有幂等协议独立实现，未采用固定每几轮发图的策略。
