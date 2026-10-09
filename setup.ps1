$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force .tmp,.cache,imports,data | Out-Null
$env:TEMP = Join-Path $PSScriptRoot '.tmp'
$env:TMP = $env:TEMP
if (!(Test-Path .venv\Scripts\python.exe)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) {throw 'venv creation failed'}
}
& .\.venv\Scripts\python.exe -m pip install --disable-pip-version-check --cache-dir .cache\pip -r requirements/readers.txt -r requirements/artifacts.txt
if ($LASTEXITCODE -ne 0) {throw 'Dependency installation failed'}
& .\.venv\Scripts\python.exe scripts\install_readers.py
if ($LASTEXITCODE -ne 0) {throw 'Reader installation failed'}
& .\.venv\Scripts\python.exe scripts\prepare_reader_runtime.py
if ($LASTEXITCODE -ne 0) {throw 'Reader runtime preparation failed; see DESKTOP.md development requirements'}
if (!(Test-Path .env)) {Copy-Item .env.example .env}
Write-Output 'Ready. Fill .env, put exports in imports, run .\start.ps1'
