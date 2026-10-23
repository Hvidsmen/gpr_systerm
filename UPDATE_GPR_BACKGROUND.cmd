@echo off
schtasks.exe /Run /TN "YDS Portal" >nul 2>&1
schtasks.exe /Run /TN "YDS Portal Update"
if errorlevel 1 pause
