@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0ops\update-windows.ps1" -Root "%~dp0."
set "GPR_UPDATE_EXIT=%ERRORLEVEL%"
pause
exit /b %GPR_UPDATE_EXIT%
