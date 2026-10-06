param(
    [string]$SourcePath = ".",
    [string]$OutputFile = "code_export.txt",
    [string[]]$ExcludePatterns = @(),
    [switch]$IncludeHidden
)

$SourcePath = (Resolve-Path $SourcePath).Path
$OutputFile = (Join-Path (Get-Location) $OutputFile)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Code Export Tool" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Source : $SourcePath" -ForegroundColor Yellow
Write-Host "Output : $OutputFile" -ForegroundColor Yellow
Write-Host ""

if (-not (Test-Path $SourcePath -PathType Container)) {
    Write-Error "Directory not found: $SourcePath"
    exit 1
}

$OutputFileAbsolute = (Get-Item $OutputFile -ErrorAction SilentlyContinue).FullName

function Test-ShouldExclude {
    param([string]$FullPath)

    if ($OutputFileAbsolute) {
        $outParent = Split-Path $OutputFileAbsolute -Parent
        $fileParent = Split-Path $FullPath -Parent
        $outName = Split-Path $OutputFileAbsolute -Leaf
        $fileName = Split-Path $FullPath -Leaf
        if ($outParent -eq $fileParent -and $outName -eq $fileName) {
            return $true
        }
    }

    foreach ($pattern in $ExcludePatterns) {
        $name = Split-Path $FullPath -Leaf
        if ($name -like $pattern) { return $true }
        if ($FullPath -like "*\$pattern\*") { return $true }
    }

    if (-not $IncludeHidden) {
        $item = Get-Item $FullPath -Force -ErrorAction SilentlyContinue
        if ($item -and ($item.Attributes -band [System.IO.FileAttributes]::Hidden)) {
            return $true
        }
    }

    return $false
}

Write-Host "Scanning files..." -ForegroundColor Green

$allFiles = Get-ChildItem -Path $SourcePath -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object { -not (Test-ShouldExclude $_.FullName) } |
    Sort-Object FullName

$totalFiles = $allFiles.Count
Write-Host "Found files: $totalFiles" -ForegroundColor Green
Write-Host ""

if ($totalFiles -eq 0) {
    Write-Warning "No files found."
    exit 0
}

$separator = "=" * 80
$shortSeparator = "-" * 80

$header = $separator + "`n" +
    "CODE EXPORT" + "`n" +
    "Date: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" + "`n" +
    "Source: $SourcePath" + "`n" +
    "Total files: $totalFiles" + "`n" +
    $separator + "`n`n"

$header | Out-File -FilePath $OutputFile -Encoding UTF8 -Force

$processedCount = 0
$skippedCount = 0
$errorCount = 0
$totalBytes = 0

$binaryExtensions = @('.dll', '.exe', '.bin', '.obj', '.png', '.jpg', '.jpeg',
                      '.gif', '.bmp', '.ico', '.zip', '.rar', '.7z', '.tar',
                      '.gz', '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt',
                      '.pptx', '.mp3', '.mp4', '.avi', '.mov', '.woff', '.woff2',
                      '.ttf', '.eot', '.so', '.dylib', '.pyc', '.pyo', '.db',
                      '.sqlite', '.sqlite3', '.log', '.swp', '.swo')

foreach ($file in $allFiles) {
    $processedCount++
    $relativePath = $file.FullName.Substring($SourcePath.Length).TrimStart('\', '/')

    $percent = [math]::Round(($processedCount / $totalFiles) * 100, 1)
    Write-Progress -Activity "Processing" -Status "[$processedCount / $totalFiles] $relativePath" -PercentComplete $percent

    try {
        $ext = $file.Extension.ToLower()

        if ($ext -in $binaryExtensions) {
            $skippedCount++
            $content = "[Binary file skipped - size: $($file.Length) bytes]"
        } else {
            $content = Get-Content -Path $file.FullName -Raw -Encoding UTF8 -ErrorAction Stop
            $totalBytes += $file.Length
        }

        $fileBlock = "`n" + $separator + "`n" +
            "FILE: $relativePath" + "`n" +
            "Size: $($file.Length) bytes | Modified: $($file.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'))" + "`n" +
            $shortSeparator + "`n" +
            $content + "`n"

        $fileBlock | Out-File -FilePath $OutputFile -Encoding UTF8 -Append

    } catch {
        $errorCount++
        $errorMsg = "[ERROR reading: $_]"
        $errorBlock = "`n" + $separator + "`n" +
            "FILE: $relativePath" + "`n" +
            $shortSeparator + "`n" +
            $errorMsg + "`n"
        $errorBlock | Out-File -FilePath $OutputFile -Encoding UTF8 -Append
    }
}

Write-Progress -Activity "Processing" -Completed

$outputSize = (Get-Item $OutputFile).Length
$outputSizeMB = [math]::Round($outputSize / 1MB, 2)

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Done!" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Processed : $processedCount" -ForegroundColor Green
Write-Host "Skipped   : $skippedCount" -ForegroundColor Yellow
Write-Host "Errors    : $errorCount" -ForegroundColor $(if ($errorCount -gt 0) { "Red" } else { "Green" })
Write-Host "Source    : $([math]::Round($totalBytes / 1KB, 2)) KB" -ForegroundColor Gray
Write-Host "Output    : $outputSizeMB MB" -ForegroundColor Gray
Write-Host ""
Write-Host "Saved to: $OutputFile" -ForegroundColor Green


