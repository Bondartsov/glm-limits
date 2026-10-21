@echo off
rem ============================================================
rem  GLM Limits - tray diagnostics
rem  Runs the tray app with a visible console so any error
rem  (missing module, bad config, network) is printed here.
rem ============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo [GLM Limits] Запуск в режиме отладки — ошибки будут видны ниже...
echo.
python tray_glm_limits.pyw
echo.
echo [GLM Limits] Приложение завершилось. Причина ошибки — в тексте выше.
echo [GLM Limits] Пришли этот текст, если нужна помощь.
echo.
pause
