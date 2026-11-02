# Download a pinned acceptance release without an archive or a production update.
[CmdletBinding()]
param([string]$Root='', [string]$ProductionRoot='', [string]$OwnerLogin='',
      [string]$Revision='4ed5e71adee65d76ac44a693323bec0f0f861db6', [int]$Port=8002,
      [string[]]$Hosts=@('192.168.70.9'), [string]$PythonExecutable='', [string]$ActuationRoot='',
      [ValidateSet('Auto','Git','GitHubFiles')][string]$DownloadMethod='Auto')
$ErrorActionPreference='Stop'

function Assert-StagingRevision([string]$Value){
    if($Value -notmatch '^[a-f0-9]{40}$'){throw 'An exact 40-character commit SHA is required. Branch names are not accepted.'}
}

function Assert-SeparateStagingPaths([string]$Destination,[string]$Production){
    $destinationPath=[IO.Path]::GetFullPath($Destination).TrimEnd([char[]]@('\','/'))
    $productionPath=[IO.Path]::GetFullPath($Production).TrimEnd([char[]]@('\','/'))
    $separator=[IO.Path]::DirectorySeparatorChar
    if($destinationPath.Equals($productionPath,[StringComparison]::OrdinalIgnoreCase) -or
       $destinationPath.StartsWith($productionPath+$separator,[StringComparison]::OrdinalIgnoreCase) -or
       $productionPath.StartsWith($destinationPath+$separator,[StringComparison]::OrdinalIgnoreCase)){
        throw 'TEST and production directories must be separate. No existing directory will be overwritten.'
    }
}

function Get-StagingFilePath([string]$Directory,[string]$RepositoryPath){
    $parts=$RepositoryPath.Split('/')
    if(-not $RepositoryPath -or $RepositoryPath.StartsWith('/') -or $RepositoryPath.Contains('\')){throw 'Unsafe repository path.'}
    foreach($part in $parts){
        if(-not $part -or $part -in @('.','..','.git','.env','.runtime','.venv') -or $part -match '[\x00-\x1f<>:"|?*]' -or $part.EndsWith('.') -or $part.EndsWith(' ')){
            throw 'Unsafe repository path.'
        }
        if($part -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)'){throw 'Reserved Windows filename.'}
    }
    if($parts[0] -in @('data','media','private_uploads','staticfiles','staging.json','secret.key')){throw 'Runtime data cannot be part of an installation release.'}
    $rootPath=[IO.Path]::GetFullPath($Directory).TrimEnd([char[]]@('\','/'))+[IO.Path]::DirectorySeparatorChar
    $target=[IO.Path]::GetFullPath((Join-Path $Directory ($parts -join [IO.Path]::DirectorySeparatorChar)))
    if(-not $target.StartsWith($rootPath,[StringComparison]::OrdinalIgnoreCase)){throw 'Repository path escapes TEST directory.'}
    return $target
}

function Get-StagingBlobHash([string]$File){
    [byte[]]$bytes=[IO.File]::ReadAllBytes($File)
    [byte[]]$header=[Text.Encoding]::ASCII.GetBytes(('blob '+$bytes.Length+"`0"))
    $hash=[Security.Cryptography.SHA1]::Create()
    try{
        [void]$hash.TransformBlock($header,0,$header.Length,$header,0)
        [void]$hash.TransformFinalBlock($bytes,0,$bytes.Length)
        return ([BitConverter]::ToString($hash.Hash)).Replace('-','').ToLowerInvariant()
    }finally{$hash.Dispose()}
}

function Get-StagingReleaseTree([string]$Commit){
    Assert-StagingRevision $Commit
    $headers=@{'Accept'='application/vnd.github+json'}
    $api='https://api.github.com/repos/Hvidsmen/gpr_systerm'
    $identity=Invoke-RestMethod -Uri "$api/git/commits/$Commit" -Headers $headers -UserAgent YDS-Test-Installer -TimeoutSec 60
    if($identity.sha -cne $Commit -or $identity.tree.sha -notmatch '^[a-f0-9]{40}$'){throw 'GitHub returned a different release identity.'}
    $tree=Invoke-RestMethod -Uri "$api/git/trees/$($identity.tree.sha)?recursive=1" -Headers $headers -UserAgent YDS-Test-Installer -TimeoutSec 60
    if($tree.truncated){throw 'Incomplete GitHub release tree. Download was cancelled.'}
    $files=@($tree.tree | Where-Object {$_.type -eq 'blob'})
    if($files.Count -eq 0 -or $files.Count -gt 10000){throw 'Invalid release file count.'}
    if(($files | Measure-Object -Property size -Sum).Sum -gt 500000000){throw 'TEST release exceeds the download size limit.'}
    if(@($tree.tree | Where-Object {$_.type -notin @('blob','tree')}).Count){throw 'Submodules are not supported in a TEST release.'}
    $seen=New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach($file in $files){
        if($file.mode -notin @('100644','100755') -or $file.sha -notmatch '^[a-f0-9]{40}$' -or $file.size -gt 50000000){throw 'Invalid or linked release file.'}
        if(-not $seen.Add($file.path)){throw 'Duplicate Windows paths in release.'}
        [void](Get-StagingFilePath ([IO.Path]::GetTempPath()) $file.path)
    }
    foreach($required in @('manage.py','ops/install-staging.ps1','ops/staging.py','config/staging_settings.py','requirements.staging.lock')){
        if(-not $seen.Contains($required)){throw 'Selected commit does not contain the TEST installer.'}
    }
    return $files
}

function Get-StagingCode([string]$Destination,[string]$Commit,[string]$Method='Auto'){
    Assert-StagingRevision $Commit
    if(Test-Path -LiteralPath $Destination){throw 'TEST destination already exists. It will not be overwritten.'}
    $destinationPath=[IO.Path]::GetFullPath($Destination)
    $parent=Split-Path $destinationPath -Parent
    if(-not (Test-Path -LiteralPath $parent -PathType Container)){throw 'Create the TEST parent directory first, or choose an existing local parent.'}
    $files=@(Get-StagingReleaseTree $Commit)
    $temporary=Join-Path $parent ('.yds-test-download-'+[Guid]::NewGuid().ToString('N'))
    [void][IO.Directory]::CreateDirectory($temporary)
    try{
        $git=Get-Command git.exe,git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        $useGit=$Method -eq 'Git' -or ($Method -eq 'Auto' -and $null -ne $git)
        if($useGit){
            if(-not $git){throw 'Git is not installed. Use -DownloadMethod GitHubFiles instead.'}
            & $git.Source init --quiet $temporary
            if($LASTEXITCODE -ne 0){throw 'Could not create the separate TEST checkout.'}
            & $git.Source -C $temporary config core.autocrlf false
            if($LASTEXITCODE -ne 0){throw 'Could not configure the TEST checkout.'}
            & $git.Source -C $temporary fetch --quiet --depth=1 https://github.com/Hvidsmen/gpr_systerm.git $Commit
            if($LASTEXITCODE -ne 0){throw 'Could not download the selected TEST commit.'}
            & $git.Source -C $temporary checkout --quiet --detach FETCH_HEAD
            if($LASTEXITCODE -ne 0){throw 'Could not check out the TEST commit.'}
            $actual=& $git.Source -C $temporary rev-parse HEAD
            if($LASTEXITCODE -ne 0 -or $actual.Trim() -cne $Commit){throw 'TEST checkout does not match the selected commit.'}
        }else{
            Write-Host 'Downloading verified TEST files directly from GitHub. No Git or archive is needed.'
            $index=0
            foreach($file in $files){
                $index++
                $target=Get-StagingFilePath $temporary $file.path
                [void][IO.Directory]::CreateDirectory((Split-Path $target -Parent))
                $escaped=($file.path.Split('/') | ForEach-Object {[Uri]::EscapeDataString($_)}) -join '/'
                $url="https://raw.githubusercontent.com/Hvidsmen/gpr_systerm/$Commit/$escaped"
                Write-Progress -Activity 'Downloading TEST release' -Status "$index / $($files.Count)" -PercentComplete ([int](100*$index/$files.Count))
                Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $target -TimeoutSec 60
                if((Get-Item -Force -LiteralPath $target).Length -ne $file.size -or (Get-StagingBlobHash $target) -cne $file.sha){throw 'A TEST file failed its GitHub content hash check.'}
            }
            Write-Progress -Activity 'Downloading TEST release' -Completed
        }
        foreach($file in $files){
            $target=Get-StagingFilePath $temporary $file.path
            if(-not (Test-Path -LiteralPath $target -PathType Leaf) -or (Get-Item -Force -LiteralPath $target).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'A TEST release file is missing or linked.'}
            if((Get-StagingBlobHash $target) -cne $file.sha){throw 'A TEST file does not match the pinned release.'}
        }
        # Move into place only after the complete source has been checked.
        [IO.Directory]::Move($temporary,$destinationPath)
    }finally{
        if(Test-Path -LiteralPath $temporary){Remove-Item -LiteralPath $temporary -Recurse -Force}
    }
}

function Install-StagingOnline{
    Assert-StagingRevision $Revision
    $principal=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Open PowerShell as Administrator, then run this command again.'}
    if(-not $ProductionRoot){
        $sourceTask=Get-ScheduledTask -TaskName 'YDS Portal' -ErrorAction SilentlyContinue
        if($sourceTask){$script:ProductionRoot=[string]$sourceTask.Actions.WorkingDirectory}
        if(-not $ProductionRoot){$script:ProductionRoot=Read-Host 'Full directory of the production portal'}
    }
    $script:ProductionRoot=(Resolve-Path -LiteralPath $ProductionRoot).Path
    if(-not $Root){$script:Root=Join-Path (Split-Path $ProductionRoot -Parent) 'YDS-Test'}
    $script:Root=[IO.Path]::GetFullPath($Root)
    if($Root.StartsWith('\\')){throw 'Use a local disk directory for TEST.'}
    Assert-SeparateStagingPaths $Root $ProductionRoot
    if(-not (Test-Path -LiteralPath (Join-Path $ProductionRoot '.runtime\installed.json'))){throw 'Production configuration was not found. No existing directory was changed.'}
    if(Get-ScheduledTask -TaskName 'YDS Portal Test' -ErrorAction SilentlyContinue){throw 'TEST is already installed. No existing installation will be overwritten.'}
    Get-StagingCode $Root $Revision $DownloadMethod
    $arguments=@{Root=$Root;ProductionRoot=$ProductionRoot;Revision=$Revision;Port=$Port;Hosts=$Hosts}
    if($OwnerLogin){$arguments.OwnerLogin=$OwnerLogin}
    if($PythonExecutable){$arguments.PythonExecutable=$PythonExecutable}
    if($ActuationRoot){$arguments.ActuationRoot=$ActuationRoot}
    & (Join-Path $Root 'ops\install-staging.ps1') @arguments
}

if($MyInvocation.InvocationName -ne '.'){
    # Windows PowerShell 5.1 otherwise may select obsolete TLS defaults for GitHub.
    [Net.ServicePointManager]::SecurityProtocol=[Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Install-StagingOnline
}
