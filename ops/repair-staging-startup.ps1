# Repair and start only the existing TEST scheduled task after a Windows reboot.
[CmdletBinding()]
param([int]$StartupDelaySeconds=90,[int]$HealthTimeoutSeconds=180)
$ErrorActionPreference='Stop'

function Test-StagingStartupAdministrator {
    $principal=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-StagingStartupAccount([string]$Expected,[string]$Provided) {
    if([string]::IsNullOrWhiteSpace($Expected) -or [string]::IsNullOrWhiteSpace($Provided)){return $false}
    if($Expected.Equals($Provided,[StringComparison]::OrdinalIgnoreCase)){return $true}
    try {
        $sidType=[Security.Principal.SecurityIdentifier]
        $expectedSid=(New-Object Security.Principal.NTAccount($Expected)).Translate($sidType).Value
        $providedSid=(New-Object Security.Principal.NTAccount($Provided)).Translate($sidType).Value
        return $expectedSid -eq $providedSid
    } catch {return $false}
}

function Get-StagingStartupCredential($Task) {
    $account=[string]$Task.Principal.UserId
    $credential=Get-Credential -UserName $account -Message "Enter the current Windows password for YDS Portal Test ($account)"
    if($null -eq $credential -or -not (Test-StagingStartupAccount $account $credential.UserName)){
        throw 'Use the same Windows account that runs YDS Portal Test.'
    }
    return $credential
}

function Assert-StagingStartupTask($Task) {
    if($Task.TaskName -ne 'YDS Portal Test' -or $Task.TaskPath -ne '\' -or @($Task.Actions).Count -ne 1){
        throw 'Only the separate YDS Portal Test task is supported.'
    }
    $action=@($Task.Actions)[0]
    if(-not $action.WorkingDirectory){throw 'The TEST task has no working directory.'}
    $root=[IO.Path]::GetFullPath([string]$action.WorkingDirectory).TrimEnd([char[]]@('\','/'))
    foreach($path in @($root,(Join-Path $root '.runtime'))){
        if(-not (Test-Path -LiteralPath $path -PathType Container) -or ((Get-Item -Force -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint)){
            throw 'Missing or linked TEST directory. Nothing was changed.'
        }
    }
    if((Test-Path -LiteralPath (Join-Path $root '.runtime/installed.json')) -or (Test-Path -LiteralPath (Join-Path $root '.env'))){
        throw 'Production state was found. Nothing was changed.'
    }
    $config=Get-Content -LiteralPath (Join-Path $root 'staging.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if($config.kind -ne 'gpr-acceptance-v1' -or $config.revision -notmatch '^[a-f0-9]{40}$' -or [int]$config.port -lt 1024 -or [int]$config.port -gt 65535){
        throw 'Invalid separate TEST configuration. Nothing was changed.'
    }
    $python=[IO.Path]::GetFullPath((Join-Path $root '.venv/Scripts/python.exe'))
    $runner=[IO.Path]::GetFullPath((Join-Path $root 'ops/staging.py'))
    $expected='"{0}" serve --root "{1}"' -f $runner,$root
    if(-not ([IO.Path]::GetFullPath([string]$action.Execute)).Equals($python,[StringComparison]::OrdinalIgnoreCase) -or
            -not ([string]$action.Arguments).Equals($expected,[StringComparison]::OrdinalIgnoreCase)){
        throw 'The task does not match the separate TEST launcher. Nothing was changed.'
    }
    return [pscustomobject]@{Root=$root;Port=[int]$config.port;Revision=[string]$config.revision}
}

function Repair-StagingStartup {
    if(-not (Test-StagingStartupAdministrator)){throw 'Run PowerShell as Administrator.'}
    if($StartupDelaySeconds -lt 30 -or $StartupDelaySeconds -gt 3600){throw 'StartupDelaySeconds must be between 30 and 3600.'}
    if($HealthTimeoutSeconds -lt 30 -or $HealthTimeoutSeconds -gt 1800){throw 'HealthTimeoutSeconds must be between 30 and 1800.'}
    $task=Get-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\' -ErrorAction Stop
    $test=Assert-StagingStartupTask $task

    $startup=New-ScheduledTaskTrigger -AtStartup
    $startup.Delay=('PT{0}S' -f $StartupDelaySeconds)
    $logon=New-ScheduledTaskTrigger -AtLogOn
    $settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    # Task Scheduler never reveals a stored password. Updating a task that uses
    # LogonType=Password therefore requires the same account's current password.
    # It is requested securely and passed directly to the Windows API; it is not
    # written to a file or log. Service-account tasks need no credential prompt.
    if([string]$task.Principal.LogonType -eq 'Password'){
        $credential=Get-StagingStartupCredential $task
        try {
            Set-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\' -Trigger @($startup,$logon) -Settings $settings `
                -User ([string]$task.Principal.UserId) -Password ($credential.GetNetworkCredential().Password) | Out-Null
        } finally {$credential=$null}
    } else {
        Set-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\' -Trigger @($startup,$logon) -Settings $settings | Out-Null
    }
    Enable-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\' | Out-Null
    $state=(Get-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\').State
    if($state -notin @('Running','Queued')){Start-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\'}

    $clock=[Diagnostics.Stopwatch]::StartNew();$ready=$false
    while($clock.Elapsed.TotalSeconds -lt $HealthTimeoutSeconds){
        try{
            $response=Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:{0}/accounts/login/" -f $test.Port) -TimeoutSec 2
            if($response.StatusCode -eq 200 -and $response.Headers['X-Portal-Environment'] -eq 'staging' -and
                    $response.Headers['X-Portal-Version'] -eq $test.Revision.Substring(0,12)){$ready=$true;break}
        }catch{}
        Start-Sleep -Seconds 1
    }
    if(-not $ready){
        $info=Get-ScheduledTaskInfo -TaskName 'YDS Portal Test' -TaskPath '\'
        throw ("TEST did not become ready. Task state: {0}; LastTaskResult: {1}. Log: {2}" -f
               (Get-ScheduledTask -TaskName 'YDS Portal Test' -TaskPath '\').State,$info.LastTaskResult,(Join-Path $test.Root '.runtime/server.log'))
    }
    Write-Host ("TEST is running: http://127.0.0.1:{0}/ . Startup recovery was repaired; production tasks were not changed." -f $test.Port)
}

if($MyInvocation.InvocationName -ne '.') {Repair-StagingStartup}
