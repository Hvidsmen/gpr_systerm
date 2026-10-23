@echo off
if "%~1"=="" (
  echo Usage: SWITCH_GPR_DATABASE.cmd "data\db.sqlite3"
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0ops\switch-windows-database.ps1" -Root "%~dp0." -DatabasePath "%~1"
if errorlevel 1 (
  pause
  exit /b 1
)
pause
