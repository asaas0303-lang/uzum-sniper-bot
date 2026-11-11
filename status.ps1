$p = Get-Process python, pythonw -ErrorAction SilentlyContinue
if ($p) {
    Write-Host "✅ [OK] BOT 24/7 FONDA ISHLAMOQDA!" -ForegroundColor Green
    $p | ForEach-Object { Write-Host ("   PID: " + $_.Id + " (" + $_.ProcessName + ")") }
} else {
    Write-Host "❌ [STOPPED] BOT HOZIR ISHLAMAYAPTI." -ForegroundColor Yellow
}
if (Test-Path "$PSScriptRoot\bot_service.log") {
    Write-Host ""
    Write-Host "Oxirgi 10 ta log:" -ForegroundColor Cyan
    Get-Content "$PSScriptRoot\bot_service.log" -Tail 10
}
