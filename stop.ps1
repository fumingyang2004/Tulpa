$ErrorActionPreference='Stop'
$appPath=Join-Path $PSScriptRoot 'app.py'
$matches=Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python' -and $_.CommandLine -and $_.CommandLine.Contains($appPath)
}
foreach ($process in $matches) {
    Stop-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
}
Write-Output 'Project app stopped.'
