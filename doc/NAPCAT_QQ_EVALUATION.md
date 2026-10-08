# NapCat QQ 后端评估：源码、隔离测试与本机实测

日期：2026-10-07。状态：**NapCat 4.18.33 已安装并完成有限真实读取；保留现有默认后端。**

## 结论

**已证实：不通过 Tulpa 新建数据库快照或解密副本，NapCat 能读取本机账号指定的 2026-02-27 旧消息。** 进一步抽查了同群 233 条消息、实时事件、服务重启、图片和群文件。本轮不再只是 mock，但也没有证明全量历史、全部媒体和全部工具可以无损迁移。

建议继续采用“**原生历史取数 + Tulpa 规范化索引 + 有限媒体缓存**”作为可选后端方向。本轮只交付独立只读探测和测试，不接入生产、不改变默认值，原因是：

1. **断点与文件身份还不能直接兼容。** 实测重启后，旧短消息 ID 的 `get_msg`、历史游标，以及旧群文件 ID 都失败。用独立取得的原生长消息 ID 拉历史、重新枚举文件后才恢复。标准历史结果不提供该长 ID，不能把现有 M 编号换成短 ID 后宣称可持久续读。
2. **搜索能力不能直接等价替换。** 历史接口没有日期、关键词、发送者筛选或跨群搜索。取消本地索引后，每次搜索和统计都需要遍历相关历史，且依赖 QQ 在线及原生接口的可见范围。
3. **部署尚未简化。** 本机 Shell 加载器没有启动服务，后用 Node 模式配合现有 QQ 内核成功；过程还发现 SnowLuma 被其他本地进程重新拉起。不能把加载失败断言为新版 bug，也不能把这次人工配置视为开箱即用。成功组合的程序文件约 321.7 MiB。
4. **部分扩展必须适配。** 表情收藏参数/回执、群文件身份及 WS 鉴权分类有差异。近期图片和小文件读取成功，一张旧图片返回 404。真实发送、群管理、表情收藏未执行；引用消息和离线期间补漏仍缺真实样本。

因此可以继续减少 QQ 取数/快照/解密成本，**不能据此取消本地索引或删除旧库**。下一个实现门槛是持久消息定位、群文件重新定位、补漏策略和单提供者启动；满足后再做可选后端与 M 编号兼容测试。本次未测得可兑现的全产品空间节省。

## 本机真实验证结果

先做源码和 fixtures；用户随后明确同意安装、连接及适当扩大只读验收。真实样本只在本机处理，公开文档不记录账号、群号、正文、下载链接或 Token。

### 环境与方法

- 官方 `NapCat.Shell.zip` **v4.18.33**，SHA256 `4b8e20e6d22288586d99eb0b34fff6c7ee1d88b00353039326f8c459a86df667`，与 GitHub 资产 digest 一致；源码 tag 对应 `ec6aadaeea38ed3bca67b82bab3d7a9c0f91b8d8`。[官方发布](https://github.com/NapNeko/NapCatQQ/releases/tag/v4.18.33)
- 本机实际 QQ 内核 **9.9.29-47354**，不是根目录 QQ.exe 显示的旧 bootstrap 版本。Shell 4.18.33 和对照 4.18.32 均未启动 HTTP/WS；DLL 加载不等于 NapCat 已运行。测试期间另一个 SnowLuma 进程被拉起，因此不对这两次失败做排他性的版本归因。
- 成功方式参考官方 Windows Node 布局，使用 **Node 22.13.0 + 本机 QQ 9.9.29 原生库 + NapCat 4.18.33**。原生库仅复制到独立实验目录，未升级/替换桌面 QQ。不是官方 Node 整包的默认 QQ/Node 组合，不推及其他机器。[官方构建流程](https://github.com/NapNeko/NapCatQQ/blob/ec6aadaeea38ed3bca67b82bab3d7a9c0f91b8d8/.github/workflows/release-publish.yml)
- `get_login_info` 核对账号；同一个 `wrapper.node` 的 `getNTUserDataInfoConfig()` 结果与既有快照记录的原始 `nt_db` 路径相同。已有解码缓存中先找到指定文本，取其长消息 ID 作为定点锚点；这一步借用了既有缓存，不等于新用户无需锚点也能一跳找到旧日期。
- HTTP `127.0.0.1:13000`、WS `127.0.0.1:13001`、管理页 `127.0.0.1:16099`，HTTP/WS 独立随机 Token，管理页预置非默认 Token。未改 Tulpa 正式连接。禁用原始日志与自动时间同步；未启用 NapCat 数据库 API。启动输出只保留固定阶段计数，正文、凭据不写入实验日志。

### 已证实与边界

| 项目 | 真实观测 | 能证明的范围 |
| --- | --- | --- |
| 指定旧消息 | 首次历史请求返回 20 条；日期、定位文字、原生序号/时间核对成功，HTTP 历史调用 365.696 ms | 该消息在此账号、本机数据目录和此版本可达；没有使用当前群公告代替 |
| 翻页方向 | 带同一锚点，`reverse_order=true` 返回锚点及更旧记录，false 返回锚点及更新记录；均为时间升序 | 原探测器的方向假设有误，已修正并补测试。参数不是单纯控制返回排序 |
| 连续分页 | 3 个窗口，每个最多 4 页，每页 20 或 25 条；共 233 个不同短 ID。日期覆盖 2025-12-10～2026-04-27 的两个相接窗口及 2026-09-26～2026-10-07 的近期窗口 | 中间月份未扫；以页预算停止，不宣称读完群历史。每个窗口观察到 3 次边界重叠 |
| 同范围对照 | 按原生 ID 的预期短散列对照：232 条对应旧缓存，1 条是用户本次新增消息；对应记录时间全部一致。5 条旧缓存 sender UIN 为 0，NapCat 补出了 UIN | 原生缓存有 92 个重复非零序号项，**序号不能单独当唯一身份**。此前按序号比较产生的时间差异不算真实不一致 |
| 原始行缺口 | 三窗口原始库分别有 2、1、32 行未在 API 样本出现；全部不符合现有导出器 `supported(kind,subtype)` 加有效时间条件 | 本次窗口内未发现当前导出器支持的旧记录漏读；不是全库完整性保证，也不覆盖未知类型 |
| 单条读取 | 映射存在时 `get_msg` 能读指定旧消息 | 此处未遇到 reply 段，**真实引用链未验证**；fixtures 的引用通过不能代替它 |
| 新消息 | 用户第一条测试消息可在最新历史查询中看到；第二条在已连接的现有 Tulpa `EventReceiver` 中收到自身群消息推送，约 2 秒延迟 | 第一条发送时监听尚未接上，不计为 WS 通过。观察到的第二条无重复；不代表所有情形绝无重复 |
| 重启 / 重连 | 停止并重启 NapCat 后，Tulpa 原有接收器经历 disconnected → connecting → verifying → connected，恢复生命周期/心跳 | 没有在停机窗口人工发消息；离线补漏、持久重放仍未验证 |
| 重启后的消息身份 | 旧短 ID 的 `get_msg` 和历史游标失败；单独用长 ID 调 `get_msg` 也失败。用长 ID 调历史成功后，原短 ID 恢复且数值相同 | 短散列数值稳定与反向映射可用是两回事；必须设计持久定位与重建 |
| 图片 | 近期图片实际取得 130965 字节；一张旧图片的下载 URL 返回 HTTP 404 | 近期本体通过；旧媒体是否客户端仍保有完整本体、过期恢复、动图原件没有全覆盖 |
| 群文件 | 文件系统报告 491 项；仅枚举根目录前 20 项（19 文件、1 文件夹），获取下载链接；实际读取一个 1212 字节文件，与目录大小一致 | 未递归/全量下载，文件本体在内存计算散列后丢弃。Packet 下载路径在此组合可用 |
| 文件重启 | 旧文件 ID 获取 URL 失败；重新枚举后 ID 变化，新 ID 可获取 URL | 当前 Tulpa 持久附件引用需要提供者适配 |
| 公告 / 精华 | 两个读取接口均成功返回空列表；群资料接口成功 | 只证明接口可调用，**不算有内容的公告/精华读取验收** |
| 写能力 | 本次没有由助手发送消息、收藏表情或改变群状态 | 发送/管理仍仅有隔离回归，不能宣称真实 NapCat 写兼容已通过 |

### 实测成本与未测指标

- Node 模式启动后 **19 秒的检查点**已完成登录且三个端口可用。这是观测上界，不是精确冷启动耗时，也未清空系统文件缓存。
- 首次旧锚点历史调用约 **366 ms**；进一步分页首次一组为 **23.580～372.905 ms/页**，重复一组为 **15.314～436.929 ms/页**，每页上限 20/25 条。重启后已知长 ID 的 5 条历史读取 **8.349 ms**，随后单条 **2.722 ms**。这些是原生历史 API 的不同查询，不能当成跨群全文检索速度。
- 现有 QQ 解码缓存中查该群 811 个原始行，首次观测约 **6179 ms**，后一次 **587 ms**；这是对约 987.2 MiB `messages.db` 的原始 SQL 查询，不是 Tulpa 已建索引的热搜索。实际完整导入冷启动、跨群同范围检索、GUI 首屏端到端对照仍未测，不能给速度倍数结论。
- 成功的 Shell 程序目录 **95051905 字节**，Node/QQ 原生依赖实验目录 **242275194 字节**，合计约 **321.7 MiB**；对照旧版本和下载包是额外实验开销，不计入候选运行组合。NapCat Node 工作集观测 **263303168 字节**，进程峰值 **309264384 字节**；不含浏览器、Tulpa 和操作系统缓存。
- 本次未调用数据库解密/复制 API，也未生成新的整库明文副本。现有 release 的 QQ 快照 **1085849117 字节**、解码缓存 **1079862005 字节**，合计约 **2.02 GiB**，继续保留。这是此机器上值得减少的现有副本规模，**不是本轮已经释放的空间**；规范索引、媒体和增量恢复仍有成本。

脱敏结果在忽略的 `.tmp/napcat-live-20261007/` 和 `reports/private/napcat-qq-probe-*.json`。私有操作配置另存本地，不提交。实验结束后已停止 NapCat，三个临时端口均关闭。用户重新登录桌面 QQ 后，原 SnowLuma 的 HTTP 返回账号与原账号一致，WS 生命周期核对为 `connected`；原 3000/3001/5099 端口恢复，正式配置和旧后端不变。这里确认的是恢复后的账号与事件连接，没有再发送消息作为恢复测试。

## 核对的版本与工作区

| 项目 | 本轮记录 |
| --- | --- |
| Git 根目录 / remote | `C:\Lab0921` / `https://github.com/fumingyang2004/Tulpa.git` |
| 起始分支 / HEAD | `codex/tulpa-mcp-readonly` / `5476cbfa58986b0dbc5b200f8f69c3239b1cf637` |
| 本轮分支 | `codex/napcat-qq-feasibility`，同一 HEAD 上的独立增量 |
| 原有修改 | 34 个已修改/未跟踪文件，涉及 SnowLuma 简化配置、QQ 账号诊断与修复；逐文件哈希核对，本轮未覆盖 |
| NapCat 已安装版本 | **v4.18.33**；成功运行的是上述 Node/本机 QQ 内核组合。另装 v4.18.32 仅作 Shell 加载对照 |
| 用户提供的源码点 | `463d3ea5b143d43dbab2c743c2e850fd17b4ecc3`，也是本次取到的 `v4.18.32` tag 解引用结果 |
| 本轮检出的源码 | `ec6aadaeea38ed3bca67b82bab3d7a9c0f91b8d8`，2026-10-07；官方仓库只读副本位于忽略的 `.tmp/napcat-source-20261007` |
| 辅助对照 | `v4.18.30` 解引用 `454cc0eda260f13d1828ab4992d7e268ace9d812`；所审阅的历史入口、消息映射、解析、网络、数据库及 CustomFace 文件与上述源码点无 diff |

未操作 `C:\Lab1006`；未改微信、默认后端、发布目录、正式 Tulpa 配置或访问授权，未删除原始库和索引。只新增独立实验程序、配置和凭据；原生 QQ 服务运行及用户测试消息会正常更新 QQ 自身数据。未由助手发送消息、改变群状态、commit、push 或发布。没有复制 NapCat 实现代码到 Tulpa；测试夹具为独立编写的协议模拟。

## 现有 QQ 链路与真实能力

```text
QQ 本地数据库
  ├─ 历史：账号/路径检测 → 快照复用 → 被动取密钥 → 页认证解密缓存
  │        → 500 条批次 → 规范化 / 去重 / 原文归档 → SQLite + FTS 索引
  └─ 实时：源文件变化检测 → 只读 VFS / WAL → 增量批次 → 同一索引
                                                         ├─ GUI 聊天浏览
                                                         ├─ full 内置 Agent
                                                         └─ full / lite MCP

OneBot HTTP ─ 群资料 / 文件目录与下载 / 已授权发送和管理
OneBot WS ─ 独立持续群聊收件箱 ─ 人格 / 看图 / 表情 / 回复
```

持续群聊已经走 OneBot 事件，不需要复制历史聊天库；更换历史后端对这部分没有新增“免数据库”的收益。

| 能力 | 当前实现与状态 | 范围、限制 / 返回契约 |
| --- | --- | --- |
| 历史导入 | 已支持。`data_routes.py` → `export_qq.py` → `import_pipeline.py` → `Store.import_file` | GUI“导入数据”选择账号、会话、日期后读取。`POST /api/data/catalog`、`/api/data/read`；不是读取 QQ 云端全部历史 |
| 大批量与进度 | 已支持。500 条 / 4 MiB 批次、最多 3 个未确认批次；消费者事务提交后确认 | GUI 全量模式没有 50000 条总上限。失败保留已提交进度；预算、磁盘余量、取消和校验仍存在。合成 50101 条回归通过 |
| 快照与解密成本 | 已有复用优化。`snapshot_cache.py`、`qq_cached_reader.py`、`page_cache.py` | 主库未变时复用，WAL/SHM 单独刷新，解密缓存按页校验；不是每次增量全量复制。默认 `balanced` 保留可重用明文缓存，`space` 成功导入后释放部分可重建缓存 |
| 跨群文本 / 日期 / 发送者搜索 | 已支持。`ChatTools.search_messages`，FTS + 文本补充匹配；平台、会话、时间、稳定 sender_id 筛选 | 返回 `messages`、`hit_ids`、`context_windows`、`has_more`、`next_offset`；查的是已导入且被授权的范围，昵称不能独立证明同一人 |
| 上下文 / 原文 / 引用 | 已支持。`get_context`、`read_conversation`、`read_message`、`chat_view.py` | 数字 `id` 在界面显示为 M 编号；规范身份包含平台、会话、原生 `source_id`。重复导入保持编号；QQ 引用按同会话原生 ID 解析，缺失目标不能凭空补齐 |
| 统计 | 已支持受限 SQL / 统计查询 | `query_communication_db` 受授权视图限制；代表已入库样本，不是服务端总量。`read_overview` 是有限概览 |
| GUI 聊天浏览 | 已支持两栏、末条摘要、历史翻页、打开时更新 | `GET /api/chat-conversations`、`/api/chat-context`、`/api/chat-updates`；结果有 `high_water/epoch`、`has_before/has_after`。界面跟随本地索引变化，不直接追溯 WS 历史 |
| 图片 / 引用混合消息 | 已支持一部分原生类型。`reader_media.py`、`qq_message_types.py` | 能展示已取得本体的图片；占位、过期、未缓存、未支持类型仍有限制。动画表情可选；没有承诺所有媒体可恢复 |
| 消息附件 / 群文件 | 已支持元数据检索与按需读取/下载。`artifacts.py`、`artifact_onebot.py` | 本地本体缺失时不能凭文件名生成正文。群文件目录/下载需要 OneBot 和权限；目录核对 `file_count`，有文件数/文件夹数/时限预算。不会自动全群批量下载 |
| full 与 lite | 共用聊天、导入、实时、附件与 MCP 路由 | `app.py` 有内置 Harness；`app_mcp.py` 无内置 Agent，交付原文件给外部 Agent，不依赖重型 Office 解析。UI 入口不同，不是两套 QQ 索引 |
| 原生实时增量 | 已支持。`live.py` → `live_reader.py` / `live_vfs.py` | `(seq,rowid)` 游标、单会话分批、事务提交游标、错误不前进；序号回退明确失败。启动尾部读取不等于历史补全 |
| 重新导入补漏 / 去重 | 已支持当前本地库范围内的核对。`incremental_qq.py`、`stable_key` | 原生 ID 清单核对能找未入库的旧 ID；热游标本身不保证找到后来插入的低序号记录。不是无限服务端历史保证 |
| OneBot 群资料 / 发送 / 管理 | 配好服务并授权后支持 | 公告、精华、成员、文件目录；发送与管理沿用用户授权、目标校验、幂等回执、UNKNOWN 不自动重试。不同提供者扩展能力不能只凭同名 API 宣称等价 |
| 持续群聊 / 表情 | full 启动器 MCP 和 lite MCP 都支持；与普通查询分开 | WS 事件 → `mcp-access.sqlite3` 中的小型收件箱；`event_id` / OneBot ID 不等于 M。人格热发现、等待、主动发送、停止、看图/收藏/发送分别受授权与配额限制 |
| WS 断线 / 重启 | 已有重连、重复事件抑制与缺口提示；**不保证补齐** | 每会话保留有限事件，单条正文有截断；溢出/重启/重连记录 `gap_count/gap_note`。现有系统也未提供任意离线期间完整回放 |

本地消息时间按毫秒保存；OneBot `time` 正常为秒。消息身份、时间单位、媒体引用和权限过滤都是后端迁移的兼容条件，不能只比较聊天文本。

代码入口：[历史导出](../scripts/export_qq.py)、[存储与去重](../chatlocal/store.py)、[检索](../chatlocal/agent_tools.py)、[聊天界面 API](../chatlocal/chat_view.py)、[原生实时](../scripts/live_reader.py)、[持续群聊](../chatlocal/mcp_chat.py)、[MCP 工具契约](MCP.md)。

## NapCat 源码证据与接口差异

下列链接固定到本轮审阅源码。源码结论和本机实测分别记录；只有前述明确列出的样本经过真实 QQ 验证。

### 历史、游标和 ID

- [`GetGroupMsgHistory.ts`](https://github.com/NapNeko/NapCatQQ/blob/ec6aadaeea38ed3bca67b82bab3d7a9c0f91b8d8/packages/napcat-onebot/action/go-cqhttp/GetGroupMsgHistory.ts) / [`GetFriendMsgHistory.ts`](https://github.com/NapNeko/NapCatQQ/blob/ec6aadaeea38ed3bca67b82bab3d7a9c0f91b8d8/packages/napcat-onebot/action/go-cqhttp/GetFriendMsgHistory.ts)：参数是 `group_id/user_id, message_seq, count, reverse_order` 及解析选项；返回 `{messages:[...]}`。无日期、关键词、发送者条件，也无 `has_more/next_cursor`。
- 未给游标时走 `getAioFirstViewLatestMsgs`；给游标后先尝试短 ID 反查，找不到便把参数当原生长 `msgId`，调用 [`getMsgsIncludeSelf`](https://github.com/NapNeko/NapCatQQ/blob/ec6aadaeea38ed3bca67b82bab3d7a9c0f91b8d8/packages/napcat-core/apis/msg.ts)。**参数名叫 message_seq，不代表应传 QQ 序列号。** 私聊还要先将 UIN 转为 UID 并判断好友/临时会话。
- [`initializeMessage`](https://github.com/NapNeko/NapCatQQ/blob/ec6aadaeea38ed3bca67b82bab3d7a9c0f91b8d8/packages/napcat-onebot/api/msg.ts) 将 `message_id/message_seq/real_id` 都设为短 ID；`real_seq` 才来自原生 `msgSeq`。普通历史结果没有直接提供 `msg.msgId`。时间字段的异常回退使用 `Date.now()`，与正常秒单位不同，探测器会拒绝这类时间戳。
- [`message-unique.ts`](https://github.com/NapNeko/NapCatQQ/blob/ec6aadaeea38ed3bca67b82bab3d7a9c0f91b8d8/packages/napcat-common/src/message-unique.ts) 用原生 ID、chatType、peerUid 生成 31 位散列，并在内存存反向映射。默认容量 5000，可调整；这是映射缓存容量，**不是历史总数上限**。散列可重算不等于反向映射可持久恢复，也不能忽略碰撞。
- [`GetMsg.ts`](https://github.com/NapNeko/NapCatQQ/blob/ec6aadaeea38ed3bca67b82bab3d7a9c0f91b8d8/packages/napcat-onebot/action/msg/GetMsg.ts) 仍依赖已有映射确定 peer；没有映射会报不存在。本机实测已复现重启后旧短 ID 失败，重新拉取历史建立映射后恢复。
- 原生返回空列表时历史入口会抛错；解析后也可能过滤消息。短页、空页、序号间隔都不能单独证明“历史读完”或“消息丢失”。方向、边界包含关系、撤回/系统消息造成的跳号、客户端可见范围必须实测。

隔离夹具模拟包含锚点的双向翻页；探测器使用 `reverse_order=true` 向旧消息读取。方向和边界包含关系已经在上述群聊样本核对，但未实测私聊或所有消息类型。夹具验证重叠/冲突/停滞处理；5200 条夹具能在 5000 项映射容量下连续翻页，清空映射后旧短游标失败、长 ID 仍可定位。**5200 条是合成压力样本，不是本机账号的实际覆盖条数。**

### 媒体和群文件

- [`GetFile.ts`](https://github.com/NapNeko/NapCatQQ/blob/ec6aadaeea38ed3bca67b82bab3d7a9c0f91b8d8/packages/napcat-onebot/action/file/GetFile.ts) 有按消息/元素、群文件模型定位并下载的路径；返回的 `url` 有时是本地路径。Tulpa 的远端下载验证不会把任意本地路径当公网 URL，这里需要提供者适配。
- [`OB11Construct.file`](https://github.com/NapNeko/NapCatQQ/blob/ec6aadaeea38ed3bca67b82bab3d7a9c0f91b8d8/packages/napcat-onebot/helper/data.ts) 的 `file_id` 经 [`file-uuid.ts`](https://github.com/NapNeko/NapCatQQ/blob/ec6aadaeea38ed3bca67b82bab3d7a9c0f91b8d8/packages/napcat-common/src/file-uuid.ts) 生成不透明 UUID，映射有容量/有效期并存在内存中；默认包装器有效期 24 小时。重新枚举也可能产生新 UUID。当前 Tulpa 附件身份包含 `file_id`，直接换服务可能使已保存文件引用失效、生成新条目；同名同大小也不能安全证明同一文件。
- [`GetGroupFileUrl`](https://github.com/NapNeko/NapCatQQ/blob/ec6aadaeea38ed3bca67b82bab3d7a9c0f91b8d8/packages/napcat-onebot/action/file/GetGroupFileUrl.ts) 依赖 UUID 映射和 Packet 后端。HTTP 连接成功不代表可下载文件；Packet 不可用、映射过期、服务重启要分别诊断。
- [`CustomFace.ts`](https://github.com/NapNeko/NapCatQQ/blob/ec6aadaeea38ed3bca67b82bab3d7a9c0f91b8d8/packages/napcat-onebot/action/extends/CustomFace.ts) 的 `add_custom_face` 对 `file` 直接做文件系统 `stat/readFile`，而 Tulpa 当前发送 `base64://...`，且要求回执有 `emoji_id`。该源码直接返回 native `addFavEmoji` 结果，不能保证这个字段。因此“看图/列表大致相似”不能推导出收藏、后续复用全兼容。
- `fetch_custom_face_detail` 的 native 类型含 `resId/url/desc`，能与现有列表字段对应；实际 URL 可用性、动图原件、发送结果及收藏后身份仍未验证。

### HTTP / WS 与一套服务部署

[`config.ts`](https://github.com/NapNeko/NapCatQQ/blob/ec6aadaeea38ed3bca67b82bab3d7a9c0f91b8d8/packages/napcat-onebot/config/config.ts)、[`http-server.ts`](https://github.com/NapNeko/NapCatQQ/blob/ec6aadaeea38ed3bca67b82bab3d7a9c0f91b8d8/packages/napcat-onebot/network/http-server.ts)、[`websocket-server.ts`](https://github.com/NapNeko/NapCatQQ/blob/ec6aadaeea38ed3bca67b82bab3d7a9c0f91b8d8/packages/napcat-onebot/network/websocket-server.ts) 确认：

| 项目 | NapCat 契约 / 对 Tulpa 的含义 |
| --- | --- |
| 节点配置 | `network.httpServers`、`network.websocketServers`，节点开关 `enable`。不是 SnowLuma 配置文件结构，现有 SnowLuma 自动导入不能直接套用 |
| HTTP | 每节点自己的 `token`；支持 `Authorization: Bearer ...` 和查询参数鉴权。Tulpa/探测器使用 Header，避免 Token 出现在 URL |
| 正向 WS | 独立节点 Token，可与 HTTP 不同；`/api` 路径不推事件。应使用配置好的事件端点，不能只验证 TCP/握手成功 |
| 同端口 | HTTP 节点可显式开 `enableWebsocket`；不能从端口相同推断事件服务一定启用 |
| 身份与心跳 | 生命周期/心跳提供 `self_id`，Tulpa 会核对 HTTP 登录身份。`reportSelfMessage` 默认 false，原有自身消息/回执语义需要实测 |
| 鉴权失败 | NapCat 可在 WS 升级后发 `retcode=1403` 再关闭；当前 Tulpa 丢弃无 `post_type` 帧，显示断开而不是认证失败。隔离 WS 已复现；未改正在进行的 SnowLuma 接收器代码 |
| 断线补漏 | 这些 WS 路径没有证明存在持久重放/补漏协议。历史 API 能否可靠补齐断线窗口、如何去重仍待验证 |

目标应是一套提供者服务加 HTTP/WS 两路端点，NapCat **可选替换** SnowLuma，而不是要求用户长期维护两者。本轮保留正式 Tulpa 地址和 Token，实验另建 loopback 端点和独立随机凭据，验收后停止实验服务。尚未实现产品内 NapCat 自动配置，部署复杂度未证明下降。

### 数据库 API 不是“无需复制”

[`database.ts`](https://github.com/NapNeko/NapCatQQ/blob/ec6aadaeea38ed3bca67b82bab3d7a9c0f91b8d8/packages/napcat-core/apis/database.ts) 默认源路径为 `dataPath/<uin>/nt_qq/nt_db`，默认明文缓存为 `NapCatDataPath/db_<uin>`；调用 [`decryptAndOpen`](https://github.com/NapNeko/NapCatQQ/blob/ec6aadaeea38ed3bca67b82bab3d7a9c0f91b8d8/packages/napcat-database/src/reader.ts)。后者可读取加密文件并生成明文缓存/临时文件；已有缓存时会直接打开。这个函数本身不提供当前源库/WAL 一致性的证明。

把现有解密副本换成这个 API 生成的副本，不能计为消除了副本。本轮探测器不开放任何数据库 API、任意 SQL 或写动作。

## 隔离验证与成本对照

### 已执行

- `check_napcat_qq_probe.py`：**13 项 unittest 通过**。包含真实 loopback HTTP + 合成响应、请求白名单、Bearer、拒绝重定向/过大响应、错误脱敏、账号/会话校验、日期与排序、双向锚点、短页继续、重叠去重、冲突、预算、引用/媒体仅计数、映射失效。
- `check_napcat_onebot_contract.py`：真实 loopback WS + 现有接收器，通过生命周期/群事件；鉴权错误被观察为 `disconnected`，没有转发消息。模拟同一群文件换 `file_id` 后现有索引有 **2 个附件条目**，证明需要身份适配。这是发现不兼容，不是全功能验收通过。
- **17 个现有回归脚本通过**：`check_qq_history`、`check_qq_replies`、`check_reader_cache`、`check_search_context`、`check_live`、`check_live_runtime --child qq`、`check_import_batches`、`check_chat_browser`、`check_chat_browser_ui`、`check_mcp`、`check_mcp_onebot`、`check_mcp_actions`、`check_mcp_downloads`、`check_mcp_chat`、`check_mcp_chat_media`、`check_mcp_ui`、`check_desktop`。
- 其中批次测试实际跑生产批次/子进程/Store，输入为 **50101 条合成 QQ 原生记录**，用时约 53.41 秒；最高 3 个待确认批次。QQ VFS 测试使用跨 Windows 特殊锁页的 >1 GiB 稀疏加密夹具，验证 WAL、只读与源一致性，不是读取个人数据库。
- GUI：MCP 前端用真实 Edge + API 夹具；聊天浏览用实际 JS + DOM 夹具及 FastAPI 路由。full/lite 共用模块和配置模式通过这些回归；**没有启动两份 EXE 接真实 NapCat 做端到端验收**。
- `check_qq_replies` 初次失败是夹具跳过目录加载后缺少 `label_status`；只补了夹具字段，重跑通过，没有修改读取逻辑。

### 同机器、同合成范围的对照

`bench_napcat_qq_fixture.py`：4 个群、每群 500 条，共 2000 条；相同日期窗口、发送者、关键词应命中 4 条。每种模式用全新 Python 进程，一次首查再做 5 次热查，所有命中身份集合一致。没有清空 Windows 文件缓存。

| 指标 | 当前 Store / 实际 ChatTools | 无索引 API 遍历模拟 |
| --- | ---: | ---: |
| 客户端准备时间（含相关导入/建索引） | 1335.58 ms | 19.43 ms |
| 首查 | 59.53 ms | 20.16 ms |
| 客户端开始准备至首个结果 | 1395.10 ms | 39.59 ms |
| 热查询中位数，5 次 | 63.16 ms | 19.56 ms |
| 每次历史请求数 | 0 | 28 |
| 每次模拟历史 JSON 字节 | 0 | 521932 |
| 新增客户端持久文件 | 2510276 字节 | 0 |
| 子进程峰值工作集 | 111472640 字节 | 32808960 字节 |

**不能把右列当成 NapCat 实际速度或内存。** 它是本进程内的模拟原生历史、零网络等待，只比较命中集合，没有实现完整的上下文/证据/统计契约；左列是实际 Store 和含上下文的生产查询。RSS 含 Python、夹具和导入库，不含真实 QQ/NapCat 服务。持久文件含规范化原文归档/索引，不含真实快照和解密缓存。两列均没有测 QQ/NapCat 冷启动、数据库复制、解密、下载或 GUI 首屏。

这组数据只能说明免建索引的客户端初始工作较少，同时重复搜索每次都要重读多页。真实服务每请求延迟、解析成本和历史可见性会改变结果。本机原生 API 的延迟、启动检查点和进程工作集已另测并列在前文；**全产品端到端冷/热检索、首次可用、总内存、可兑现磁盘节省和全量历史完整性仍未验证。**

日志与基准 JSON 在忽略的 `.tmp/napcat-regression-20261007/`；只有合成数据。不要将当前活动聊天数据库或 Token 加入版本控制。

## 复现隔离测试与限定真实验证

在仓库根目录，用现有开发环境执行：

```powershell
.\.venv\Scripts\python.exe scripts/check_napcat_qq_probe.py
.\.venv\Scripts\python.exe scripts/check_napcat_onebot_contract.py
.\.venv\Scripts\python.exe scripts/bench_napcat_qq_fixture.py
```

本轮已在用户指定范围执行下面的真实模式，并用独立只读辅助脚本扩大验收。复现前仍需核对安装/登录授权、实际版本、QQ 登录账号和对应 `dataDir`，避免启动了另一个空数据目录。不要同时向同一 QQ 进程叠加两个提供者的注入。

为已授权的一次读取在 `.tmp/napcat-probe.local.json` 保存本地配置。下例全是假值；真实 Token 和定位文本仅填在本地，不放命令行、不提交：

```json
{
  "endpoint": "http://127.0.0.1:3000",
  "token": "REPLACE_LOCALLY",
  "expected_account": "111",
  "peer_id": "222",
  "kind": "group",
  "day": "2026-02-27",
  "match_text": "替换为已确认消息的一小段",
  "data_dir_confirmed": true,
  "count": 20,
  "max_pages": 3,
  "timeout": 5,
  "deadline_seconds": 30
}
```

`data_dir_confirmed` 是操作者已核对的声明，报告仍说明接口无法独立验证 dataDir。HTTP 必须为 loopback 字面地址，节点消息格式设为数组；Token 可以为空，取决于实际节点配置。私聊使用 `kind: "private"`，`peer_id` 是对方 QQ；响应中的本人发言没有收件人字段，探测结果会记录这一核验限制。

```powershell
.\.venv\Scripts\python.exe scripts/napcat_qq_probe.py --live --spec .tmp/napcat-probe.local.json
```

- 上述 CLI 请求仅限 `get_login_info`、`get_version_info` 和一种会话历史接口。`disable_get_url=true, parse_mult_msg=false, quick_reply=true` 避免主动展开合并消息和额外媒体 URL 查询。CLI 没有媒体下载、原始聊天落盘、发言、群管理或自动重试功能；传输层另允许少量只读资料/媒体接口，供本轮辅助验收使用，不开放任意 action。
- 默认 3 页，每页 20 条；最多 20 页 × 100 条，带连接/读取超时和轮询时间预算。失败不增加预算。**从最近页开始未必能到二月份**；预算耗尽只表示本次没到，不能结论“接口不支持旧历史”。
- 若已独立核实同一 peer 的原生长 `msgId`，可在本地 spec 加 `start_native_id` 做定点查询；禁止把短 ID、`real_seq`、Tulpa M 编号或未经映射核实的 `source_id` 当成它。探测器拒绝用短 ID 启动持久续读，不保存翻页 checkpoint。
- 找到样本立即停止。报告仅保存版本、账号是否匹配、目标是否命中、页数、排序、重叠、时间范围、引用/媒体计数及失败代码，保存在 `reports/private/napcat-qq-probe-*.json`。不保存原始 Token、账号/群号、消息正文、ID 清单、URL 或服务端原始错误。
- 退出码 0 表示指定样本命中，2 表示未命中或受限。`complete_history` 始终 false；窗口内引用可解析数量不代表所有历史引用可恢复；媒体计数不代表已下载/看过原件。

本轮已完成指定旧样本、有限连续翻页与原始缓存对照、群聊排序/边界、部分媒体、重启映射失效和 WS 重连。后续重点是实际引用链、私聊、持久定位、断线期间新增消息的补漏/去重及单提供者其他能力。对每一步记录实际版本、范围、停止原因；发送与群管理继续只做 mock，真实写操作需另获具体批准。

## 启用、回退与下一步决策门槛

**当前没有新后端可启用**，也没有需要恢复的数据迁移；运行上述隔离脚本不改变 full/lite/MCP/GUI 的原有行为。停止探测进程即停止实验，原读取器一直可用。

旧样本可达和有限窗口对照已通过。开始生产可选后端前仍需：确定可跨重启恢复的规范身份与游标；引用和群文件 ID 可重新定位；事件断线缺口处理可解释；媒体、发送、管理、持续群聊和表情契约得到兼容验证；同范围实测磁盘/首次可用/热查询成本有收益。

若原生历史达不到这些条件，保留数据库读取；可单独适配 NapCat 作为实时 OneBot 提供者，但不要把这个小改进宣传为“历史无需导入”。若可以替换取数，也应保留旧索引和原始库，版本化存储 provider 别名，先做重复导入与 M 编号不变的对照，再允许用户主动选择；默认值的切换要另做兼容验收。
