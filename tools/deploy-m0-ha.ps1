# deploy-m0-ha.ps1 — push M0 (measurement instrumentation) HA-side
# changes to the live HAOS install, restart HA core, and confirm.
#
# Per Addendum 27 / Section 13. Run from the home repo root:
#   powershell.exe -NoProfile -File tools/deploy-m0-ha.ps1
#
# What this deploys:
#   • ha-config/.../external_routing.py  →  log_decision now accepts
#     a `kind` field; new kinds: tool_call, perception_dispatch,
#     confirmation. Backcompat: missing `kind` → routing_decision.
#   • ha-config/.../functions/native.py  →  execute_service_single
#     returns additive `ok`, `latency_ms`, `domain`, `service`
#     (legacy `success`/`error` preserved).
#
# What this does NOT deploy:
#   • stack/services/metrics-sidecar/main.py (Ubuntu AI box at
#     192.168.0.100; needs SSH key not present on this workstation;
#     copy + `docker compose up -d metrics-sidecar` from JetKVM or a
#     terminal on the AI box).
#
# Safety:
#   • SCP to /tmp first, then mv into place (atomic-ish).
#   • Restart HA core (~30s downtime). HA brings itself back up.
#   • This script is idempotent — running it twice is safe.

$ErrorActionPreference = "Stop"
$HA_HOST = "root@homeassistant.local"
$HA_PORT = "22222"
$LOCAL_ER   = "ha-config/extended_openai_conversation/external_routing.py"
$LOCAL_NV   = "ha-config/extended_openai_conversation/functions/native.py"
$REMOTE_DIR = "/config/custom_components/extended_openai_conversation"

Write-Host "==> Pre-flight: confirm local files exist + parse"
foreach ($f in @($LOCAL_ER, $LOCAL_NV)) {
    if (-not (Test-Path $f)) { throw "missing local file: $f" }
}
py -3 -c "import ast; ast.parse(open(r'$LOCAL_ER', encoding='utf-8').read())"
py -3 -c "import ast; ast.parse(open(r'$LOCAL_NV', encoding='utf-8').read())"
Write-Host "  OK"

Write-Host "==> SCP files to HAOS /tmp"
scp -P $HA_PORT -o ConnectTimeout=10 -o StrictHostKeyChecking=no `
    $LOCAL_ER $LOCAL_NV "${HA_HOST}:/tmp/"
Write-Host "  OK"

Write-Host "==> Backup current files + move new files into place"
ssh -p $HA_PORT -o ConnectTimeout=10 $HA_HOST @'
set -e
cp ${REMOTE_DIR}/external_routing.py ${REMOTE_DIR}/external_routing.py.bak.$(date +%s) 2>/dev/null || true
cp ${REMOTE_DIR}/functions/native.py ${REMOTE_DIR}/functions/native.py.bak.$(date +%s) 2>/dev/null || true
mv /tmp/external_routing.py /config/custom_components/extended_openai_conversation/external_routing.py
mv /tmp/native.py            /config/custom_components/extended_openai_conversation/functions/native.py
ls -la /config/custom_components/extended_openai_conversation/external_routing.py /config/custom_components/extended_openai_conversation/functions/native.py
'@
Write-Host "  OK"

Write-Host "==> ha core restart"
ssh -p $HA_PORT -o ConnectTimeout=10 $HA_HOST "ha core restart"
Write-Host "  restart requested. Waiting for HA to come back up..."

# Poll /api/ until it returns 401 (HA is up + asking for auth)
$deadline = (Get-Date).AddMinutes(3)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 5
    $r = (curl.exe -s -o $null -w "%{http_code}" http://homeassistant.local:8123/api/ 2>$null)
    Write-Host "    HA status: $r"
    if ($r -eq "401") { break }
}
if ($r -ne "401") { throw "HA did not come back within 3 min" }
Write-Host "  HA_UP"

Write-Host "==> Verify the integration loaded cleanly"
ssh -p $HA_PORT -o ConnectTimeout=10 $HA_HOST "ha core logs 2>&1 | grep -iE 'extended_openai|external_routing|native' | tail -10"
Write-Host ""

Write-Host "==> Capture 1-hour post-deploy baseline"
py -3 tools/measurement-baseline.py --hours 1

Write-Host ""
Write-Host "==> DONE. Now redeploy the sidecar separately:"
Write-Host "       scp stack/services/metrics-sidecar/main.py <ubuntu-ai-box>:/opt/home/stack/services/metrics-sidecar/"
Write-Host "       (then on the box) docker compose up -d metrics-sidecar"
