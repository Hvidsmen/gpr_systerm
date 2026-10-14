@echo off
setlocal
cd /d "%~dp0"
title GPR System - port 8001

if not exist "manage.py" (
    echo ERROR: Place this file next to manage.py.
    goto :failed
)

set "GPR_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "GPR_PYTHON=%CD%\.venv\Scripts\python.exe"
"%GPR_PYTHON%" -c "import django" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python with Django is not available. Activate your environment first.
    goto :failed
)

if defined DJANGO_DB_PATH goto :database_ready
if exist "data\db.sqlite3" if exist "db.sqlite3" (
    echo ERROR: Two databases found: data\db.sqlite3 and db.sqlite3.
    echo Set DJANGO_DB_PATH to the database you use before launching.
    goto :failed
)
if exist "data\db.sqlite3" set "DJANGO_DB_PATH=%CD%\data\db.sqlite3"
if not defined DJANGO_DB_PATH if exist "db.sqlite3" set "DJANGO_DB_PATH=%CD%\db.sqlite3"
if not defined DJANGO_DB_PATH (
    echo ERROR: No existing database found. Set DJANGO_DB_PATH explicitly.
    goto :failed
)
:database_ready
if not exist "%DJANGO_DB_PATH%" (
    echo ERROR: Database does not exist: %DJANGO_DB_PATH%
    goto :failed
)

if not defined DJANGO_ALLOWED_HOSTS set "DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1"
set "DJANGO_ALLOWED_HOSTS=%DJANGO_ALLOWED_HOSTS%,192.168.70.9"
if not defined GPR_PORT set "GPR_PORT=8001"
echo Database: %DJANGO_DB_PATH%
echo Applying migrations...
"%GPR_PYTHON%" manage.py migrate
if errorlevel 1 goto :failed

echo.
echo Open http://192.168.70.9:%GPR_PORT%/ or http://127.0.0.1:%GPR_PORT%/
echo Keep this window open. Press Ctrl+C to stop the server.
"%GPR_PYTHON%" manage.py runserver 0.0.0.0:%GPR_PORT% --noreload
if errorlevel 1 goto :failed
pause
exit /b 0

:failed
echo.
echo Startup stopped. Read the error above.
pause
exit /b 1
