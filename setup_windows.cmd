@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_windows.ps1" %*
set "setup_exit=%ERRORLEVEL%"
if not "%setup_exit%"=="0" (
  echo.
  echo Setup failed. Read the message above.
  pause
)
exit /b %setup_exit%
