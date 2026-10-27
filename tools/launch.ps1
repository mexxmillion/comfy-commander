# Shared startup, clean restart, and Windows sign-in startup.
param(
    [switch]$Restart, [switch]$IncludeComfy, [switch]$Stop, [switch]$NoBrowser,
    [switch]$Status, [switch]$EnableAutoStart, [switch]$DisableAutoStart,
    [switch]$RebootWindows, [switch]$NoSupervisor,
    [AllowEmptyString()][ValidateSet('','1','2','Both')][string]$Gpu = '',
    [switch]$WorkersOnly
)
$startupMutex = [Threading.Mutex]::new($false, 'Local\TensorMasterLaunch')
try { $ownsStartup = $startupMutex.WaitOne(20000) }
catch [Threading.AbandonedMutexException] { $ownsStartup = $true }
if (-not $ownsStartup) {
    $startupMutex.Dispose()
    throw 'Another service startup/recovery is in progress. It will preserve your credentials.'
}
try {
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$python = Join-Path $root '.venv312\Scripts\python.exe'
$state = Join-Path $root 'state'
$credentials = Join-Path $state 'credentials'
$startupLink = Join-Path ([Environment]::GetFolderPath('Startup')) 'Tensor Master.lnk'
function Initialize-Credentials {
    New-Item -ItemType Directory -Path $credentials -Force | Out-Null
    # icacls changes only access rules, without requesting audit-policy privileges.
    & icacls.exe $credentials /inheritance:r | Out-Null
    if ($LASTEXITCODE) { throw 'Could not protect the credential folder.' }
    foreach ($identity in @([Security.Principal.WindowsIdentity]::GetCurrent().User.Value, 'S-1-5-18', 'S-1-5-32-544')) {
        & icacls.exe $credentials /grant:r "*${identity}:(OI)(CI)F" | Out-Null
        if ($LASTEXITCODE) { throw 'Could not set credential folder access.' }
    }
    $legacy = Join-Path $env:APPDATA 'TensorMaster'
    $sourceToken = if ($env:TOKEN_FILE) { $env:TOKEN_FILE } else { Join-Path $legacy 'api_token' }
    foreach ($name in @('api_token', 'admin_password.json')) {
        $destination = Join-Path $credentials $name
        $source = Join-Path (Split-Path $sourceToken) $name
        if (-not (Test-Path -LiteralPath $destination) -and (Test-Path -LiteralPath $source)) {
            Copy-Item -LiteralPath $source -Destination $destination
            Write-Host "Preserved existing $name in the stable credential folder."
        }
    }
    $secret = Join-Path $credentials 'openrouter_api_key.dpapi'
    $legacySecret = if ($env:OPENROUTER_SECRET_FILE) { $env:OPENROUTER_SECRET_FILE } else { Join-Path $env:APPDATA 'ComfyUI\openrouter_api_key.dpapi' }
    if (-not (Test-Path -LiteralPath $secret) -and (Test-Path -LiteralPath $legacySecret)) {
        Copy-Item -LiteralPath $legacySecret -Destination $secret
    }
    $env:TOKEN_FILE = Join-Path $credentials 'api_token'
    $env:TENSOR_MASTER_TOKEN_FILE = $env:TOKEN_FILE
    $env:OPENROUTER_SECRET_FILE = $secret
    $env:TENSOR_MASTER_CREDENTIAL_DIR = $credentials
    $env:TENSOR_MASTER_BRIDGE_TOKEN_FILE = Join-Path $credentials 'pocket_bridge_token'
    Push-Location $root
    try {
        & $python -c 'from backend.app.auth import ensure_token; from backend.app.config import get_settings; from backend.app.identity import IdentityStore; s=get_settings(); identity=IdentityStore(s.token_file); identity.export() if identity.path.exists() else None; ensure_token(s.token_file); identity.initialize(); identity.ready().export(); print("Persistent identity authority and service exports verified")'
        if ($LASTEXITCODE) { throw 'Credential preflight failed. Existing credentials were not replaced.' }
    } finally { Pop-Location }
    # Direct secure ComfyUI starts use the same saved credentials after a reboot.
    [Environment]::SetEnvironmentVariable('TENSOR_MASTER_CREDENTIAL_DIR', $credentials, 'User')
}

if ($DisableAutoStart) {
    if (Test-Path -LiteralPath $startupLink) { Remove-Item -LiteralPath $startupLink }
    Write-Host 'Automatic startup after Windows sign-in disabled.'
    return
}
if ($Status -or $Stop) {
    & (Join-Path $PSScriptRoot 'local_gpus.ps1') -Gpu $Gpu -Status:$Status -Stop:$Stop -WorkersOnly:$WorkersOnly
    return
}
Initialize-Credentials
if ($EnableAutoStart) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($startupLink)
    $shortcut.TargetPath = (Get-Command pwsh.exe).Source
    $shortcut.Arguments = '-WindowStyle Hidden -NoLogo -NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -NoBrowser'
    $shortcut.WorkingDirectory = $root
    $shortcut.WindowStyle = 7
    $shortcut.Save()
    Write-Host 'Automatic startup after Windows sign-in enabled.'
    return
}
if ($RebootWindows) {
    if ((Read-Host 'Reboot Windows now? Type REBOOT to continue') -cne 'REBOOT') { return }
    $selectionFile = Join-Path $state 'local-gpus.json'
    $selection = if (Test-Path $selectionFile) { Get-Content $selectionFile -Raw } else { '{"enabled":[1]}' }
    & (Join-Path $PSScriptRoot 'local_gpus.ps1') -Stop -Gpu Both
    $selection | Set-Content $selectionFile
    & $PSCommandPath -EnableAutoStart
    & shutdown.exe /r /t 30 /c 'Tensor Master clean Windows reboot. Services start after sign-in.'
    return
}
& (Join-Path $PSScriptRoot 'local_gpus.ps1') -Gpu $Gpu -Restart:$Restart -WorkersOnly:$WorkersOnly -NoBrowser:$NoBrowser -NoSupervisor:$NoSupervisor
return

} finally {
    $startupMutex.ReleaseMutex()
    $startupMutex.Dispose()
}
