# Emit only supported ComfyUI memory flags; used by normal start and recovery.
param([Parameter(Mandatory)][string]$Path)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Path)) { return }
$profile = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
$allowed = @('disable_pinned_memory', 'disable_async_offload', 'cache_headroom_gb')
foreach ($property in $profile.PSObject.Properties.Name) {
    if ($property -notin $allowed) { throw "Unknown local memory setting: $property" }
}
foreach ($setting in @('disable_pinned_memory', 'disable_async_offload')) {
    if ($profile.PSObject.Properties.Name -contains $setting) {
        if ($profile.$setting -isnot [bool]) { throw "$setting must be a boolean." }
        if ($profile.$setting) { '--' + $setting.Replace('_', '-') }
    }
}
if ($profile.PSObject.Properties.Name -contains 'cache_headroom_gb') {
    $limits = @($profile.cache_headroom_gb)
    if ($limits.Count -ne 2) { throw 'cache_headroom_gb requires active and inactive GB.' }
    foreach ($limit in $limits) {
        if (($limit -isnot [long] -and $limit -isnot [int] -and $limit -isnot [double]) -or
            [double]::IsNaN($limit) -or [double]::IsInfinity($limit) -or $limit -lt 2 -or $limit -gt 128) {
            throw 'Cache headroom must be numeric GB between 2 and 128.'
        }
    }
    if ($limits[1] -lt $limits[0]) { throw 'Inactive cache headroom must be at least active cache headroom.' }
    '--cache-ram'
    foreach ($limit in $limits) { $limit.ToString([Globalization.CultureInfo]::InvariantCulture) }
}
