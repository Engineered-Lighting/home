param(
  [string]$HostName = "hav-ubuntu",
  [string]$RemoteRoot = "/opt/home-ai-voice",
  [string]$LocalRoot = "C:\Claude\home",
  [switch]$SkipBackup
)

$ErrorActionPreference = "Stop"

$serviceDir = Join-Path $LocalRoot "stack\services"
$archive = Join-Path $env:TEMP "home-intelligence-deploy.tgz"
if (Test-Path -LiteralPath $archive) {
  Remove-Item -LiteralPath $archive -Force
}

tar -C $serviceDir --exclude "intelligence/data" -czf $archive intelligence
scp $archive "${HostName}:/tmp/home-intelligence-deploy.tgz"

$backupFlag = if ($SkipBackup) { "1" } else { "0" }
$remoteScript = @"
set -euo pipefail
REMOTE_ROOT='$RemoteRoot'
SKIP_BACKUP='$backupFlag'
rm -rf /tmp/home-intelligence-deploy
mkdir -p /tmp/home-intelligence-deploy
tar -xzf /tmp/home-intelligence-deploy.tgz -C /tmp/home-intelligence-deploy
mkdir -p "`$REMOTE_ROOT/services/intelligence" "`$REMOTE_ROOT/intelligence-data" "`$REMOTE_ROOT/backups/intelligence"
if [ "`$SKIP_BACKUP" != "1" ] && [ -f "`$REMOTE_ROOT/intelligence-data/intelligence.sqlite" ]; then
  bash /tmp/home-intelligence-deploy/intelligence/scripts/backup-intelligence-db.sh
fi
rsync -a --delete --exclude data/ /tmp/home-intelligence-deploy/intelligence/ "`$REMOTE_ROOT/services/intelligence/"
cat > "`$REMOTE_ROOT/docker-compose.intelligence.yml" <<'YAML'
# Compose OVERLAY for the Home Intelligence Core service.
# Port 8095 avoids the existing 8094 conflict (hav-personaplex-bridge).
# HA API evidence pull is canonical; /config is mounted only as a visibly labeled fallback snapshot.
services:
  intelligence:
    build:
      context: ./services/intelligence
    image: home-ai-voice/intelligence:local
    container_name: hav-intelligence
    restart: unless-stopped
    ports:
      - "8095:8094"
    volumes:
      - /opt/home-ai-voice/intelligence-data:/data
      - ./ha-evidence:/config:ro
    environment:
      INTELLIGENCE_DATA_DIR: /data
      INTELLIGENCE_HA_CONFIG_DIR: /config
      INTELLIGENCE_SCHEDULER_ENABLED: `${INTELLIGENCE_SCHEDULER_ENABLED:-1}
      INTELLIGENCE_EVIDENCE_PULL_INTERVAL_S: `${INTELLIGENCE_EVIDENCE_PULL_INTERVAL_S:-60}
      INTELLIGENCE_INGEST_INTERVAL_S: `${INTELLIGENCE_INGEST_INTERVAL_S:-3600}
      INTELLIGENCE_MEMORY_INTERVAL_S: `${INTELLIGENCE_MEMORY_INTERVAL_S:-86400}
      INTELLIGENCE_HA_BASE_URL: `${INTELLIGENCE_HA_BASE_URL:-http://192.168.0.125:8123}
      INTELLIGENCE_HA_TOKEN: `${INTELLIGENCE_HA_TOKEN:-}
      INTELLIGENCE_VLLM_URL: `${INTELLIGENCE_VLLM_URL:-http://vllm:8000}
      INTELLIGENCE_QWEN_MODEL: `${INTELLIGENCE_QWEN_MODEL:-qwen3-vl-30b}
      INTELLIGENCE_MULTIMODAL_ENABLED: `${INTELLIGENCE_MULTIMODAL_ENABLED:-1}
      INTELLIGENCE_MULTIMODAL_RING_ENABLED: `${INTELLIGENCE_MULTIMODAL_RING_ENABLED:-1}
      INTELLIGENCE_MULTIMODAL_RING_INTERVAL_S: `${INTELLIGENCE_MULTIMODAL_RING_INTERVAL_S:-2}
      INTELLIGENCE_MULTIMODAL_RING_RETENTION_S: `${INTELLIGENCE_MULTIMODAL_RING_RETENTION_S:-90}
      INTELLIGENCE_MULTIMODAL_CAMERAS: `${INTELLIGENCE_MULTIMODAL_CAMERAS:-living_room}
      INTELLIGENCE_MULTIMODAL_CAPTURE_FRAMES: `${INTELLIGENCE_MULTIMODAL_CAPTURE_FRAMES:-1}
      INTELLIGENCE_MULTIMODAL_MAX_CAPTURES_PER_HOUR: `${INTELLIGENCE_MULTIMODAL_MAX_CAPTURES_PER_HOUR:-6}
      INTELLIGENCE_MULTIMODAL_MAX_QWEN_JOBS_PER_HOUR: `${INTELLIGENCE_MULTIMODAL_MAX_QWEN_JOBS_PER_HOUR:-6}
      INTELLIGENCE_MULTIMODAL_MIN_DISK_FREE_MB: `${INTELLIGENCE_MULTIMODAL_MIN_DISK_FREE_MB:-2048}
      INTELLIGENCE_MULTIMODAL_CAPTURE_INTERVAL_S: `${INTELLIGENCE_MULTIMODAL_CAPTURE_INTERVAL_S:-60}
      INTELLIGENCE_MULTIMODAL_PILOT_ENABLED: `${INTELLIGENCE_MULTIMODAL_PILOT_ENABLED:-1}
      INTELLIGENCE_MULTIMODAL_PILOT_ZONES: `${INTELLIGENCE_MULTIMODAL_PILOT_ZONES:-office}
      INTELLIGENCE_MULTIMODAL_PILOT_KINDS: `${INTELLIGENCE_MULTIMODAL_PILOT_KINDS:-pending_preference,override_event}
      INTELLIGENCE_MULTIMODAL_PILOT_LOOKBACK_S: `${INTELLIGENCE_MULTIMODAL_PILOT_LOOKBACK_S:-180}
      INTELLIGENCE_MULTIMODAL_PILOT_MAX_PACKETS_PER_RUN: `${INTELLIGENCE_MULTIMODAL_PILOT_MAX_PACKETS_PER_RUN:-2}
      INTELLIGENCE_MULTIMODAL_PILOT_AUTO_LABEL: `${INTELLIGENCE_MULTIMODAL_PILOT_AUTO_LABEL:-1}
      INTELLIGENCE_MULTIMODAL_PILOT_MIN_LABEL_FRAMES: `${INTELLIGENCE_MULTIMODAL_PILOT_MIN_LABEL_FRAMES:-2}
      HA_TOKEN: `${HA_TOKEN:-}
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8094/healthz').read()\""]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    networks: [homeai]
YAML
cd "`$REMOTE_ROOT"
docker compose -f docker-compose.yml -f docker-compose.intelligence.yml build intelligence
docker compose -f docker-compose.yml -f docker-compose.intelligence.yml up -d intelligence
for i in `$(seq 1 40); do
  if python3 - <<'PY'
import sys
import urllib.request
try:
    urllib.request.urlopen('http://127.0.0.1:8095/healthz', timeout=3).read()
except Exception:
    sys.exit(1)
PY
  then
    break
  fi
  if [ "`$i" = "40" ]; then
    echo "intelligence did not become healthy" >&2
    exit 1
  fi
  sleep 1
done
python3 services/intelligence/scripts/smoke-intelligence.py --base http://127.0.0.1:8095 --expect-scheduler
"@

$remoteScript | ssh $HostName "tr -d '\r' | bash -s"
