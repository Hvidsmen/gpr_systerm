@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0ops\control-staging.ps1" %*
pause
