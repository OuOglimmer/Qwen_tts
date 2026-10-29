@echo off
REM 一键启动整个系统：后端 API (7871) + Next.js 前端 (3000)，各自独立窗口。
REM 历史上的 start_studio.cmd 启动的是已废弃的 Gradio 界面 (7870)，现改指向新系统。
setlocal
cd /d "%~dp0"

echo Starting Qwen3 TTS Voiceover Studio
echo   backend : http://127.0.0.1:7871  (LAN: http://^<本机IP^>:7871)
echo   frontend: http://127.0.0.1:3000  (LAN: http://^<本机IP^>:3000)
echo Close the two windows to stop the services.
echo.

start "QwenTTS Backend" powershell.exe -NoProfile -ExecutionPolicy Bypass -NoExit -File "%~dp0scripts\start_api.ps1"
REM 前端首次启动要 npm install，给后端留几秒先占住 7871，避免前端页面刷出 502。
timeout /t 3 /nobreak >nul
start "QwenTTS Frontend" powershell.exe -NoProfile -ExecutionPolicy Bypass -NoExit -File "%~dp0scripts\start_frontend.ps1"

echo Launched. The browser will open once the frontend is ready.
echo If a window closed immediately, check the error shown in it.
timeout /t 2 /nobreak >nul
endlocal
