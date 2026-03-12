$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$distRoot = Join-Path $projectRoot "dist"
$buildRoot = Join-Path $projectRoot "build"
$specPath = Join-Path $projectRoot "main.spec"
$onefileExe = Join-Path $distRoot "main.exe"
$legacyOnedirRoot = Join-Path $distRoot "main"
$outputScrapers = Join-Path $distRoot "scrapers\sites"
$sourceScrapers = Join-Path $projectRoot "scrapers\sites"
$outputWebtoons = Join-Path $distRoot "webtoons"
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

if (-not (Test-Path $specPath)) {
    throw "PyInstaller spec not found at $specPath"
}

Write-Host "Installing or upgrading PyInstaller..."
& $venvPython -m pip install --upgrade pyinstaller

if (Test-Path $buildRoot) {
    Write-Host "Removing previous build directory..."
    Remove-Item $buildRoot -Recurse -Force
}

if (Test-Path $legacyOnedirRoot) {
    Write-Host "Removing previous onedir output..."
    Remove-Item $legacyOnedirRoot -Recurse -Force
}

if (Test-Path $onefileExe) {
    Write-Host "Removing previous onefile executable..."
    Remove-Item $onefileExe -Force
}

Write-Host "Building onefile executable..."
& $venvPython -m PyInstaller --clean --noconfirm $specPath

if (-not (Test-Path $onefileExe)) {
    throw "Build did not produce $onefileExe"
}

New-Item -ItemType Directory -Force -Path $outputScrapers | Out-Null
New-Item -ItemType Directory -Force -Path $outputWebtoons | Out-Null

Copy-Item (Join-Path $sourceScrapers "__init__.py") (Join-Path $outputScrapers "__init__.py") -Force
Get-ChildItem $sourceScrapers -Filter *.py | Where-Object { $_.Name -ne "__init__.py" } | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $outputScrapers $_.Name) -Force
}

Write-Host ""
Write-Host "Build complete."
Write-Host "Run:"
Write-Host "  .\dist\main.exe"
Write-Host ""
Write-Host "Editable scrapers folder:"
Write-Host "  .\dist\scrapers\sites"
