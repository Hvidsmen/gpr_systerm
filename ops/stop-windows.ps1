[CmdletBinding()]
param([string]$Root=(Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference='Stop'
$Root=(Resolve-Path $Root).Path
New-Item -ItemType File -Force (Join-Path $Root '.runtime\supervisor.stop') | Out-Null
Write-Host 'Graceful stop requested. Wait for the YDS Portal task to finish.'
