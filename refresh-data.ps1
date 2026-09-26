param([int]$QQDays=30, [int]$QQPerChat=500, [int]$WeChatPerChat=500, [switch]$NoStickers)
$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
$env:PYTHONUTF8='1'
$env:TEMP=Join-Path $PSScriptRoot '.tmp'
$env:TMP=$env:TEMP
$python=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$mediaOptions=if ($NoStickers) { @('--no-stickers') } else { @() }
& $python scripts\export_qq.py --refresh --days $QQDays --per-chat $QQPerChat @mediaOptions
if ($LASTEXITCODE -ne 0) {throw 'QQ export failed; original imports were retained'}
& $python -m chatlocal import imports\qq-real.json @mediaOptions
if ($LASTEXITCODE -ne 0) {throw 'QQ import failed'}
& $python scripts\export_wechat.py --refresh --limit $WeChatPerChat @mediaOptions
if ($LASTEXITCODE -ne 0) {throw 'WeChat export failed; QQ remains available'}
& $python -m chatlocal import imports\wechat-real.json @mediaOptions
if ($LASTEXITCODE -ne 0) {throw 'WeChat import failed'}
Write-Output 'Refresh complete. Refresh the imported-data status in the UI.'
