# 水群工具调用与重试

本次是本地可靠性优化，不改变人物卡、授权、OneBot 写协议或版本号。

## 调用协议

- 有新输入才规划；空闲直接等待。`input_batch.plan_call` 给出规划参数，`next_call` 给出发送或继续等待的参数。带 `requires` 的模板需补齐所列字段。编号原样保留为 JSON 字符串。
- `plan_chat_reply` 的 `idempotency_key` 可省略，由程序按会话、读取批次和完整决定生成。`send_chat_reply` 按计划和气泡内容去重，调用方不必另起编号。旧参数继续兼容；同一计划改内容会被拒绝。
- 等待默认建议 180 秒，空闲不增加一次空规划。有成功发送回执的连续自身回声由等待工具确认，仍保留在上下文中；同账号手机手动消息正常交付。不会只因 `is_self` 就丢弃消息。
- 重复确认旧游标不再报错，也不倒退进度或覆盖新便签。未来游标依然拒绝；普通上下文读取不能跳过未返回的分页。
- 格式错误返回具体字段、类型或缺失字段，不回显参数内容。Schema 按完整定义缓存，最多 128 项；每次仍核对当前授权并验证参数。

## 出错后怎么继续

| 结果 | 下一步 |
| --- | --- |
| `invalid_arguments` | 按 `argument_errors` 修正字段；程序尚未执行，不原样反复调用 |
| `stale`、读取批次或计划过期 | 按 `recovery.next_call` 补读并重新规划，不能重发旧答案 |
| `needs_wait`、冷却、暂时忙碌 | 沿用 `recovery.next_call`；等待工具用 `minimum_wait_seconds` 在程序内退避，可被停止或取消打断 |
| 已知成功，重复相同计划及内容 | 返回已有回执，没有新增发送 |
| `UNKNOWN`、`INTERRUPTED` | 通过 `get_chat_session.recent_turns` 查看实际结果；不换编号补发，不恢复旧尾部 |
| 权限、停止、无效目标等拒绝 | 修正请求或停止；不自动开权限、替换目标或换表达方式 |

程序无法阻止所有外部宿主反复发出错误调用，但不会因此重复已知发送或自动重试不确定的 QQ 写入。

## DSH 的模型请求重试

本机问题的触发点是 DeepSeek 生成了没有双引号的 `session_id`。这发生在 DSH 解析工具参数时，尚未到达 Tulpa，不能只在 MCP 服务里补救。

为已安装的官方 DSH `0.2.0-rc.2` 使用其已有 `llm-deepseek.retryPolicy`，不改 `node_modules`。每个模型请求步骤最多重试两次（合计三次尝试），约 0.75 秒、1.5 秒退避并带少量抖动；服务端要求的等待最多 10 秒。覆盖坏 JSON/协议响应、流提前断开、空响应及原有可重试的网络/限流/服务端错误。认证、权限、额度、参数拒绝不加入可重试名单。

这是重新请求尚未成功解析的模型输出，不是重放 QQ 工具。DSH 在整个模型响应验证完成后才派发工具；已完成的工具不会因为下一步模型响应出错而再次执行。停止时取消等待，连续错误耗尽次数后仍向用户报错，不承诺自动修好所有参数。

配置脚本默认只预览；`--apply` 才修改指定 DSH Home，并在该 Home 的 `backups` 目录保存原文件。它保留账号、Token、MCP 配置及其他插件，且不打印这些内容。配置只对指定安装的 DSH 生效，其他 Agent 仍由各自宿主处理模型错误。

```powershell
node scripts/configure_dsh_retry.mjs --dsh-root tools/deepseek-harness --home tools/deepseek-harness/home --apply
node scripts/check_dsh_retry.mjs --dsh-root tools/deepseek-harness
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp_chat_turns.py
```

DSH 测试使用实际安装的 SDK Agent 循环、官方 SSE 解析器、重试插件与本地模拟模型；包含恢复、耗尽上限、断流、认证错误、写入后下一步报错、工具 UNKNOWN 和停止退避。没有调用真实模型或 QQ。Tulpa 测试贯通 MCP HTTP 与隔离 OneBot，覆盖参数错误、幂等、回声、重复游标及原有文字、图片、回应、引用、@、撤权和中断队列。

## 本机更新与回滚

退出 DSH 中正在运行的任务，重启 DSH 进程以加载配置。Tulpa 更新后重启 EXE/托盘并重新连接 MCP，让宿主重新发现 schema；可以用 `get_chat_session` 接续已有会话，不需要重新授权或导入聊天。人物卡仍是会话快照，修改人物卡后若要生效，应停止旧会话再新建。

本次程序备份路径见安装目录 `reports/private/updates/tool-reliability-*`。退出 Tulpa 后按其中 `RESTORE.txt` 恢复程序即可，保留当前数据和回执。DSH 回滚仅恢复其配置备份并重启。用户自己修改的小鲸鱼2号及其他人物卡不在程序覆盖名单中。

自然聊天的报错率还需后续真实使用观察。上述离线测试证明协议恢复和防重复边界，不代表真实模型一定按要求组织工具参数。
