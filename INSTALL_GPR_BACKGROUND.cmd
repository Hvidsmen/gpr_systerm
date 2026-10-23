@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0ops\install-windows.ps1"
if errorlevel 1 (
  echo Installation failed. Read the message above.
  pause
  exit /b 1
)
pause
