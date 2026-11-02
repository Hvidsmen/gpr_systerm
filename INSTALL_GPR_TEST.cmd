@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0ops\install-staging.ps1" %*
if errorlevel 1 echo TEST installation failed. Production tasks were not changed.
pause
