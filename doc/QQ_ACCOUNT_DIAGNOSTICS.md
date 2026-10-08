# QQ 账号识别诊断

适用于 Tulpa 完整版和 MCP 轻量版，尤其是点击“重新检测账号”仍没有 QQ 账号、但微信正常的情况。诊断包可以用于已有的 0.5.1 安装，无需安装 Python，也不需要重装或降级 QQ。

0.5.3 已在 `scripts/` 随包提供诊断脚本，无需单独下载诊断 ZIP。在安装目录打开 PowerShell，运行 `./runtime/python.exe -X utf8 -B scripts/diagnose_qq_accounts.py --root . --open`。下面的双击 CMD 方法适用于另外分发的旧版诊断小包。

## 发给遇到问题的用户

1. 保持 QQ 已登录，用平常运行 Tulpa 的 Windows 用户操作，先不要专门改为管理员运行。
2. 解压诊断 ZIP，把里面的 `诊断QQ账号.cmd` 和 `diagnose_qq_accounts.py` 放到 **`Tulpa.exe` 同一层**。旁边应能看到 `runtime`、`tools`、`scripts` 等目录。
3. 双击 **`诊断QQ账号.cmd`**，等待窗口显示“诊断完成”。一般几十秒；单个检测阶段卡住时会超时并继续生成报告。
4. 完成后会打开报告文件夹。将同名的 **`QQ账号诊断-时间.txt` 和 `.json`** 发给开发者。

报告也可手动找到：`Tulpa/reports/private/qq-account-diagnostics/`。

如果默认检查没有发现数据，请到 QQ 设置查看文件/聊天数据的存储位置。可以将那个**文件夹拖到 `诊断QQ账号.cmd` 上**重新运行；这只增加该目录的定点检查，不会改动存储位置。不要移动或删除原数据。

## 会检查什么

- Windows、Python、Tulpa 版本和版本类型，以及正在运行的 QQ 程序版本。
- 使用这份安装包自带的 `list_client_accounts.py` 与 QQ 读取器，实际复现账号检测。
- 分别记录模块加载、数据根目录查找、账号列表生成是否成功，保留耗时、异常类型和 WinError / errno。
- 对照 Windows 文档目录、OneDrive、各本地盘常见位置、明确的数据目录环境变量，以及 QQ 的数据路径设置线索。
- 逐个检查候选目录中是否有数字/非数字子目录，是否存在已知布局的 `nt_msg.db`，以及文件是否为空。这里仅检查元数据，不打开数据库正文。
- 帮助区分目录遗漏、账号目录命名变化、布局变化、权限错误、依赖缺失和检测超时。发现某种线索不等于已经确定根因，需结合完整报告判断。

## 隐私与边界

账号和私人目录名在报告里替换为一致的占位符。不包含聊天正文、联系人、API Key、OneBot Token、解密密钥、完整私人路径、原始配置或原始错误日志。不会扫描进程内存、联网上传、发送消息、导入数据，或修改 QQ/Tulpa 的配置。

只枚举有限目录层级与已知文件名，不扫描整盘。网络目录不会进行额外定点扫描；安装版自身若被路径访问卡住，会在 25 秒后中止该诊断子进程。超时、缺少权限和没有找到是不同状态。

此包只增加诊断文件，不覆盖当前账号识别代码，也不提供未经验证的兼容性修复。

## 开发者

```powershell
.\release\Tulpa\runtime\python.exe -X utf8 -B scripts\diagnose_qq_accounts.py --root release\Tulpa
.\.venv\Scripts\python.exe -X utf8 scripts\check_qq_account_diagnostics.py
```

诊断脚本只有 Python 标准库依赖。实际账号检测在独立子进程中使用目标安装包的读取器，因此依赖缺失或老读取器的异常也能报告；不以开发版检测结果替代对方安装版的结果。
