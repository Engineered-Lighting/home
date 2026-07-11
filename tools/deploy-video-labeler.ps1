# deploy-video-labeler.ps1 — push + run the video-labeler on the Ubuntu box.
#
# What it does (YOU run it deliberately — it writes to the live AI box):
#   1. scp stack/services/video-labeler -> hav-ubuntu:~/video-labeler-deploy
#   2. docker build -t video-labeler
#   3. docker run standalone (does NOT touch the existing compose stack):
#      --network host (native Ollama on 127.0.0.1 in later milestones),
#      --gpus all, with no Docker socket or host-control capability,
#      --env-file /etc/home-ai-voice/video-labeler.env is service-specific,
#      /opt/home-ai-voice/video-labeler-data:/data
#   4. verify /healthz from this machine (retry loop — first boot runs
#      migrations before binding)
#
# Rollback: ssh hav-ubuntu 'docker rm -f hav-video-labeler'

$ErrorActionPreference = "Stop"
$REPO = Split-Path -Parent $PSScriptRoot

Write-Host "1/4 copying service"
ssh hav-ubuntu 'rm -rf ~/video-labeler-deploy'
scp -r -q "$REPO\stack\services\video-labeler" hav-ubuntu:~/video-labeler-deploy
# belt-and-braces: never ship a local venv/caches into the build context
ssh hav-ubuntu 'rm -rf ~/video-labeler-deploy/.venv ~/video-labeler-deploy/.pytest_cache ~/video-labeler-deploy/__pycache__'

Write-Host "2/4 building image"
ssh hav-ubuntu 'docker build -q -t video-labeler ~/video-labeler-deploy'

ssh hav-ubuntu @'
set -eu
token=/etc/home-ai-voice/secrets/video_labeler_token
environment=/etc/home-ai-voice/video-labeler.env
test -s "$token"
test "$(stat -c %a "$token")" = 600
test -s "$environment"
test "$(stat -c %a "$environment")" = 600
'@

Write-Host "3/4 starting container (standalone, env from /opt/home-ai-voice/.env)"
ssh hav-ubuntu @'
docker rm -f hav-video-labeler 2>/dev/null
docker run -d --name hav-video-labeler \
  --network host \
  --gpus all \
  --restart unless-stopped \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=256m \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  -v /opt/home-ai-voice/video-labeler-data:/data \
  -v /etc/home-ai-voice/secrets/video_labeler_token:/run/secrets/video_labeler_token:ro \
  --env-file /etc/home-ai-voice/video-labeler.env \
  -e PORT=8099 -e BIND_HOST=127.0.0.1 -e DATA_DIR=/data \
  -e VIDEO_LABELER_API_TOKEN_FILE=/run/secrets/video_labeler_token \
  -e VLM_MODEL=labeler-qwen2.5vl-32b \
  -e VLM_KEEP_ALIVE=30m \
  video-labeler
'@
# VLM_MODEL: the context-capped derivative (num_ctx 16384). The base
# qwen2.5vl:32b default-loads a 128k context -> 137GB, CPU spill, ~55x
# slower inference. Recreate on a fresh box with:
#   printf 'FROM qwen2.5vl:32b\nPARAMETER num_ctx 16384\n' | ollama create labeler-qwen2.5vl-32b -f -

Write-Host "4/4 verifying"
$ok = $false
foreach ($i in 1..12) {
    Start-Sleep -Seconds 5
    try {
        $raw = ssh hav-ubuntu 'curl -fsS --max-time 5 http://127.0.0.1:8099/healthz'
        $h = $raw | ConvertFrom-Json
        Write-Host ("healthz: ok={0} db={1} jobs_running={2} gpu_free_gb={3} disk_free_gb={4}" `
            -f $h.ok, $h.db, $h.jobs_running, $h.gpu_free_gb, $h.disk_free_gb)
        $ok = $true
        break
    } catch {
        Write-Host ("  attempt {0}/12: not up yet" -f $i)
    }
}
if (-not $ok) {
    throw "video-labeler /healthz never came up - check: ssh hav-ubuntu 'docker logs hav-video-labeler'"
}
Write-Host "deployed loopback-only. Use an authenticated admin tunnel for the API. inbox: /opt/home-ai-voice/video-labeler-data/inbox" -ForegroundColor Green
