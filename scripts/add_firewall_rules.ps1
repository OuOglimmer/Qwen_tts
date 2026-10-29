# 一次性放行 QwenTTS Studio 的局域网入站端口（需管理员权限运行）
New-NetFirewallRule -DisplayName "QwenTTS Frontend 3000" -Direction Inbound -Protocol TCP -LocalPort 3000 -Action Allow | Out-Null
New-NetFirewallRule -DisplayName "QwenTTS API 7871" -Direction Inbound -Protocol TCP -LocalPort 7871 -Action Allow | Out-Null
