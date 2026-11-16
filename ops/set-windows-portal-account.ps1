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
if([string]::IsNullOrWhiteSpace($User)){$User=$identity.Name}
if($User -notmatch '^[^\\]+\\[^\\]+$'){throw 'Укажите доменную учётную запись в формате DOMAIN\user.'}

$taskName='YDS Portal'
$task=Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
if(-not $task.Settings.Enabled){throw 'Задание YDS Portal отключено. Сначала выясните причину и включите его; изменения не внесены.'}
$currentPrincipal=[string]$task.Principal.UserId
$currentSid=try{(New-Object Security.Principal.NTAccount($currentPrincipal)).Translate([Security.Principal.SecurityIdentifier]).Value}catch{$currentPrincipal}
if($currentSid -ne 'S-1-5-18' -and $currentPrincipal -notin @('SYSTEM','NT AUTHORITY\SYSTEM')){
    throw "Задание уже работает не от SYSTEM, а от $currentPrincipal. Чтобы не потерять сохранённый Планировщиком пароль, измените его в свойствах задания YDS Portal."
}
$runtime=Join-Path $Root '.runtime'
$installed=Join-Path $runtime 'installed.json'
if(-not (Test-Path -LiteralPath $installed)){throw 'Фоновый портал в этой папке не установлен.'}
$action=@($task.Actions)[0]
if(-not $action -or [IO.Path]::GetFullPath([string]$action.WorkingDirectory).TrimEnd('\') -ne $Root.TrimEnd('\')){throw 'Задание YDS Portal относится к другой папке. Изменения не внесены.'}

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
$config=Get-Content -LiteralPath $installed -Raw | ConvertFrom-Json
$port=[int]$config.port
try{
    $targetSid=(New-Object Security.Principal.NTAccount($User)).Translate([Security.Principal.SecurityIdentifier]).Value
    & icacls.exe $runtime /grant "*${targetSid}:(OI)(CI)F" | Out-Null
    if($LASTEXITCODE -ne 0){throw 'Не удалось выдать новой учётной записи доступ к рабочим файлам портала.'}
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
