<#
.SYNOPSIS
    Собирает содержимое всех файлов каталога (рекурсивно) в один текстовый файл.

.DESCRIPTION
    Рекурсивно обходит указанный каталог и записывает содержимое каждого файла
    в единый выходной файл с разделителями и именами файлов.
    Автоматически исключает сам выходной файл из обработки.

.PARAMETER SourcePath
    Путь к исходному каталогу. По умолчанию — текущая директория.

.PARAMETER OutputFile
    Путь к выходному файлу. По умолчанию — "code_export.txt" в текущей директории.

.PARAMETER ExcludePatterns
    Массив шаблонов для исключения файлов/папок (например: @("*.dll", "bin", "obj")).

.PARAMETER Encoding
    Кодировка выходного файла. По умолчанию UTF8.

.PARAMETER IncludeHidden
    Включать скрытые файлы и папки. По умолчанию — нет.

.EXAMPLE
    .\Export-CodeToSingleFile.ps1 -SourcePath "C:\MyProject" -OutputFile "C:\export.txt"

.EXAMPLE
    .\Export-CodeToSingleFile.ps1 -ExcludePatterns @("*.dll", "*.exe", "node_modules", ".git")
#>

param(
    [Parameter(Mandatory = $false)]
    [string]$SourcePath = ".",

    [Parameter(Mandatory = $false)]
    [string]$OutputFile = "code_export.txt",

    [Parameter(Mandatory = $false)]
    [string[]]$ExcludePatterns = @(),

    [Parameter(Mandatory = $false)]
    [string]$Encoding = "UTF8",

    [Parameter(Mandatory = $false)]
    [switch]$IncludeHidden
)

# =============================================================================
# Инициализация
# =============================================================================

# Приводим пути к абсолютным
$SourcePath = (Resolve-Path $SourcePath).Path
$OutputFile = (Join-Path (Get-Location) $OutputFile)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Экспорт файлов в единый документ" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Исходный каталог : $SourcePath" -ForegroundColor Yellow
Write-Host "Выходной файл    : $OutputFile" -ForegroundColor Yellow
Write-Host "Кодировка        : $Encoding" -ForegroundColor Yellow
Write-Host ""

# Проверяем существование каталога
if (-not (Test-Path $SourcePath -PathType Container)) {
    Write-Error "Каталог не найден: $SourcePath"
    exit 1
}

# Получаем абсолютный путь выходного файла для корректного исключения
$OutputFileAbsolute = (Get-Item $OutputFile -ErrorAction SilentlyContinue).FullName

# =============================================================================
# Функция проверки: нужно ли исключить файл/папку
# =============================================================================
function Test-ShouldExclude {
    param([string]$FullPath)

    # Исключаем сам выходной файл
    if ($OutputFileAbsolute -and (Split-Path $OutputFileAbsolute -Parent) -eq (Split-Path $FullPath -Parent)) {
        if ((Split-Path $OutputFileAbsolute -Leaf) -eq (Split-Path $FullPath -Leaf)) {
            return $true
        }
    }

    # Исключаем по шаблонам
    foreach ($pattern in $ExcludePatterns) {
        $name = Split-Path $FullPath -Leaf
        if ($name -like $pattern) { return $true }
        if ($FullPath -like "*\$pattern\*") { return $true }
    }

    # Исключаем скрытые, если не запрошено
    if (-not $IncludeHidden) {
        $item = Get-Item $FullPath -Force -ErrorAction SilentlyContinue
        if ($item -and ($item.Attributes -band [System.IO.FileAttributes]::Hidden)) {
            return $true
        }
    }

    return $false
}

# =============================================================================
# Сбор файлов
# =============================================================================
Write-Host "Сканирование файлов..." -ForegroundColor Green

$allFiles = Get-ChildItem -Path $SourcePath -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object { -not (Test-ShouldExclude $_.FullName) } |
    Sort-Object FullName

$totalFiles = $allFiles.Count
Write-Host "Найдено файлов: $totalFiles" -ForegroundColor Green
Write-Host ""

if ($totalFiles -eq 0) {
    Write-Warning "Файлы не найдены. Проверьте путь и фильтры."
    exit 0
}

# =============================================================================
# Запись в файл
# =============================================================================
Write-Host "Запись в файл..." -ForegroundColor Green

$separator = "=" * 80
$shortSeparator = "-" * 80

# Заголовок
$header = @"
$separator
ЭКСПОРТ ФАЙЛОВ
Дата: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
Исходный каталог: $SourcePath
Всего файлов: $totalFiles
$separator

"@

# Записываем заголовок
$header | Out-File -FilePath $OutputFile -Encoding $Encoding -Force

$processedCount = 0
$skippedCount = 0
$errorCount = 0
$totalBytes = 0

foreach ($file in $allFiles) {
    $processedCount++
    $relativePath = $file.FullName.Substring($SourcePath.Length).TrimStart('\', '/')

    # Прогресс
    $percent = [math]::Round(($processedCount / $totalFiles) * 100, 1)
    Write-Progress -Activity "Обработка файлов" `
                   -Status "[$processedCount / $totalFiles] $relativePath" `
                   -PercentComplete $percent

    try {
        # Проверяем, не бинарный ли файл (по расширению)
        $binaryExtensions = @('.dll', '.exe', '.bin', '.obj', '.png', '.jpg', '.jpeg',
                              '.gif', '.bmp', '.ico', '.zip', '.rar', '.7z', '.tar',
                              '.gz', '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt',
                              '.pptx', '.mp3', '.mp4', '.avi', '.mov', '.woff', '.woff2',
                              '.ttf', '.eot', '.so', '.dylib', '.pyc', '.pyo')
        $ext = $file.Extension.ToLower()

        if ($ext -in $binaryExtensions) {
            $skippedCount++
            $content = "[Бинарный файл пропущен — размер: $($file.Length) байт]"
        } else {
            # Читаем содержимое
            $content = Get-Content -Path $file.FullName -Raw -Encoding UTF8 -ErrorAction Stop
            $totalBytes += $file.Length
        }

        # Формируем блок файла
        $fileBlock = @"

$separator
ФАЙЛ: $relativePath
Размер: $($file.Length) байт | Изменён: $($file.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'))
$shortSeparator
$content
"@

        # Дописываем в файл
        $fileBlock | Out-File -FilePath $OutputFile -Encoding $Encoding -Append

    } catch {
        $errorCount++
        $errorMsg = "[ОШИБКА чтения: $_]"
        $errorBlock = @"

$separator
ФАЙЛ: $relativePath
$shortSeparator
$errorMsg
"@
        $errorBlock | Out-File -FilePath $OutputFile -Encoding $Encoding -Append
    }
}

Write-Progress -Activity "Обработка файлов" -Completed

# =============================================================================
# Итоговая статистика
# =============================================================================
$outputSize = (Get-Item $OutputFile).Length
$outputSizeMB = [math]::Round($outputSize / 1MB, 2)

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Готово!" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Обработано файлов : $processedCount" -ForegroundColor Green
Write-Host "Пропущено (бинар.) : $skippedCount" -ForegroundColor Yellow
Write-Host "Ошибок чтения     : $errorCount" -ForegroundColor $(if ($errorCount -gt 0) { "Red" } else { "Green" })
Write-Host "Размер исходных   : $([math]::Round($totalBytes / 1KB, 2)) КБ" -ForegroundColor Gray
Write-Host "Размер результата : $outputSizeMB МБ" -ForegroundColor Gray
Write-Host ""
Write-Host "Файл сохранён: $OutputFile" -ForegroundColor Green