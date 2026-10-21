@echo off
rem ============================================================
rem  GLM Limits - tray setup + launch (for a new PC)
rem  Checks Python, installs dependencies, creates the config
rem  template if missing, then starts the tray app windowless.
rem ============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo [GLM Limits] Проверяю Python...
python --version >nul 2>&1
if errorlevel 1 (
  echo [GLM Limits] ОШИБКА: Python не найден.
  echo   Установи Python с https://www.python.org/downloads/
  echo   При установке отметь галочку "Add python.exe to PATH",
  echo   затем запусти этот файл снова.
  echo.
  pause
  exit /b 1
)
for /f "delims=" %%v in ('python --version 2^>^&1') do echo [GLM Limits] Найден %%v

echo [GLM Limits] Устанавливаю зависимости (pystray, Pillow, requests)...
python -m pip install --quiet -r requirements.txt
if errorlevel 1 (
  echo [GLM Limits] ОШИБКА: не удалось установить зависимости.
  echo   Проверь интернет и запусти этот файл ещё раз.
  echo.
  pause
  exit /b 1
)

if not exist tray_config.json (
  echo {"apiKey": "PASTE_API_KEY_HERE", "platform": "auto", "intervalSec": 120, "lang": "ru", "notify": true} > tray_config.json
  echo [GLM Limits] Создан tray_config.json — открыл его в Блокноте:
  echo   впиши вместо PASTE_API_KEY_HERE свой API-ключ, сохрани ^(Ctrl+S^)
  echo   и запусти этот файл снова.
  start "" notepad tray_config.json
  echo.
  pause
  exit /b 0
)

findstr /c:"PASTE_API_KEY_HERE" tray_config.json >nul 2>&1
if not errorlevel 1 (
  echo [GLM Limits] В tray_config.json остался плейсхолдер — открываю его:
  echo   впиши свой API-ключ, сохрани и запусти этот файл снова.
  start "" notepad tray_config.json
  echo.
  pause
  exit /b 0
)

echo [GLM Limits] Запускаю трей-иконку (это окно можно закрыть)...
start "" pythonw tray_glm_limits.pyw
echo [GLM Limits] Готово. Иконка появится возле часов (возможно, под стрелкой ^^).
echo [GLM Limits] Не появилась? Запусти tray_debug.bat и посмотри ошибку.
echo.
timeout /t 5 >nul
