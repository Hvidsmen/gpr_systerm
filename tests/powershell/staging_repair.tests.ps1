param([string]$RepositoryRoot='')
$ErrorActionPreference='Stop'
if(-not $RepositoryRoot){$RepositoryRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))}
. (Join-Path $RepositoryRoot 'ops/repair-staging.ps1')
$folder=Join-Path ([IO.Path]::GetTempPath()) ('yds-test-repair-checks-'+[Guid]::NewGuid().ToString('N'))
$commit='b'*40;$old='a'*40;$passed=0
function Assert-True($Value,[string]$Message){if(-not $Value){throw $Message}}
function Assert-Fails([scriptblock]$Action){$failed=$false;try{& $Action}catch{$failed=$true};if(-not $failed){throw 'Expected refusal did not happen.'}}
function Reset-Fixture{
    if(Test-Path -LiteralPath $folder){Remove-Item -LiteralPath $folder -Recurse -Force}
    [void][IO.Directory]::CreateDirectory($folder)
    foreach($part in @('.runtime','.venv/Scripts','ops','data')){[void][IO.Directory]::CreateDirectory((Join-Path $folder $part))}
    [IO.File]::WriteAllText((Join-Path $folder '.venv/Scripts/python.exe'),'synthetic executable')
    [IO.File]::WriteAllText((Join-Path $folder 'data/db.sqlite3'),'test data retained')
    [IO.File]::WriteAllText((Join-Path $folder 'secret.key'),'synthetic test secret retained')
    $script:config=[pscustomobject]@{kind='gpr-acceptance-v1';revision=$old;port=18002}
    $script:config | ConvertTo-Json | Set-Content (Join-Path $folder 'staging.json') -Encoding UTF8
    $script:task=[pscustomobject]@{TaskName='YDS Portal Test';TaskPath='\';State='Running';Settings=[pscustomobject]@{Enabled=$true};Actions=@([pscustomobject]@{WorkingDirectory=$folder;Execute=(Join-Path $folder '.venv/Scripts/python.exe');Arguments=('"{0}" serve --root "{1}"' -f (Join-Path $folder 'ops/staging.py'),$folder)})}
    $script:calls=New-Object 'System.Collections.Generic.List[string]'
    $script:downloadFail=$script:applyFail=$script:needsRecovery=$script:changed=$script:busy=$script:portBusy=$script:badArchive=$script:hiddenRunning=$false
}
function Test-TestRepairAdministrator{return $true}
function Get-ScheduledTask{[CmdletBinding()]param($TaskName,$TaskPath)
    if($TaskName -ne 'YDS Portal Test' -or $TaskPath -ne '\'){throw 'A non-TEST task was accessed.'};return $script:task
}
function Disable-ScheduledTask{param($TaskName,$TaskPath)
    if($TaskName -ne 'YDS Portal Test' -or $TaskPath -ne '\'){throw 'A non-TEST task was disabled.'};$script:calls.Add('disable');$script:task.Settings.Enabled=$false;if($script:hiddenRunning){$script:task.State='Disabled'}
}
function Stop-ScheduledTask{param($TaskName,$TaskPath)
    if($TaskName -ne 'YDS Portal Test' -or $TaskPath -ne '\'){throw 'A non-TEST task was stopped.'};$script:calls.Add('stop');$script:task.State='Disabled'
}
function Enable-ScheduledTask{param($TaskName,$TaskPath)
    if($TaskName -ne 'YDS Portal Test' -or $TaskPath -ne '\'){throw 'A non-TEST task was enabled.'};$script:calls.Add('enable');$script:task.Settings.Enabled=$true
}
function Start-ScheduledTask{param($TaskName,$TaskPath)
    if($TaskName -ne 'YDS Portal Test' -or $TaskPath -ne '\'){throw 'A non-TEST task was started.'};$script:calls.Add('start');$script:task.State='Running'
}
function Get-TestRepairSource{param($Directory,$Commit,$Method)
    if($script:downloadFail){throw 'Synthetic download failure'}
    $path=Join-Path $Directory ('.runtime/repair-code/'+$Commit);[void][IO.Directory]::CreateDirectory((Join-Path $path 'ops'));return $path
}
function Invoke-TestRepairHelper{param($Python,$Helper,$Arguments)
    $command=$Arguments[0];$script:calls.Add($command)
    if($command -eq 'check'){
        if($script:busy){throw 'Synthetic active job'}
        return [pscustomobject]@{revision=$(if($script:changed){'c'*40}else{$script:config.revision});port=18002}
    }
    if($command -eq 'pack'){
        $path=Join-Path $folder ('.runtime/staging-repair-'+('f'*32)+'.zip');[IO.File]::WriteAllText($path,'synthetic archive')
        if($script:badArchive){return [pscustomobject]@{archive=(Join-Path $folder 'secret.key')}}
        return [pscustomobject]@{archive=$path}
    }
    if($command -eq 'apply'){
        if($script:task.State -eq 'Running'){throw 'Applied while TEST was still running.'}
        if($script:applyFail){
            if($script:needsRecovery){[IO.File]::WriteAllText((Join-Path $folder '.runtime/staging-recovery.json'),'synthetic recovery')}
            throw 'Synthetic apply failure'
        }
        $script:config.revision=$commit;$script:config | ConvertTo-Json | Set-Content (Join-Path $folder 'staging.json') -Encoding UTF8
        return [pscustomobject]@{revision=$commit;job_id='test-job';port=18002}
    }
    if($command -eq 'complete'){return [pscustomobject]@{state='done';revision=$commit}}
    throw 'Unexpected helper command.'
}
function Test-TestRepairPort{param($Port);return ($script:portBusy -or ($script:hiddenRunning -and -not $script:calls.Contains('stop')))}
function Wait-TestRepairHealth{param($Port,$Commit);$script:calls.Add('health')}
try{
    Reset-Fixture;Repair-TestPortal $commit '' 'Auto' 0 0
    Assert-True (($script:calls -join ',') -eq 'check,pack,disable,stop,apply,enable,start,health,complete') 'Unexpected task or helper sequence.'
    Assert-True ($script:task.Settings.Enabled) 'TEST was left disabled after success.'
    Assert-True ((Get-Content (Join-Path $folder 'data/db.sqlite3') -Raw) -eq 'test data retained') 'Test data was changed by the controller.'
    Assert-True ((Get-Content (Join-Path $folder 'secret.key') -Raw) -eq 'synthetic test secret retained') 'Test secret was changed.';$passed++
    Reset-Fixture;$script:hiddenRunning=$true;Repair-TestPortal $commit '' 'Auto' 0 0;Assert-True ($script:calls.Contains('stop') -and $script:calls.Contains('complete')) 'A disabled task with a live HTTP listener was not stopped.';$passed++
    Reset-Fixture;$script:downloadFail=$true;Assert-Fails {Repair-TestPortal $commit '' 'Auto' 0 0};Assert-True (-not $script:calls.Contains('disable')) 'Failed download stopped TEST.';$passed++
    Reset-Fixture;$script:busy=$true;Assert-Fails {Repair-TestPortal $commit '' 'Auto' 0 0};Assert-True (-not $script:calls.Contains('disable')) 'Active queue was stopped.';$passed++
    Reset-Fixture;$script:changed=$true;Assert-Fails {Repair-TestPortal $commit '' 'Auto' 0 0};Assert-True (-not $script:calls.Contains('disable')) 'Changed revision was stopped.';$passed++
    Reset-Fixture;$script:badArchive=$true;Assert-Fails {Repair-TestPortal $commit '' 'Auto' 0 0};Assert-True (Test-Path (Join-Path $folder 'secret.key')) 'Unexpected archive path was deleted.';Assert-True (-not $script:calls.Contains('disable')) 'Invalid archive stopped TEST.';$passed++
    Reset-Fixture;$script:applyFail=$true;Assert-Fails {Repair-TestPortal $commit '' 'Auto' 0 0};Assert-True ($script:task.Settings.Enabled -and $script:calls.Contains('start')) 'Failed apply did not restore TEST task availability.';$passed++
    Reset-Fixture;$script:applyFail=$true;$script:needsRecovery=$true;Assert-Fails {Repair-TestPortal $commit '' 'Auto' 0 0};Assert-True (-not $script:task.Settings.Enabled -and -not $script:calls.Contains('start')) 'Recovery marker was ignored.';$passed++
    Reset-Fixture;$script:portBusy=$true;Assert-Fails {Repair-TestPortal $commit '' 'Auto' 0 0};Assert-True (-not $script:calls.Contains('apply')) 'Busy port allowed an offline update.';$passed++
    Reset-Fixture;$script:task.Actions[0].WorkingDirectory=Join-Path $folder 'production';Assert-Fails {Repair-TestPortal $commit '' 'Auto' 0 0};Assert-True ($script:calls.Count -eq 0) 'Different task root was accepted.';$passed++
    Reset-Fixture;$script:task.Actions[0].Execute=Join-Path $folder '../production/python.exe';Assert-Fails {Repair-TestPortal $commit '' 'Auto' 0 0};Assert-True ($script:calls.Count -eq 0) 'Outside Python was accepted.';$passed++
    Reset-Fixture;$script:task.Actions[0].Arguments='production supervise';Assert-Fails {Repair-TestPortal $commit '' 'Auto' 0 0};Assert-True ($script:calls.Count -eq 0) 'Different launcher was accepted.';$passed++
    Reset-Fixture;[IO.File]::WriteAllText((Join-Path $folder '.runtime/installed.json'),'production marker');Assert-Fails {Repair-TestPortal $commit '' 'Auto' 0 0};Assert-True ($script:calls.Count -eq 0) 'Production marker was accepted.';$passed++
    Reset-Fixture;$script:task.Settings.Enabled=$false;Assert-Fails {Repair-TestPortal $commit '' 'Auto' 0 0};Assert-True ($script:calls.Count -eq 0) 'An explicitly disabled task was changed.';$passed++
    Reset-Fixture;Assert-Fails {Repair-TestPortal 'main' '' 'Auto' 0 0};Assert-True ($script:calls.Count -eq 0) 'Unpinned revision was accepted.';$passed++
    Write-Host "PASS: $passed TEST repair controller isolation and failure scenarios."
}finally{if(Test-Path -LiteralPath $folder){Remove-Item -LiteralPath $folder -Recurse -Force}}
