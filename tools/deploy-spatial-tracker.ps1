# deploy-spatial-tracker.ps1 — push + run the spatial-tracker on the Ubuntu box.
#
# What it does (YOU run it deliberately — it writes to the live AI box):
#   1. scp stack/services/spatial-tracker -> hav-ubuntu:~/spatial-tracker-deploy
#   2. seed ~/spatial-tracker-data/model with floor.json (ray-cast hull)
#   3. docker build + run standalone (does NOT touch the existing compose stack):
#      --env-file /opt/home-ai-voice/.env supplies MQTT_*/HA_URL/HA_TOKEN on-box
#   4. verify /healthz from this machine
#
# Rollback: ssh hav-ubuntu 'docker rm -f hav-spatial-tracker'

$ErrorActionPreference = "Stop"
$REPO = Split-Path -Parent $PSScriptRoot

Write-Host "1/4 copying service + model data"
scp -r -q "$REPO\stack\services\spatial-tracker" hav-ubuntu:~/spatial-tracker-deploy
ssh hav-ubuntu 'mkdir -p ~/spatial-tracker-data/model'
scp -q "$REPO\tools\spatial-pipeline\data\model\floor.json" hav-ubuntu:~/spatial-tracker-data/model/floor.json

Write-Host "2/4 building image"
ssh hav-ubuntu 'docker build -q -t spatial-tracker ~/spatial-tracker-deploy'

Write-Host "3/4 starting container (standalone, env from /opt/home-ai-voice/.env)"
ssh hav-ubuntu @'
docker rm -f hav-spatial-tracker 2>/dev/null
docker run -d --name hav-spatial-tracker --restart unless-stopped \
  -p 8098:8098 \
  --env-file /opt/home-ai-voice/.env \
  -e MQTT_HOST=192.168.0.125 -e FRIGATE_URL=http://192.168.0.125:5000 \
  -v ~/spatial-tracker-data:/data \
  spatial-tracker
'@

Write-Host "4/4 verifying"
Start-Sleep -Seconds 6
$h = Invoke-RestMethod -Uri "http://192.168.0.100:8098/healthz" -TimeoutSec 10
Write-Host ("healthz: ok={0} mqtt_connected={1} tracks_active={2}" -f $h.ok, $h.mqtt_connected, $h.tracks_active)
Write-Host "deployed. Live tracks: ws://192.168.0.100:8098/ws/tracks" -ForegroundColor Green
