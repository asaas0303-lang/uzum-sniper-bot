@echo off
chcp 65001 >nul
echo [Uzum Sniper Bot Holati]
powershell -NoProfile -Command "$procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*bot.py*' }; if ($procs) { Write-Host '✅ BOT ISHLAMOQDA!' -ForegroundColor Green; $procs | ForEach-Object { Write-Host ('   PID: ' + $_.ProcessId + ' | Boshlangan vaqt: ' + $_.CreationDate) } } else { Write-Host '❌ BOT HOZIR ISHLAMAYAPTI.' -ForegroundColor Yellow }"
echo.
pause

