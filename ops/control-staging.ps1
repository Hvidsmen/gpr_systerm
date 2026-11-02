[CmdletBinding()]
param([ValidateSet('Start','Stop','Update','Status')][string]$Action='Status',[string]$Root='', [string]$Archive='', [string]$Revision='')
$ErrorActionPreference='Stop'
if(-not $Root){$Root=Join-Path $PSScriptRoot '..'}
$Root=(Resolve-Path -LiteralPath $Root).Path
if(-not (Test-Path (Join-Path $Root 'staging.json'))){throw 'Not a prepared TEST installation.'}
$task=Get-ScheduledTask -TaskName 'YDS Portal Test'
# Reject a task from a different directory; never act on production names.
if($task.Actions.WorkingDirectory.TrimEnd('\') -ne $Root.TrimEnd('\')){throw 'TEST task belongs to a different directory.'}
$python=Join-Path $Root '.venv\Scripts\python.exe'
$runner=Join-Path $Root 'ops\staging.py'
if($Action -eq 'Update' -and (-not $Archive -or $Revision -notmatch '^[a-f0-9]{40}$')){throw 'Pass -Archive and the exact -Revision SHA before stopping TEST.'}
if($Action -in @('Stop','Update')){
    & $python $runner stop --root $Root
    if($LASTEXITCODE -ne 0){throw 'Could not request TEST stop.'}
    $timer=[Diagnostics.Stopwatch]::StartNew()
    while((Get-ScheduledTask -TaskName 'YDS Portal Test').State -eq 'Running'){
        if($timer.Elapsed.TotalSeconds -gt 120){throw 'TEST has not stopped. No update was applied.'}
        Start-Sleep -Milliseconds 500
    }
}
if($Action -eq 'Update'){
    & $python $runner update --root $Root --archive $Archive --revision $Revision
    if($LASTEXITCODE -ne 0){throw 'TEST update failed. Check .runtime\prepare.log before restarting. Production was not changed.'}
    Start-ScheduledTask -TaskName 'YDS Portal Test'
}
if($Action -eq 'Start'){Start-ScheduledTask -TaskName 'YDS Portal Test'}
if($Action -eq 'Status'){& $python $runner status --root $Root}
