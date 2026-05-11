#!/usr/bin/env bash
# Local Home Assistant voice assistant stack — single-command control.
#
#   bash scripts/stack.sh up        # ensure all services running + healthy + smoke-test (default)
#   bash scripts/stack.sh down      # stop all services
#   bash scripts/stack.sh restart   # graceful restart
#   bash scripts/stack.sh status    # show current state + per-layer health
#   bash scripts/stack.sh logs <service>   # tail one service's logs
#
# Designed to be idempotent — re-running 'up' on an already-running stack
# is safe and just verifies health. Times out cleanly if a service refuses.

set -euo pipefail

STACK_DIR="${STACK_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$STACK_DIR"

CMD="${1:-up}"
SERVICES=(vllm wyoming-parakeet kokoro-tts wyoming-kokoro vision-sidecar metrics-sidecar)

c_green() { printf '\033[32m%s\033[0m\n' "$*"; }
c_red()   { printf '\033[31m%s\033[0m\n' "$*"; }
c_dim()   { printf '\033[2m%s\033[0m\n' "$*"; }

wait_healthy() {
  local name="$1" max="${2:-180}"
  local i=0
  while [ "$i" -lt "$max" ]; do
    local s
    s=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$name" 2>/dev/null || echo "missing")
    case "$s" in
      healthy|running)
        c_green "  $name: $s"
        return 0
        ;;
      missing|"")
        c_red "  $name: not running"
        return 1
        ;;
    esac
    sleep 3; i=$((i+3))
  done
  c_red "  $name: still '$s' after ${max}s"
  return 1
}

smoke_test() {
  echo
  echo "==> Smoke tests"
  local ok=1

  # vLLM
  if curl -fsS -m 5 http://localhost:8000/health >/dev/null 2>&1; then
    c_green "  vllm /health: OK"
  else
    c_red "  vllm /health: FAIL"; ok=0
  fi

  # Kokoro
  if curl -fsS -m 5 http://localhost:8880/v1/models >/dev/null 2>&1; then
    c_green "  kokoro /v1/models: OK"
  else
    c_red "  kokoro /v1/models: FAIL"; ok=0
  fi

  # Vision sidecar — camera frame grabber + multimodal proxy to vllm.
  if curl -fsS -m 5 http://localhost:8091/healthz >/dev/null 2>&1; then
    c_green "  vision-sidecar /healthz: OK"
  else
    c_red "  vision-sidecar /healthz: FAIL"; ok=0
  fi

  # Metrics sidecar — used by the Home desktop client.
  if curl -fsS -m 5 http://localhost:8092/healthz >/dev/null 2>&1; then
    c_green "  metrics-sidecar /healthz: OK"
  else
    c_red "  metrics-sidecar /healthz: FAIL"; ok=0
  fi

  # Wyoming Parakeet TCP
  if (echo > /dev/tcp/localhost/10300) 2>/dev/null; then
    c_green "  wyoming-parakeet :10300: OPEN"
  else
    c_red "  wyoming-parakeet :10300: closed"; ok=0
  fi

  # Wyoming Kokoro bridge TCP
  if (echo > /dev/tcp/localhost/10301) 2>/dev/null; then
    c_green "  wyoming-kokoro :10301: OPEN"
  else
    c_red "  wyoming-kokoro :10301: closed"; ok=0
  fi

  # LAN-priority rule (regression check — should auto-install via systemd)
  if ip rule list 2>/dev/null | grep -q "192.168.0.0/24 lookup main"; then
    c_green "  lan-priority ip rule: present"
  else
    c_red "  lan-priority ip rule: MISSING (run: sudo systemctl restart hav-lan-priority)"; ok=0
  fi

  # GPU + nvidia-smi
  if nvidia-smi -L >/dev/null 2>&1; then
    c_green "  GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
  else
    c_red "  GPU: nvidia-smi failed"; ok=0
  fi

  echo
  [ "$ok" = "1" ] && c_green "All smoke tests passed." || c_red "Some smoke tests FAILED."
  return $((1 - ok))
}

case "$CMD" in
  up)
    echo "==> Bringing up the stack (idempotent — safe to re-run)"
    docker compose up -d 2>&1 | grep -vE "^\s*$"
    echo
    echo "==> Waiting for health (per service, up to 3 min each)"
    for s in "${SERVICES[@]}"; do
      container="hav-${s}"
      wait_healthy "$container" 180 || true
    done
    smoke_test
    ;;
  down)
    echo "==> Stopping all services"
    docker compose down
    c_green "Stopped."
    ;;
  restart)
    "$0" down
    "$0" up
    ;;
  status)
    docker compose ps
    echo
    smoke_test || true
    ;;
  logs)
    SVC="${2:?usage: stack.sh logs <service>}"
    docker compose logs --tail 100 -f "$SVC"
    ;;
  *)
    echo "Usage: $0 {up|down|restart|status|logs <service>}" >&2
    exit 2
    ;;
esac
