# 他人回应接收修复

2026-10-09，本地更新，未发布 GitHub release。用户实际使用目录为 `C:\Lab0921\release\0.5.2-mcp\Tulpa-Bot`。

## 原因与修复

用户反馈机器人能贴回应并收到自身回声，但他人对机器人发言贴表情后，通知列表为空。

核对实际 SnowLuma 1.14.20 配置：机器人 WebSocket 节点 `reportSelfMessage=false`；发送后的聊天记录来自 Tulpa 的成功发送回执。原通知接收代码仅接受 `source=onebot_websocket`，因而过滤掉针对这些发言的通知。SnowLuma 同时间段日志记录了真实他人回应，包含 Unicode 鲸鱼，证明桥接端已识别这些动作。

修复允许通知关联经过成功操作账本核验的本人发送回执：同授权、群、本人账号、原始 ID、时间及当前实例必须一致；必须提供有效 sequence。仍拒绝失败、未知、伪造、过期、跨授权、撤回和虚拟目标。这项放行只用于读取观察，不改变添加/撤销或原生引用的写入边界。

另修复同一秒内的时间比较：成功回执记录毫秒，原生通知只有秒；按秒比较，不把同秒内真实回应误判为早于发言。重复同一操作者、表情、动作及计数在有限窗口内去重，保留 add→remove→add 的变化。

只有他人对本人发言的新回应会形成 `event=reactions`，按现有静默窗口合并，给模型原消息和操作者信息，允许自愿接话或沉默。自己的回声不会触发新参与。独立交付水位避免旧通知循环；不伪造聊天消息、不自动发送、不新建模型调用。

## 验收范围

`scripts/check_mcp_reactions.py` 新增完整复现：WS 不上报本人发言 → 通过 MCP 成功发送一条 fixture 标记 → 另一操作者添加/撤销 → 返回独立参与批次 → 允许接话的提示 → 重复及自身通知不再触发。通知不产生自动文字或 reaction 写入；发送回执仍不能作为 reaction 写目标。额外覆盖伪造/过期回执、错误本人账号、虚拟、撤回及无效 sequence。

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp_reactions.py
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp_chat_targets.py
.\.venv\Scripts\python.exe -X utf8 scripts/check_mcp_chat.py
```

自动验收使用隔离 fixture。真实试验及覆盖后的安装验收另记录在本机 `reports/private/updates`；不把原始聊天、账号、凭据和私人反馈文件加入发布包。日志中的真实他人回应不等于已经在修复后再次完成双账号真机测试，报告分别注明。

重启实际 `Tulpa.exe` 并让外部 Agent 重连、重新开启持续群聊。无需重建已有授权或修改人物卡。回滚时恢复本次备份的程序和文档；保留当前数据库及新增兼容列，避免丢失升级后授权和操作回执。
