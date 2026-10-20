#!/bin/sh
# Explicit owner activation after recording permission and private provisioning.
# No models, camera-owner changes, package installation or firewall changes.
set -eu
# The Jetson account comes from the private deploy env, never from the repo.
: "${JETSON_USER:?Set JETSON_USER (and optionally JETSON_HOME) from the private deploy env.}"
JETSON_HOME=${JETSON_HOME:-/home/$JETSON_USER}
base=$JETSON_HOME/perception-lab
unit=/etc/systemd/system/perception-lab.service
source="$base/incoming/perception-lab-victoria.service"
[ "$(id -u)" -eq 0 ] || { echo 'Run this reviewed activation with sudo.' >&2; exit 1; }
[ -f "$source" ] && [ -f "$base/private/home-capture.json" ] || {
  echo 'Recording provisioning is not ready; nothing changed.' >&2; exit 1;
}
[ ! -e "$unit" ] || { echo 'Existing system Lab unit needs explicit reconciliation.' >&2; exit 1; }
systemctl is-active --quiet oak-den.service
systemctl is-active --quiet mediamtx.service
! grep -q '@[A-Z_]*@' "$source" || { echo 'The unit still has template tokens; render it first.' >&2; exit 1; }
systemd-analyze verify "$source"
uid=$(id -u "$JETSON_USER")
runuser -u "$JETSON_USER" -- env LAB_BASE="$base" "$base/venv/bin/python" - <<'PY'
import json, os, pathlib, sqlite3
base=pathlib.Path(os.environ['LAB_BASE'])
settings=json.loads((base/'private/home-settings.json').read_text())
db=sqlite3.connect('file:'+str(pathlib.Path(settings['storage_root'])/'lab.sqlite3')+'?mode=ro',uri=True)
active=db.execute("select count(*) from sessions where state in ('starting','recording','stopping')").fetchone()[0]
db.close()
if active:raise SystemExit('Stop the active Lab recording before persistent activation; nothing changed.')
PY
rollback() {
  echo 'Activation failed; restoring the temporary Lab controller.' >&2
  systemctl disable --now perception-lab.service || true
  rm -f -- "$unit"
  systemctl daemon-reload
  runuser -u "$JETSON_USER" -- env XDG_RUNTIME_DIR="/run/user/$uid" \
    sh "$base/incoming/start-lab-temporary.sh" || true
}
trap rollback EXIT
runuser -u "$JETSON_USER" -- env XDG_RUNTIME_DIR="/run/user/$uid" \
  systemctl --user stop perception-lab-home-trial.service perception-lab-review-v2.service || true
install -o root -g root -m 0644 "$source" "$unit"
systemctl daemon-reload
systemctl enable --now perception-lab.service
sleep 1
systemctl is-active --quiet perception-lab.service
trap - EXIT
echo 'Lab controller activated. Recording still requires an explicit Home action.'
