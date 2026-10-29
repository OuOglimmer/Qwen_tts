Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# 桌面快捷方式入口：后台静默拉起后端 API (7871) 与 Next.js 前端 (3000)，然后打开浏览器。
# 历史上这里拉起的是已下线的 Gradio 界面（7870，见已删除的 start_studio.ps1）；
# 现在与 start_studio.cmd 启动的是同一套新系统，区别只是不弹控制台窗口、由日志留痕。
$projectRoot = Split-Path -Parent $PSScriptRoot
$apiScript = Join-Path $PSScriptRoot "start_api.ps1"
$frontendScript = Join-Path $PSScriptRoot "start_frontend.ps1"
$studioUrl = "http://127.0.0.1:3000"
$launcherLog = Join-Path $projectRoot "data\logs\desktop-launcher.log"

function Write-LauncherLog {
    param([string]$Message)
    try {
        $logDir = Split-Path -Parent $launcherLog
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
        Add-Content -LiteralPath $launcherLog -Value ("{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message) -Encoding UTF8
    } catch {
        # Logging must never prevent the workbench from starting.
    }
}

function Show-LauncherError {
    param([string]$Message)
    Write-LauncherLog "ERROR $Message"
    try {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show($Message, "Qwen3 TTS Voice Studio startup failed", "OK", "Error") | Out-Null
    } catch {
        # The desktop launcher is hidden; keep the failure available in the log.
    }
}

function Test-Port {
    param([int]$Port)
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Start-HiddenScript {
    param([string]$ScriptPath, [string]$Name)
    Write-LauncherLog "Starting $Name from $ScriptPath"
    return Start-Process powershell.exe `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $ScriptPath) `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -PassThru
}

function Wait-Port {
    param([int]$Port, [System.Diagnostics.Process]$Process, [int]$Seconds)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while (-not (Test-Port $Port) -and (Get-Date) -lt $deadline) {
        if ($Process.HasExited) {
            Write-LauncherLog "Process exited early with code $($Process.ExitCode)"
            break
        }
        Start-Sleep -Milliseconds 500
    }
    return (Test-Port $Port)
}

try {
    # 后端不监听就拉起；已在运行（比如先点过 start_studio.cmd）则直接复用，不重复占显存。
    if (-not (Test-Port 7871)) {
        $apiProcess = Start-HiddenScript -ScriptPath $apiScript -Name "backend API"
        if (-not (Wait-Port -Port 7871 -Process $apiProcess -Seconds 60)) {
            Show-LauncherError "The backend API did not start within 60 seconds. Check data\logs\desktop-launcher.log."
            exit 1
        }
    }
    else {
        Write-LauncherLog "Backend API already listening on 7871; reusing it"
    }

    if (-not (Test-Port 3000)) {
        $frontendProcess = Start-HiddenScript -ScriptPath $frontendScript -Name "frontend"
        # 首次启动要 npm install，宽带给到 180 秒；后续热启动通常十几秒。
        if (-not (Wait-Port -Port 3000 -Process $frontendProcess -Seconds 180)) {
            Show-LauncherError "The frontend did not start within 180 seconds. Check data\logs\desktop-launcher.log."
            exit 1
        }
    }
    else {
        Write-LauncherLog "Frontend already listening on 3000; reusing it"
    }

    Write-LauncherLog "Workbench is ready; opening $studioUrl"
    Start-Process -FilePath $studioUrl
} catch {
    Show-LauncherError ("Launcher error: {0}" -f $_.Exception.Message)
    exit 1
}
