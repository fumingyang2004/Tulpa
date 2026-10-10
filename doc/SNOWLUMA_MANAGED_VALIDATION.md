# SnowLuma 托管接入验证记录

基线：`d9bfec7bff9f2ea8cc2c5dd0a37b4264723bdb56`，本地分支 `codex/snowluma-managed`；本轮不 push、不改 GitHub Release。源码与测试使用模拟账号，不包含真实聊天、Token 或截图。

## 官方契约核对

- [v1.14.22 官方完整包](https://github.com/SnowLuma/SnowLuma/releases/tag/v1.14.22)，固定大小 37,866,010 字节、SHA256 `c8076ba2fbcdc19afeb8e8bf9b2956eaab3605e5fa4618c4db702749ae501681`。摘要来自官方 GitHub Release 资产元数据；本轮没有下载或执行这个二进制。
- 官方 bootstrap 环境变量可设置首次管理密码；本实现走正常 login、agreements、record-consent、auth/state 接口，不使用 DEV_MODE，不伪造 UI 点击，不修改上游 gate。
- /processes、probe-login、单 PID load/unload、/config/{uin} 的写入与 applied/online 回执按固定源码核对。
- [hook-session.ts](https://github.com/SnowLuma/SnowLuma/blob/v1.14.22/packages/bridge/src/hook-session.ts) 和 [hook-manager.ts](https://github.com/SnowLuma/SnowLuma/blob/v1.14.22/packages/bridge/src/hook-manager.ts) 显示预存管道可被采用。保守处理为拒绝外部 Hook 共存；未将关闭自动注入误称为完全进程隔离。
- QQ-agent 的固定提交仅用于参考包校验和托管流程，未复制实现。SnowLuma 是独立上游许可组件，不能按 Tulpa 的 MIT 许可再分发。

## 可重复命令

在仓库目录执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\check_snowluma_managed.py
.\.venv\Scripts\python.exe -X utf8 scripts\check_snowluma_managed_transport.py
.\.venv\Scripts\python.exe -X utf8 scripts\check_snowluma_setup.py
node scripts\check_snowluma_managed_ui.cjs
node scripts\check_snowluma_ui.cjs
node scripts\check_mcp_ui.cjs
```

后端新增检查也接受 `--package <打包目录>`，并纳入 `desktop/build.py` 的打包门禁。浏览器依赖现有 Playwright Core / Edge；`TULPA_TEST_PACKAGE` 可指定打包目录，`SNOWLUMA_UI_SCREENSHOTS` 可将截图存到本地私有目录。

## 已验证范围

| 场景 | 证据种类与结论 |
| --- | --- |
| 默认不授权、协议哈希或授权范围变化 | 隔离测试拒绝执行；生产清单的上游授权门禁在建目录和下载之前 |
| 官方源、固定哈希、续传、损包、越界 ZIP、错误版本、取消 | 合成 ZIP + HTTP mock；半成品不启动，不静默降级，不接触官方二进制 |
| 首次初始化和中途停止重开、登录和事件超时、认证失败、配置未应用 | 状态机 fixture；保留凭据，不用重置密码掩盖异常 |
| 多 QQ、无 QQ、未知账号、PID 复用、账号变化、外部 Hook | fixture；只加载选择的 PID；外部 Hook 情形没有启动或停止外部进程 |
| 两窗口、遗留锁、并发状态读写 | 同目录双 controller + Windows 实际跨进程文件锁；原子状态读写压力检查 |
| 凭据保护及子进程退出保护 | 本机 Windows DPAPI 合成数据往返；Windows Job Object 实际 Python 子进程测试，未运行 QQ/SnowLuma |
| HTTP/WS 账号核对、分别鉴权、撤权后旧客户端 | 真实 loopback TCP/WS + 模拟服务；只接收合成心跳，只调用 get_login_info，外部写入为 0 |
| WS 断开/恢复、撤销在途结果 | 模拟 receiver 状态及实际 cached-client 防线；没有另换账号/Token |
| 完整版 / 轻量版界面 | Edge 无头浏览器：未授权、下载阶段、选择 QQ、失败重试、模拟就绪、撤权、条款变化、窄屏；模拟画面明确标注 |
| 旧连接导入 | 原有隔离 HTTP/WS 浏览器及后端测试；.env 旧流程保留，托管开始不覆盖外部配置 |

## 尚未验证 / 尚未完成

用户确认尚无作者书面授权，本轮只做离线实现与测试。因此未执行真实下载、清空原安装路径、QQ 注入、扫码、消息发送、机器重启或真实首次初始化；没有真实“已就绪”截图。完整包内原生组件布局和运行效果仍需实际许可后的验收，不以 source/API mocks 代替。

真实外部服务并发竞争、选定 QQ 原生绑定、进程重启后的残留 Hook 卸载、平台 UAC/防护软件、多 QQ 权限组合、断电和机器重启均未获真机证明。当前外部 Hook 冲突保守阻止；上游动态采用管道的竞态仍是开放自动托管前的阻塞项。

自动跨版本升级 / 回滚、已初始化外部管理实例的密码安全导入和自动接管不在本离线预览中开放。仍可沿用已有明确选择目录的 OneBot 导入。恢复用本地程序备份；不修改外部实例、不处理 QQ 登录数据、不删除真实库。

取得授权后，先输出确认过的 SnowLuma 实例 / 路径 / 自启清单，完整备份验证，再另行执行用户要求的真实冷启动。需要 UAC、扫码或其他用户安全交接时停在相应步骤；不借过去群聊授权发送验收消息。
