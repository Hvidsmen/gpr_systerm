@echo off
setlocal
set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=%ROOT%venv313\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=%ROOT%venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo Python portal environment was not found.
  pause
  exit /b 1
)
"%PYTHON%" "%ROOT%manage.py" check_mssql --server MSK-DB2 --database YDS_PORTAL
set "RESULT=%ERRORLEVEL%"
echo.
whoami
if not "%RESULT%"=="0" echo Run this check as YAMALGAZPROM\dvivanov after the database administrator grants access.
pause
exit /b %RESULT%
