@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0ops\set-windows-portal-account.ps1" -Root "%~dp0."
if errorlevel 1 (
  echo Account change failed. Read the message above.
  pause
  exit /b 1
)
pause
