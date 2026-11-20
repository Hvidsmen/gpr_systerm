param([string]$RepositoryRoot='')
$ErrorActionPreference='Stop'
if(-not $RepositoryRoot){$RepositoryRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))}
. (Join-Path $RepositoryRoot 'ops/repair-staging-startup.ps1') -StartupDelaySeconds 90 -HealthTimeoutSeconds 30
$folder=Join-Path ([IO.Path]::GetTempPath()) ('yds-test-startup-checks-'+[Guid]::NewGuid().ToString('N'))
$revision='a'*40;$passed=0
function Assert-True($Value,[string]$Message){if(-not $Value){throw $Message}}
function Assert-Fails([scriptblock]$Action){$failed=$false;try{& $Action}catch{$failed=$true};if(-not $failed){throw 'Expected refusal did not happen.'}}
function Reset-Fixture {
    if(Test-Path -LiteralPath $folder){Remove-Item -LiteralPath $folder -Recurse -Force}
    foreach($part in @('.runtime','.venv/Scripts','ops')){[void][IO.Directory]::CreateDirectory((Join-Path $folder $part))}
    [IO.File]::WriteAllText((Join-Path $folder '.venv/Scripts/python.exe'),'synthetic executable')
    [IO.File]::WriteAllText((Join-Path $folder 'ops/staging.py'),'synthetic launcher')
    @{kind='gpr-acceptance-v1';revision=$revision;port=18002} | ConvertTo-Json | Set-Content (Join-Path $folder 'staging.json') -Encoding UTF8
    $principal=[pscustomobject]@{UserId='YAMALGAZPROM\dvivanov';LogonType='Password'}
    $script:task=[pscustomobject]@{
        TaskName='YDS Portal Test';TaskPath='\';State='Ready';Principal=$principal;Triggers=@();Settings=[pscustomobject]@{Enabled=$true}
        Actions=@([pscustomobject]@{WorkingDirectory=$folder;Execute=(Join-Path $folder '.venv/Scripts/python.exe');Arguments=('"{0}" serve --root "{1}"' -f (Join-Path $folder 'ops/staging.py'),$folder)})
    }
    $script:originalPrincipal=$principal;$script:calls=New-Object 'System.Collections.Generic.List[string]'
}
function Test-StagingStartupAdministrator{return $true}
function Get-ScheduledTask{[CmdletBinding()]param($TaskName,$TaskPath)
    if($TaskName -ne 'YDS Portal Test' -or $TaskPath -ne '\'){throw 'A non-TEST task was accessed.'};return $script:task
}
function New-ScheduledTaskTrigger{param([switch]$AtStartup,[switch]$AtLogOn)
    if($AtStartup){return [pscustomobject]@{Kind='startup';Delay=''}}
    if($AtLogOn){return [pscustomobject]@{Kind='logon';Delay=''}}
    throw 'Unexpected trigger.'
}
function New-ScheduledTaskSettingsSet{param($MultipleInstances,$ExecutionTimeLimit,$RestartCount,$RestartInterval,[switch]$StartWhenAvailable,[switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries)
    return [pscustomobject]@{Enabled=$true;MultipleInstances=$MultipleInstances;RestartCount=$RestartCount;RestartInterval=$RestartInterval;StartWhenAvailable=[bool]$StartWhenAvailable}
}
function Set-ScheduledTask{param($TaskName,$TaskPath,$Trigger,$Settings)
    if($TaskName -ne 'YDS Portal Test' -or $TaskPath -ne '\'){throw 'A non-TEST task was changed.'}
    $script:calls.Add('set');$script:task.Triggers=$Trigger;$script:task.Settings=$Settings;return $script:task
}
function Enable-ScheduledTask{param($TaskName,$TaskPath)
    if($TaskName -ne 'YDS Portal Test' -or $TaskPath -ne '\'){throw 'A non-TEST task was enabled.'};$script:calls.Add('enable');return $script:task
}
function Start-ScheduledTask{param($TaskName,$TaskPath)
    if($TaskName -ne 'YDS Portal Test' -or $TaskPath -ne '\'){throw 'A non-TEST task was started.'};$script:calls.Add('start');$script:task.State='Running'
}
function Invoke-WebRequest{param($Uri,$TimeoutSec,[switch]$UseBasicParsing)
    return [pscustomobject]@{StatusCode=200;Headers=@{'X-Portal-Environment'='staging';'X-Portal-Version'=$revision.Substring(0,12)}}
}
try {
    Reset-Fixture;Repair-StagingStartup
    Assert-True (($script:calls -join ',') -eq 'set,enable,start') 'Unexpected scheduled-task sequence.'
    Assert-True ([object]::ReferenceEquals($script:task.Principal,$script:originalPrincipal)) 'The stored TEST principal was replaced.'
    Assert-True ($script:task.Settings.RestartCount -eq 999) 'Persistent retries were not configured.'
    Assert-True (@($script:task.Triggers).Count -eq 2) 'Startup and logon fallback triggers were not configured.'
    Assert-True (@($script:task.Triggers)[0].Delay -eq 'PT90S') 'Startup delay was not configured.';$passed++
    Reset-Fixture;[IO.File]::WriteAllText((Join-Path $folder '.runtime/installed.json'),'production marker')
    Assert-Fails {Repair-StagingStartup};Assert-True ($script:calls.Count -eq 0) 'Production marker was ignored.';$passed++
    Reset-Fixture;$script:task.TaskName='YDS Portal'
    Assert-Fails {Repair-StagingStartup};Assert-True ($script:calls.Count -eq 0) 'Production task was accepted.';$passed++
    Write-Host "PASS: $passed TEST startup recovery isolation scenarios."
} finally {
    if(Test-Path -LiteralPath $folder){Remove-Item -LiteralPath $folder -Recurse -Force}
}
