[CmdletBinding()]
param([string]$Root='',[int]$TimeoutMinutes=35)
$ErrorActionPreference='Stop'
if([string]::IsNullOrWhiteSpace($Root)){$Root=Join-Path $PSScriptRoot '..'}
$Root=(Resolve-Path -LiteralPath $Root).Path
$runtime=Join-Path $Root '.runtime'
function Read-RuntimeJson([string]$name){
    try{return Get-Content (Join-Path $runtime $name) -Raw -ErrorAction Stop | ConvertFrom-Json}catch{return $null}
}
function Test-PortalReady([int]$port){
    $response=$null
    try {
        $request=[Net.HttpWebRequest]::Create("http://127.0.0.1:$port/accounts/login/")
        $request.Proxy=$null
        $request.Timeout=3000
        $request.ReadWriteTimeout=3000
        $request.AllowAutoRedirect=$true
        $response=$request.GetResponse()
        return [int]$response.StatusCode -eq 200
    } catch{return $false} finally{if($response){$response.Close()}}
}
try {
    $config=Read-RuntimeJson 'installed.json'
    if(-not $config){throw 'Фоновый режим не установлен. Сначала запустите INSTALL_GPR_BACKGROUND.cmd.'}
    $task=Get-ScheduledTask -TaskName 'YDS Portal Update'
    if(-not $task.Settings.Enabled){throw 'Задание YDS Portal Update отключено в Планировщике.'}
    $initial=Get-ScheduledTaskInfo -TaskName 'YDS Portal Update'
    $observed=$task.State -eq 'Running'
    $server=Get-ScheduledTask -TaskName 'YDS Portal'
    if($server.State -ne 'Running'){Start-ScheduledTask -TaskName 'YDS Portal'}
    if(-not $observed){Start-ScheduledTask -TaskName 'YDS Portal Update'}
    Write-Host 'Обновление запущено. Это окно дождётся полного окончания.' -ForegroundColor Cyan
    Write-Host 'Портал продолжает работать во время скачивания. При применении будет короткий перерыв.'
    $stages=@{
        checking='Проверка новой версии'; downloading='Скачивание новой версии'; dependencies='Установка зависимостей';
        validation='Проверка кода и стилей'; stopping='Завершение текущих запросов'; backup='Резервная копия базы';
        migrating='Обновление структуры базы'; candidate='Проверка новой версии'; starting='Запуск портала';
        verifying='Проверка доступности портала'
    }
    $timer=[Diagnostics.Stopwatch]::StartNew();$lastMessage='';$lastPrinted=-20
    while($timer.Elapsed.TotalMinutes -lt $TimeoutMinutes){
        $task=Get-ScheduledTask -TaskName 'YDS Portal Update'
        $info=Get-ScheduledTaskInfo -TaskName 'YDS Portal Update'
        $status=Read-RuntimeJson 'status.json'
        if($task.State -eq 'Running' -or $info.LastRunTime -gt $initial.LastRunTime){$observed=$true}
        $message='Идёт обновление'
        if($status -and $status.stage -and $stages.ContainsKey([string]$status.stage)){$message=$stages[[string]$status.stage]}
        if($message -ne $lastMessage -or $timer.Elapsed.TotalSeconds-$lastPrinted -ge 15){
            Write-Host ('[{0:hh\:mm\:ss}] {1}…' -f $timer.Elapsed,$message)
            $lastMessage=$message;$lastPrinted=$timer.Elapsed.TotalSeconds
        }
        if($observed -and $task.State -notin @('Running','Queued') -and $info.LastTaskResult -ne 267009){
            if($info.LastTaskResult -ne 0){throw ('Задание завершилось с ошибкой 0x{0:X}. Подробности: .runtime\service.log и .runtime\commands.log.' -f $info.LastTaskResult)}
            if($status -and ($status.state -eq 'recovery_required' -or $status.ok -eq $false)){throw 'Обновление не применено. Проверьте .runtime\service.log; текущая версия указана в .runtime\current.json.'}
            Write-Host 'Задание закончено. Проверяем, что страница входа уже открывается…' -ForegroundColor Cyan
            $ready=$false
            for($attempt=0;$attempt -lt 30;$attempt++){
                if(Test-PortalReady ([int]$config.port)){$ready=$true;break}
                Start-Sleep -Seconds 2
            }
            if(-not $ready){throw 'Задание закончено, но портал пока недоступен. Обновление не отмечено как успешно завершённое; проверьте .runtime\worker.log.'}
            $current=Read-RuntimeJson 'current.json'
            $version=if($current -and $current.sha){([string]$current.sha).Substring(0,[Math]::Min(12,([string]$current.sha).Length))}else{'не указана'}
            Write-Host ''
            Write-Host 'ГОТОВО. Обновление полностью завершено, портал доступен.' -ForegroundColor Green
            Write-Host ("Версия: $version. Время: {0:hh\:mm\:ss}." -f $timer.Elapsed)
            Write-Host 'Теперь обновите страницу приложения через Ctrl+F5.' -ForegroundColor Green
            exit 0
        }
        if(-not $observed -and $timer.Elapsed.TotalSeconds -gt 60){throw 'Планировщик не запустил задание обновления. Проверьте YDS Portal Update.'}
        Start-Sleep -Seconds 2
    }
    throw 'Время ожидания истекло. Не запускайте повторное обновление, пока не проверите состояние задания и .runtime\status.json.'
} catch {
    Write-Host ''
    Write-Host ('ОБНОВЛЕНИЕ НЕ ЗАВЕРШЕНО: '+$_.Exception.Message) -ForegroundColor Red
    exit 1
}
