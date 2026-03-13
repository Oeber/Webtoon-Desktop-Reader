param(
    [string]$Name,
    [ValidateSet("wall", "cpu")]
    [string]$Clock = "wall",
    [ValidateSet("ttot", "tsub", "tavg", "ncall", "name")]
    [string]$Sort = "ttot",
    [int]$Limit = 75,
    [switch]$Builtins,
    [string[]]$AppArgs
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$profileDir = Join-Path $projectRoot "data\profiles"
$requiredPythonMinor = "3.14"

if (-not (Test-Path $venvPython)) {
    throw "Virtual environment not found at $venvPython. Run .\setup.ps1 first."
}

$venvVersion = & $venvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to detect the virtual environment Python version."
}

if ($venvVersion.Trim() -ne $requiredPythonMinor) {
    throw "The virtual environment must use Python $requiredPythonMinor. Found Python $($venvVersion.Trim()). Recreate it with .\setup.ps1."
}

New-Item -ItemType Directory -Force -Path $profileDir | Out-Null

if ([string]::IsNullOrWhiteSpace($Name)) {
    $Name = "session-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
}

$safeName = ($Name -replace "[^A-Za-z0-9_-]", "-").Trim("-_")
if ([string]::IsNullOrWhiteSpace($safeName)) {
    $safeName = "session"
}

$mainPath = Join-Path $projectRoot "main.py"

$arguments = @(
    $mainPath
    "--profile"
    "--profile-name", $safeName
    "--profile-clock", $Clock
    "--profile-sort", $Sort
    "--profile-limit", $Limit
)

if ($Builtins) {
    $arguments += "--profile-builtins"
}

if ($AppArgs) {
    $arguments += $AppArgs
}

Write-Host "Starting profiled app session..."
Write-Host "Profile name: $safeName"
Write-Host "Output directory: $profileDir"
Write-Host "Close the app to finish profiling."

& $venvPython $arguments

if ($LASTEXITCODE -ne 0) {
    throw "Profiled app run failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "Profile complete:"
Write-Host "  $(Join-Path $profileDir "$safeName.functions.txt")"
Write-Host "  $(Join-Path $profileDir "$safeName.threads.txt")"
Write-Host "  $(Join-Path $profileDir "$safeName.callgrind")"
Write-Host "  $(Join-Path $profileDir "$safeName.pstat")"
