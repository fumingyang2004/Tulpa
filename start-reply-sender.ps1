$ErrorActionPreference = 'Stop'
$senderDir = Join-Path $PSScriptRoot 'tools\snowluma-v1.14.20'
$senderExe = Join-Path $senderDir 'node.exe'
if (-not (Test-Path -LiteralPath $senderExe)) {
    throw 'QQ sender is not installed. See doc/SNOWLUMA_SETUP.md.'
}
$existing = Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.ExecutablePath -eq $senderExe }
if ($existing) { Write-Host 'QQ sender is already running.'; return }
$env:SNOWLUMA_TELEMETRY = '0'
# Reuse consent saved by SnowLuma; leave new agreement acceptance to its WebUI.
$env:SNOWLUMA_LOG_LEVEL = 'warn'
$env:TEMP = Join-Path $PSScriptRoot '.tmp'
$env:TMP = $env:TEMP
$bootstrapFile = Join-Path $senderDir 'bootstrap-password.local'
if (Test-Path -LiteralPath $bootstrapFile) { $env:SNOWLUMA_WEBUI_BOOTSTRAP_PASSWORD = Get-Content -LiteralPath $bootstrapFile -Raw }
$senderProcess = Start-Process -FilePath $senderExe -ArgumentList 'index.mjs' -WorkingDirectory $senderDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $senderDir 'service.log') -RedirectStandardError (Join-Path $senderDir 'service-error.log')
Remove-Item Env:\SNOWLUMA_WEBUI_BOOTSTRAP_PASSWORD -ErrorAction SilentlyContinue
Write-Host "QQ sender started (PID $($senderProcess.Id)), localhost only."
