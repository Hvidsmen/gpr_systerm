[CmdletBinding()]
param([string]$Root='')
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
$Root=(Resolve-Path $Root).Path
New-Item -ItemType File -Force (Join-Path $Root '.runtime\supervisor.stop') | Out-Null
Write-Host 'Graceful stop requested. Wait for the YDS Portal task to finish.'
