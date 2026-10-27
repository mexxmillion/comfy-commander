# Called under launch.ps1's startup mutex with the existing protected credentials.
param(
    [AllowEmptyString()][ValidateSet('','1','2','Both')][string]$Gpu = '',
    [switch]$Stop, [switch]$Restart, [switch]$Status,
    [switch]$WorkersOnly, [switch]$NoBrowser, [switch]$NoSupervisor
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv312\Scripts\python.exe'
$state = Join-Path $root 'state'
$selectionFile = Join-Path $state 'local-gpus.json'
$memoryProfileFile = Join-Path $state 'local-memory-profile.json'
$memoryArguments = @(& (Join-Path $PSScriptRoot 'local_memory_profile.ps1') -Path $memoryProfileFile)
$comfyRoot = if ($env:COMFYUI_ROOT) { $env:COMFYUI_ROOT } else { Join-Path (Split-Path $root -Parent) 'ComfyUI' }
$selected = @(if (Test-Path $selectionFile) { (Get-Content $selectionFile -Raw | ConvertFrom-Json).enabled } else { 1 })
$targets = if ($Gpu -eq 'Both') { @(1,2) } elseif ($Gpu) { @([int]$Gpu) } else { @($selected) }
if ($Stop -and -not $Gpu) { $targets = @(1,2) }
function Save-Selection {
    @{enabled=@($script:selected | Sort-Object -Unique)} | ConvertTo-Json | Set-Content "$selectionFile.tmp"
    Move-Item -LiteralPath "$selectionFile.tmp" -Destination $selectionFile -Force
}
function Processes([string]$pattern) {
    Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python|pythonw|pwsh)\.exe$' -and $_.CommandLine -match $pattern }
}
function Stop-Tree([int]$processId) {
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$processId")
    foreach ($child in $children) { Stop-Tree $child.ProcessId }
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
}
function Listening([int]$port) { [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) }
function Ready([string]$url, [int]$seconds, [hashtable]$headers = @{}) {
    $deadline = (Get-Date).AddSeconds($seconds)
    do {
        try { if ((Invoke-WebRequest $url -Headers $headers -TimeoutSec 3).StatusCode -eq 200) { return $true } } catch {}
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    return $false
}
function Worker-Pattern([int]$number) {
    if ($number -eq 1) { return '-m backend\.worker(?:\s+--local-gpu 1(?:\s|$)|\s*$)' }
    return '-m backend\.worker\s+--local-gpu 2(?:\s|$)'
}
function Stop-Gpu([int]$number) {
    $port = 8187 + $number
    New-Item -ItemType File -Path (Join-Path $state "gpu$number.drain") -Force | Out-Null
    # Let an in-flight claim finish before checking leases and the Comfy queue.
    Start-Sleep -Seconds 4
    & $python (Join-Path $PSScriptRoot 'service_check.py') --idle --gpu $number
    if ($LASTEXITCODE) { throw "GPU $number is rendering. Draining enabled; retry Stop after it finishes, or Start to resume claims." }
    if (Listening $port) {
        $queue = Invoke-RestMethod "http://127.0.0.1:$port/queue" -TimeoutSec 5
        if ($queue.queue_running.Count -or $queue.queue_pending.Count) { throw "GPU $number ComfyUI queue is busy; finish/cancel it and retry Stop." }
    }
    $script:selected = @($script:selected | Where-Object { $_ -ne $number })
    Save-Selection
    foreach ($process in @(Processes (Worker-Pattern $number))) { Stop-Tree $process.ProcessId }
    foreach ($ownerId in @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)) {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$ownerId"
        if (-not $owner) { continue }
        if ($owner.Name -ne 'python.exe' -or $owner.CommandLine -notmatch 'main\.py') { throw "Port $port belongs to another app; left untouched." }
        Stop-Tree $owner.ProcessId
    }
    foreach ($process in @(Processes "run_comfyui_secure\.ps1.*--port $port(?:\s|$)")) { Stop-Tree $process.ProcessId }
    Write-Host "GPU $number worker and ComfyUI stopped."
}
if ($Status) {
    & nvidia-smi --query-gpu=index,uuid,pci.bus_id,name,temperature.gpu,fan.speed,utilization.gpu,memory.used,power.draw,power.limit --format=csv
    Write-Host 'GPU 1 = NVIDIA index 0; GPU 2 = NVIDIA index 1'
    Write-Host "Enabled workers: $($selected -join ', ')"
    & $python (Join-Path $PSScriptRoot 'service_check.py')
    return
}
if ($Stop -or $Restart) {
    foreach ($number in $targets) { Stop-Gpu $number }
    if (-not $WorkersOnly) {
        foreach ($pattern in @('tools[\\/]supervise\.py','uvicorn backend\.app\.main')) {
            foreach ($process in @(Processes $pattern)) { Stop-Tree $process.ProcessId }
        }
    }
    if ($Stop) { return }
}
foreach ($number in $targets) {
    $selected = @((@($selected) + @($number)) | Sort-Object -Unique)
    Remove-Item -LiteralPath (Join-Path $state "gpu$number.drain") -ErrorAction SilentlyContinue
}
Save-Selection
$headers = @{Authorization = 'Bearer ' + (Get-Content -LiteralPath $env:TOKEN_FILE -Raw).Trim()}
if (-not (Listening 8000)) {
    Start-Process -FilePath $python -WorkingDirectory $root -WindowStyle Hidden `
        -ArgumentList '-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port','8000' `
        -RedirectStandardOutput (Join-Path $state 'tensor-master-session.log') -RedirectStandardError (Join-Path $state 'tensor-master-session.err.log') | Out-Null
}
if (-not (Ready 'http://127.0.0.1:8000/api/v1/creative' 60 $headers)) { throw 'Tensor Master readiness failed.' }
foreach ($number in $selected) {
    $port = 8187 + $number
    $device = $number - 1
    if (-not (Listening $port)) {
        # Secure startup imports nodes before binding the port. A supervisor
        # must wait for that process instead of starting a second CUDA runtime.
        if (Processes "main\.py.*--port $port(?:\s|$)") {
            Write-Host "GPU $number runtime is already starting; waiting for readiness..."
            continue
        }
        $gpuInfo = @(& nvidia-smi --query-gpu=uuid --format=csv,noheader)
        if ($LASTEXITCODE -or $gpuInfo.Count -le $device) { throw "GPU $number is unavailable." }
        # Stable physical identity despite CUDA/NVIDIA index ordering differences.
        $env:CUDA_VISIBLE_DEVICES = $gpuInfo[$device].Trim()
        $temp = Join-Path $comfyRoot "temp-gpu$number"
        $user = if ($number -eq 1) { Join-Path $comfyRoot 'user' } else { Join-Path $comfyRoot 'user-gpu2' }
        New-Item -ItemType Directory -Path $temp,$user -Force | Out-Null
        $secureScript = Join-Path $comfyRoot 'custom_nodes\h3_mobile\tools\run_comfyui_secure.ps1'
        Write-Host "Starting GPU $number secure lean ComfyUI on port $port..."
        $secureArgs = @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',('"'+$secureScript+'"'),'-Launcher','run_comfyui_lean.bat','--port',$port,'--temp-directory',('"'+$temp+'"'),'--user-directory',('"'+$user+'"'))
        $secureArgs += $memoryArguments
        try {
            Start-Process -FilePath (Get-Command pwsh.exe).Source -WorkingDirectory $comfyRoot -WindowStyle Hidden `
                -ArgumentList $secureArgs `
                -RedirectStandardOutput (Join-Path $state "comfy-gpu$number.log") -RedirectStandardError (Join-Path $state "comfy-gpu$number.err.log") | Out-Null
        } finally { Remove-Item Env:CUDA_VISIBLE_DEVICES -ErrorAction SilentlyContinue }
    }
}
foreach ($number in $selected) {
    $port = 8187 + $number
    if (-not (Ready "http://127.0.0.1:$port/system_stats" 300)) { throw "GPU $number did not become ready; see state\comfy-gpu$number.err.log." }
}
foreach ($number in $selected) {
    if (-not (Processes (Worker-Pattern $number))) {
        $env:COMFYUI_URL = "http://127.0.0.1:$(8187 + $number)"
        $env:TENSOR_WORKER_ID = if ($number -eq 1) { 'local-3090' } else { 'local-3090-gpu2' }
        $env:TENSOR_WORKER_NAME = "Home RTX 3090 - GPU $number"
        try {
            Start-Process -FilePath $python -WorkingDirectory $root -WindowStyle Hidden `
                -ArgumentList '-m','backend.worker','--local-gpu',$number `
                -RedirectStandardOutput (Join-Path $state "worker-gpu$number.log") -RedirectStandardError (Join-Path $state "worker-gpu$number.err.log") | Out-Null
        } finally { Remove-Item Env:COMFYUI_URL,Env:TENSOR_WORKER_ID,Env:TENSOR_WORKER_NAME -ErrorAction SilentlyContinue }
    }
}
$deadline = (Get-Date).AddSeconds(60)
do {
    & $python (Join-Path $PSScriptRoot 'service_check.py') --quiet
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)
if ($LASTEXITCODE) { throw 'Service readiness failed; check the per-GPU logs in state.' }
if (-not $NoSupervisor -and -not (Processes 'tools[\\/]supervise\.py')) {
    Start-Process -FilePath $python -WorkingDirectory $root -WindowStyle Hidden `
        -ArgumentList (Join-Path $PSScriptRoot 'supervise.py') `
        -RedirectStandardOutput (Join-Path $state 'supervisor.log') -RedirectStandardError (Join-Path $state 'supervisor.err.log') | Out-Null
}
Write-Host "Ready: GPU workers $($selected -join ', '). http://127.0.0.1:8000/#generator"
if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8000/#generator' }
