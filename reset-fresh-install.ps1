param(
    [switch]$KeepLogs
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$dataRoot = Join-Path $scriptRoot "data"

if (-not (Test-Path -LiteralPath $dataRoot)) {
    Write-Host "Data folder not found at $dataRoot"
    exit 1
}

$targets = @(
    (Join-Path $dataRoot "reader.db"),
    (Join-Path $dataRoot "reader.db-shm"),
    (Join-Path $dataRoot "reader.db-wal"),
    (Join-Path $dataRoot "download_history.json"),
    (Join-Path $dataRoot "scene_bookmarks"),
    (Join-Path $dataRoot "thumbnails"),
    (Join-Path $dataRoot "profiles"),
    (Join-Path $dataRoot "webengine"),
    (Join-Path $dataRoot "_download_temp"),
    (Join-Path $dataRoot "force_first_run.flag")
)

if (-not $KeepLogs) {
    $targets += (Join-Path $dataRoot "logs")
}

foreach ($target in $targets) {
    if (-not (Test-Path -LiteralPath $target)) {
        continue
    }

    $item = Get-Item -LiteralPath $target -Force
    if ($item.PSIsContainer) {
        Remove-Item -LiteralPath $target -Recurse -Force
        Write-Host "Removed folder: $target"
    }
    else {
        Remove-Item -LiteralPath $target -Force
        Write-Host "Removed file: $target"
    }
}

$foldersToCreate = @(
    (Join-Path $dataRoot "logs"),
    (Join-Path $dataRoot "profiles"),
    (Join-Path $dataRoot "scene_bookmarks"),
    (Join-Path $dataRoot "thumbnails")
)

foreach ($folder in $foldersToCreate) {
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
}

New-Item -ItemType File -Path (Join-Path $dataRoot "force_first_run.flag") -Force | Out-Null

Write-Host ""
Write-Host "Fresh-install reset complete."
Write-Host "Library folders and downloaded series were not changed."
Write-Host "Start the app again to see the first-run setup flow."
