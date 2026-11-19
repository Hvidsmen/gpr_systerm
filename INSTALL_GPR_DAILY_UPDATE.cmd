@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0ops\configure-update-schedule.ps1" -Root "%~dp0."
set "GPR_SCHEDULE_EXIT=%ERRORLEVEL%"
if not "%GPR_SCHEDULE_EXIT%"=="0" echo Schedule was not changed. Read the message above.
pause
exit /b %GPR_SCHEDULE_EXIT%
