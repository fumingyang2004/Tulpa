@echo off
chcp 65001 >nul
setlocal
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
echo Tulpa 0.5.1 - QQ 读取修复与导入 v2
echo 请先右键托盘 Tulpa 图标，选择“彻底退出”。QQ 保持登录。
echo 原 QQ 数据不会移动；现有 Tulpa 数据会先备份，再分批导入。
echo.
if not exist "%~dp0runtime\python.exe" (
  echo 请将修复包里的全部文件解压到正在使用的 Tulpa.exe 同一层。
  pause
  exit /b 1
)
set "TULPA_FIX_QQ_SOURCE=F:\QQ\缓存"
if not "%~1"=="" set "TULPA_FIX_QQ_SOURCE=%~1"
"%~dp0runtime\python.exe" -X utf8 -B "%~dp0qq_path_fix\fix.py" --root "%~dp0." --source "%TULPA_FIX_QQ_SOURCE%"
set "TULPA_FIX_RESULT=%ERRORLEVEL%"
echo.
if not "%TULPA_FIX_RESULT%"=="0" echo 本次未完整完成。请保留窗口信息与 reports/private 内的运行记录。
pause
exit /b %TULPA_FIX_RESULT%
