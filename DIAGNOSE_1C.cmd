@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0ops\diagnose-1c.ps1" -Root "%~dp0." %*
set "GPR_1C_DIAGNOSTIC_EXIT=%ERRORLEVEL%"
pause
exit /b %GPR_1C_DIAGNOSTIC_EXIT%
