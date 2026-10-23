@echo off
if "%~1"=="" (
  echo Usage: NEW_GPR_DATABASE.cmd keep
  echo        NEW_GPR_DATABASE.cmd clear
  echo keep = preserve construction objects and access assignments
  echo clear = empty objects and clear their assignments
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0ops\create-users-database.ps1" -Root "%~dp0." -ObjectAccess "%~1"
set "GPR_NEW_DATABASE_EXIT=%ERRORLEVEL%"
pause
exit /b %GPR_NEW_DATABASE_EXIT%
