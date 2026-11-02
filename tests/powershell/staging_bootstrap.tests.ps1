param([string]$RepositoryRoot='')
$ErrorActionPreference='Stop'
if(-not $RepositoryRoot){$RepositoryRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))}
. (Join-Path $RepositoryRoot 'ops/bootstrap-staging.ps1')
$testFolder=Join-Path ([IO.Path]::GetTempPath()) ('yds-bootstrap-tests-'+[Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($testFolder)
$commit='a'*40
$treeId='b'*40
$script:fixtureFiles=@{}
$script:fixtureTree=@()
$script:truncated=$false
$script:corrupt=$false
$script:downloads=0
$script:wrongIdentity=$false
$passed=0
function Assert-True($Value,[string]$Message){if(-not $Value){throw $Message}}
function Assert-Fails([scriptblock]$Action){$failed=$false;try{& $Action}catch{$failed=$true};if(-not $failed){throw 'Expected rejection did not occur.'}}
function Invoke-RestMethod{
    param($Uri,$Headers,$UserAgent,$TimeoutSec)
    if($Uri -like '*/git/commits/*'){
        $identity=$commit;if($script:wrongIdentity){$identity='c'*40}
        return [pscustomobject]@{sha=$identity;tree=[pscustomobject]@{sha=$treeId}}
    }
    if($Uri -like '*/git/trees/*'){return [pscustomobject]@{truncated=$script:truncated;tree=$script:fixtureTree}}
    throw 'Unexpected API call.'
}
function Invoke-WebRequest{
    param([switch]$UseBasicParsing,$Uri,$OutFile,$TimeoutSec)
    $path=[Uri]::UnescapeDataString(([Uri]$Uri).AbsolutePath).Substring(('/Hvidsmen/gpr_systerm/'+$commit+'/').Length)
    if(-not $script:fixtureFiles.ContainsKey($path)){throw 'Unexpected raw file request.'}
    [IO.File]::WriteAllBytes($OutFile,$script:fixtureFiles[$path])
    if($script:corrupt){[IO.File]::WriteAllBytes($OutFile,[byte[]]@(9,8,7))}
    $script:downloads++
}
try{
    $fixtures=Join-Path $testFolder 'fixtures';[void][IO.Directory]::CreateDirectory($fixtures)
    $required=@('manage.py','ops/install-staging.ps1','ops/staging.py','config/staging_settings.py','requirements.staging.lock','static/binary.dat','templates/a b.html','.env.example','.gitignore')
    foreach($name in $required){
        [byte[]]$bytes=[Text.Encoding]::UTF8.GetBytes('synthetic '+$name)
        if($name -eq 'static/binary.dat'){$bytes=[byte[]]@(0,255,128,13,10,7)}
        $script:fixtureFiles[$name]=$bytes
        $file=Join-Path $fixtures ([Guid]::NewGuid().ToString('N'))
        [IO.File]::WriteAllBytes($file,$bytes)
        $script:fixtureTree+=[pscustomobject]@{path=$name;sha=(Get-StagingBlobHash $file);mode='100644';type='blob';size=$bytes.Length}
    }
    $originalTree=$script:fixtureTree
    $known=Join-Path $fixtures 'known';[IO.File]::WriteAllBytes($known,[Text.Encoding]::ASCII.GetBytes("hello`n"))
    Assert-True ((Get-StagingBlobHash $known) -eq 'ce013625030ba8dba906f756967f9e9ca394464a') 'Git blob hash is incorrect.';$passed++
    $destination=Join-Path $testFolder 'test'
    Get-StagingCode $destination $commit 'GitHubFiles'
    Assert-True ($script:downloads -eq $required.Count) 'Expected every file to be downloaded directly.'
    Assert-True ([Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $destination 'static/binary.dat'))) -eq [Convert]::ToBase64String($script:fixtureFiles['static/binary.dat'])) 'Binary content was changed.'
    Assert-True (-not (Test-Path (Join-Path $destination '.git'))) 'File mode should not need Git.';$passed++
    $countBeforeResume=$script:downloads
    $venv=Join-Path $destination '.venv';[void][IO.Directory]::CreateDirectory($venv)
    $venvMarker=Join-Path $venv 'preserve.txt';[IO.File]::WriteAllText($venvMarker,'existing environment')
    Get-StagingCode $destination $commit 'GitHubFiles'
    Assert-True ($script:downloads -eq $countBeforeResume) 'Resume downloaded or replaced source files.'
    Assert-True ([IO.File]::ReadAllText($venvMarker) -eq 'existing environment') 'Resume changed the existing environment.';$passed++
    foreach($protected in @('staging.json','data','secret.key','staticfiles','.env','.runtime/installed.json','.runtime/current.json')){
        $marker=Join-Path $destination $protected
        [void][IO.Directory]::CreateDirectory((Split-Path $marker -Parent))
        if($protected -in @('data','staticfiles')){[void][IO.Directory]::CreateDirectory($marker)}else{[IO.File]::WriteAllText($marker,'private existing state')}
        Assert-Fails {Get-StagingCode $destination $commit 'GitHubFiles'}
        Assert-True (Test-Path -LiteralPath $marker) 'Protected state was removed.'
        if($protected -in @('data','staticfiles')){[IO.Directory]::Delete($marker)}else{[IO.File]::Delete($marker)}
    };$passed++
    $modified=Join-Path $destination 'manage.py';[IO.File]::WriteAllText($modified,'modified locally')
    Assert-Fails {Get-StagingCode $destination $commit 'GitHubFiles'}
    Assert-True ([IO.File]::ReadAllText($modified) -eq 'modified locally') 'Modified source was overwritten.'
    [IO.File]::WriteAllBytes($modified,$script:fixtureFiles['manage.py']);$passed++
    # Runtime directory aliases must never lead setup into a working environment.
    [IO.File]::Delete($venvMarker);[IO.Directory]::Delete($venv)
    $external=Join-Path $testFolder 'external';[void][IO.Directory]::CreateDirectory($external)
    $externalMarker=Join-Path $external 'preserve.txt';[IO.File]::WriteAllText($externalMarker,'external environment')
    if($env:OS -eq 'Windows_NT'){$linkType='Junction'}else{$linkType='SymbolicLink'}
    [void](New-Item -ItemType $linkType -Path $venv -Target $external)
    Assert-Fails {Get-StagingCode $destination $commit 'GitHubFiles'}
    Assert-True ([IO.File]::ReadAllText($externalMarker) -eq 'external environment') 'A linked environment was changed.'
    [IO.Directory]::Delete($venv);$passed++
    $sentinel=Join-Path $destination 'preserve';[IO.File]::WriteAllText($sentinel,'preserve')
    Assert-Fails {Get-StagingCode $destination $commit 'GitHubFiles'}
    Assert-True ([IO.File]::ReadAllText($sentinel) -eq 'preserve') 'Existing destination changed.';$passed++
    Assert-Fails {Get-StagingCode (Join-Path $testFolder 'bad-ref') 'main' 'GitHubFiles'};$passed++
    $script:truncated=$true
    Assert-Fails {Get-StagingCode (Join-Path $testFolder 'incomplete') $commit 'GitHubFiles'}
    $script:truncated=$false;$passed++
    $script:wrongIdentity=$true
    Assert-Fails {Get-StagingCode (Join-Path $testFolder 'wrong-release') $commit 'GitHubFiles'}
    $script:wrongIdentity=$false;$passed++
    foreach($bad in @('../escape.txt','/absolute.txt','C:/outside.txt','ops\outside.txt','ops/../escape.txt','ops/file:stream','ops/.. /escape.txt','NUL.txt','data/db.sqlite3','.env')){
        $script:fixtureTree=$originalTree+@([pscustomobject]@{path=$bad;sha='c'*40;mode='100644';type='blob';size=3})
        Assert-Fails {Get-StagingCode (Join-Path $testFolder 'bad-path') $commit 'GitHubFiles'}
    }
    $script:fixtureTree=$originalTree;$passed++
    $script:fixtureTree=$originalTree+@([pscustomobject]@{path='linked';sha='c'*40;mode='120000';type='blob';size=3})
    Assert-Fails {Get-StagingCode (Join-Path $testFolder 'link') $commit 'GitHubFiles'}
    $script:fixtureTree=$originalTree;$passed++
    $script:fixtureTree=$originalTree+@([pscustomobject]@{path='MANAGE.PY';sha='c'*40;mode='100644';type='blob';size=3})
    Assert-Fails {Get-StagingCode (Join-Path $testFolder 'case-alias') $commit 'GitHubFiles'}
    $script:fixtureTree=$originalTree;$passed++
    $oversized=@();foreach($number in 1..51){$oversized+=[pscustomobject]@{path=('static/large-'+$number);sha='c'*40;mode='100644';type='blob';size=10000000}}
    $script:fixtureTree=$originalTree+$oversized
    Assert-Fails {Get-StagingCode (Join-Path $testFolder 'too-large') $commit 'GitHubFiles'}
    $script:fixtureTree=$originalTree;$passed++
    $script:corrupt=$true
    $badDestination=Join-Path $testFolder 'corrupt'
    Assert-Fails {Get-StagingCode $badDestination $commit 'GitHubFiles'}
    Assert-True (-not (Test-Path $badDestination)) 'Corrupt download was activated.'
    Assert-True (@(Get-ChildItem -LiteralPath $testFolder -Filter '.yds-test-download-*').Count -eq 0) 'Incomplete temporary data was not removed.'
    $script:corrupt=$false;$passed++
    $production=Join-Path $testFolder 'production'
    Assert-Fails {Assert-SeparateStagingPaths $production $production}
    Assert-Fails {Assert-SeparateStagingPaths (Join-Path $production 'test') $production}
    Assert-Fails {Assert-SeparateStagingPaths $testFolder $production}
    Assert-SeparateStagingPaths (Join-Path $testFolder 'acceptance') $production;$passed++
    Write-Host "PASS: $passed PowerShell bootstrap scenarios (direct verified files, binary, immutable release, directory isolation and failure cleanup)"
}finally{
    Remove-Item -LiteralPath $testFolder -Recurse -Force
}
