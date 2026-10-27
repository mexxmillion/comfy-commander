param(
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
if ($Port -lt 1 -or $Port -gt 65535) { throw 'Port must be between 1 and 65535.' }

$tailscale = Get-Command tailscale.exe -ErrorAction SilentlyContinue
if (-not $tailscale) {
    $installed = 'C:\Program Files\Tailscale\tailscale.exe'
    if (Test-Path -LiteralPath $installed) { $tailscale = Get-Item -LiteralPath $installed }
}
if (-not $tailscale) {
    throw 'Install Tailscale on this computer and phone, then sign both into the same tailnet.'
}

$health = "http://127.0.0.1:$Port/health"
try {
    Invoke-RestMethod -Uri $health -TimeoutSec 5 | Out-Null
}
catch {
    throw "Tensor Master is not responding at $health. Start the controller first."
}

& $tailscale.Source status | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Tailscale is not connected on this computer.' }

& $tailscale.Source serve --bg --yes "http://127.0.0.1:$Port"
if ($LASTEXITCODE -ne 0) { throw 'Tailscale Serve could not publish Tensor Master to the tailnet.' }

Write-Output ''
Write-Output 'Tensor Master is available only inside your tailnet at the HTTPS URL above.'
Write-Output 'Install Tailscale on the phone, sign into the same tailnet, and open that URL.'
Write-Output 'Check later with: tailscale serve status'
Write-Output 'To remove every Serve route on this host: tailscale serve reset'
