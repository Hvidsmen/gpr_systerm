# Change only the production portal task identity. The password is passed directly
# to Windows Task Scheduler and is never written to the project or its logs.
[CmdletBinding()]
param([string]$Root='',[string]$User='')
$ErrorActionPreference='Stop'

if([string]::IsNullOrWhiteSpace($Root)){$Root=Join-Path $PSScriptRoot '..'}
$Root=(Resolve-Path -LiteralPath $Root).Path
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$admin=New-Object Security.Principal.WindowsPrincipal($identity)
if(-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Запустите SET_GPR_PORTAL_ACCOUNT.cmd от имени администратора.'}
$userWasSpecified=-not [string]::IsNullOrWhiteSpace($User)
if([string]::IsNullOrWhiteSpace($User)){$User=$identity.Name}
if($User -notmatch '^[^\\]+\\[^\\]+$'){throw 'Укажите доменную учётную запись в формате DOMAIN\user.'}

$taskName='YDS Portal'
$task=Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
if(-not $task.Settings.Enabled){throw 'Задание YDS Portal отключено. Сначала выясните причину и включите его; изменения не внесены.'}
$currentPrincipal=[string]$task.Principal.UserId
$currentSid=try{(New-Object Security.Principal.NTAccount($currentPrincipal)).Translate([Security.Principal.SecurityIdentifier]).Value}catch{$currentPrincipal}
if(-not $userWasSpecified -and $currentSid -ne 'S-1-5-18' -and $currentPrincipal -notin @('SYSTEM','NT AUTHORITY\SYSTEM')){$User=$currentPrincipal}
$runtime=Join-Path $Root '.runtime'
$installed=Join-Path $runtime 'installed.json'
if(-not (Test-Path -LiteralPath $installed)){throw 'Фоновый портал в этой папке не установлен.'}
$config=Get-Content -LiteralPath $installed -Raw | ConvertFrom-Json
$action=@($task.Actions)[0]
if(-not $action -or [IO.Path]::GetFullPath([string]$action.WorkingDirectory).TrimEnd('\') -ne $Root.TrimEnd('\')){throw 'Задание YDS Portal относится к другой папке. Изменения не внесены.'}
$targetSid=try{(New-Object Security.Principal.NTAccount($User)).Translate([Security.Principal.SecurityIdentifier]).Value}catch{throw "Не удалось найти доменную учётную запись $User."}

function Resolve-PortalPath([string]$Value){
    if([IO.Path]::IsPathRooted($Value)){return [IO.Path]::GetFullPath($Value)}
    return [IO.Path]::GetFullPath((Join-Path $Root $Value))
}
function Grant-PortalFileAccess([string]$Sid){
    $database=Resolve-PortalPath ([string]$config.database)
    $databaseDirectory=Split-Path -Parent $database
    $media=Resolve-PortalPath ([string]$config.media)
    & icacls.exe $runtime /grant "*${Sid}:(OI)(CI)F" | Out-Null
    if($LASTEXITCODE -ne 0){throw 'Не удалось выдать учётной записи доступ к рабочим файлам портала.'}
    & icacls.exe $databaseDirectory /grant "*${Sid}:(OI)(CI)M" | Out-Null
    if($LASTEXITCODE -ne 0){throw 'Не удалось выдать учётной записи доступ к базе портала.'}
    if(Test-Path -LiteralPath $media){
        & icacls.exe $media /grant "*${Sid}:(OI)(CI)M" | Out-Null
        if($LASTEXITCODE -ne 0){throw 'Не удалось выдать учётной записи доступ к файлам вложений.'}
    }
    $environmentFile=Join-Path $Root '.env'
    if(Test-Path -LiteralPath $environmentFile){
        & icacls.exe $environmentFile /grant "*${Sid}:R" | Out-Null
        if($LASTEXITCODE -ne 0){throw 'Не удалось выдать учётной записи доступ к настройкам портала.'}
    }
}

$isSystem=$currentSid -eq 'S-1-5-18' -or $currentPrincipal -in @('SYSTEM','NT AUTHORITY\SYSTEM')
if(-not $isSystem){
    if($currentSid -ne $targetSid){
        throw "Задание уже работает не от SYSTEM, а от $currentPrincipal. Чтобы не потерять сохранённый Планировщиком пароль, изменения не внесены."
    }
    Grant-PortalFileAccess $targetSid
    if($task.State -notin @('Running','Queued')){Start-ScheduledTask -TaskName $taskName}
    Write-Host ''
    Write-Host "ГОТОВО. Права $currentPrincipal на базу, вложения и рабочие файлы портала восстановлены." -ForegroundColor Green
    Write-Host 'Пароль повторно вводить не требуется. Откройте портал и повторите сохранение.'
    exit 0
}

Write-Host "Сейчас YDS Portal работает от: $($task.Principal.UserId)" -ForegroundColor Cyan
Write-Host "Новая учётная запись: $User"
Write-Host 'Пароль не сохраняется в файлах портала и не выводится в журнал.'
$credential=Get-Credential -UserName $User -Message 'Введите пароль доменной учётной записи для фонового портала'
if(-not $credential){throw 'Ввод учётных данных отменён.'}
$User=$credential.UserName
$password=$credential.GetNetworkCredential().Password
if([string]::IsNullOrEmpty($password)){throw 'Пустой пароль не допускается.'}

$backup=Export-ScheduledTask -TaskName $taskName
$wasRunning=$task.State -in @('Running','Queued')
$port=[int]$config.port
try{
    Grant-PortalFileAccess $targetSid
    if($wasRunning){
        New-Item -ItemType File -Force (Join-Path $runtime 'supervisor.stop') | Out-Null
        for($attempt=0;$attempt -lt 130;$attempt++){
            if((Get-ScheduledTask -TaskName $taskName).State -notin @('Running','Queued')){break}
            Start-Sleep -Seconds 1
        }
        if((Get-ScheduledTask -TaskName $taskName).State -in @('Running','Queued')){
            Stop-ScheduledTask -TaskName $taskName
            Remove-Item -LiteralPath (Join-Path $runtime 'supervisor.stop') -Force -ErrorAction SilentlyContinue
            throw 'Портал не завершил текущие запросы за 130 секунд. Изменения не внесены.'
        }
    }
    Register-ScheduledTask -TaskName $taskName -Xml $backup -User $User -Password $password -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    $ready=$false
    for($attempt=0;$attempt -lt 90;$attempt++){
        $state=(Get-ScheduledTask -TaskName $taskName).State
        if($state -notin @('Running','Queued')){break}
        try{
            $response=Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/accounts/login/" -TimeoutSec 2
            if([int]$response.StatusCode -eq 200){$ready=$true;break}
        }catch{}
        Start-Sleep -Seconds 1
    }
    if(-not $ready){throw 'Портал не запустился под новой учётной записью. Проверьте пароль и право «Вход в качестве пакетного задания».'}
    $actual=(Get-ScheduledTask -TaskName $taskName).Principal.UserId
    Write-Host ''
    Write-Host "ГОТОВО. YDS Portal запущен от $actual." -ForegroundColor Green
    Write-Host 'Задание YDS Portal Update осталось под SYSTEM. Теперь повторите загрузку сотрудников или должностей.'
}catch{
    $failure=$_.Exception.Message
    Write-Host 'Не удалось запустить портал под новой учётной записью. Восстанавливаем прежнее задание…' -ForegroundColor Yellow
    try{
        $changedTask=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if($changedTask -and $changedTask.State -in @('Running','Queued')){
            Stop-ScheduledTask -TaskName $taskName
            for($attempt=0;$attempt -lt 30;$attempt++){
                if((Get-ScheduledTask -TaskName $taskName).State -notin @('Running','Queued')){break}
                Start-Sleep -Seconds 1
            }
        }
        Register-ScheduledTask -TaskName $taskName -Xml $backup -Force | Out-Null
        if($wasRunning){Start-ScheduledTask -TaskName $taskName}
    }catch{throw "Смена учётной записи завершилась ошибкой: $failure. Не удалось восстановить прежнее задание: $($_.Exception.Message)"}
    throw "Смена учётной записи не применена: $failure. Прежнее задание восстановлено."
}finally{
    $password=$null;$credential=$null
}
