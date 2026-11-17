# Final cutover from the live SQLite database to a freshly rebuilt and verified MSSQL copy.
[CmdletBinding()]
param([string]$Root='')
$ErrorActionPreference='Stop'

if([string]::IsNullOrWhiteSpace($Root)){$Root=Join-Path $PSScriptRoot '..'}
$Root=(Resolve-Path -LiteralPath $Root).Path
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$admin=New-Object Security.Principal.WindowsPrincipal($identity)
if(-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Запустите SWITCH_GPR_TO_MSSQL.cmd от имени администратора.'}
$requiredAccount='YAMALGAZPROM\dvivanov'
$requiredSid=(New-Object Security.Principal.NTAccount($requiredAccount)).Translate([Security.Principal.SecurityIdentifier]).Value
if($identity.User.Value -ne $requiredSid){throw "Запустите переключение от $requiredAccount. Сейчас используется $($identity.Name)."}

$runtime=Join-Path $Root '.runtime'
$installedPath=Join-Path $runtime 'installed.json'
$currentPath=Join-Path $runtime 'current.json'
if(-not (Test-Path -LiteralPath $installedPath) -or -not (Test-Path -LiteralPath $currentPath)){throw 'Фоновый портал не установлен.'}
$installed=Get-Content -LiteralPath $installedPath -Raw | ConvertFrom-Json
if($installed.database_engine -and [string]$installed.database_engine -ine 'sqlite'){throw 'Портал уже переключён с SQLite. Повторный запуск остановлен.'}
$current=Get-Content -LiteralPath $currentPath -Raw | ConvertFrom-Json
$python=[string]$current.python;$manage=Join-Path ([string]$current.code) 'manage.py'
if(-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $manage)){throw 'Активная версия портала не найдена. Сначала обновите приложение.'}

$server=Get-ScheduledTask -TaskName 'YDS Portal' -ErrorAction Stop
$taskIdentity=[string]$server.Principal.UserId
$taskAccount=if($taskIdentity.Contains('\')){$taskIdentity}else{'YAMALGAZPROM\'+$taskIdentity}
$taskSid=try{(New-Object Security.Principal.NTAccount($taskAccount)).Translate([Security.Principal.SecurityIdentifier]).Value}catch{throw "Не удалось определить Windows-учётную запись задания YDS Portal: $taskIdentity."}
if($taskSid -ne $requiredSid){throw "Задание YDS Portal работает от $taskIdentity, а для MSSQL требуется $requiredAccount."}
$updater=Get-ScheduledTask -TaskName 'YDS Portal Update' -ErrorAction Stop
if($updater.State -in @('Running','Queued')){throw 'Сейчас выполняется обновление портала. Дождитесь его окончания.'}
$statusPath=Join-Path $runtime 'status.json'
if(Test-Path -LiteralPath $statusPath){
    $status=Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
    if($status.state -eq 'updating'){throw 'Портал отмечен как обновляющийся. Сначала завершите обновление.'}
}

Write-Host ''
Write-Host 'ВНИМАНИЕ: портал будет остановлен, YDS_PORTAL будет пересоздана из самого свежего снимка SQLite.' -ForegroundColor Yellow
Write-Host 'При ошибке рабочая настройка вернётся на SQLite.' -ForegroundColor Yellow
$confirmation=Read-Host 'Введите ПЕРЕКЛЮЧИТЬ для продолжения'
if($confirmation -cne 'ПЕРЕКЛЮЧИТЬ'){Write-Host 'Переключение отменено. Изменений нет.';exit 1}

$destination=Join-Path $runtime ('mssql-cutover\'+[DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss'))
$logDirectory=Join-Path $runtime 'mssql-cutover-logs'
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$log=Join-Path $logDirectory ([DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')+'.log')
$utf8=New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding=$utf8;$OutputEncoding=$utf8;$env:PYTHONIOENCODING='utf-8';$env:PYTHONUNBUFFERED='1'
$env:GPR_CONFIG_DIR=$Root;$env:DJANGO_DB_ENGINE='sqlite';$env:DJANGO_DB_PATH=[string]$installed.database
$env:MSSQL_HOST='MSK-DB2';$env:MSSQL_NAME='YDS_PORTAL';$env:MSSQL_DRIVER='ODBC Driver 17 for SQL Server';$env:MSSQL_TRUST_SERVER_CERTIFICATE='true'
$env:DJANGO_MEDIA_ROOT=[string]$installed.media;$env:DJANGO_SECRET_KEY=(Get-Content -LiteralPath (Join-Path $runtime 'secret.key') -Raw).Trim()
$env:DJANGO_DEBUG='0';$env:DJANGO_SECURE_SSL_REDIRECT='0';$env:DJANGO_COOKIE_SECURE='0'

function Test-PortalReady([int]$port){
    $response=$null
    try{
        $request=[Net.HttpWebRequest]::Create("http://127.0.0.1:$port/accounts/login/")
        $request.Proxy=$null;$request.Timeout=3000;$request.ReadWriteTimeout=3000;$request.AllowAutoRedirect=$true
        $response=$request.GetResponse();return [int]$response.StatusCode -eq 200
    }catch{return $false}finally{if($response){$response.Close()}}
}
function Stop-PortalGracefully{
    $task=Get-ScheduledTask -TaskName 'YDS Portal'
    if($task.State -notin @('Running','Queued')){return}
    New-Item -ItemType File -Force (Join-Path $runtime 'supervisor.stop') | Out-Null
    for($attempt=0;$attempt -lt 150;$attempt++){
        if((Get-ScheduledTask -TaskName 'YDS Portal').State -notin @('Running','Queued')){return}
        Start-Sleep -Seconds 1
    }
    throw 'Портал не завершил текущие запросы за 150 секунд. Переключение не начиналось.'
}
function Start-PortalAndWait{
    Start-ScheduledTask -TaskName 'YDS Portal'
    $observedRunning=$false
    for($attempt=0;$attempt -lt 120;$attempt++){
        if(Test-PortalReady ([int]$installed.port)){return $true}
        $state=(Get-ScheduledTask -TaskName 'YDS Portal').State
        if($state -in @('Running','Queued')){$observedRunning=$true}
        elseif($observedRunning -or $attempt -ge 30){return $false}
        Start-Sleep -Seconds 1
    }
    return $false
}

$activated=$false
try{
    Write-Host 'Останавливаем портал и ждём завершения текущих запросов…' -ForegroundColor Cyan
    Stop-PortalGracefully
    Write-Host 'Создаём свежую MSSQL-копию и сверяем все модели…' -ForegroundColor Cyan
    Write-Host "Журнал: $log" -ForegroundColor Cyan
    $stdoutLog=$log+'.stdout';$stderrLog=$log+'.stderr'
    # Driver 17 is the command default. Do not pass its spaced name through
    # Windows PowerShell 5 Start-Process, which would split it into arguments.
    $arguments=@($manage,'cutover_mssql','prepare','--output',$destination,'--server','MSK-DB2','--database','YDS_PORTAL')
    $process=Start-Process -FilePath $python -ArgumentList $arguments -NoNewWindow -PassThru -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
    $elapsed=0
    while(-not $process.HasExited){
        Start-Sleep -Seconds 1;$elapsed++;$process.Refresh()
        if(($elapsed % 10) -eq 0){Write-Host "Перенос выполняется: $elapsed сек. Не закрывайте окно." -ForegroundColor Cyan}
    }
    $process.WaitForExit();$result=$process.ExitCode;Start-Sleep -Milliseconds 200
    $stdout=if(Test-Path -LiteralPath $stdoutLog){[IO.File]::ReadAllText($stdoutLog,$utf8)}else{''}
    $stderr=if(Test-Path -LiteralPath $stderrLog){[IO.File]::ReadAllText($stderrLog,$utf8)}else{''}
    [IO.File]::WriteAllText($log,$stdout+$(if($stderr){[Environment]::NewLine+'--- STDERR ---'+[Environment]::NewLine+$stderr}else{''}),$utf8)
    Remove-Item -LiteralPath $stdoutLog,$stderrLog -Force -ErrorAction SilentlyContinue
    if($stdout){Write-Host $stdout};if($stderr){Write-Host $stderr -ForegroundColor Red}
    $after=Get-Content -LiteralPath $installedPath -Raw | ConvertFrom-Json
    $activated=$after.database_engine -and [string]$after.database_engine -ieq 'mssql'
    if($result -ne 0 -or -not $activated){throw "Свежая копия или сверка не завершена. Журнал: $log"}
    Write-Host 'Запускаем портал на MSSQL…' -ForegroundColor Cyan
    if(-not (Start-PortalAndWait)){throw 'Портал не прошёл проверку запуска на MSSQL.'}
    Write-Host ''
    Write-Host 'ГОТОВО. Портал работает на MSK-DB2 / YDS_PORTAL.' -ForegroundColor Green
    Write-Host "Свежий снимок SQLite и отчёт сохранены: $destination"
    exit 0
}catch{
    $failure=$_.Exception.Message
    Write-Host "Переключение не завершено: $failure" -ForegroundColor Red
    try{
        $after=Get-Content -LiteralPath $installedPath -Raw | ConvertFrom-Json
        $activated=$after.database_engine -and [string]$after.database_engine -ieq 'mssql'
        if($activated){
            Stop-ScheduledTask -TaskName 'YDS Portal' -ErrorAction SilentlyContinue
            & $python $manage cutover_mssql rollback --output $destination
            if($LASTEXITCODE -ne 0){throw 'Не удалось восстановить настройку SQLite по журналу переключения.'}
        }
        if(-not (Start-PortalAndWait)){throw 'SQLite восстановлена в настройках, но портал не запустился. Проверьте .runtime\worker.log.'}
        Write-Host 'Портал снова работает на SQLite; данные не потеряны.' -ForegroundColor Yellow
    }catch{throw "$failure Автоматический возврат не завершён: $($_.Exception.Message)"}
    throw $failure
}
