param([Parameter(Mandatory)][ValidateSet(1,2)][int]$Gpu)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$startupMutex = [Threading.Mutex]::new($false, 'Local\TensorMasterLaunch')
try { $ownsStartup = $startupMutex.WaitOne(60000) }
catch [Threading.AbandonedMutexException] { $ownsStartup = $true }
if (-not $ownsStartup) { $startupMutex.Dispose(); throw 'Another startup is in progress; recovery left the runtime untouched.' }
try {
$port = 8187 + $Gpu
# Called only after the controller verifies the failed task and its queue.
$owners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($ownerId in $owners) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ownerId"
    if ($process.Name -ne 'python.exe' -or $process.CommandLine -notmatch "main\.py.*--port $port(?:\s|$)") { throw "Port $port does not belong to the local runtime." }
    Stop-Process -Id $ownerId -Force
}
$pattern = "-m backend\.worker\s+--local-gpu $Gpu(?:\s|$)"
$agents = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object CommandLine -Match $pattern)
foreach ($agent in $agents) { Stop-Process -Id $agent.ProcessId -Force -ErrorAction SilentlyContinue }
$python = Join-Path $root '.venv312\Scripts\python.exe'
$env:TOKEN_FILE = (& $python -c 'from backend.app.config import get_settings; print(get_settings().token_file)').Trim()
& (Join-Path $PSScriptRoot 'local_gpus.ps1') -Gpu $Gpu -NoBrowser -NoSupervisor
} finally {
    $startupMutex.ReleaseMutex()
    $startupMutex.Dispose()
}
