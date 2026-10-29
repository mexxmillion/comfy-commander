param([int]$Port = 8000)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Host 'Creating the private Python environment…'
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -m venv .venv
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $version = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        if ($version -ne '3.12') { throw "Python 3.12 is required; python currently resolves to $version." }
        & python -m venv .venv
    } else {
        throw 'Install Python 3.12, then run this script again.'
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python 3.12 environment.' }
}

Write-Host 'Installing Comfy Commander…'
& $venvPython -m pip install --disable-pip-version-check -e .
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }

foreach ($path in @('state\credentials', 'state\comfy-input', 'state\cloud-outputs', 'models')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot $path) | Out-Null
}

if (-not $env:APP_SECRET_KEY) { $env:APP_SECRET_KEY = [guid]::NewGuid().ToString('N') }
Write-Host ''
Write-Host "Open http://127.0.0.1:$Port/setup"
Write-Host 'This first run has no provider credentials, jobs, workers, or bundled private models.'
Write-Host 'Press Ctrl+C to stop Comfy Commander.'
& $venvPython -m uvicorn backend.app.main:app --host 127.0.0.1 --port $Port
