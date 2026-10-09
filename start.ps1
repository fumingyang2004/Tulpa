$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'
$env:TEMP = Join-Path $PSScriptRoot '.tmp'
$env:TMP = $env:TEMP
$env:GRADIO_ANALYTICS_ENABLED = 'False'
if ((Test-Path -LiteralPath "$PSScriptRoot\tools\snowluma-v1.14.20\node.exe") -and
    (Test-Path -LiteralPath "$PSScriptRoot\.env") -and
    (Select-String -LiteralPath "$PSScriptRoot\.env" -Pattern '^REPLY_ONEBOT_URL\s*=\s*.?http' -Quiet)) {
    & "$PSScriptRoot\scripts\start-reply-sender.ps1"
}
& "$PSScriptRoot\.venv\Scripts\python.exe" "$PSScriptRoot\app.py"
