@echo off
chcp 65001 >nul
setlocal
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
echo Tulpa - QQ 账号识别诊断
echo 请保持 QQ 已登录。本工具不读取聊天正文、不提取密钥、不改配置。
echo.
if not exist "%~dp0runtime\python.exe" (
  echo 没有找到 Tulpa 自带的运行环境。
  echo 请将压缩包里的文件解压到 Tulpa.exe 同一层，再双击本文件。
  pause
  exit /b 1
)
if "%~1"=="" (
  "%~dp0runtime\python.exe" -X utf8 -B "%~dp0diagnose_qq_accounts.py" --root "%~dp0." --open
) else (
  "%~dp0runtime\python.exe" -X utf8 -B "%~dp0diagnose_qq_accounts.py" --root "%~dp0." --data-dir "%~1" --open
)
if errorlevel 1 echo 诊断未完整完成，请保留窗口中的错误信息交给开发者。
echo.
pause
endlocal
