Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Get-Command py -ErrorAction SilentlyContinue) -and -not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python 3.12+ was not found. Install Python from python.org and rerun this script."
}

if (-not (Test-Path $venvPython)) {
    if (Get-Command py -ErrorAction SilentlyContinue) { & py -3.12 -m venv .venv }
    else { & python -m venv .venv }
}

& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements-deployment.txt
foreach ($directory in @("data", "data\voices", "data\projects", "data\temp", "outputs", "models")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot $directory) | Out-Null
}

Write-Host "Python environment is ready: $venvPython" -ForegroundColor Green
Write-Host "Model snapshots are not downloaded automatically." -ForegroundColor Yellow
Write-Host "Run: .venv\Scripts\python.exe scripts\download_models.py"
Write-Host "Then start: start_studio.cmd  (backend API :7871 + frontend :3000)"
