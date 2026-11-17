# Final cutover of the isolated TEST portal to its own MSSQL database.
[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
$taskName='YDS Portal Test'
$requiredAccount='YAMALGAZPROM\dvivanov'
$requiredSid=(New-Object Security.Principal.NTAccount($requiredAccount)).Translate([Security.Principal.SecurityIdentifier]).Value
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$principal=New-Object Security.Principal.WindowsPrincipal($identity)
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Запустите SWITCH_GPR_TEST_TO_MSSQL.cmd от имени администратора.'}
if($identity.User.Value -ne $requiredSid){throw "Запустите переключение от $requiredAccount. Сейчас используется $($identity.Name)."}

$task=Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
$root=[string]$task.Actions[0].WorkingDirectory
if([string]::IsNullOrWhiteSpace($root) -or -not (Test-Path -LiteralPath (Join-Path $root 'staging.json'))){throw 'Не найдена отдельная установка тестового портала.'}
$root=(Resolve-Path -LiteralPath $root).Path
$manifestPath=Join-Path $root 'staging.json';$state=Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if([string]$state.kind -ne 'gpr-acceptance-v1' -or [int]$state.port -ne 8002){throw 'Задание не относится к изолированному тестовому порталу на порту 8002.'}
if($state.database_engine -and [string]$state.database_engine -ine 'sqlite'){throw 'Тестовый портал уже переключён с SQLite.'}
$python=[string]$state.python;$code=[string]$state.code;$manage=Join-Path $code 'manage.py'
if(-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $manage) -or -not (Test-Path -LiteralPath (Join-Path $code 'core\management\commands\cutover_staging_mssql.py'))){throw 'Сначала обновите тестовый портал до версии с поддержкой MSSQL.'}
foreach($marker in @('staging-recovery.json','staging-refresh-transaction.json')){if(Test-Path -LiteralPath (Join-Path $root ".runtime\$marker")){throw 'У теста есть незавершённое восстановление. Переключение остановлено.'}}

$taskIdentity=[string]$task.Principal.UserId
$taskAccount=if($taskIdentity.Contains('\')){$taskIdentity}elseif($taskIdentity -in @('SYSTEM','NT AUTHORITY\SYSTEM')){'NT AUTHORITY\SYSTEM'}else{'YAMALGAZPROM\'+$taskIdentity}
$taskSid=try{(New-Object Security.Principal.NTAccount($taskAccount)).Translate([Security.Principal.SecurityIdentifier]).Value}catch{''}
$credential=$null
if($taskSid -ne $requiredSid){
    $credential=Get-Credential -UserName $requiredAccount -Message 'Введите пароль Windows для запуска тестового портала от dvivanov'
    $credentialSid=try{(New-Object Security.Principal.NTAccount($credential.UserName)).Translate([Security.Principal.SecurityIdentifier]).Value}catch{''}
    if($credentialSid -ne $requiredSid){throw "Нужна учётная запись $requiredAccount."}
}

Write-Host ''
Write-Host 'Будет изменён только ТЕСТОВЫЙ портал. Рабочая YDS_PORTAL не затрагивается.' -ForegroundColor Yellow
Write-Host 'Тестовая база будет создана как MSK-DB2 / YDS_PORTAL_TEST.' -ForegroundColor Yellow
$confirmation=Read-Host 'Введите ПЕРЕКЛЮЧИТЬ ТЕСТ для продолжения'
if($confirmation -cne 'ПЕРЕКЛЮЧИТЬ ТЕСТ'){Write-Host 'Переключение отменено.';exit 1}

$runtime=Join-Path $root '.runtime';$destination=Join-Path $runtime ('mssql-cutover\'+[DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss'))
$logDirectory=Join-Path $runtime 'mssql-cutover-logs';New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$log=Join-Path $logDirectory ([DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')+'.log')
$utf8=New-Object Text.UTF8Encoding($false);[Console]::OutputEncoding=$utf8;$OutputEncoding=$utf8
$env:PYTHONIOENCODING='utf-8';$env:PYTHONUNBUFFERED='1';$env:DJANGO_SETTINGS_MODULE='config.staging_settings';$env:GPR_CONFIG_DIR=$root
$env:MSSQL_HOST='MSK-DB2';$env:MSSQL_NAME='YDS_PORTAL_TEST';$env:MSSQL_DRIVER='ODBC Driver 17 for SQL Server';$env:MSSQL_TRUST_SERVER_CERTIFICATE='true'

function Stop-TestGracefully{
    if((Get-ScheduledTask -TaskName $taskName).State -notin @('Running','Queued')){return}
    New-Item -ItemType File -Force (Join-Path $runtime 'server.stop') | Out-Null
    for($attempt=0;$attempt -lt 180;$attempt++){
        if((Get-ScheduledTask -TaskName $taskName).State -notin @('Running','Queued')){return}
        Start-Sleep -Seconds 1
    }
    throw 'Тестовый портал не остановился за 180 секунд. Перенос не начинался.'
}
function Start-TestAndWait{
    Start-ScheduledTask -TaskName $taskName
    for($attempt=0;$attempt -lt 180;$attempt++){
        try{
            $response=Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$([int]$state.port)/accounts/login/" -TimeoutSec 3
            if($response.StatusCode -eq 200 -and $response.Headers['X-Portal-Environment'] -eq 'staging'){return $true}
        }catch{}
        if((Get-ScheduledTask -TaskName $taskName).State -notin @('Running','Queued')){return $false}
        Start-Sleep -Seconds 1
    }
    return $false
}
function Test-CutoverActivated{
    $journalPath=Join-Path $destination 'cutover.json';$verificationPath=Join-Path $destination 'verification.json'
    if(-not (Test-Path -LiteralPath $journalPath) -or -not (Test-Path -LiteralPath $verificationPath)){return $false}
    try{
        $active=Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        $journal=Get-Content -LiteralPath $journalPath -Raw | ConvertFrom-Json
        $verification=Get-Content -LiteralPath $verificationPath -Raw | ConvertFrom-Json
        return ([string]$active.database_engine -ieq 'mssql') -and ([string]$active.mssql.name -ieq 'YDS_PORTAL_TEST') -and
            ([string]$journal.target_config.mssql.name -ieq 'YDS_PORTAL_TEST') -and (@($verification.errors).Count -eq 0)
    }catch{return $false}
}

try{
    Write-Host 'Останавливаем только тестовый портал…' -ForegroundColor Cyan;Stop-TestGracefully
    Write-Host 'Создаём и сверяем MSK-DB2 / YDS_PORTAL_TEST…' -ForegroundColor Cyan
    Write-Host "Журнал: $log" -ForegroundColor Cyan
    $stdoutLog=$log+'.stdout';$stderrLog=$log+'.stderr'
    $arguments=@($manage,'cutover_staging_mssql','prepare','--output',$destination,'--server','MSK-DB2','--database','YDS_PORTAL_TEST')
    $process=Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $code -NoNewWindow -PassThru -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
    $elapsed=0
    while(-not $process.HasExited){Start-Sleep -Seconds 1;$elapsed++;$process.Refresh();if(($elapsed%10)-eq 0){Write-Host "Перенос теста выполняется: $elapsed сек." -ForegroundColor Cyan}}
    $process.WaitForExit();Start-Sleep -Milliseconds 200
    $stdout=if(Test-Path -LiteralPath $stdoutLog){[IO.File]::ReadAllText($stdoutLog,$utf8)}else{''};$stderr=if(Test-Path -LiteralPath $stderrLog){[IO.File]::ReadAllText($stderrLog,$utf8)}else{''}
    [IO.File]::WriteAllText($log,$stdout+$(if($stderr){[Environment]::NewLine+'--- STDERR ---'+[Environment]::NewLine+$stderr}else{''}),$utf8)
    Remove-Item -LiteralPath $stdoutLog,$stderrLog -Force -ErrorAction SilentlyContinue
    if($stdout){Write-Host $stdout};if($stderr){Write-Host $stderr -ForegroundColor Red}
    if(-not (Test-CutoverActivated)){throw "Копирование или сверка тестовой базы не завершены. Журнал: $log"}
    if($credential){
        Set-ScheduledTask -TaskName $taskName -User $requiredAccount -Password $credential.GetNetworkCredential().Password | Out-Null
        $credential=$null
    }
    Write-Host 'Запускаем тестовый портал на MSSQL…' -ForegroundColor Cyan
    if(-not (Start-TestAndWait)){throw 'Тестовый портал не прошёл проверку запуска на MSSQL.'}
    Write-Host ''
    Write-Host 'ГОТОВО. Тестовый портал работает на MSK-DB2 / YDS_PORTAL_TEST.' -ForegroundColor Green
    Write-Host "Снимок SQLite и отчёт сохранены: $destination"
    exit 0
}catch{
    $failure=$_.Exception.Message;Write-Host "Переключение теста не завершено: $failure" -ForegroundColor Red
    try{
        $active=Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        if([string]$active.database_engine -ieq 'mssql'){
            & $python $manage cutover_staging_mssql rollback --output $destination
            if($LASTEXITCODE -ne 0){throw 'Не удалось вернуть staging.json на SQLite.'}
        }
        if(-not (Start-TestAndWait)){throw 'SQLite возвращена, но тестовый портал не запустился. Проверьте .runtime\server.log.'}
        Write-Host 'Тестовый портал снова работает на SQLite; рабочий портал не затрагивался.' -ForegroundColor Yellow
    }catch{throw "$failure Автоматический возврат теста не завершён: $($_.Exception.Message)"}
    throw $failure
}
