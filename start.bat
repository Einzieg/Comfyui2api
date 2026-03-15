@echo off
setlocal

powershell -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set "exit_code=%ERRORLEVEL%"

if not "%exit_code%"=="0" (
  echo.
  echo comfyui2api failed to start. Exit code: %exit_code%
  pause
)

exit /b %exit_code%
