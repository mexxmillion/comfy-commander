param([int]$Port = 8014)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$demoRoot = Join-Path $projectRoot 'state\test-drive'
foreach ($name in @('credentials', 'models', 'comfy-input', 'outputs')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $demoRoot $name) | Out-Null
}

$env:APP_ENV = 'test-drive'
$env:APP_SECRET_KEY = [guid]::NewGuid().ToString('N')
$env:DATABASE_URL = 'sqlite:///' + ((Join-Path $demoRoot 'commander.sqlite3') -replace '\\','/')
$env:TOKEN_FILE = Join-Path $demoRoot 'credentials\identity.sqlite3'
$env:RUNPOD_API_KEY_FILE = Join-Path $demoRoot 'credentials\runpod'
$env:VAST_API_KEY_FILE = Join-Path $demoRoot 'credentials\vast'
$env:HUGGINGFACE_TOKEN_FILE = Join-Path $demoRoot 'credentials\huggingface'
$env:COMMUNITY_LORA_MANIFEST = Join-Path $demoRoot 'community-loras.json'
$env:MODEL_DIRECTORIES = '["' + ((Join-Path $demoRoot 'models') -replace '\\','/') + '"]'
$env:COMFYUI_INPUT_DIR = Join-Path $demoRoot 'comfy-input'
$env:CLOUD_OUTPUT_DIR = Join-Path $demoRoot 'outputs'
$env:COMFYUI_URL = 'http://127.0.0.1:18188'

$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }
Write-Host "Comfy Commander clean-room test drive: http://127.0.0.1:$Port/setup"
Write-Host 'No production credentials, models, jobs, or workers are used.'
& $python -m uvicorn backend.app.main:app --host 127.0.0.1 --port $Port
