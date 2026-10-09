$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$senderExe = Join-Path $projectRoot 'tools\snowluma-v1.14.20\node.exe'
$targets = Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.ExecutablePath -eq $senderExe }
foreach ($target in $targets) { Stop-Process -Id $target.ProcessId -ErrorAction SilentlyContinue }
Write-Host 'Project QQ sender stopped. QQ client was not closed.'
