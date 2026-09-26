$ErrorActionPreference = 'Stop'
$senderExe = Join-Path $PSScriptRoot 'tools\snowluma-v1.14.19\node.exe'
$targets = Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.ExecutablePath -eq $senderExe }
foreach ($target in $targets) { Stop-Process -Id $target.ProcessId -ErrorAction SilentlyContinue }
Write-Host 'Project QQ sender stopped. QQ client was not closed.'
