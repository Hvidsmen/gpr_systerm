# Separate acceptance service. Never registers, updates or stops production tasks.
[CmdletBinding()]
param([string]$Root='', [string]$ProductionRoot='', [string]$OwnerLogin='', [string]$Revision='',
      [int]$Port=8002, [string[]]$Hosts=@('192.168.70.9'), [string]$PythonExecutable='', [string]$ActuationRoot='')
$ErrorActionPreference='Stop'
if(-not $Root){$Root=Join-Path $PSScriptRoot '..'}
$Root=(Resolve-Path -LiteralPath $Root).Path
if($Root.StartsWith('\\')){throw 'Use a local disk directory for TEST, not a network share.'}
if(-not $ProductionRoot){$ProductionRoot=Read-Host 'Full directory of the production portal'}
$ProductionRoot=(Resolve-Path -LiteralPath $ProductionRoot).Path
if(-not $OwnerLogin){$OwnerLogin=Read-Host 'Your existing portal login (no password)'}
if(-not $Revision){
    if((Split-Path $Root -Leaf) -match '-([a-f0-9]{40})$'){$Revision=$Matches[1]}
    else{$Revision=Read-Host 'Exact 40-character commit SHA of this downloaded TEST release'}
}
if($Revision -notmatch '^[a-f0-9]{40}$'){throw 'An exact commit SHA is required. Branch main is not accepted.'}
if($Port -lt 1024 -or $Port -gt 65535){throw 'Invalid test port.'}
$principal=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Run INSTALL_GPR_TEST.cmd as Administrator.'}
$rootPrefix=$Root.TrimEnd('\')+'\';$prodPrefix=$ProductionRoot.TrimEnd('\')+'\'
if($rootPrefix.StartsWith($prodPrefix,[StringComparison]::OrdinalIgnoreCase) -or $prodPrefix.StartsWith($rootPrefix,[StringComparison]::OrdinalIgnoreCase)){throw 'Production and test directories must be separate.'}
if((Test-Path (Join-Path $Root 'staging.json')) -or (Test-Path (Join-Path $Root 'data')) -or (Test-Path (Join-Path $Root '.env'))){throw 'Test is already configured or contains data/.env. Nothing will be overwritten.'}
$taskName='YDS Portal Test'
if(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue){throw 'The TEST task already exists. Production tasks were not changed.'}
$sourceConfig=Get-Content (Join-Path $ProductionRoot '.runtime\installed.json') -Raw | ConvertFrom-Json
if($Port -eq [int]$sourceConfig.port){throw 'TEST cannot use the production port.'}
if(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue){throw 'Test port is already occupied.'}
if(-not $PythonExecutable){
    $current=Get-Content (Join-Path $ProductionRoot '.runtime\current.json') -Raw | ConvertFrom-Json
    $PythonExecutable=[string]$current.python
}
if(-not (Test-Path -LiteralPath $PythonExecutable)){throw 'Supply a working Python 3.10+ executable with -PythonExecutable.'}
$baseOutput=& $PythonExecutable -c "import sys; assert sys.version_info >= (3,10); print(getattr(sys,'_base_executable',sys.executable))"
if($LASTEXITCODE -ne 0){throw 'Python validation failed.'}
$basePython=($baseOutput | Select-Object -Last 1).Trim()
$venv=Join-Path $Root '.venv'
if(-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))){
    & $basePython -m venv $venv
    if($LASTEXITCODE -ne 0){throw 'Could not create a separate test environment.'}
}
$python=Join-Path $venv 'Scripts\python.exe'
# The copied real data and test password hash are private to this installation.
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $Root /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' "*${sid}:(OI)(CI)F" | Out-Null
if($LASTEXITCODE -ne 0){throw 'Could not protect the TEST folder.'}
Push-Location $Root
try {
    & $python -m pip install -r (Join-Path $Root 'requirements.staging.lock')
    if($LASTEXITCODE -ne 0){throw 'Could not install separate test dependencies.'}
    $arguments=@((Join-Path $Root 'ops\staging.py'),'prepare','--root',$Root,'--source-root',$ProductionRoot,'--owner-login',$OwnerLogin,'--port',"$Port",'--revision',$Revision)
    foreach($serverName in $Hosts){$arguments+=@('--host',$serverName)}
    if($ActuationRoot){$arguments+=@('--actuation-root',$ActuationRoot)}
    & $python @arguments
    if($LASTEXITCODE -ne 0){throw 'Test preparation failed. Production was not changed. See .runtime\prepare.log.'}
    $action=New-ScheduledTaskAction -Execute $python -Argument ('"{0}" serve --root "{1}"' -f (Join-Path $Root 'ops\staging.py'),$Root) -WorkingDirectory $Root
    $servicePrincipal=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $trigger=New-ScheduledTaskTrigger -AtStartup
    $settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $servicePrincipal -Settings $settings -Description 'Separate persistent acceptance portal. Explicit releases only, no automatic updates.' | Out-Null
    if(-not (Get-NetFirewallRule -Name 'YDS-Portal-Test' -ErrorAction SilentlyContinue)){
        New-NetFirewallRule -Name 'YDS-Portal-Test' -DisplayName 'YDS portal TEST' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Domain,Private -RemoteAddress LocalSubnet | Out-Null
    }
    Start-ScheduledTask -TaskName $taskName
    $ready=$false
    $timer=[Diagnostics.Stopwatch]::StartNew()
    while($timer.Elapsed.TotalSeconds -lt 60){
        try {
            $response=Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/accounts/login/" -TimeoutSec 2
            if($response.StatusCode -eq 200 -and $response.Headers['X-Portal-Environment'] -eq 'staging' -and $response.Headers['X-Portal-Version'] -eq $Revision.Substring(0,12)){$ready=$true;break}
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    if(-not $ready){throw 'TEST login health check failed. See .runtime\server.log. Production was not changed.'}
    Write-Host "TEST started: http://$($Hosts[0]):$Port/ . Production tasks and settings were not changed."
} finally {Pop-Location}
