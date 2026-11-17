# Check the target MSSQL using the active immutable release and its Python.
[CmdletBinding()]
param([string]$Root='')
$ErrorActionPreference='Stop'

if([string]::IsNullOrWhiteSpace($Root)){$Root=Join-Path $PSScriptRoot '..'}
$Root=(Resolve-Path -LiteralPath $Root).Path
$runtime=Join-Path $Root '.runtime'
$currentPath=Join-Path $runtime 'current.json'
$installedPath=Join-Path $runtime 'installed.json'
if(-not (Test-Path -LiteralPath $currentPath) -or -not (Test-Path -LiteralPath $installedPath)){throw 'Фоновый портал в этой папке не установлен.'}
$current=Get-Content -LiteralPath $currentPath -Raw | ConvertFrom-Json
$installed=Get-Content -LiteralPath $installedPath -Raw | ConvertFrom-Json
$python=[string]$current.python
$code=[string]$current.code
if(-not (Test-Path -LiteralPath $python)){throw 'Не найден Python активной версии портала. Выполните обновление ещё раз.'}
$manage=Join-Path $code 'manage.py'
if(-not (Test-Path -LiteralPath $manage)){throw 'Не найден активный выпуск портала. Выполните обновление ещё раз.'}

$env:GPR_CONFIG_DIR=$Root
$env:DJANGO_DB_PATH=[string]$installed.database
$env:DJANGO_SECRET_KEY=(Get-Content -LiteralPath (Join-Path $runtime 'secret.key') -Raw).Trim()
$env:DJANGO_DEBUG='0'
$env:DJANGO_SECURE_SSL_REDIRECT='0'
$env:DJANGO_COOKIE_SECURE='0'
Write-Host "Проверка выполняется от: $([Security.Principal.WindowsIdentity]::GetCurrent().Name)" -ForegroundColor Cyan
& $python $manage check_mssql --server MSK-DB2 --database YDS_PORTAL --expected-login 'YAMALGAZPROM\dvivanov'
if($LASTEXITCODE -ne 0){throw 'Проверка MSSQL не пройдена. Рабочая SQLite-база не изменялась.'}
