@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_local.ps1" %*
set "start_exit=%ERRORLEVEL%"
if not "%start_exit%"=="0" (
  echo.
  echo Startup failed. Read the message above.
  pause
)
exit /b %start_exit%
