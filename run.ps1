$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$exePath = Join-Path $projectRoot "dist\Webtoon Desktop Reader.exe"
$logPath = Join-Path $projectRoot "dist\data\logs\current.log"

if (-not (Test-Path $exePath)) {
    throw "Built executable not found at $exePath. Run .\build.ps1 first."
}

try {
    & $exePath
} catch {
    Write-Host ""
    Write-Host "Application failed to launch."
    if (Test-Path $logPath) {
        Write-Host "Packaged log:"
        Write-Host "  $logPath"
        Write-Host ""
        Write-Host "Last 40 log lines:"
        Get-Content $logPath -Tail 40
    } else {
        Write-Host "No packaged log found at $logPath"
    }
    throw
}
