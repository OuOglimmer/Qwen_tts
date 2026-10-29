Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $projectRoot "frontend"

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm not found. Install Node.js LTS (https://nodejs.org) first."
}

if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
    Write-Host "Installing frontend dependencies (first run)..." -ForegroundColor Cyan
    Push-Location $frontend
    try { npm install } finally { Pop-Location }
}

Write-Host "Starting Next.js frontend on http://0.0.0.0:3000 (LAN accessible via http://<本机IP>:3000)" -ForegroundColor Green
Write-Host "Stop: Ctrl+C"
Push-Location $frontend
try { npm run dev -- --hostname 0.0.0.0 --port 3000 } finally { Pop-Location }
