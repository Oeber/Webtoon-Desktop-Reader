$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$activateScript = Join-Path $projectRoot ".venv\Scripts\Activate.ps1"

if (-not (Test-Path $activateScript)) {
    throw "Virtual environment not found at $activateScript. Run .\setup.ps1 first."
}

. $activateScript
