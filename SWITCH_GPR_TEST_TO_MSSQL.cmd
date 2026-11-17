@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0ops\switch-test-to-mssql.ps1"
pause
