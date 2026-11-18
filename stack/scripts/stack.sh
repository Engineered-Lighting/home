#!/usr/bin/env bash
# Local Home Assistant AI stack. VOICE_ENABLED=1 explicitly opts into voice.
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
VOICE_ENABLED="${VOICE_ENABLED:-0}"
case "$VOICE_ENABLED" in 0|1) ;; *) echo "VOICE_ENABLED must be 0 or 1" >&2; exit 2 ;; esac
SERVICES=(vllm vision-sidecar metrics-sidecar)
COMPOSE_ARGS=()
if [ "$VOICE_ENABLED" = 1 ]; then
  COMPOSE_ARGS=(--profile voice)
  SERVICES+=(wyoming-parakeet kokoro-tts chatterbox-tts wyoming-kokoro)
fi
# An inherited COMPOSE_PROFILES (or a .env default) must not revive retired
# voice/S2S services during an ordinary start. Voice needs the explicit flag.
compose() { COMPOSE_PROFILES="" docker compose "${COMPOSE_ARGS[@]}" "$@"; }

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

  # vLLM — recent vllm-openai:latest builds return 404 for /health; use
  # /v1/models instead, which is a stronger readiness signal anyway
  # (returns 200 only after the model is loaded into VRAM, matching the
  # supervisor's `warming -> ready` gate in services/supervisor/main.py).
  if curl -fsS -m 5 http://localhost:8000/v1/models >/dev/null 2>&1; then
    c_green "  vllm /v1/models: OK"
  else
    c_red "  vllm /v1/models: FAIL"; ok=0
  fi

  if [ "$VOICE_ENABLED" = 1 ]; then
    # Kokoro and Chatterbox
    if curl -fsS -m 5 http://localhost:8880/v1/models >/dev/null 2>&1; then
      c_green "  kokoro /v1/models: OK"
    else
      c_red "  kokoro /v1/models: FAIL"; ok=0
    fi
    if curl -fsS -m 5 http://localhost:8881/v1/audio/voices >/dev/null 2>&1; then
      c_green "  chatterbox /v1/audio/voices: OK"
    else
      c_red "  chatterbox /v1/audio/voices: FAIL"; ok=0
    fi
  else
    c_dim "  voice: disabled (VOICE_ENABLED=1 to opt in)"
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

  if [ "$VOICE_ENABLED" = 1 ]; then
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
    compose up -d 2>&1 | grep -vE "^\s*$"
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
    compose down
    c_green "Stopped."
    ;;
  restart)
    VOICE_ENABLED="$VOICE_ENABLED" bash "$0" down
    VOICE_ENABLED="$VOICE_ENABLED" bash "$0" up
    ;;
  status)
    compose ps
    echo
    smoke_test || true
    ;;
  logs)
    SVC="${2:?usage: stack.sh logs <service>}"
    compose logs --tail 100 -f "$SVC"
    ;;
  *)
    echo "Usage: $0 {up|down|restart|status|logs <service>}" >&2
    exit 2
    ;;
esac
