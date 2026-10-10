# 本地 SnowLuma 自动配置验证记录

本轮以 `a0b3170f2c5088acbef451c83c81fc8f43404046` 为开发前检查点，继续在本地 `codex/snowluma-managed` 工作，不 push、不发布。按用户修正，删除自动下载部署路径，改为配置用户选定的本地安装；不将原有离线测试当作真实 QQ 通过。

## 版本和接口

自动初始化以官方 [v1.14.22](https://github.com/SnowLuma/SnowLuma/releases/tag/v1.14.22) / `87527cb7641a5a42f8f0efb73cb066102e004dee` 为准。复用官方 bootstrap 环境变量、login、agreements/record-consent、processes、单 PID load、账号 config API，不修改上游源码或绕过 gate。已有配置导入支持 v1.14.19–v1.14.22。

运行包布局另按官方 `packages/core/vite.config.ts` 和 `.github/workflows/release.yml` 核对：检查 Windows QQ/WS/FFmpeg 原生组件及 EULA/PRIVACY，不把未由打包脚本复制的根目录 LICENSE 当作必需文件。仓库 `.gitattributes` 固定 Markdown 为 LF，保留协议精确哈希检查。

## 可重复命令

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\check_snowluma_managed.py
.\.venv\Scripts\python.exe -X utf8 scripts\check_snowluma_managed_transport.py
.\.venv\Scripts\python.exe -X utf8 scripts\check_snowluma_setup.py
node scripts\check_snowluma_managed_ui.cjs
node scripts\check_snowluma_ui.cjs
```

后端检查支持 `--package <目录>`，纳入打包门禁。浏览器脚本使用现有 Playwright Core / Edge；`PLAYWRIGHT_MODULE` 可指定模块，`SNOWLUMA_UI_SCREENSHOTS` 可保存本机测试截图。

| 场景 | 验证方式 |
| --- | --- |
| 所选文件夹、缺少 Node、错误版本、协议变化、链接目录、文件指纹变化 | 真实临时文件；检查无执行、无下载，变化后需要重新选择 |
| 默认不勾选、伪造/过期选择、同源限制、重复点击 | 本地接口及 Edge；没有有效选择及同意时不能启动 |
| 原地配置、已有密码保护、备份/锁、不同 Tulpa 目录竞争 | 隔离目录、实际文件锁；已有独立配置只走导入，不重置认证 |
| 单 QQ 自动接入、多 QQ 选择、未知账号、PID 复用、账号切换 | 模拟 QQ 进程及管理 API；固定身份，不扩大授权 |
| cancel/retry、重开、WS 断开、撤权后的旧客户端 | 状态机 + 真实 loopback HTTP/WS 合成账号；外部 QQ 写入为零 |
| 凭据、锁与结束准确子进程 | Windows DPAPI、跨进程锁、原子读写、Job Object；子进程只是 Python fixture |
| full/lite、自动导入已有单一节点、多节点不猜测、窄屏 | 真实 Edge + 实际 UI 代码 + 合成接口；模拟画面标注模拟 |

## 未验证及剩余限制

### 一键配置界面回归（2026-10-10）

修复 MCP 入口未接入已有配置导入，以及两个入口各自维护路径和状态的问题。现在输入或选择文件夹、勾选授权后，一次点击“载入并配置”连续完成检查和配置；文件选择本身不启动服务或保存连接。

`check_snowluma_ui.cjs` 同时加载实际 full/lite 设置脚本和 `mcp.js`，覆盖从 MCP 入口导入、切换到 OneBot 入口继续显示同一路径和结果、多账号选择接续、并发重复点击、路径及协议变化清除勾选、HTTP-only 显式选择、失败保留配置、结果未知不自动重试、手动设置及 390px 窄屏。`check_snowluma_managed_ui.cjs` 覆盖新目录一键初始化、QQ 选择、重试和撤销。浏览器为真实 Edge，接口及 QQ 仍为模拟；没有执行真实初始化、改密或 QQ 写入。

未替用户在真实新解压的 SnowLuma 上勾选、设置凭据、加载 QQ，也没有真实发送消息；没有真实首次初始化“已就绪”截图。API fixtures 不代表真实 QQ、扫码、多 QQ、系统重启或安全软件行为已验收。

上游自动采用其他 Hook 管道的动态竞态、真实原生卸载/重启后的残留 Hook 仍待实测。跨版本自动升级、自动降级、接管已设密码的独立管理实例不提供；这些情况保留原配置和已有导入入口，不伪装成全新初始化。

该功能不下载或复制 SnowLuma，所选文件的指纹用于确认用户选择没有变化，不等于上游签名认证。用户应自行从官方渠道取得完整包并遵循其协议。
