Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-QwenTtsService {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("VoiceClone", "VoiceDesign")]
        [string]$Service
    )

    $projectRoot = Split-Path -Parent $PSScriptRoot
    $settings = Import-PowerShellDataFile (Join-Path $projectRoot "config/models.psd1")
    $serviceConfig = $settings[$Service]
    $python = Join-Path $projectRoot ".venv/Scripts/python.exe"
    $modelOverride = if ($Service -eq "VoiceClone") { $env:QWEN_TTS_CLONE_MODEL_PATH } else { $env:QWEN_TTS_GENERATION_MODEL_PATH }
    $modelPath = if ($modelOverride) { [Environment]::ExpandEnvironmentVariables($modelOverride) } else { $serviceConfig.ModelPath }
    # Never Join-Path a rooted value: it concatenates literally, so
    # '<repo>' + 'D:/ouyang/model/x' becomes '<repo>\D:\ouyang\model\x', and the
    # model.safetensors check below then fails with a misleading "Model is
    # incomplete or missing". Both the psd1 and the env vars may be absolute.
    if (-not [System.IO.Path]::IsPathRooted($modelPath)) {
        $modelPath = Join-Path $projectRoot $modelPath
    }
    $outputPath = Join-Path $projectRoot ("outputs/" + $Service.ToLowerInvariant())

    if (-not (Test-Path $python)) {
        throw "Virtual environment not found: $python"
    }
    if (-not (Test-Path (Join-Path $modelPath "model.safetensors"))) {
        throw "Model is incomplete or missing: $modelPath"
    }

    New-Item -ItemType Directory -Force -Path $outputPath | Out-Null
    $env:QWEN_TTS_OUTPUT_DIR = $outputPath
    $env:PYTHONUTF8 = "1"
    # Gradio calls its own localhost startup endpoint. Keep that health check
    # out of desktop/system HTTP proxies, which otherwise return a false 502.
    $localBypass = "127.0.0.1,localhost,::1"
    $env:NO_PROXY = if ($env:NO_PROXY) { "$localBypass,$env:NO_PROXY" } else { $localBypass }
    $env:no_proxy = $env:NO_PROXY

    # WinGet updates the user PATH for future terminals only. Discover SoX so
    # the launchers also work immediately in an already-open Codex/terminal.
    if (-not (Get-Command sox -ErrorAction SilentlyContinue)) {
        $wingetPackages = Join-Path $env:LOCALAPPDATA "Microsoft/WinGet/Packages"
        $soxExe = Get-ChildItem $wingetPackages -Recurse -Filter "sox.exe" -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -like "*ChrisBagwell.SoX*" } |
            Select-Object -First 1
        if ($soxExe) {
            $env:Path = "$($soxExe.DirectoryName);$env:Path"
        }
    }

    # One 8 GB GPU cannot safely keep both 1.7B models resident. The named
    # mutex follows the foreground service process and prevents accidental
    # dual startup without relying on fragile PID files.
    $gpuMutex = [System.Threading.Mutex]::new($false, "Local\QwenTTS-1.7B-GPU")
    $hasMutex = $false
    try {
        $hasMutex = $gpuMutex.WaitOne(0)
        if (-not $hasMutex) {
            throw "Another QwenTTS 1.7B service is already using the GPU. Stop it with Ctrl+C before switching models."
        }

        Write-Host "Starting $($serviceConfig.Name)" -ForegroundColor Cyan
        Write-Host "Model: $modelPath"
        Write-Host "Open:  http://127.0.0.1:$($serviceConfig.Port)" -ForegroundColor Green
        Write-Host "Stop:  Ctrl+C"

        Push-Location $projectRoot
        try {
            & $python -m qwen_tts.cli.demo `
                $modelPath `
                --device cuda:0 `
                --dtype bfloat16 `
                --no-flash-attn `
                --ip 127.0.0.1 `
                --port $serviceConfig.Port `
                --concurrency 1
            if ($LASTEXITCODE -ne 0) {
                throw "QwenTTS service exited with code $LASTEXITCODE"
            }
        }
        finally {
            Pop-Location
        }
    }
    finally {
        if ($hasMutex) {
            $gpuMutex.ReleaseMutex()
        }
        $gpuMutex.Dispose()
    }
}
