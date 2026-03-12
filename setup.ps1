$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPath = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$requirementsPath = Join-Path $projectRoot "requirements.txt"
$requiredPythonMinor = "3.14"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python was not found on PATH. Install Python $requiredPythonMinor and rerun this script."
}

$pythonVersion = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to detect the Python version from PATH."
}

if ($pythonVersion.Trim() -ne $requiredPythonMinor) {
    throw "Python $requiredPythonMinor is required on PATH. Found Python $($pythonVersion.Trim())."
}

if (-not (Test-Path $requirementsPath)) {
    throw "requirements.txt was not found at $requirementsPath"
}

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment in $venvPath..."
    python -m venv $venvPath
} else {
    Write-Host "Using existing virtual environment in $venvPath..."
}

Write-Host "Upgrading pip..."
& $venvPython -m pip install --upgrade pip

Write-Host "Installing dependencies from requirements.txt..."
& $venvPython -m pip install -r $requirementsPath

Write-Host ""
Write-Host "Setup complete."
Write-Host "Run the app with:"
Write-Host "  .\.venv\Scripts\python.exe .\main.py"
