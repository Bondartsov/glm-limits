@echo off
rem ============================================================
rem  GLM Limits - recovery: kills hung tray instances and starts fresh.
rem  Use when the tray does not appear ("GLM Limits already running").
rem ============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo [GLM Limits] Останавливаю зависшие экземпляры трея...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*tray_glm_limits.pyw*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('  закрыт PID ' + $_.ProcessId) }"

timeout /t 1 >nul
echo [GLM Limits] Запускаю трей заново...
start "" pythonw tray_glm_limits.pyw
timeout /t 4 >nul

echo [GLM Limits] Готово. Иконка должна появиться возле часов (возможно, под стрелкой ^).
echo [GLM Limits] Если иконки нет — запусти tray_debug.bat и пришли текст ошибки.
echo.
pause
