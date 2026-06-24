#!/usr/bin/env bash
# install.sh — one-time bootstrap for the HAV stack supervisor.
#
# Run on the Ubuntu AI box, as root (sudo):
#
#   sudo bash /opt/home/stack/services/supervisor/install.sh
#
# What this does (all idempotent — safe to re-run):
#
#   1. Creates the `hav-supervisor` system user (no shell, no home),
#      added to the `docker` group so it can talk to the daemon socket.
#   2. Creates a Python venv inside the supervisor dir, installs
#      requirements.txt into it.
#   3. Touches /var/log/hav-supervisor.log with mode 0640, owner
#      hav-supervisor:adm — so the supervisor can append and admins in
#      the `adm` group can read.
#   4. Installs the systemd unit and enables + starts it.
#
# Pre-conditions:
#   - The repo is checked out at /opt/home (so this file is at
#     /opt/home/stack/services/supervisor/install.sh).
#   - /opt/home/stack/.env exists and has STACK_TOKEN (>=16 chars) +
#     BIND_ADDR (a real LAN/Tailscale IP, NOT 0.0.0.0). The supervisor
#     refuses to start otherwise — the unit will fail and journald
#     will say why.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# STACK_DIR = parent of services/supervisor (3 dirs up from this script).
# Works whether the project lives at /opt/home/stack, /opt/home-ai-voice,
# or anywhere else — install.sh adapts.
STACK_DIR="$(cd "${HERE}/../.." && pwd)"
SVC_USER="hav-supervisor"
SVC_NAME="hav-stack-supervisor.service"
SYSTEMD_PATH="/etc/systemd/system/${SVC_NAME}"
AUDIT_LOG="/var/log/hav-supervisor.log"

echo "==> using STACK_DIR=${STACK_DIR}"
[ -f "${STACK_DIR}/scripts/stack.sh" ] || {
  echo "!! ${STACK_DIR}/scripts/stack.sh not found — install.sh expects to live at"
  echo "   <STACK_DIR>/services/supervisor/install.sh"
  exit 1
}
[ -f "${STACK_DIR}/.env" ] || {
  echo "!! ${STACK_DIR}/.env not found — create one with STACK_TOKEN and BIND_ADDR first."
  echo "   See .env.example for the required keys."
  exit 1
}

[ "$(id -u)" -eq 0 ] || { echo "must run as root"; exit 1; }

# 1. user
#
# We DO create a home dir even though the user has no login shell. Reason:
# docker compose v2 reads ${HOME}/.docker/config.json for plugin-path
# discovery; without a real HOME, the supervisor's `docker compose up -d`
# fails with "unknown shorthand flag: 'd' in -d" because the compose
# plugin doesn't get registered. Burned by this once during Phase 2
# bring-up — see runbook "Stack supervisor" troubleshooting.
if ! id "$SVC_USER" >/dev/null 2>&1; then
  echo "==> creating system user ${SVC_USER}"
  useradd --system --create-home --shell /usr/sbin/nologin "$SVC_USER"
fi
# Make sure the home dir exists + is owned correctly even if the account
# was created earlier with --no-create-home (Phase-2 retrofit).
if [ ! -d "/home/${SVC_USER}" ]; then
  echo "==> creating /home/${SVC_USER}"
  install -d -o "${SVC_USER}" -g "${SVC_USER}" -m 0750 "/home/${SVC_USER}"
fi
if ! id -nG "$SVC_USER" | tr ' ' '\n' | grep -qx docker; then
  echo "==> adding ${SVC_USER} to docker group"
  usermod -aG docker "$SVC_USER"
fi

# 2. venv
if [ ! -d "${HERE}/venv" ]; then
  echo "==> creating venv at ${HERE}/venv"
  python3 -m venv "${HERE}/venv"
fi
echo "==> installing requirements"
"${HERE}/venv/bin/pip" install --quiet --upgrade pip
"${HERE}/venv/bin/pip" install --quiet -r "${HERE}/requirements.txt"
# venv files owned by current user (root) is fine — uvicorn just needs to
# be exec'able and the supervisor user can read /opt by default.

# 3. audit log
echo "==> preparing ${AUDIT_LOG}"
touch "$AUDIT_LOG"
chown "${SVC_USER}:adm" "$AUDIT_LOG"
chmod 0640 "$AUDIT_LOG"

# 4. systemd unit — template __STACK_DIR__ placeholders so the unit works
#    whether the project lives at /opt/home/stack or /opt/home-ai-voice.
echo "==> installing ${SVC_NAME} with STACK_DIR=${STACK_DIR}"
sed "s|__STACK_DIR__|${STACK_DIR}|g" "${HERE}/${SVC_NAME}" > "$SYSTEMD_PATH"
chmod 0644 "$SYSTEMD_PATH"
systemctl daemon-reload
systemctl enable "${SVC_NAME}"
systemctl restart "${SVC_NAME}"

# Brief wait + status echo
sleep 1
if systemctl is-active --quiet "${SVC_NAME}"; then
  echo
  echo "==> ${SVC_NAME} is running."
  echo "    healthcheck (no auth): curl -s http://\$BIND_ADDR:8093/healthz"
  echo "    status   (auth):  curl -s -H \"Authorization: Bearer \$STACK_TOKEN\" http://\$BIND_ADDR:8093/api/stack/status | jq"
  echo "    logs:    journalctl -u ${SVC_NAME} -f"
else
  echo
  echo "!! ${SVC_NAME} failed to start. Check:"
  echo "   journalctl -u ${SVC_NAME} -n 40"
  echo "Common causes: STACK_TOKEN unset/short, BIND_ADDR unset/0.0.0.0, .env not readable."
  exit 1
fi
