@echo off
chcp 65001 >nul
echo [Uzum Sniper Bot] Bot to'xtatilmoqda...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*bot.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('[+] Jarayon to''xtatildi (PID: ' + $_.ProcessId + ')') }"
echo.
echo Bot muvaffaqiyatli to'xtatildi.
timeout /t 3 >nul

