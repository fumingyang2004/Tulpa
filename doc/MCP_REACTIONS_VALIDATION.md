# 消息表情回应：验收记录

实现基线：`c4d76fdb8675d5db0a907e41cfe2123181355705`；开发分支 `codex/mcp-chat-reactions`。这是本地功能覆盖，不是新的 GitHub release。人物卡未修改。

本页记录最初的离线验收。随后新增的 7 项候选已另行取得真群测试授权并完成测试；见[七项候选真群验收](MCP_REACTION_CATALOG_VALIDATION.md)。下文“未实测”是本次初始离线记录的范围，不覆盖该补充测试。

## 验证环境

2026-10-09，Windows x64，Python 3.13。全部自动验收使用临时数据库、合成消息，以及独立 loopback OneBot HTTP / WebSocket 服务；未连接真实 QQ 接口、未发送真实消息、未贴真实回应，也未新增真实授权。

`scripts/check_mcp_reactions.py` 使用实际 MCP SDK 和 HTTP 传输，并校验导入的代码路径。使用 `--package` 时读取指定安装目录的模块，但账号、消息、权限及接口仍全部来自临时 fixture。测试不使用安装目录的 `.env` 或用户数据库。

## 两条贯通路径

### 已读消息 → 添加 → 撤销

合成账号 `111`、群 `222`；读取由模拟 WS 收到的真实格式群消息，原始 OneBot ID 为 `-9010`，native sequence 为 `9110`。工具只接收当前 session 的 `event_id`。

```json
{"session_id":"<fixture session>","event_id":"<返回的整数 id>","reaction_id":"qq_like","operation":"add","idempotency_key":"wire-reaction-add"}
```

通过当前授权、已读引用、账号/群/发送者/时间、`get_msg` 和 sequence 校验后，下游参数为：

```json
{"message_id":-9010,"emoji_id":"76","set":true}
```

模拟接口返回 `{"status":"ok","retcode":0,"data":null}`。工具回执为 `SUCCEEDED`，`client_display_confirmed=false`，`turn_complete=true`。随后同一目标用 `operation=remove`、新幂等编号调用，下游仅把 `set` 改为 `false`，同样成功。

此路径共 **2 次模拟 reaction 写入、0 次文字发送**。该用例把临时配置冷却设为 0；产品默认仍是 10 秒。真实验收需等待冷却并另获指定目标及动作授权。

### 他人添加/撤销 → 下次正常上下文

模拟操作者 `444` 对群 `222` 的原始消息 `9500`、sequence `9600` 发出 `group_msg_emoji_like`：`sub_type=add`、`likes=[{"emoji_id":"76","count":1}]`，再发出 `remove`、计数 0。

下一次原有 `wait_chat_messages` 正常超时返回两条 `reaction_notices`：准确的目标 event、操作者 `444`、`add/remove` 和原样计数 `1/0`。目标短摘录只来自该会话已读缓存。

此路径 **0 次写入、0 次文字发送、没有提前唤醒 wait**。重复通知去重；自己的回声明确标识，不能触发新操作。缺失操作者、计数或时间时为 `null`，不编造人数。

## 已通过的源码检查

- 添加/撤销状态转换、负数 ID、空 `data` 成功、默认冷却、同请求及并发去重。
- 原始 ID 冒充内部引用、未读、跨群/跨 grant、私聊、虚拟/通知/摘要目标、撤回、缓存淘汰、错误账号/发送者/时间、sequence 缺失/变化/不可信的拦截。
- 日期范围、关闭服务、撤权，以及 native 预检途中取消、冻结或修改权限；被拒时没有派发。
- QQ face 编号和 Unicode 十进制单码点；未知、禁用、待验证及无效目录无默认替换。
- 明确接口失败、连接拒绝、HTTP 鉴权/限流、503、不明确回执、超时；失败不污染成功状态，UNKNOWN 不换编号重试。
- 重连/重启使旧引用和远端成功状态缓存失效，UNKNOWN 账本仍保留；可选 sequence 字段的变化不能绕过未知写入保护。
- 操作派发后撤权仍保留真实回执；自己的相反通知与 HTTP 回执竞态不会留下错误的成功状态缓存。
- 通知目标关联、重复、添加/撤销、自身回声、缺失字段；不新增正文、动作或模型调用。
- 回复表情包、原生引用和真实 @ 的原有 fixture 回归，以及 MCP 权限/动作/HTTP 协议、人格提示和聊天工具回归。

完整 reaction fixture 的源码运行有 **27 次模拟 reaction 请求、0 次真实外部连接、0 次文字发送**。慢 reaction 被模拟服务器阻塞时，另一个 `get_chat_session` 用时约 **0.066 秒**返回，证明未占用普通工具的等待锁。它不是网络性能或 QQ 客户端性能承诺。

## 可复验命令

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp_reactions.py --report .tmp/reaction-check.json
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp_chat_targets.py
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp_chat.py
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp_chat_media.py
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp_sticker_library.py
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp_chat_prompts.py
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp_actions.py
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp.py
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp_onebot.py
.\.venv\Scripts\python.exe -X utf8 scripts/check_reply_copilot.py
node scripts/check_mcp_ui.cjs
```

UI 用例需要已安装的 `playwright-core` 和 Edge；非标准位置可设置 `PLAYWRIGHT_MODULE` 指向模块路径。它验证新权限默认关闭、依赖关系、提交、连接说明以及生成的 MCP 客户端配置。

安装环境使用该包的 `runtime/python.exe` 运行相同脚本，加 `--package <实际安装目录>`。UI 脚本也接受 `--package`，实际读取该包的 `web/mcp.js`。构建流程 `desktop/build.py --mcp-only --qa --no-zip --output <release 下的测试目录/Tulpa>` 已纳入 reaction、native quote/@ 和现有 MCP 回归门禁；完整版本也运行同一组门禁。CI 纳入 reaction 和 MCP UI 用例。

本地备份、覆盖文件哈希、安装测试和入口检查记录保存在本次维护的私有工作目录及安装目录 `reports/private/updates`，不作为测试数据上传。覆盖只涉及该功能程序文件和说明，不替换 EXE、运行库、人物卡、账号、配置、授权、聊天或缓存。版本号保持实际安装版本，构建清单记录 `local_update.name=mcp-message-reactions`。

## 本次构建及实际安装结果

- 现有轻量 QA 构建流程完成，包含 reader/runtime、升级保护以及全部 MCP 门禁；其中本功能、native quote/@、表情包及持久图库测试通过。没有生成或发布新的 GitHub 版本。
- 已用用户指定目录自己的 Python 和模块复验：`check_mcp_reactions.py`、`check_mcp_chat_targets.py`、`check_mcp_chat.py`、`check_mcp_chat_media.py`、`check_mcp_sticker_library.py`、`check_mcp_actions.py`、`check_mcp.py`、`check_mcp_onebot.py`、`check_mcp_ui.cjs`，共 9 项通过。
- 发行卡一致性检查 `check_mcp_chat_prompts.py` 直接对用户安装运行时，报告小鲸鱼原文与发行 provenance 哈希不同。这是已存在的自定义卡，不将其改写成发行卡。补充检查确认 5 个人格相关文件逐字节未变，小鲸鱼和龙娘原文完整进入上下文，回应工具引导可见；安装代码再配合规范发行卡 fixture 完成提示组装回归，通过。原始失败记录保留，没有冒称所有发行文件一致。
- 以实际 EXE 的同字节副本，在没有账号、OneBot、MCP 授权的独立测试目录中启动。配套 `.config` 和入口模块齐备后，约 3.33 秒取得后端就绪、WebView2 子进程及新权限脚本，入口链路通过。初次副本检查漏带 `.exe.config`，补齐后重测；正式安装的该文件始终存在且未修改。
- 实际使用目录保留原来的版本号及 EXE，只覆盖经校验的功能模块和网页文件。程序覆盖时 2,271 个数据文件的大小/时间状态不变；配置、人物卡和入口哈希一致。程序启动时只增加独立 reaction 表，不迁移或删除旧聊天。

这些结果证明代码、安装依赖、入口和模拟协议链路可用，仍不等同于真群或 QQ 客户端显示验收。

## 改动文件

| 文件 | 用途 |
| --- | --- |
| `chatlocal/mcp_reactions.py` | 独立候选配置、引用校验、冷却、幂等账本、受控执行、通知归一化和上下文。 |
| `chatlocal/onebot.py` | 仅新增 `set_msg_emoji_like` 写白名单，安全错误分类，保留回执状态码。 |
| `chatlocal/onebot_events.py` | 保存不对外暴露的 HTTP/WS 实例绑定，防止切换节点后复用消息引用。 |
| `chatlocal/message_sender.py` | 提取原生引用的消息身份校验供 reaction 共用；原引用/@ 行为不变。 |
| `chatlocal/mcp_chat.py` | 挂载子功能、连接生命周期、通知路由及正常 get/wait 上下文。 |
| `chatlocal/mcp_access.py`、`mcp_tools.py`、`mcp_server.py`、`mcp_connect.py` | 独立授权标志、工具发现/执行/注解、审计以及客户端连接配置。 |
| `chatlocal/mcp_chat_prompts.py` | 通用工具提示，小鲸鱼跳过示例时仍生效；不编辑人物卡。 |
| `web/mcp.js` | 完整版/轻量版共用的新权限复选框、依赖提示、连接说明。 |
| `scripts/check_mcp_reactions.py` | 两条贯通 dry-run 和安全、并发、重启、错误回执回归。 |
| `scripts/check_mcp_chat.py`、`check_mcp_ui.cjs` | 旧授权兼容及 UI 校验；UI 支持检查指定安装目录。 |
| `desktop/build.py`、`.github/workflows/ci.yml` | 构建与未来 CI 的验收门禁。未触发远端 CI 或发布。 |
| `doc/MCP_CHAT.md`、`MCP_REACTIONS.md`、`MCP_REACTIONS_VALIDATION.md` | 功能入口、配置、人工体验、回退及本次证据。旧安装的 `MCP_CHAT.md` 单独保留。 |

## 未测及剩余边界

- 没有真群添加/撤销、QQ 客户端目视验证或真实通知时序验证；mock 通过不代表某个 QQ 版本一定支持每项候选。
- 完整版与轻量版使用同一 MCP 实现和 UI；本次实际覆盖和安装验证针对用户指定的轻量版，未重新发布完整版 EXE。
- 不同步完整远端 reaction 历史；关联不到授权缓存目标的通知忽略，不扩大查询范围。上游没有全局通知序号，同一秒乱序动作不能保证完全还原。
- UNKNOWN 不提供强制重试按钮或自动远端对账；记录不会因重启而被清除。需用户核对，不能通过删除回执解除保护。
- 原安装中的说明或人物卡与开发区不同则保留。回滚只恢复备份的程序/网页/清单；不要把旧 MCP 数据库覆盖到已产生新操作回执的安装上。

配置、授权、人工测试及回滚操作见 [功能说明](MCP_REACTIONS.md)。
