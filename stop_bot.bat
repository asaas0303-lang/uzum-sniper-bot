@echo off
powershell -NoProfile -ExecutionPolicy Bypass -Command "Stop-Process -Name python, pythonw -Force -ErrorAction SilentlyContinue"
echo Bot muvaffaqiyatli to'xtatildi.
timeout /t 3 >nul
