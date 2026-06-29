param(
    [switch]$Json,
    [string]$ExpectedTravelHost = "hootersblade",
    [switch]$HomeWindowsPc
)

$ErrorActionPreference = "Continue"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Checks = @()

function Add-Check {
    param(
        [string]$Name,
        [ValidateSet("blocker", "degraded", "optional")]
        [string]$Severity,
        [bool]$Ok,
        [string]$Detail,
        [string]$Command
    )
    $script:Checks += [pscustomobject]@{
        name = $Name
        severity = $Severity
        ok = $Ok
        detail = if ($Detail) { $Detail.Substring(0, [Math]::Min(240, $Detail.Length)) } else { "" }
        command = $Command
    }
}

function Invoke-Check {
    param(
        [string]$Name,
        [ValidateSet("blocker", "degraded", "optional")]
        [string]$Severity,
        [string]$Command,
        [string[]]$CommandArgs
    )
    try {
        $output = & $Command @CommandArgs 2>&1
        $ok = $LASTEXITCODE -eq 0
        $detail = if ($ok) { "ok" } else { (($output | Out-String).Trim() -replace "`r?`n", " ") }
        Add-Check $Name $Severity $ok $detail "$Command $($CommandArgs -join ' ')"
    } catch {
        Add-Check $Name $Severity $false $_.Exception.Message "$Command $($CommandArgs -join ' ')"
    }
}

function Get-PowerAcSeconds {
    param([string]$SettingAlias)
    try {
        $output = powercfg /query SCHEME_CURRENT SUB_SLEEP $SettingAlias 2>&1
        $text = $output | Out-String
        if ($text -match "Current AC Power Setting Index:\s*(0x[0-9A-Fa-f]+)") {
            return [Convert]::ToInt32($Matches[1], 16)
        }
    } catch {}
    return $null
}

$hostname = $env:COMPUTERNAME
$expected = $ExpectedTravelHost.ToLowerInvariant()
$isExpectedTravelHost = $hostname.ToLowerInvariant() -eq $expected
Add-Check "running on expected travel host" "degraded" $isExpectedTravelHost "current host: $hostname; expected: $ExpectedTravelHost" "hostname"

Invoke-Check "tailscale online" "blocker" "tailscale" @("status")

try {
    $tailnet = tailscale status 2>&1 | Out-String
    $ubuntuOk = $tailnet -match "\bengineeredlightingserver1\b" -and $tailnet -notmatch "engineeredlightingserver1\s+.*offline"
    $haOk = $tailnet -match "\bhomeassistant\b" -and $tailnet -notmatch "homeassistant\s+.*offline"
    $razerSeen = $isExpectedTravelHost -or ($tailnet -match "\b$([Regex]::Escape($ExpectedTravelHost))\b" -and $tailnet -notmatch "$([Regex]::Escape($ExpectedTravelHost))\s+.*offline")
    Add-Check "tailnet Ubuntu AI box" "blocker" $ubuntuOk "engineeredlightingserver1 should be active" "tailscale status"
    Add-Check "tailnet Home Assistant" "blocker" $haOk "homeassistant should be active" "tailscale status"
    Add-Check "tailnet Razer Blade peer" "degraded" $razerSeen "$ExpectedTravelHost should be active or this should be run directly on it" "tailscale status"
} catch {
    Add-Check "tailnet peer scan" "blocker" $false $_.Exception.Message "tailscale status"
}

Invoke-Check "ssh hav-ubuntu" "blocker" "ssh" @("-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "hav-ubuntu", "echo ok")

try {
    $readinessJson = powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "travel-readiness.ps1") -Json
    $readiness = $readinessJson | ConvertFrom-Json
    Add-Check "Windows travel readiness" "blocker" ($readiness.status -eq "ready") $readiness.status "tools\travel-readiness.ps1 -Json"
} catch {
    Add-Check "Windows travel readiness" "blocker" $false $_.Exception.Message "tools\travel-readiness.ps1 -Json"
}

try {
    Push-Location $RepoRoot
    $gitOutput = git ls-remote --heads origin main 2>&1
    $gitOk = $LASTEXITCODE -eq 0
    Pop-Location
    $gitDetail = if ($gitOk) { "origin/main visible" } else { (($gitOutput | Out-String).Trim()) }
    Add-Check "GitHub repo access" "blocker" $gitOk $gitDetail "git ls-remote --heads origin main"
} catch {
    try { Pop-Location } catch {}
    Add-Check "GitHub repo access" "blocker" $false $_.Exception.Message "git ls-remote --heads origin main"
}

$homeExe = Join-Path $env:LOCALAPPDATA "Programs\Home\home.exe"
if (Test-Path $homeExe) {
    $item = Get-Item $homeExe
    Add-Check "installed Home app" "blocker" $true ("found; modified {0}" -f $item.LastWriteTime) $homeExe
} else {
    Add-Check "installed Home app" "blocker" $false "Home app is not installed for this user" $homeExe
}

try {
    $tailscaleSvc = Get-Service Tailscale -ErrorAction Stop
    $tailscaleMode = (Get-CimInstance Win32_Service -Filter "Name='Tailscale'" -ErrorAction SilentlyContinue).StartMode
    $tailscaleOk = $tailscaleSvc.Status -eq "Running" -and $tailscaleMode -eq "Auto"
    Add-Check "Windows Tailscale service" "blocker" $tailscaleOk "$($tailscaleSvc.Status), start=$tailscaleMode" "Get-Service Tailscale"
} catch {
    Add-Check "Windows Tailscale service" "blocker" $false $_.Exception.Message "Get-Service Tailscale"
}

try {
    $sshdSvc = Get-Service sshd -ErrorAction Stop
    $sshdMode = (Get-CimInstance Win32_Service -Filter "Name='sshd'" -ErrorAction SilentlyContinue).StartMode
    $sshdOk = $sshdSvc.Status -eq "Running" -and $sshdMode -eq "Auto"
    Add-Check "Windows SSH service" "degraded" $sshdOk "$($sshdSvc.Status), start=$sshdMode" "Get-Service sshd"
} catch {
    Add-Check "Windows SSH service" "degraded" $false $_.Exception.Message "Get-Service sshd"
}

if ($HomeWindowsPc -or $hostname -ieq "FormD-T1") {
    $standby = Get-PowerAcSeconds "STANDBYIDLE"
    $hibernate = Get-PowerAcSeconds "HIBERNATEIDLE"
    Add-Check "AC sleep disabled" "blocker" ($standby -eq 0) "standby timeout seconds: $standby" "powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE"
    Add-Check "AC hibernate disabled" "blocker" ($hibernate -eq 0) "hibernate timeout seconds: $hibernate" "powercfg /query SCHEME_CURRENT SUB_SLEEP HIBERNATEIDLE"
}

$blockers = @($Checks | Where-Object { -not $_.ok -and $_.severity -eq "blocker" }).Count
$degraded = @($Checks | Where-Object { -not $_.ok -and $_.severity -ne "blocker" }).Count
$reachable = @($Checks | Where-Object { $_.ok }).Count
$status = if ($blockers -gt 0) { "blocked" } elseif ($degraded -gt 0) { "degraded" } else { "ready" }

$result = [pscustomobject]@{
    status = $status
    generatedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    host = $hostname
    expectedTravelHost = $ExpectedTravelHost
    reachable = $reachable
    total = $Checks.Count
    blockers = $blockers
    degraded = $degraded
    checks = $Checks
}

if ($Json) {
    $result | ConvertTo-Json -Depth 5
} else {
    "pre-departure readiness: {0} ({1}/{2} passed, {3} blockers, {4} degraded)" -f $status, $reachable, $Checks.Count, $blockers, $degraded
    foreach ($check in $Checks) {
        "{0} {1} {2} - {3}" -f $check.ok, $check.severity, $check.name, $check.detail
    }
    ""
    "json:"
    $result | ConvertTo-Json -Depth 5
}

if ($status -eq "blocked") {
    exit 1
}
exit 0
