@echo off
rem ============================================================
rem  GLM Limits - update script
rem  Pulls the latest code and opens chrome://extensions
rem  so you can press Reload on the extension card.
rem ============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo [GLM Limits] Обновляю код из GitHub...
git pull --ff-only origin main
if errorlevel 1 (
  echo.
  echo [GLM Limits] ОШИБКА: не удалось обновиться.
  echo   - проверьте интернет;
  echo   - если правили файлы локально, выполните вручную:
  echo     git stash ^&^& git pull --ff-only origin main
  echo.
  pause
  exit /b 1
)

echo.
echo [GLM Limits] Готово. Нажмите кнопку Reload (круговая стрелка)
echo [GLM Limits] на карточке GLM Limits — обновление вступит в силу.
echo [GLM Limits] Открываю chrome://extensions...
start "" chrome "chrome://extensions" 2>nul
echo [GLM Limits] Если Chrome не открылся — откройте chrome://extensions вручную.
echo.
pause
