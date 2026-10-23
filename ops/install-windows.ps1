# Run from an elevated PowerShell. Both tasks run without an interactive logon.
[CmdletBinding()]
param([string]$Root='',[int]$Port=8001,[string[]]$Hosts=@('192.168.70.9'),[string]$PythonExecutable='')
$ErrorActionPreference='Stop'
# Automatic script-path variables are not reliable in parameter default expressions
# in Windows PowerShell. Resolve them after parameter binding instead.
if([string]::IsNullOrWhiteSpace($Root)){
    $scriptDirectory=$PSScriptRoot
    if([string]::IsNullOrWhiteSpace($scriptDirectory)){
        $scriptPath=$MyInvocation.MyCommand.Path
        if([string]::IsNullOrWhiteSpace($scriptPath)){throw 'Cannot determine the script path. Pass -Root with the application directory.'}
        $scriptDirectory=[IO.Path]::GetDirectoryName($scriptPath)
    }
    $Root=Join-Path $scriptDirectory '..'
}
$admin=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Run INSTALL_GPR_BACKGROUND.cmd as Administrator.'}
$Root=(Resolve-Path $Root).Path
if($Root.StartsWith('\\')){throw 'Use a local disk path, not a network share.'}
if($Port -lt 1024 -or $Port -gt 65535){throw 'Port must be between 1024 and 65535.'}
if(Get-ScheduledTask -TaskName 'YDS Portal' -ErrorAction SilentlyContinue){throw 'The portal task is already installed. Use Task Scheduler to start or update it.'}
function Find-PythonBase([string]$candidatePath){
    if([string]::IsNullOrWhiteSpace($candidatePath) -or -not (Test-Path -LiteralPath $candidatePath)){return $null}
    if($candidatePath -match '\\WindowsApps\\'){return $null}
    try {
        $output=& $candidatePath -c "import json,sys,venv,ensurepip; assert sys.version_info >= (3,10); print(json.dumps(getattr(sys,'_base_executable',sys.executable)))" 2>$null
        if($LASTEXITCODE -ne 0 -or -not $output){return $null}
        $basePath=($output | Select-Object -Last 1) | ConvertFrom-Json
        if($basePath -and (Test-Path -LiteralPath $basePath)){return [string]$basePath}
    } catch {return $null}
    return $null
}
$venvPath=Join-Path $Root '.venv'
$python=Join-Path $venvPath 'Scripts\python.exe'
$workingVenv=Find-PythonBase $python
if(-not $workingVenv){
    $base=$null
    if(-not [string]::IsNullOrWhiteSpace($PythonExecutable)){
        $base=Find-PythonBase $PythonExecutable
        if(-not $base){throw 'The supplied -PythonExecutable is not a working Python 3.10+ installation.'}
    }
    # Existing environments may have been copied from a different Windows profile.
    # Probe the executable; the presence of python.exe alone does not mean it works.
    foreach($relativePath in @('venv313\Scripts\python.exe','venv\Scripts\python.exe')){
        if(-not $base){$base=Find-PythonBase (Join-Path $Root $relativePath)}
    }
    $launcher=Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if(-not $base -and $launcher){
        $launcherPath=$launcher.Source
        foreach($version in @('-3.13','-3.12','-3.11','-3.10','-3.14')){
            if($base){break}
            try {
                $found=& $launcherPath $version -c "import json,sys; print(json.dumps(sys.executable))" 2>$null
                if($LASTEXITCODE -eq 0 -and $found){$base=Find-PythonBase (($found | Select-Object -Last 1) | ConvertFrom-Json)}
            } catch { }
        }
    }
    if(-not $base){
        foreach($command in @(Get-Command python.exe,python3.exe -CommandType Application -ErrorAction SilentlyContinue)){
            if(-not $base){$base=Find-PythonBase $command.Source}
        }
    }
    if(-not $base){
        foreach($registryRoot in @('HKCU:\Software\Python\PythonCore','HKLM:\Software\Python\PythonCore','HKLM:\Software\WOW6432Node\Python\PythonCore')){
            foreach($versionKey in @(Get-ChildItem $registryRoot -ErrorAction SilentlyContinue)){
                if($base){break}
                $install=Get-ItemProperty (Join-Path $versionKey.PSPath 'InstallPath') -ErrorAction SilentlyContinue
                if($install){
                    $registered=$install.ExecutablePath
                    if(-not $registered -and $install.'(default)'){$registered=Join-Path $install.'(default)' 'python.exe'}
                    $base=Find-PythonBase $registered
                }
            }
        }
    }
    if(-not $base){throw 'No working Python 3.10+ was found. Install Python 3.13 for all users, then run the installer again. The database and .env were not changed.'}
    if(Test-Path -LiteralPath $venvPath){
        $backupPath=$venvPath+'.broken-'+[DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8)
        Move-Item -LiteralPath $venvPath -Destination $backupPath
        Write-Host "Broken environment preserved: $backupPath"
    }
    Write-Host "Creating a clean environment with: $base"
    & $base -m venv $venvPath
    if($LASTEXITCODE -ne 0 -or -not (Find-PythonBase $python)){throw 'Could not create a working Python virtual environment. The old environment backup was retained.'}
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
