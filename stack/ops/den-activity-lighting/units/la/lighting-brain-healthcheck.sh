#!/bin/sh
# GET the brain's /healthz on loopback. After THRESHOLD consecutive failures,
# restart lighting-brain.service (only that unit) and reset the count.
# The unit's own StartLimitBurst still bounds restarts.
set -u
URL="${HEALTH_URL:-http://127.0.0.1:8101/healthz}"
STATE="${HEALTH_STATE:-/var/lib/lighting-brain-health/failures}"
THRESHOLD="${HEALTH_THRESHOLD:-3}"

count=0
[ -f "$STATE" ] && count=$(cat "$STATE" 2>/dev/null || echo 0)
case "$count" in ''|*[!0-9]*) count=0 ;; esac

if curl -fsS -m 5 -o /dev/null "$URL"; then
  echo 0 > "$STATE"
  exit 0
fi

count=$((count + 1))
echo "lighting-brain /healthz failed ($count/$THRESHOLD)" >&2
if [ "$count" -ge "$THRESHOLD" ]; then
  echo 0 > "$STATE"
  echo "restarting lighting-brain.service after $count failed health checks" >&2
  systemctl restart lighting-brain.service
  exit 1
fi
echo "$count" > "$STATE"
exit 1
