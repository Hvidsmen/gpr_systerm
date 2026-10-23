[CmdletBinding()]
param([Parameter(Mandatory=$true)][ValidateSet('keep','clear')][string]$ObjectAccess,[string]$Root='',[string]$DatabasePath='')
$ErrorActionPreference='Stop'
if([string]::IsNullOrWhiteSpace($Root)){$Root=Join-Path $PSScriptRoot '..'}
$Root=(Resolve-Path -LiteralPath $Root).Path
$admin=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Run as Administrator.'}
if([string]::IsNullOrWhiteSpace($DatabasePath)){$DatabasePath=Join-Path $Root ('data\users-only-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.sqlite3')}
if(Test-Path -LiteralPath $DatabasePath){throw 'The destination database already exists. Choose a new filename.'}
$server=Get-ScheduledTask -TaskName 'YDS Portal'
$updater=Get-ScheduledTask -TaskName 'YDS Portal Update'
if($updater.State -eq 'Running'){throw 'Wait for the update task to finish.'}
$serverEnabled=$server.Settings.Enabled;$updaterEnabled=$updater.Settings.Enabled
$wasRunning=$server.State -eq 'Running'
Disable-ScheduledTask -TaskName 'YDS Portal Update' | Out-Null
Disable-ScheduledTask -TaskName 'YDS Portal' | Out-Null
$success=$false
try {
    Write-Host 'Создаём новую базу. Исходный файл сохраняется. Портал будет ненадолго остановлен.' -ForegroundColor Cyan
    & (Join-Path $Root '.venv\Scripts\python.exe') (Join-Path $Root 'ops\windows_runtime.py') new-database --root $Root --database $DatabasePath --object-access $ObjectAccess
    if($LASTEXITCODE -ne 0){throw 'New database operation failed. See .runtime\control.log and .runtime\commands.log.'}
    $success=$true
} finally {
    if($updaterEnabled){Enable-ScheduledTask -TaskName 'YDS Portal Update' | Out-Null}
    if($serverEnabled){Enable-ScheduledTask -TaskName 'YDS Portal' | Out-Null}
    if($serverEnabled -or $wasRunning){
        if(-not $serverEnabled){Enable-ScheduledTask -TaskName 'YDS Portal' | Out-Null}
        for($attempt=0;$attempt -lt 150;$attempt++){
            if((Get-ScheduledTask -TaskName 'YDS Portal').State -ne 'Running'){break}
            if(-not (Test-Path (Join-Path $Root '.runtime\supervisor.stop'))){break}
            Start-Sleep -Seconds 1
        }
        Start-ScheduledTask -TaskName 'YDS Portal'
        if(-not $serverEnabled){Disable-ScheduledTask -TaskName 'YDS Portal' | Out-Null}
    }
}
if($success){
    $config=Get-Content (Join-Path $Root '.runtime\installed.json') -Raw | ConvertFrom-Json
    $ready=$false
    for($attempt=0;$attempt -lt 60;$attempt++){
        try {
            $request=[Net.HttpWebRequest]::Create("http://127.0.0.1:$($config.port)/accounts/login/")
            $request.Proxy=$null;$request.Timeout=2000
            $response=$request.GetResponse();$ready=[int]$response.StatusCode -eq 200;$response.Close()
        } catch {}
        if($ready){break};Start-Sleep -Seconds 2
    }
    if(-not $ready){throw 'Database created, but the portal is not responding yet. Check .runtime\worker.log.'}
    Write-Host 'ГОТОВО. Новая база подключена, портал доступен. Войдите заново.' -ForegroundColor Green
    Write-Host $config.database
}
