# Clear only TEST rotation assignments/movements; retain the employee catalogue.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Revision)
$ErrorActionPreference='Stop'
if($Revision -notmatch '^[a-f0-9]{40}$'){throw 'Pass an exact commit SHA.'}
[Net.ServicePointManager]::SecurityProtocol=[Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$temp=Join-Path ([IO.Path]::GetTempPath()) ('yds-personnel-clear-'+[Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($temp)
$disabled=$false;$stopped=$false;$root=''
try{
    # Fetch before downtime. Reuse the exact TEST task identity guards from recovery.
    foreach($name in @('repair-staging.ps1','clear_staging_personnel.py')){
        Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/Hvidsmen/gpr_systerm/$Revision/ops/$name" -OutFile (Join-Path $temp $name) -TimeoutSec 60
    }
    . (Join-Path $temp 'repair-staging.ps1')
    if(-not (Test-TestRepairAdministrator)){throw 'Run PowerShell as Administrator.'}
    $task=Get-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\' -ErrorAction Stop
    $root=[IO.Path]::GetFullPath([string](@($task.Actions)[0].WorkingDirectory)).TrimEnd([char[]]@('\','/'))
    $config=Assert-TestRepairTask $task $root
    if([int]$config.port -ne 8002 -or -not $task.Settings.Enabled){throw 'Only the enabled TEST task on port 8002 is supported.'}
    $python=Join-Path $root '.venv/Scripts/python.exe'
    $helper=Join-Path $temp 'clear_staging_personnel.py'
    Write-Host '1/3 Checking only TEST personnel. Employee catalogue and other sections will be retained.'
    $before=Invoke-TestRepairHelper $python $helper @('--root',$root)
    $before | ConvertTo-Json -Depth 5 | Out-Host
    Write-Host '2/3 Stopping TEST, backing up its database, and clearing assignments, schedules and movements.'
    Disable-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\' | Out-Null;$disabled=$true
    [void](New-Item -ItemType File -Force -Path (Join-Path $root '.runtime/server.stop'))
    $clock=[Diagnostics.Stopwatch]::StartNew()
    while(((Get-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\').State -eq 'Running' -or (Test-TestRepairPort 8002)) -and $clock.Elapsed.TotalSeconds -lt 150){Start-Sleep -Milliseconds 500}
    if((Get-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\').State -eq 'Running' -or (Test-TestRepairPort 8002)){
        throw 'TEST did not stop. Nothing cleared; the stop marker remains for inspection.'
    }
    $stopped=$true
    $result=Invoke-TestRepairHelper $python $helper @('--root',$root,'--execute')
    $result | ConvertTo-Json -Depth 5 | Out-Host
}finally{
    if($disabled){
        $recovery=(Test-Path -LiteralPath (Join-Path $root '.runtime/staging-recovery.json')) -or (Test-Path -LiteralPath (Join-Path $root '.runtime/staging-refresh-transaction.json'))
        if($recovery){Write-Warning 'TEST stays stopped because database recovery is pending.'}
        else{
            if($stopped){Remove-Item -LiteralPath (Join-Path $root '.runtime/server.stop') -Force -ErrorAction SilentlyContinue}
            Enable-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\' | Out-Null
            if($stopped){Start-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\'}
        }
    }
    Remove-Item -LiteralPath $temp -Recurse -Force
}
Wait-TestRepairHealth 8002 ([string]$config.revision)
Write-Host '3/3 TEST personnel cleared; TEST is healthy. Production was not changed.'
