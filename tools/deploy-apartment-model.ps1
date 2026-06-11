# deploy-apartment-model.ps1 — push the apartment_model endpoint to HAOS.
#
# What it does (asks nothing, but YOU run it deliberately):
#   1. scp ha-config/extended_openai_conversation/__init__.py to HAOS
#   2. restart HA core (ha core restart over SSH)
#   3. wait for HA to come back, then verify GET apartment_model + a POST
#      round-trip including the 409 conflict path
#
# Prereqs: SSH key auth to root@homeassistant.local:22222 (already used by
# qa-preflight), HA long-lived token at /tmp/ha_token.txt on HAOS.

$ErrorActionPreference = "Stop"
$REPO = Split-Path -Parent $PSScriptRoot
$SRC = Join-Path $REPO "ha-config\extended_openai_conversation\__init__.py"
$HAOS = "root@homeassistant.local"
$PORT = 22222
$DEST = "/config/custom_components/extended_openai_conversation/__init__.py"
$API = "http://192.168.0.125:8123/api/extended_openai_conversation/apartment_model"

Write-Host "1/4 deploying __init__.py -> HAOS $DEST"
scp -P $PORT $SRC "${HAOS}:$DEST"
if (-not $?) { throw "scp failed" }

Write-Host "2/4 restarting HA core (takes ~30-60s)"
ssh -p $PORT $HAOS "ha core restart"

Write-Host "3/4 waiting for HA API"
$token = (ssh -p $PORT $HAOS "cat /tmp/ha_token.txt").Trim()
$h = @{ Authorization = "Bearer $token" }
$up = $false
foreach ($i in 1..30) {
    Start-Sleep -Seconds 5
    try {
        $null = Invoke-RestMethod -Uri "http://192.168.0.125:8123/api/" -Headers $h -TimeoutSec 5
        $up = $true; break
    } catch { Write-Host "  waiting ($i)..." }
}
if (-not $up) { throw "HA did not come back within 150s" }

Write-Host "4/4 verifying apartment_model round-trip"
$get1 = Invoke-RestMethod -Uri $API -Headers $h
Write-Host ("  GET ok: exists={0} revision={1}" -f $get1.exists, $get1.revision)

$doc = @{ schema_version = 1; revision = [int]$get1.revision; zones = @(); devices = @();
          meta = @{ frame = "z_up_metric_floor0"; deployed_check = (Get-Date -Format o) } }
$post = Invoke-RestMethod -Uri $API -Headers $h -Method Post -Body ($doc | ConvertTo-Json -Depth 6) -ContentType "application/json"
Write-Host ("  POST ok: new revision={0}" -f $post.revision)

# stale-revision 409 path
try {
    $null = Invoke-RestMethod -Uri $API -Headers $h -Method Post -Body ($doc | ConvertTo-Json -Depth 6) -ContentType "application/json"
    Write-Host "  WARNING: stale POST did not 409!" -ForegroundColor Yellow
} catch {
    if ($_.Exception.Response.StatusCode.value__ -eq 409) { Write-Host "  409 conflict path ok" }
    else { throw }
}
Write-Host "deploy + verify complete" -ForegroundColor Green
