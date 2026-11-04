# One-time recovery for an old TEST updater that cannot stop its HTTP child.
[CmdletBinding()]
param([string]$Revision='', [string]$TestRoot='', [ValidateSet('Auto','Git','GitHubFiles')][string]$DownloadMethod='Auto')
$ErrorActionPreference='Stop'

function Assert-TestRepairTask($Task,[string]$Directory){
    if($Task.TaskName -ne 'YDS Portal Test' -or $Task.TaskPath -ne '\' -or @($Task.Actions).Count -ne 1){throw 'Only the separate YDS Portal Test task is supported.'}
    $root=[IO.Path]::GetFullPath($Directory).TrimEnd([char[]]@('\','/'))
    $action=@($Task.Actions)[0]
    if(-not $action.WorkingDirectory -or -not ([IO.Path]::GetFullPath($action.WorkingDirectory).TrimEnd([char[]]@('\','/'))).Equals($root,[StringComparison]::OrdinalIgnoreCase)){throw 'TEST task belongs to a different directory.'}
    $python=Join-Path $root '.venv/Scripts/python.exe'
    $expected='"{0}" serve --root "{1}"' -f ([IO.Path]::GetFullPath((Join-Path $root 'ops/staging.py'))),$root
    if(-not ([IO.Path]::GetFullPath($action.Execute)).Equals([IO.Path]::GetFullPath($python),[StringComparison]::OrdinalIgnoreCase) -or -not ([string]$action.Arguments).Equals($expected,[StringComparison]::OrdinalIgnoreCase)){throw 'The task does not match the separate TEST launcher. Nothing was stopped.'}
    foreach($path in @($root,(Join-Path $root '.runtime'))){
        if(-not (Test-Path -LiteralPath $path -PathType Container) -or ((Get-Item -Force -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Missing or linked TEST directory.'}
    }
    if((Test-Path -LiteralPath (Join-Path $root '.runtime/installed.json')) -or (Test-Path -LiteralPath (Join-Path $root '.env'))){throw 'Production state was found. Nothing was stopped.'}
    $config=Get-Content -LiteralPath (Join-Path $root 'staging.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if($config.kind -ne 'gpr-acceptance-v1' -or $config.revision -notmatch '^[a-f0-9]{40}$' -or [int]$config.port -lt 1024 -or [int]$config.port -gt 65535){throw 'Invalid separate TEST configuration.'}
    if(-not (Test-Path -LiteralPath $python -PathType Leaf)){throw 'The separate TEST Python was not found.'}
    return $config
}

function Get-TestRepairSource([string]$Directory,[string]$Commit,[string]$Method){
    $parent=Join-Path $Directory '.runtime/repair-code'
    if(Test-Path -LiteralPath $parent){if((Get-Item -Force -LiteralPath $parent).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Linked repair source directory.'}}
    [void][IO.Directory]::CreateDirectory($parent)
    $destination=Join-Path $parent $Commit
    $temp=Join-Path ([IO.Path]::GetTempPath()) ('yds-test-repair-'+[Guid]::NewGuid().ToString('N'))
    [void][IO.Directory]::CreateDirectory($temp)
    try{
        $bootstrap=Join-Path $temp 'bootstrap-staging.ps1'
        Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/Hvidsmen/gpr_systerm/$Commit/ops/bootstrap-staging.ps1" -OutFile $bootstrap -TimeoutSec 60
        . $bootstrap -Revision $Commit
        Get-StagingCode $destination $Commit $Method | Out-Host
        return $destination
    }finally{Remove-Item -LiteralPath $temp -Recurse -Force}
}

function Invoke-TestRepairHelper([string]$Python,[string]$Helper,[string[]]$Arguments){
    $output=@(& $Python $Helper @Arguments)
    if($LASTEXITCODE -ne 0){throw ('TEST repair helper failed: '+($output -join ' '))}
    if(-not $output.Count){throw 'TEST repair helper returned no result.'}
    return ($output[-1] | ConvertFrom-Json)
}

function Test-TestRepairPort([int]$Port){
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Wait-TestRepairHealth([int]$Port,[string]$Commit){
    $clock=[Diagnostics.Stopwatch]::StartNew()
    while($clock.Elapsed.TotalSeconds -lt 90){
        try{
            $response=Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/accounts/login/" -TimeoutSec 2
            if($response.StatusCode -eq 200 -and $response.Headers['X-Portal-Environment'] -eq 'staging' -and $response.Headers['X-Portal-Version'] -eq $Commit.Substring(0,12)){return}
        }catch{}
        Start-Sleep -Milliseconds 500
    }
    throw 'The selected TEST version did not confirm startup. See .runtime/server.log.'
}

function Test-TestRepairAdministrator{
    $principal=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Repair-TestPortal([string]$Commit,[string]$Directory='',[string]$Method='Auto',[int]$GracefulWaitSeconds=15,[int]$StoppedWaitSeconds=30){
    if($Commit -notmatch '^[a-f0-9]{40}$'){throw 'An exact verified commit SHA is required.'}
    if(-not (Test-TestRepairAdministrator)){throw 'Run PowerShell as Administrator.'}
    $task=Get-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\' -ErrorAction Stop
    if(-not $Directory){$Directory=[string](@($task.Actions)[0].WorkingDirectory)}
    $Directory=[IO.Path]::GetFullPath($Directory).TrimEnd([char[]]@('\','/'))
    $config=Assert-TestRepairTask $task $Directory
    if(-not $task.Settings.Enabled){throw 'The TEST task is disabled. Nothing was changed.'}
    if($config.revision -eq $Commit){Write-Host 'The selected TEST version is already installed.';return}
    $lock=$null;$disabled=$false;$stopped=$false;$started=$false;$archive=''
    try{
        $lock=[IO.File]::Open((Join-Path $Directory '.runtime/staging-repair.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
        Write-Host '1/5 Downloading and verifying the pinned source. TEST remains available.'
        $source=Get-TestRepairSource $Directory $Commit $Method
        $python=Join-Path $Directory '.venv/Scripts/python.exe'
        $helper=Join-Path $source 'ops/staging_repair.py'
        $common=@('--root',$Directory,'--revision',$Commit)
        $before=Invoke-TestRepairHelper $python $helper (@('check')+$common)
        if($before.revision -ne $config.revision){throw 'TEST changed during download. Nothing was stopped.'}
        if(Test-Path -LiteralPath (Join-Path $Directory ('.runtime/releases/'+$Commit))){throw 'This immutable TEST release already exists. Inspect its previous result before retrying.'}
        $packed=Invoke-TestRepairHelper $python $helper (@('pack')+$common)
        $archivePath=[IO.Path]::GetFullPath([string]$packed.archive)
        $runtimePath=[IO.Path]::GetFullPath((Join-Path $Directory '.runtime'))
        if(-not (Split-Path $archivePath -Parent).Equals($runtimePath,[StringComparison]::OrdinalIgnoreCase) -or (Split-Path $archivePath -Leaf) -notmatch '^staging-repair-[a-f0-9]{32}\.zip$'){throw 'Unexpected repair archive path. Nothing was stopped.'}
        $archive=$archivePath
        $task=Get-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\' -ErrorAction Stop
        [void](Assert-TestRepairTask $task $Directory)
        # Prevent the TEST task's automatic restart while its files/database are offline.
        Write-Host '2/5 Stopping only YDS Portal Test. Production tasks are not changed.'
        Disable-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\' | Out-Null;$disabled=$true
        [void](New-Item -ItemType File -Force -Path (Join-Path $Directory '.runtime/server.stop'))
        $clock=[Diagnostics.Stopwatch]::StartNew()
        while(((Get-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\').State -eq 'Running' -or (Test-TestRepairPort $before.port)) -and $clock.Elapsed.TotalSeconds -lt $GracefulWaitSeconds){Start-Sleep -Milliseconds 500}
        if((Get-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\').State -eq 'Running' -or (Test-TestRepairPort $before.port)){Stop-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\'}
        $clock.Restart()
        while(((Get-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\').State -eq 'Running' -or (Test-TestRepairPort $before.port)) -and $clock.Elapsed.TotalSeconds -lt $StoppedWaitSeconds){Start-Sleep -Milliseconds 500}
        if((Get-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\').State -eq 'Running' -or (Test-TestRepairPort $before.port)){throw 'TEST has not fully stopped. No repair was applied.'}
        $stopped=$true
        Write-Host '3/5 Installing TEST with its normal backup and migration checks. Details: .runtime/prepare.log.'
        $applied=Invoke-TestRepairHelper $python $helper (@('apply')+$common+@('--archive',$archive,'--expected-revision',[string]$before.revision))
        Write-Host '4/5 Starting the unchanged TEST task and checking the selected version.'
        Remove-Item -LiteralPath (Join-Path $Directory '.runtime/server.stop') -Force -ErrorAction SilentlyContinue
        Enable-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\' | Out-Null;$disabled=$false
        Start-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\';$started=$true
        Wait-TestRepairHealth $before.port $Commit
        [void](Invoke-TestRepairHelper $python $helper (@('complete')+$common+@('--job-id',[string]$applied.job_id)))
        Write-Host "5/5 TEST updated and healthy: $Commit . Database, documents and accounts retained."
    }finally{
        $recovery=(Test-Path -LiteralPath (Join-Path $Directory '.runtime/staging-recovery.json')) -or (Test-Path -LiteralPath (Join-Path $Directory '.runtime/staging-refresh-transaction.json'))
        if($disabled -and -not $recovery){
            Enable-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\' | Out-Null
            if($stopped -and -not $started){
                Remove-Item -LiteralPath (Join-Path $Directory '.runtime/server.stop') -Force -ErrorAction SilentlyContinue
                Start-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\'
                Write-Warning 'Repair did not complete; the selected TEST version was restarted. See .runtime/repair.log and prepare.log.'
            }
        }
        if($disabled -and $recovery){Write-Warning 'TEST stays stopped because database recovery is required. See .runtime/staging-recovery.json.'}
        if($archive -and (Test-Path -LiteralPath $archive)){Remove-Item -LiteralPath $archive -Force}
        if($lock){$lock.Dispose()}
    }
}

if($MyInvocation.InvocationName -ne '.'){
    [Net.ServicePointManager]::SecurityProtocol=[Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Repair-TestPortal $Revision $TestRoot $DownloadMethod
}
