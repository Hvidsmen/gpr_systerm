# Run from an elevated PowerShell. Both tasks run without an interactive logon.
[CmdletBinding()]
param([string]$Root=(Split-Path $PSScriptRoot -Parent),[int]$Port=8001,[string[]]$Hosts=@('192.168.70.9'))
$ErrorActionPreference='Stop'
$admin=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Run INSTALL_GPR_BACKGROUND.cmd as Administrator.'}
$Root=(Resolve-Path $Root).Path
if($Root.StartsWith('\\')){throw 'Use a local disk path, not a network share.'}
if($Port -lt 1024 -or $Port -gt 65535){throw 'Port must be between 1024 and 65535.'}
if(Get-ScheduledTask -TaskName 'YDS Portal' -ErrorAction SilentlyContinue){throw 'The portal task is already installed. Use Task Scheduler to start or update it.'}
$python=Join-Path $Root '.venv\Scripts\python.exe'
if(-not (Test-Path $python)){
    $base=(Get-Command python.exe -ErrorAction Stop).Source
    & $base -m venv (Join-Path $Root '.venv')
    if($LASTEXITCODE -ne 0){throw 'Could not create the Python virtual environment.'}
}
$runtime=Join-Path $Root '.runtime'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
# Logs, the service key, update token copies and database backups are private.
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $runtime /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' "*${sid}:(OI)(CI)F" | Out-Null
if($LASTEXITCODE -ne 0){throw 'Could not protect the runtime directory.'}
Push-Location $Root
try {
    & $python -m pip install -r (Join-Path $Root 'requirements.txt')
    if($LASTEXITCODE -ne 0){throw 'Dependencies could not be installed.'}
    $source=Join-Path $Root 'ops\windows_runtime.py'
    Copy-Item $source (Join-Path $runtime 'bootstrap.py') -Force
    $init=@((Join-Path $runtime 'bootstrap.py'),'init','--root',$Root,'--port',"$Port")
    foreach($hostName in $Hosts){$init+=@('--host',$hostName)}
    if(-not (Test-Path (Join-Path $runtime 'installed.json'))){
    & $python @init
    if($LASTEXITCODE -ne 0){throw 'Initialization failed. Stop the old START_GPR console if its port is occupied; see .runtime\installer.log.'}
    }
    $saved=Get-Content (Join-Path $runtime 'installed.json') -Raw | ConvertFrom-Json
    $Port=[int]$saved.port
    $bootstrap=Join-Path $runtime 'bootstrap.py'
    $principal=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $start=New-ScheduledTaskTrigger -AtStartup
    $start.Delay='PT15S'
    $action=New-ScheduledTaskAction -Execute $python -Argument ('"{0}" supervise --root "{1}"' -f $bootstrap,$Root) -WorkingDirectory $Root
    Register-ScheduledTask -TaskName 'YDS Portal' -Action $action -Trigger $start -Principal $principal -Settings $settings -Description 'ЯДС портал: фоновый сервер при включении компьютера, без входа пользователя.' | Out-Null
    $update=New-ScheduledTaskTrigger -AtStartup
    $update.Delay='PT60S'
    $updateSettings=New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 35) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $updateAction=New-ScheduledTaskAction -Execute $python -Argument ('"{0}" update --root "{1}"' -f $bootstrap,$Root) -WorkingDirectory $Root
    Register-ScheduledTask -TaskName 'YDS Portal Update' -Action $updateAction -Trigger $update -Principal $principal -Settings $updateSettings -Description 'Проверить main и безопасно обновить ЯДС портал при запуске компьютера.' | Out-Null
    if(-not (Get-NetFirewallRule -Name 'YDS-Portal' -ErrorAction SilentlyContinue)){
        New-NetFirewallRule -Name 'YDS-Portal' -DisplayName 'ЯДС портал' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Domain,Private | Out-Null
    }
    Start-ScheduledTask -TaskName 'YDS Portal'
    Write-Host "Installed: YDS Portal and YDS Portal Update. Open http://$($Hosts[0]):$Port/"
    Write-Host 'The portal task remains Running (0x41301). No console window is required.'
} finally {Pop-Location}
