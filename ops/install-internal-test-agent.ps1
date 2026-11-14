# Installs a dedicated GitHub Actions runner on a Windows computer inside the corporate network.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$RepositoryUrl,
    [Parameter(Mandatory=$true)][string]$RegistrationToken,
    [string]$Root='C:\GPR-Internal-Test-Agent',
    [string]$RunnerName=$env:COMPUTERNAME
)

$ErrorActionPreference='Stop'

function Assert-Administrator {
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    $principal=New-Object Security.Principal.WindowsPrincipal($identity)
    if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
        throw 'Open PowerShell as Administrator and run the installer again.'
    }
}

function Assert-RepositoryUrl([string]$Value) {
    $uri=$null
    if(-not [Uri]::TryCreate($Value,[UriKind]::Absolute,[ref]$uri) -or
       $uri.Scheme -ne 'https' -or $uri.Host -ne 'github.com' -or
       $uri.AbsolutePath -notmatch '^/[^/]+/[^/]+/?$'){
        throw 'RepositoryUrl must be an HTTPS GitHub repository URL.'
    }
}

Assert-Administrator
Assert-RepositoryUrl $RepositoryUrl

$rootPath=[IO.Path]::GetFullPath($Root)
if($rootPath.StartsWith('\\')){throw 'Use a local disk directory, not a network share.'}
if(Test-Path -LiteralPath (Join-Path $rootPath '.runner')){
    throw "A GitHub runner is already configured in $rootPath. Nothing was changed."
}
if(Test-Path -LiteralPath $rootPath){
    if(@(Get-ChildItem -Force -LiteralPath $rootPath).Count){
        throw "The destination $rootPath is not empty. Nothing was changed."
    }
}else{
    [void][IO.Directory]::CreateDirectory($rootPath)
}

$headers=@{'Accept'='application/vnd.github+json'}
$release=Invoke-RestMethod -Uri 'https://api.github.com/repos/actions/runner/releases/latest' -Headers $headers -UserAgent 'GPR-Internal-Test-Agent-Installer' -TimeoutSec 60
$asset=@($release.assets | Where-Object {$_.name -match '^actions-runner-win-x64-[0-9.]+\.zip$'}) | Select-Object -First 1
if(-not $asset -or -not $asset.browser_download_url){throw 'GitHub did not return a Windows x64 runner package.'}

$archive=Join-Path ([IO.Path]::GetTempPath()) ('gpr-actions-runner-'+[Guid]::NewGuid().ToString('N')+'.zip')
try{
    Write-Host "Downloading $($asset.name) from GitHub..."
    Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $archive -TimeoutSec 180
    Expand-Archive -LiteralPath $archive -DestinationPath $rootPath
    $config=Join-Path $rootPath 'config.cmd'
    if(-not (Test-Path -LiteralPath $config -PathType Leaf)){throw 'The downloaded runner package is incomplete.'}

    Push-Location $rootPath
    try{
        & $config --url $RepositoryUrl --token $RegistrationToken --name $RunnerName --labels 'gpr-internal' --unattended --runasservice
        if($LASTEXITCODE -ne 0){throw 'GitHub runner registration failed. Review the message above.'}
    }finally{Pop-Location}
}finally{
    if(Test-Path -LiteralPath $archive){Remove-Item -LiteralPath $archive -Force}
}

Write-Host ''
Write-Host 'The internal TEST agent is installed as a Windows service.'
Write-Host 'Next: add the three INTERNAL_TEST_* repository secrets and start the workflow.'
