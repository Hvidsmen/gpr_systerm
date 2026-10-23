[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$DatabasePath,[string]$Root='')
$ErrorActionPreference='Stop'
if([string]::IsNullOrWhiteSpace($Root)){
    $directory=$PSScriptRoot
    if([string]::IsNullOrWhiteSpace($directory)){$directory=[IO.Path]::GetDirectoryName($MyInvocation.MyCommand.Path)}
    $Root=Join-Path $directory '..'
}
$Root=(Resolve-Path -LiteralPath $Root).Path
$admin=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Run this command as Administrator.'}
if(-not [IO.Path]::IsPathRooted($DatabasePath)){$DatabasePath=Join-Path $Root $DatabasePath}
$DatabasePath=(Resolve-Path -LiteralPath $DatabasePath).Path
$runtime=Join-Path $Root '.runtime'
$config=Get-Content (Join-Path $runtime 'installed.json') -Raw | ConvertFrom-Json
if($config.database -eq $DatabasePath){Write-Host "Already using $DatabasePath";exit 0}
$python=Join-Path $Root '.venv\Scripts\python.exe'
$server=Get-ScheduledTask -TaskName 'YDS Portal'
$updater=Get-ScheduledTask -TaskName 'YDS Portal Update'
if($updater.State -eq 'Running'){throw 'The update task is running. Wait until it completes, then retry.'}
$serverEnabled=$server.Settings.Enabled
$updaterEnabled=$updater.Settings.Enabled
$serverWasRunning=$server.State -eq 'Running'
Disable-ScheduledTask -TaskName 'YDS Portal Update' | Out-Null
Disable-ScheduledTask -TaskName 'YDS Portal' | Out-Null
$success=$false
try {
    # Keep startup recovery logic available if switching is interrupted by a reboot.
    Copy-Item (Join-Path $Root 'ops\windows_runtime.py') (Join-Path $runtime 'bootstrap.py') -Force
    & $python (Join-Path $Root 'ops\windows_runtime.py') switch-database --root $Root --database $DatabasePath
    if($LASTEXITCODE -ne 0){throw 'Database selection failed. See .runtime\control.log and .runtime\commands.log. No automatic restart was performed.'}
    $success=$true
} finally {
    if($updaterEnabled){Enable-ScheduledTask -TaskName 'YDS Portal Update' | Out-Null}
    if($serverEnabled){Enable-ScheduledTask -TaskName 'YDS Portal' | Out-Null}
}
if($success -and ($serverEnabled -or $serverWasRunning)){
    if(-not $serverEnabled){Enable-ScheduledTask -TaskName 'YDS Portal' | Out-Null}
    Start-ScheduledTask -TaskName 'YDS Portal'
    if(-not $serverEnabled){Disable-ScheduledTask -TaskName 'YDS Portal' | Out-Null}
}
Write-Host "Portal database: $DatabasePath"
Write-Host 'Sign in again and verify the objects and plans.'
