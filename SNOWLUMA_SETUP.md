# 从零连接 QQ：取得 Tulpa 要填的 OneBot 地址和 Token

这篇教程适用于 **Windows 10/11 x64、Tulpa 0.3.0、SnowLuma v1.14.19**。不需要编程，也不需要购买域名、服务器或申请 QQ 开发者账号。

按下面的步骤操作，你会得到两项可以填进 Tulpa 的内容：

| Tulpa 中的输入框 | 完成教程后填什么 |
| --- | --- |
| 本机 OneBot 地址 | `http://127.0.0.1:3000`，前提是按本文使用端口 `3000` |
| 访问 Token | 在 SnowLuma 的 **HTTP API 节点**里生成并复制的一串字符，每个人的值不同 |

**地址是你在本机开出的接口；Token 是你为这个接口设置的访问密码。** 它们不是从 QQ 官网领取的，也不是模型服务商的 API Key。只有接口已启动、Token 与该接口保存的值一致，这一对配置才真正可用。

## 第 1 步：下载并解压 SnowLuma

1. 打开 [SnowLuma v1.14.19 官方下载页](https://github.com/SnowLuma/SnowLuma/releases/tag/v1.14.19)。
2. 向下找到 **Assets**，点击 `SnowLuma-v1.14.19-win-x64.zip`。若附件列表没有展开，先点 **Assets**。也可以使用 [这个官方文件的直接下载链接](https://github.com/SnowLuma/SnowLuma/releases/download/v1.14.19/SnowLuma-v1.14.19-win-x64.zip)。
3. 下载结束后，在 Windows 文件资源管理器中找到 ZIP，右键选择 **全部解压缩**。例如解压到 `C:\SnowLuma`，也可以选择自己有写入权限的其他本地文件夹。
4. 打开解压后的文件夹，找到 `launcher.bat`。它旁边应当有 `node.exe`、`index.mjs` 等文件；如果外面还有一层同名文件夹，就继续进入。

**做到这里应当看到：** `launcher.bat` 和 `node.exe` 在同一个普通文件夹中，而不是仍在 ZIP 预览里。

下载时选文件名中**没有 `lite`** 的 Windows 完整版；它自带 Node.js，不需要另外安装。`Source code` 是源码包，不是这里要运行的程序。

## 第 2 步：登录 QQ，再启动 SnowLuma

1. 在这台电脑上打开 QQ，登录你要在 Tulpa 中使用的账号，进入聊天列表。
2. 回到刚才解压的 SnowLuma 文件夹，双击 `launcher.bat`。如果 Windows 隐藏扩展名，它可能只显示为 `launcher`，类型是“Windows 批处理文件”。
3. 会打开一个不断显示文字的命令窗口。等待它显示 WebUI 地址，通常是：

   ```text
   [WebUI] listening http://127.0.0.1:5099
   ```

4. 在这个窗口里找到 **WebUI 初始登录凭据 / Initial WebUI Credentials**。记下该部分显示的初始密码，下一步用它登录管理页面。
5. 保留这个命令窗口，可以最小化。关闭它会停止 SnowLuma。

**做到这里应当看到：** SnowLuma 的启动窗口仍在运行，并显示了管理页面的地址。这里的 `5099` 是管理页面端口，还不是要填给 Tulpa 的接口端口。

如果你以前已经使用过这个 SnowLuma 文件夹，启动时可能不再显示初始密码，请使用此前设置的管理页面密码。

## 第 3 步：进入 SnowLuma 管理页面

1. 打开 Edge 或 Chrome，将 `http://127.0.0.1:5099` 粘贴到浏览器**地址栏**并回车。若启动窗口显示了其他端口，以窗口中的地址为准。
2. 在 **输入访问令牌** 中填写上一步的 WebUI 初始密码，点击 **进入控制台**。v1.14.19 的这个登录页只有密码输入框，不需要寻找用户名输入框。
3. 首次进入会出现设置向导。阅读《用户协议》和《隐私政策》；决定接受后，勾选 **我已阅读并同意《用户协议》与《隐私政策》**，点击 **同意并继续**。
4. 如果出现 **设置新的访问密码**，填写 **新密码** 和 **确认新密码**，按照页面的密码强度要求调整，再点 **保存并完成**。保存好这个密码，以后用它进入 SnowLuma 管理页面。

**做到这里应当看到：** SnowLuma 控制台，可以找到“总览”“节点配置”“日志”等入口。

注意：登录框虽然写着“访问令牌”，这里输入的仍然是 **SnowLuma 管理页面密码**。Tulpa 要的 **OneBot Token** 在第 5 步单独生成，两者不要混用。

## 第 4 步：选中你的 QQ 账号

1. 点击 SnowLuma 左侧的 **节点配置**。如果左侧导航收起来了，先展开导航菜单。
2. 在这个页面的账号列表中，点击你已经登录的 QQ 账号。窗口较窄时列表可能只显示头像，可点击展开账号列表。
3. 查看右侧 **OneBot 协议端点** 下方的 `UIN`，它就是 QQ 号。确认是你准备在 Tulpa 中使用的那个账号。

**做到这里应当看到：** 右侧有 **通用设置、HTTP API、HTTP 客户端、WS 服务端、WS 客户端** 这些标签。

如果显示“暂无在线会话”或没有账号：先确认 QQ 已经登录。再进入左侧 **进程注入**，点刷新；找到 QQ 进程后，如果它显示“可加载”，点击该行的 **加载**，阅读提示并确认加载，等待账号出现，然后回到“节点配置”。若显示“等待登录”，先完成 QQ 登录。仍失败时保留该页面和启动窗口的错误信息，不要在没有账号的情况下继续猜配置。

## 第 5 步：创建 HTTP API，并取得 Token

1. 点击 **HTTP API** 标签。
2. 点击右上方 **新建 HTTP API**。弹窗标题应为 **新建 HTTP API 服务**。
3. 按下表填写或核对：

   | 弹窗中的项目 | 本教程使用的值 |
   | --- | --- |
   | 启用 | 打开 |
   | 名称 | `tulpa-local`，只是方便你辨认这个节点 |
   | 主机 | `127.0.0.1` |
   | 端口 | `3000` |
   | 路径 | `/` |
   | 授权 Token | 保留新建时自动生成的随机值；为空时按下文生成 |
   | 启用 WebSocket | 关闭 |
   | 消息格式 | 数组 |
   | 上报自身消息 | 保持默认关闭 |

4. 找到 **鉴权 → 授权 Token**。输入框默认以密码形式隐藏内容；新建节点时已经生成了随机 Token。
5. 如果这一栏是空的，点击输入框右侧的**循环箭头**按钮。鼠标停在按钮上会显示 **生成新的随机令牌**。这会自动产生一串随机字符，不需要你自己编密码。
6. 点击弹窗底部 **创建节点**，等待页面显示 **保存成功，热重载完成**，并核对节点卡片上出现 **正常**、`127.0.0.1:3000/` 和 **已设 Token**。若只显示“当前离线”或“应用失败”，先解决连接问题，尚不能算配置完成。
7. 点击 `tulpa-local` 卡片右侧的**铅笔图标（编辑）**。在 **授权 Token** 旁点击**两个重叠方框的图标（复制）**，看到 **已复制** 即可。此时剪贴板里就是已保存的 Token，可直接粘贴到下一步的验证输入中；不要再点循环箭头。

**做到这里，你的两项配置是：**

```text
URL：http://127.0.0.1:3000
Token：刚刚从“授权 Token”旁的复制按钮取得的完整字符
```

上面第二行是说明，不是可用 Token；不要把这句话填进 Tulpa。Token 的大小写和全部字符都必须保留。之后需要再次复制时，回到同一个节点点“编辑 → 复制”即可，不需要重新生成。

如果 **HTTP API** 里已经有你自己配置的节点，可以直接点它的铅笔图标，核对主机、端口、路径和启用状态，然后复制已有 Token。不要在同一端口再建一个节点，也不要改掉其他程序正在使用的 Token。本教程后面的地址按 `3000`、路径 `/` 举例。

## 第 6 步：验证这对 URL 和 Token 确实有效

这一步只读取当前登录 QQ 号，**不发送消息，也不修改群设置**。它能同时检查接口地址、Token 和登录账号；仅看到“已保存”不能代替这个检查。

1. 保持 QQ 和 SnowLuma 运行。
2. 打开 Windows 开始菜单，搜索 **Windows PowerShell**，正常打开即可，不需要管理员权限。
3. 复制下面的整段命令，粘贴到 PowerShell 中执行。如果最后停在等待输入命令的状态，再按一次回车。
4. 出现 **粘贴 SnowLuma 的授权 Token** 提示后，回到 SnowLuma 的节点编辑页再次点击 Token 的 **复制**，切回 PowerShell，按 `Ctrl+V` 粘贴并回车。输入会被遮住，不会把 Token 明文打印出来。

```powershell
& {
    $onebotUrl = 'http://127.0.0.1:3000'
    $onebotSecret = Read-Host '粘贴 SnowLuma 的授权 Token，然后回车' -AsSecureString
    try {
        $onebotToken = [System.Net.NetworkCredential]::new('', $onebotSecret).Password
        $onebotResult = Invoke-RestMethod -Method Post -Uri "$onebotUrl/get_login_info" -Headers @{ Authorization = "Bearer $onebotToken" } -ContentType 'application/json' -Body '{}' -TimeoutSec 10 -MaximumRedirection 0
        if ($onebotResult.status -eq 'ok' -and $onebotResult.retcode -eq 0 -and $onebotResult.data.user_id) {
            Write-Host "连接成功；登录 QQ：$($onebotResult.data.user_id)"
            Write-Host '请核对这个 QQ 号与你准备在 Tulpa 中使用的账号一致。'
        } else {
            Write-Host "接口未返回成功结果：status=$($onebotResult.status)，retcode=$($onebotResult.retcode)"
        }
    } catch {
        Write-Host "未连通：$($_.Exception.Message)"
    } finally {
        $onebotToken = $null
        $onebotSecret = $null
    }
}
```

**做到这里应当看到：** `连接成功；登录 QQ：你的QQ号`，而且号码正确。没有这个结果时，先按本文后面的故障表排查，不要用实际发送来反复试连接。

若你在第 5 步改用了其他端口，例如 `3001`，也要把这段命令中 `$onebotUrl` 的值改成 `http://127.0.0.1:3001`。

## 第 7 步：把 URL 和 Token 填进 Tulpa

1. 打开 `Tulpa.exe`，点击左下方 **模型设置**。
2. 在“连接你的模型”窗口里展开 **QQ 发送接口（可选）**。
3. 在 **本机 OneBot 地址** 填入 `http://127.0.0.1:3000`。不要添加 `/get_login_info`，也不要填写管理页面的 `5099` 地址。
4. 回到 SnowLuma，打开刚才节点的编辑弹窗，点击 **授权 Token** 旁的 **复制**。
5. 切回 Tulpa，将它粘贴到 **访问 Token**。不要填写 WebUI 登录密码，也不要填写模型 API Key。
6. 点击 **保存设置**。

如果你是第一次启动 Tulpa，还需要先在同一窗口上方填好模型服务商提供的 **API 地址、API Key、模型名称**，否则表单不能保存。这三项属于大模型服务，与本教程取得的 OneBot 地址和 Token 是两套配置；已有模型配置可以保留，已保存的 Key 留空不变。

**做到这里应当看到：** Tulpa 提示设置已保存。重新打开设置时，Token 输入框显示“已保存 · 留空保留”且不回显明文，这是正常现象。保存提示只说明 Tulpa 已记录配置，连通性以第 6 步结果为准。

接下来，使用与该 QQ 账号匹配的本地聊天。在“帮我回复”中生成并审核文字后再批准发送；群管理仍取决于账号权限和当前审批设置。配置成功不会替你自动回复消息。

## 以后每次使用怎么开

先打开并登录 QQ，再运行 SnowLuma 文件夹里的 `launcher.bat`，最后打开 Tulpa。第一次保存后不用每天重新建节点、生成 Token 或填写设置。SnowLuma 的命令窗口保持运行，浏览器中的管理页面可以关闭。

只导入、实时读取或查询本地聊天时不要求 OneBot；回复发送、群管理、实时读取公告 / 精华 / 群文件目录才需要这个连接。

## 卡住时，对照这里处理

| 看到的情况 | 下一步怎么做 |
| --- | --- |
| 找不到 `launcher.bat`，或提示找不到 Node | 核对下载的是 `win-x64.zip` 完整版；完整解压后，在同时有 `launcher.bat`、`node.exe` 的文件夹里运行，不要单独拖出批处理文件。 |
| `5099` 管理页面打不开 | 看 SnowLuma 启动窗口是否还在运行；使用其中 `[WebUI] listening` 后面的地址。若程序启动失败，先看窗口末尾错误。 |
| 找不到初始管理密码 | 在启动窗口向上找“WebUI 初始登录凭据”。全新安装且尚未完成改密时，关闭并重新启动会生成新的临时密码；已经改过密码时使用自己保存的新密码，这个办法不会重置它。 |
| 管理页面能登录，却看不到 QQ 账号 | 回到第 4 步，确认 QQ 登录和“进程注入”状态；管理页面正常不代表 QQ 已连接。 |
| 找不到“HTTP 服务端” | 本版本标签实际叫 **HTTP API**。不要选择 HTTP 客户端、WS 服务端或 WS 客户端。 |
| “创建节点”按钮不能点 | 核对名称非空且不重复；若 Token 强度提示不通过，点循环箭头重新生成，再保存。 |
| 节点显示“异常 / 应用失败”，日志有 `EADDRINUSE` | `3000` 被占用了。先看是否已经存在可用节点；如果确需新建，改用未占用端口，例如 `3001`，点击“保存修改”，并同步修改验证命令和 Tulpa 地址中的端口。不要关闭不认识的进程。 |
| 第 6 步提示 `401` 或 `unauthorized` | URL 上有服务响应，但 Token 不匹配。重新复制**这个 HTTP API 节点**保存的 Token；改过 Token 时，要先保存节点，再更新 Tulpa。 |
| 第 6 步提示无法连接或超时 | 确认 QQ、SnowLuma 都在运行，节点已启用且显示“正常”；核对主机 `127.0.0.1`、端口和路径 `/`。 |
| 第 6 步显示了另一个 QQ 号 | 回到“节点配置”选择正确 QQ 号，为它配置接口；多个账号的 HTTP 节点使用不同端口，Tulpa 填目标账号对应的地址和 Token。 |
| 直接在浏览器打开 `3000`，看到 `unauthorized` | 浏览器地址栏没有附带 Token，不代表服务没启动。用第 6 步带 Token 的只读查询验证，不要把 Token 拼到网址中。 |
| Tulpa 再次打开设置，Token 框是空的 | 查看是否提示“已保存 · 留空保留”；程序不会显示已保存的密钥，不需要重新生成。 |
| URL 验证成功，但 Tulpa 仍不能用相关功能 | 再复制一次相同节点的 Token 到 Tulpa 并保存；确认本地所选 QQ 会话属于验证返回的账号，并开始新一轮操作。若返回的是权限错误，按群内实际角色处理，改 Token 不会增加群权限。 |

提供错误截图时遮住 Token 和密码即可，保留错误文字、节点状态与端口，方便判断问题。

## 版本核对与独立安装说明

教程的菜单、字段、复制按钮及首次设置流程按 SnowLuma **v1.14.19** 的[官方界面源码](https://github.com/SnowLuma/SnowLuma/blob/v1.14.19/packages/webui/src/components/pages/config-page.tsx)、[节点编辑器](https://github.com/SnowLuma/SnowLuma/blob/v1.14.19/packages/webui/src/components/config/node-edit-dialog.tsx)、[登录页](https://github.com/SnowLuma/SnowLuma/blob/v1.14.19/packages/webui/src/components/pages/login-page.tsx)和[首次设置向导](https://github.com/SnowLuma/SnowLuma/blob/v1.14.19/packages/webui/src/components/pages/onboarding-wizard-page.tsx)核对。其他版本若界面不同，应查看对应版本文档。

需要校验下载完整性时，该官方 Windows 完整 ZIP 的 SHA256 是：

```text
2c77f0bcbe1f828e2d5109d193e71d8dac995e715dd144222a0a2c60a783c5a5
```

Tulpa 0.3.0 包含 OneBot 适配代码，但没有附带 SnowLuma 服务程序，所以本教程从官方独立下载开始。SnowLuma v1.14.19 的 [EULA 第 5.4 条](https://github.com/SnowLuma/SnowLuma/blob/v1.14.19/EULA.md)要求将专有组件并入第三方安装包前取得书面授权；独立使用时的协议由用户在官方界面阅读并决定是否接受。
