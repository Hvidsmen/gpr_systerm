# Build a verified MSSQL trial copy while production keeps using SQLite.
[CmdletBinding()]
param([string]$Root='')
$ErrorActionPreference='Stop'

if([string]::IsNullOrWhiteSpace($Root)){$Root=Join-Path $PSScriptRoot '..'}
$Root=(Resolve-Path -LiteralPath $Root).Path
$runtime=Join-Path $Root '.runtime'
$current=Get-Content -LiteralPath (Join-Path $runtime 'current.json') -Raw | ConvertFrom-Json
$installed=Get-Content -LiteralPath (Join-Path $runtime 'installed.json') -Raw | ConvertFrom-Json
$python=[string]$current.python;$manage=Join-Path ([string]$current.code) 'manage.py'
if(-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $manage)){throw 'Активная версия портала не найдена. Сначала выполните обновление.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent().Name
if($identity -ine 'YAMALGAZPROM\dvivanov'){throw "Запустите файл от YAMALGAZPROM\dvivanov. Сейчас используется $identity."}
$destination=Join-Path $runtime ('mssql-copy\'+[DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss'))
$logDirectory=Join-Path $runtime 'mssql-copy-logs'
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$log=Join-Path $logDirectory ([DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')+'.log')
$lockPath=Join-Path $runtime 'mssql-copy.lock'
try{$lock=[IO.File]::Open($lockPath,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)}
catch{throw 'Подготовка MSSQL уже запущена в другом окне. Дождитесь её завершения или закройте прежний процесс.'}
$env:GPR_CONFIG_DIR=$Root
$env:DJANGO_DB_ENGINE='sqlite'
$env:DJANGO_DB_PATH=[string]$installed.database
$env:MSSQL_HOST='MSK-DB2'
$env:MSSQL_NAME='YDS_PORTAL'
$env:MSSQL_DRIVER='ODBC Driver 17 for SQL Server'
$env:MSSQL_TRUST_SERVER_CERTIFICATE='true'
$env:DJANGO_SECRET_KEY=(Get-Content -LiteralPath (Join-Path $runtime 'secret.key') -Raw).Trim()
$env:DJANGO_DEBUG='0';$env:DJANGO_SECURE_SSL_REDIRECT='0';$env:DJANGO_COOKIE_SECURE='0'
$utf8=New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding=$utf8;$OutputEncoding=$utf8;$env:PYTHONIOENCODING='utf-8'
Write-Host 'Создаётся проверочная копия. Рабочий портал продолжает работать с SQLite.' -ForegroundColor Cyan
Write-Host "Журнал: $log" -ForegroundColor Cyan
try{
    $ErrorActionPreference='Continue'
    & $python $manage prepare_mssql_copy --target mssql --output $destination 2>&1 | ForEach-Object {
        $line=$_.ToString();Write-Host $line
        [IO.File]::AppendAllText($log,$line+[Environment]::NewLine,$utf8)
    }
    $result=$LASTEXITCODE
    $ErrorActionPreference='Stop'
    if($result -ne 0){throw "Подготовка копии MSSQL не завершена. Рабочая SQLite-база не изменялась. Журнал: $log"}
}finally{
    $ErrorActionPreference='Stop'
    if($lock){$lock.Dispose()}
}
