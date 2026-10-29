Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv/Scripts/python.exe"
if (-not (Test-Path $python)) { throw "Virtual environment not found: $python" }

if (-not (Get-Command sox -ErrorAction SilentlyContinue)) {
    $wingetPackages = Join-Path $env:LOCALAPPDATA "Microsoft/WinGet/Packages"
    $soxExe = Get-ChildItem $wingetPackages -Recurse -Filter "sox.exe" -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -like "*ChrisBagwell.SoX*" } |
        Select-Object -First 1
    if ($soxExe) {
        $env:Path = "$($soxExe.DirectoryName);$env:Path"
    }
}

Push-Location $projectRoot
try {
    # 中文日志在 GBK 控制台输出乱码的防护，与 common.ps1 的 Demo 启动保持一致
    $env:PYTHONUTF8 = "1"
    # 0.0.0.0 = 监听所有网卡，局域网同事可通过 http://<本机IP>:7871 直连
    & $python -m studio.api --host 0.0.0.0 --port 7871
} finally { Pop-Location }
