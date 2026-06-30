param(
    [switch]$Json
)

$ErrorActionPreference = "Continue"

$AiHost = if ($env:HOME_TRAVEL_AI_HOST) { $env:HOME_TRAVEL_AI_HOST } else { "home-app" }
$HaHost = if ($env:HOME_TRAVEL_HA_HOST) { $env:HOME_TRAVEL_HA_HOST } else { "homeassistant" }
$WebUrl = if ($env:HOME_TRAVEL_WEB_URL) { $env:HOME_TRAVEL_WEB_URL } else { "https://home-app.taild52a15.ts.net/healthz" }

$script:Checks = @()

function Add-TravelCheck {
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
        detail = $Detail
        command = $Command
    }
}

function Test-CommandOk {
    param(
        [string]$Name,
        [string]$Severity,
        [string]$Command,
        [string[]]$CommandArgs
    )
    try {
        $output = & $Command @CommandArgs 2>&1
        $ok = $LASTEXITCODE -eq 0
        $detail = if ($ok) { "ok" } else { (($output | Out-String).Trim() -replace "`r?`n", " ") }
        Add-TravelCheck $Name $Severity $ok ($detail.Substring(0, [Math]::Min(220, $detail.Length))) "$Command $($CommandArgs -join ' ')"
    } catch {
        Add-TravelCheck $Name $Severity $false $_.Exception.Message "$Command $($CommandArgs -join ' ')"
    }
}

function Test-DnsNameOk {
    param([string]$HostName, [string]$Severity)
    try {
        $result = Resolve-DnsName -Name $HostName -ErrorAction Stop
        $addresses = ($result | Where-Object { $_.IPAddress } | Select-Object -ExpandProperty IPAddress) -join ", "
        Add-TravelCheck "dns $HostName" $Severity $true $addresses "Resolve-DnsName $HostName"
    } catch {
        Add-TravelCheck "dns $HostName" $Severity $false $_.Exception.Message "Resolve-DnsName $HostName"
    }
}

function Test-HttpOk {
    param(
        [string]$Name,
        [string]$Severity,
        [string]$Url,
        [int[]]$OkStatuses = @(200)
    )
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 6 -Method Get
        $status = [int]$response.StatusCode
        Add-TravelCheck $Name $Severity ($OkStatuses -contains $status) "HTTP $status" "Invoke-WebRequest $Url"
    } catch {
        $status = 0
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            $status = [int]$_.Exception.Response.StatusCode
        }
        $ok = $OkStatuses -contains $status
        $detail = if ($status) { "HTTP $status" } else { $_.Exception.Message }
        Add-TravelCheck $Name $Severity $ok $detail "Invoke-WebRequest $Url"
    }
}

Test-CommandOk "tailscale online" "blocker" "tailscale" @("status")
Test-DnsNameOk $AiHost "blocker"
Test-DnsNameOk $HaHost "blocker"
Test-CommandOk "ssh hav-ubuntu" "degraded" "ssh" @("-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "hav-ubuntu", "echo ok")

Test-HttpOk "browser gateway" "degraded" $WebUrl @(200, 401)
Test-HttpOk "Home Assistant API" "blocker" "http://${HaHost}:8123/api/" @(200, 401)
Test-HttpOk "Frigate stats" "degraded" "http://${HaHost}:5000/api/stats" @(200, 401)
Test-HttpOk "vLLM models" "degraded" "http://${AiHost}:8000/v1/models" @(200, 401)
Test-HttpOk "vision" "degraded" "http://${AiHost}:8091/healthz" @(200)
Test-HttpOk "metrics" "degraded" "http://${AiHost}:8092/healthz" @(200)
Test-HttpOk "supervisor" "degraded" "http://${AiHost}:8093/healthz" @(200)
Test-HttpOk "S2S bridge" "degraded" "http://${AiHost}:8094/healthz" @(200)
Test-HttpOk "intelligence" "degraded" "http://${AiHost}:8095/healthz" @(200)
Test-HttpOk "tracker" "degraded" "http://${AiHost}:8098/healthz" @(200)
Test-HttpOk "video labeler" "degraded" "http://${AiHost}:8099/healthz" @(200)
Test-HttpOk "Apartment assets" "degraded" "http://${AiHost}:5190/healthz" @(200)

$blockers = @($Checks | Where-Object { -not $_.ok -and $_.severity -eq "blocker" }).Count
$degraded = @($Checks | Where-Object { -not $_.ok -and $_.severity -ne "blocker" }).Count
$reachable = @($Checks | Where-Object { $_.ok }).Count
$status = if ($blockers -gt 0) { "blocked" } elseif ($degraded -gt 0) { "degraded" } else { "ready" }

$result = [pscustomobject]@{
    status = $status
    generatedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    reachable = $reachable
    total = $Checks.Count
    blockers = $blockers
    degraded = $degraded
    checks = $Checks
}

if ($Json) {
    $result | ConvertTo-Json -Depth 5
} else {
    "travel readiness: {0} ({1}/{2} reachable, {3} blockers, {4} degraded)" -f $status, $reachable, $Checks.Count, $blockers, $degraded
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
