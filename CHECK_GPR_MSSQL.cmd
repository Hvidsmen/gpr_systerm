@echo off
%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0ops\check-mssql.ps1" -Root "%~dp0."
set "RESULT=%ERRORLEVEL%"
echo.
pause
exit /b %RESULT%
