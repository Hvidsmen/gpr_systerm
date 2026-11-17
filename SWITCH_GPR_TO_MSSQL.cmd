@echo off
%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0ops\switch-to-mssql.ps1" -Root "%~dp0."
set "GPR_SWITCH_EXIT=%ERRORLEVEL%"
pause
exit /b %GPR_SWITCH_EXIT%
