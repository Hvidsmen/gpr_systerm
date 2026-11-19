# Configure the existing safe updater without replacing its action, account or settings.
[CmdletBinding()]
param([string]$Root='')
$ErrorActionPreference='Stop'
if([string]::IsNullOrWhiteSpace($Root)){$Root=Join-Path $PSScriptRoot '..'}
$admin=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
    throw 'Запустите INSTALL_GPR_DAILY_UPDATE.cmd от имени администратора.'
}
$Root=(Resolve-Path -LiteralPath $Root).Path.TrimEnd('\')
if(-not (Test-Path -LiteralPath (Join-Path $Root '.runtime\installed.json'))){
    throw 'Фоновый портал не установлен в этой папке. Сначала запустите INSTALL_GPR_BACKGROUND.cmd.'
}
$taskName='YDS Portal Update'
$task=Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction Stop
$action=@($task.Actions)[0]
if(-not $action){throw 'У задания YDS Portal Update отсутствует действие обновления.'}
$workingDirectory=[IO.Path]::GetFullPath([string]$action.WorkingDirectory).TrimEnd('\')
if(-not [string]::Equals($workingDirectory,$Root,[StringComparison]::OrdinalIgnoreCase)){
    throw "Задание YDS Portal Update относится к другой папке: $workingDirectory"
}
$startup=New-ScheduledTaskTrigger -AtStartup
$startup.Delay='PT60S'
$daily=New-ScheduledTaskTrigger -Daily -At '20:00'
Set-ScheduledTask -TaskName $taskName -TaskPath '\' -Trigger @($startup,$daily) | Out-Null
Enable-ScheduledTask -TaskName $taskName -TaskPath '\' | Out-Null
$configured=Get-ScheduledTask -TaskName $taskName -TaskPath '\'
$dailyTrigger=@($configured.Triggers | Where-Object {$_.StartBoundary -match 'T20:00:00'})
if($dailyTrigger.Count -ne 1){throw 'Планировщик не подтвердил ежедневный запуск в 20:00.'}
$info=Get-ScheduledTaskInfo -TaskName $taskName -TaskPath '\'
Write-Host 'Готово: YDS Portal Update ежедневно проверяет main в 20:00 по местному времени сервера.' -ForegroundColor Green
Write-Host 'Триггер при запуске компьютера также сохранён. Обновление выполняется без входа пользователя.'
if($info.NextRunTime -and $info.NextRunTime.Year -gt 2000){Write-Host ("Следующий запуск: {0:dd.MM.yyyy HH:mm}" -f $info.NextRunTime)}
