#!/usr/bin/env bash
# Frigate face-recognition bench harness (Addendum 25).
#
# Captures /api/stats samples + /api/events for one config "option".
# The config itself must already be applied + Frigate restarted/reloaded
# before calling this (so we can capture clean post-warmup data).
#
# Usage:
#   ./run-bench.sh <option_label> <warmup_seconds> <capture_seconds>
#   e.g. ./run-bench.sh A 60 600
#
# Writes:
#   tools/frigate-bench/option-<label>/stats-NN.json   (10 samples by default)
#   tools/frigate-bench/option-<label>/events.json     (all person events in window)
#   tools/frigate-bench/option-<label>/window.json     (start/end timestamps + label)
#
# Frigate stays on HAOS at 192.168.0.125:5000. SSH tunnels via the
# ssh-server addon on port 22222.
set -euo pipefail

LABEL="${1:?option label required (A/B/C/D/E/A2/...)}"
WARMUP="${2:-300}"          # seconds — wait after restart before sampling
CAPTURE="${3:-600}"         # seconds — total sampling window
SAMPLES="${SAMPLES:-10}"    # number of stats snapshots in the capture window

FRIGATE_URL="${FRIGATE_URL:-http://192.168.0.125:5000}"
SSH_HOST="${SSH_HOST:-root@homeassistant.local}"
SSH_PORT="${SSH_PORT:-22222}"

OUT_DIR="$(dirname "$0")/option-${LABEL}"
mkdir -p "${OUT_DIR}"

curl_remote() {
  ssh -p "${SSH_PORT}" -o StrictHostKeyChecking=no "${SSH_HOST}" \
    "curl -s --max-time 8 \"${FRIGATE_URL}$1\""
}

# Warmup window
echo "[bench:${LABEL}] warmup ${WARMUP}s (waiting for caches + JIT)..."
sleep "${WARMUP}"

# Capture loop — SAMPLES stats snapshots spread across CAPTURE seconds.
SAMPLE_INTERVAL=$(( CAPTURE / SAMPLES ))
START_TS=$(date +%s)
echo "[bench:${LABEL}] capture begins at ${START_TS} (Unix), ${SAMPLES} samples × ${SAMPLE_INTERVAL}s"

for i in $(seq 1 "${SAMPLES}"); do
  PAD=$(printf "%02d" "${i}")
  echo "[bench:${LABEL}] sample ${PAD}/${SAMPLES}"
  curl_remote /api/stats > "${OUT_DIR}/stats-${PAD}.json" || {
    echo "[bench:${LABEL}] WARNING stats sample ${PAD} failed"
  }
  if [ "${i}" -lt "${SAMPLES}" ]; then
    sleep "${SAMPLE_INTERVAL}"
  fi
done

END_TS=$(date +%s)
echo "[bench:${LABEL}] capture ends at ${END_TS}"

# Pull all person events from the window. has_clip=0 = include events
# without saved clips (we just want metadata). Limit=500 is plenty for
# 10 min of walk-loop activity.
EVENTS_URL="/api/events?after=${START_TS}&before=${END_TS}&label=person&limit=500&has_clip=0"
curl_remote "${EVENTS_URL}" > "${OUT_DIR}/events.json" || {
  echo "[bench:${LABEL}] WARNING events fetch failed"
}

# Capture window metadata
cat > "${OUT_DIR}/window.json" <<EOF
{
  "label": "${LABEL}",
  "warmup_seconds": ${WARMUP},
  "capture_seconds": ${CAPTURE},
  "samples": ${SAMPLES},
  "start_ts": ${START_TS},
  "end_ts": ${END_TS},
  "frigate_url": "${FRIGATE_URL}"
}
EOF

# Summary
EVENT_COUNT=$(grep -o '"id"' "${OUT_DIR}/events.json" 2>/dev/null | wc -l || echo 0)
echo "[bench:${LABEL}] DONE — wrote ${SAMPLES} stats + ${EVENT_COUNT} events to ${OUT_DIR}"
