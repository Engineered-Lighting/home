# deploy-video-labeler.ps1 — push + run the video-labeler on the Ubuntu box.
#
# What it does (YOU run it deliberately — it writes to the live AI box):
#   1. scp stack/services/video-labeler -> hav-ubuntu:~/video-labeler-deploy
#   2. docker build -t video-labeler
#   3. docker run standalone (does NOT touch the existing compose stack):
#      --network host (native Ollama on 127.0.0.1 in later milestones),
#      --gpus all, docker.sock mounted for the M2 eviction/deadman machinery,
#      --env-file /opt/home-ai-voice/.env supplies shared secrets on-box,
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

Write-Host "3/4 starting container (standalone, env from /opt/home-ai-voice/.env)"
ssh hav-ubuntu @'
docker rm -f hav-video-labeler 2>/dev/null
docker run -d --name hav-video-labeler \
  --network host \
  --gpus all \
  --restart unless-stopped \
  -v /opt/home-ai-voice/video-labeler-data:/data \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --env-file /opt/home-ai-voice/.env \
  -e PORT=8099 -e DATA_DIR=/data \
  video-labeler
'@

Write-Host "4/4 verifying"
$ok = $false
foreach ($i in 1..12) {
    Start-Sleep -Seconds 5
    try {
        $h = Invoke-RestMethod -Uri "http://192.168.0.100:8099/healthz" -TimeoutSec 5
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
Write-Host "deployed. API: http://192.168.0.100:8099/api/video-labeler/  inbox: /opt/home-ai-voice/video-labeler-data/inbox" -ForegroundColor Green
