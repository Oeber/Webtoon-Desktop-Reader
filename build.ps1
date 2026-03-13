param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Version
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$distRoot = Join-Path $projectRoot "dist"
$buildRoot = Join-Path $projectRoot "build"
$specPath = Join-Path $projectRoot "main.spec"
$exeName = "Webtoon Desktop Reader.exe"
$onefileExe = Join-Path $distRoot $exeName
$legacyOnedirRoot = Join-Path $distRoot "main"
$outputScraperRoot = Join-Path $distRoot "scrapers"
$outputScrapers = Join-Path $outputScraperRoot "sites"
$sourceScraperRoot = Join-Path $projectRoot "scrapers"
$sourceScrapers = Join-Path $projectRoot "scrapers\sites"
$outputDiscoveryScrapers = Join-Path $outputScraperRoot "discovery_sites"
$sourceDiscoveryScrapers = Join-Path $projectRoot "scrapers\discovery_sites"
$outputWebtoons = Join-Path $distRoot "webtoons"
$archiveName = "Webtoon-Desktop-Reader-v$Version.zip"
$archivePath = Join-Path $projectRoot $archiveName
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

if (Test-Path $distRoot) {
    Write-Host "Removing previous dist directory..."
    Remove-Item $distRoot -Recurse -Force
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

New-Item -ItemType Directory -Force -Path $outputScraperRoot | Out-Null
New-Item -ItemType Directory -Force -Path $outputScrapers | Out-Null
New-Item -ItemType Directory -Force -Path $outputDiscoveryScrapers | Out-Null
New-Item -ItemType Directory -Force -Path $outputWebtoons | Out-Null

Copy-Item (Join-Path $sourceScraperRoot "__init__.py") (Join-Path $outputScraperRoot "__init__.py") -Force
Get-ChildItem $sourceScraperRoot -File -Filter *.py | Where-Object { $_.Name -notin @("__init__.py", "registry.py", "discovery_registry.py") } | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $outputScraperRoot $_.Name) -Force
}

Copy-Item (Join-Path $sourceScrapers "__init__.py") (Join-Path $outputScrapers "__init__.py") -Force
Get-ChildItem $sourceScrapers -Filter *.py | Where-Object { $_.Name -ne "__init__.py" } | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $outputScrapers $_.Name) -Force
}

Copy-Item (Join-Path $sourceDiscoveryScrapers "__init__.py") (Join-Path $outputDiscoveryScrapers "__init__.py") -Force
Get-ChildItem $sourceDiscoveryScrapers -Filter *.py | Where-Object { $_.Name -ne "__init__.py" } | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $outputDiscoveryScrapers $_.Name) -Force
}

if (Test-Path $archivePath) {
    Write-Host "Removing previous archive $archiveName..."
    Remove-Item $archivePath -Force
}

Write-Host "Creating archive $archiveName..."
Compress-Archive -Path (Join-Path $distRoot "*") -DestinationPath $archivePath -CompressionLevel Optimal

if (-not (Test-Path $archivePath)) {
    throw "Build archive was not created at $archivePath"
}

Write-Host ""
Write-Host "Build complete."
Write-Host "Run:"
Write-Host "  .\dist\$exeName"
Write-Host "Archive:"
Write-Host "  .\$archiveName"
Write-Host ""
Write-Host "Editable scraper folders:"
Write-Host "  .\dist\scrapers\sites"
Write-Host "  .\dist\scrapers\discovery_sites"
