@echo off
%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0ops\prepare-mssql-copy.ps1" -Root "%~dp0."
set "RESULT=%ERRORLEVEL%"
echo.
pause
exit /b %RESULT%
