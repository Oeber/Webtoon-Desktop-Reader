param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$v
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$distRoot = Join-Path $projectRoot "dist"
$buildRoot = Join-Path $projectRoot "build"
$logoPngPath = Join-Path $projectRoot "imgs\logo.png"
$setupIconPath = Join-Path $buildRoot "installer-icon.ico"
$specPath = Join-Path $projectRoot "main.spec"
$updaterSpecPath = Join-Path $projectRoot "update_helper.spec"
$installerScriptPath = Join-Path $projectRoot "installer.iss"
$updaterExeName = "Webtoon Desktop Reader Updater.exe"
$updaterExePath = Join-Path $distRoot $updaterExeName
$appVersionPath = Join-Path $projectRoot "data\app_version.txt"
$exeName = "Webtoon Desktop Reader.exe"
$onefileExe = Join-Path $distRoot $exeName
$legacyOnedirRoot = Join-Path $distRoot "main"
$outputScraperRoot = Join-Path $distRoot "scrapers"
$outputScrapers = Join-Path $outputScraperRoot "sites"
$sourceScraperRoot = Join-Path $projectRoot "scrapers"
$sourceScrapers = Join-Path $projectRoot "scrapers\sites"
$outputDiscoveryScrapers = Join-Path $outputScraperRoot "discovery_sites"
$sourceDiscoveryScrapers = Join-Path $projectRoot "scrapers\discovery_sites"
$outputSiteSettings = Join-Path $outputScraperRoot "site_settings"
$sourceSiteSettings = Join-Path $projectRoot "scrapers\site_settings"
$outputWebtoons = Join-Path $distRoot "webtoons"
$version = $v.Trim()
if ($version.StartsWith("v")) {
    $version = $version.Substring(1)
}

if ($version -notmatch '^\d+(?:\.\d+)+$') {
    throw "Version must look like 1.0.0 or 0.9.5. Received '$v'."
}

$setupExeName = "Webtoon-Desktop-Reader-Setup-v$version.exe"
$setupExePath = Join-Path $projectRoot $setupExeName
$portableArchiveName = "Webtoon-Desktop-Reader-v$version-portable.zip"
$portableArchivePath = Join-Path $projectRoot $portableArchiveName
$installerArchiveName = "Webtoon-Desktop-Reader-v$version-installer.zip"
$installerArchivePath = Join-Path $projectRoot $installerArchiveName
$requiredPythonMinor = "3.14"

function Resolve-InnoSetupCompiler {
    $candidates = @()

    if ($env:ISCC_PATH) {
        $candidates += $env:ISCC_PATH
    }

    if ($env:ProgramFiles) {
        $candidates += Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"
    }

    if ($env:ProgramFiles -and (Test-Path (Join-Path $env:ProgramFiles "x86"))) {
        $candidates += Join-Path (Join-Path $env:ProgramFiles "x86") "Inno Setup 6\ISCC.exe"
    }

    if (${env:ProgramFiles(x86)}) {
        $candidates += Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
    }

    foreach ($candidate in $candidates | Where-Object { $_ } | Select-Object -Unique) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    $isccCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($isccCommand) {
        return $isccCommand.Source
    }

    throw "Inno Setup compiler not found. Install Inno Setup 6 or set ISCC_PATH to ISCC.exe."
}

function Update-InnoSetupVersion {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath,

        [Parameter(Mandatory = $true)]
        [string]$Version
    )

    $content = Get-Content $ScriptPath -Raw
    $updated = $content
    $updated = [regex]::Replace($updated, '(?m)^AppVersion=.*$', "AppVersion=$Version")
    $updated = [regex]::Replace($updated, '(?m)^;AppVerName=.*$', ";AppVerName=Webtoon Desktop Reader $Version")
    $updated = [regex]::Replace($updated, '(?m)^OutputBaseFilename=.*$', "OutputBaseFilename=Webtoon-Desktop-Reader-Setup-v$Version")

    if ($content -notmatch '(?m)^AppVersion=' -or $content -notmatch '(?m)^OutputBaseFilename=') {
        throw "Installer script is missing required version fields in $ScriptPath"
    }

    Set-Content -Path $ScriptPath -Value $updated -Encoding utf8
}

function New-IcoFromPng {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PngPath,

        [Parameter(Mandatory = $true)]
        [string]$IcoPath
    )

    Add-Type -AssemblyName System.Drawing

    $sourceImage = $null
    $bitmap = $null
    $graphics = $null
    $memoryStream = $null
    $fileStream = $null
    $writer = $null

    try {
        $sourceImage = [System.Drawing.Image]::FromFile($PngPath)
        $bitmap = New-Object System.Drawing.Bitmap 256, 256
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        $graphics.Clear([System.Drawing.Color]::Transparent)
        $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
        $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
        $graphics.DrawImage($sourceImage, 0, 0, 256, 256)

        $memoryStream = New-Object System.IO.MemoryStream
        $bitmap.Save($memoryStream, [System.Drawing.Imaging.ImageFormat]::Png)
        $pngBytes = $memoryStream.ToArray()

        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $IcoPath) | Out-Null
        $fileStream = [System.IO.File]::Open($IcoPath, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write)
        $writer = New-Object System.IO.BinaryWriter($fileStream)

        $writer.Write([UInt16]0)
        $writer.Write([UInt16]1)
        $writer.Write([UInt16]1)
        $writer.Write([byte]0)
        $writer.Write([byte]0)
        $writer.Write([byte]0)
        $writer.Write([byte]0)
        $writer.Write([UInt16]1)
        $writer.Write([UInt16]32)
        $writer.Write([UInt32]$pngBytes.Length)
        $writer.Write([UInt32]22)
        $writer.Write($pngBytes)
    }
    finally {
        if ($writer) { $writer.Dispose() }
        elseif ($fileStream) { $fileStream.Dispose() }
        if ($memoryStream) { $memoryStream.Dispose() }
        if ($graphics) { $graphics.Dispose() }
        if ($bitmap) { $bitmap.Dispose() }
        if ($sourceImage) { $sourceImage.Dispose() }
    }
}

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
if (-not (Test-Path $updaterSpecPath)) {
    throw "PyInstaller spec not found at $updaterSpecPath"
}
if (-not (Test-Path $installerScriptPath)) {
    throw "Inno Setup script not found at $installerScriptPath"
}
if (-not (Test-Path $logoPngPath)) {
    throw "App icon source not found at $logoPngPath"
}

$isccPath = Resolve-InnoSetupCompiler


New-Item -ItemType Directory -Force -Path (Split-Path -Parent $appVersionPath) | Out-Null
Set-Content -Path $appVersionPath -Value $version -Encoding utf8
Write-Host "App version set to $version"
Update-InnoSetupVersion -ScriptPath $installerScriptPath -Version $version
Write-Host "Installer script version set to $version"

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
if (Test-Path $updaterExePath) {
    Write-Host "Removing previous updater executable..."
    Remove-Item $updaterExePath -Force
}
if (Test-Path $setupExePath) {
    Write-Host "Removing previous installer executable..."
    Remove-Item $setupExePath -Force
}

Write-Host "Generating installer icon from app icon..."
New-IcoFromPng -PngPath $logoPngPath -IcoPath $setupIconPath


Write-Host "Building onefile executable..."
& $venvPython -m PyInstaller --clean --noconfirm $specPath

Write-Host "Building updater helper executable..."
& $venvPython -m PyInstaller --clean --noconfirm $updaterSpecPath

if (-not (Test-Path $updaterExePath)) {
    throw "Build did not produce $updaterExePath"
}

if (-not (Test-Path $onefileExe)) {
    throw "Build did not produce $onefileExe"
}

New-Item -ItemType Directory -Force -Path $outputScraperRoot | Out-Null
New-Item -ItemType Directory -Force -Path $outputScrapers | Out-Null
New-Item -ItemType Directory -Force -Path $outputDiscoveryScrapers | Out-Null
New-Item -ItemType Directory -Force -Path $outputSiteSettings | Out-Null
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

Copy-Item (Join-Path $sourceSiteSettings "__init__.py") (Join-Path $outputSiteSettings "__init__.py") -Force
Get-ChildItem $sourceSiteSettings -Filter *.py | Where-Object { $_.Name -ne "__init__.py" } | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $outputSiteSettings $_.Name) -Force
}

Write-Host "Adding app_version.txt to archive..."
$outputDataDir = Join-Path $distRoot "data"
New-Item -ItemType Directory -Force -Path $outputDataDir | Out-Null
Copy-Item $appVersionPath (Join-Path $outputDataDir "app_version.txt") -Force

Write-Host "Building installer with Inno Setup..."
& $isccPath `
    "/DOutputDir=$projectRoot" `
    "/DDistDir=$distRoot" `
    "/DSetupIconFile=$setupIconPath" `
    $installerScriptPath

if (-not (Test-Path $setupExePath)) {
    throw "Installer was not created at $setupExePath"
}

if (Test-Path $portableArchivePath) {
    Write-Host "Removing previous archive $portableArchiveName..."
    Remove-Item $portableArchivePath -Force
}

if (Test-Path $installerArchivePath) {
    Write-Host "Removing previous archive $installerArchiveName..."
    Remove-Item $installerArchivePath -Force
}

Write-Host "Creating portable archive $portableArchiveName..."
Compress-Archive -Path (Join-Path $distRoot "*") -DestinationPath $portableArchivePath -CompressionLevel Optimal

if (-not (Test-Path $portableArchivePath)) {
    throw "Portable build archive was not created at $portableArchivePath"
}

Write-Host "Creating installer archive $installerArchiveName..."
Compress-Archive -Path $setupExePath -DestinationPath $installerArchivePath -CompressionLevel Optimal

if (-not (Test-Path $installerArchivePath)) {
    throw "Installer archive was not created at $installerArchivePath"
}

Write-Host ""
Write-Host "Build complete."
Write-Host "Run:"
Write-Host "  .\dist\$exeName"
Write-Host "Installer:"
Write-Host "  .\$setupExeName"
Write-Host "Portable archive:"
Write-Host "  .\$portableArchiveName"
Write-Host "Installer archive:"
Write-Host "  .\$installerArchiveName"
Write-Host ""
Write-Host "Editable scraper folders:"
Write-Host "  .\dist\scrapers\sites"
Write-Host "  .\dist\scrapers\discovery_sites"
Write-Host "  .\dist\scrapers\site_settings"
