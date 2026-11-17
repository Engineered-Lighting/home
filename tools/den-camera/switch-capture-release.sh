#!/usr/bin/env bash
# Point oak-den at a staged camera release, atomically, with a backup and an automatic rollback.
#
# Run on the den Jetson only, with sudo, after staging the release (see README.md):
#   sudo bash switch-capture-release.sh check    RELEASE CAPTURE_SHA256   # no changes
#   sudo bash switch-capture-release.sh switch   RELEASE CAPTURE_SHA256   # restarts oak-den
#   sudo bash switch-capture-release.sh rollback BACKUP_FILE              # restarts oak-den
#
# RELEASE is a directory under ~/victoria-camera-pilot holding capture.py and dependencies/.
# Only the release directory in the drop-in's ExecStart and PYTHONPATH changes; every flag, the
# unit, user, env file and resource limits stay exactly as they are. The previous release stays
# in place (perception-lab releases read their pinned capture.py by path).
set -euo pipefail
umask 022

die() { echo "switch-capture-release: $*" >&2; exit 1; }

# The service account is whoever ran sudo; the repo never names it.
account=${SUDO_USER:-}
[[ -n $account && $account != root && $(id -u "$account" 2>/dev/null) == 1000 ]] \
    || die 'run with sudo from the camera service account (UID 1000)'
home=$(getent passwd "$account" | cut -d: -f6)
pilot=$home/victoria-camera-pilot
python=$home/victoria-camera/venv/bin/python
env_file=$home/victoria-camera/private/camera.env
unit=oak-den.service
drop_dir=/etc/systemd/system/oak-den.service.d
drop=$drop_dir/90-victoria-jpeg-pilot.conf
backups=/etc/systemd/system/oak-den-pilot-backups
script=$(readlink -f "$0")

[[ $EUID == 0 ]] || die 'run with sudo'
[[ $(uname -m) == aarch64 && $(hostname) == victoria-den-jetson ]] || die 'only on the den Jetson'

probe() {  # GET a loopback camera path as the service user; the token is never printed
    runuser -u "$account" -- bash -c 'set -a; . "$1"; set +a; exec "$2" -I - "$3"' _ "$env_file" "$python" "$1" <<'PY'
import os, sys, urllib.error, urllib.request
url = 'http://127.0.0.1:%s%s' % (os.environ.get('HTTP_PORT', '8090'), sys.argv[1])
request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + os.environ['VICTORIA_CAMERA_TOKEN']})
try:
    with urllib.request.urlopen(request, timeout=5) as response:
        body = response.read(64 * 1024)
        if sys.argv[1] == '/calibration':
            import json
            print(response.status, json.dumps(json.loads(body).get('depth_correction')))
        else:
            print(response.status, body[:400].decode('utf-8', 'replace').replace('\n', ' '))
except urllib.error.HTTPError as error:
    print(error.code)
except Exception as error:
    print(0, type(error).__name__)
PY
}

wait_healthy() {  # $1 seconds
    local deadline=$((SECONDS + $1)) status=
    while (( SECONDS < deadline )); do
        if systemctl is-active --quiet "$unit"; then
            status=$(probe /health)
            if [[ $status == 200* ]]; then
                echo "healthy: ${status:0:300}"
                return 0
            fi
        fi
        sleep 3
    done
    echo "not healthy after $1 s: $(systemctl is-active "$unit" || true) ${status:-}" >&2
    return 1
}

protected_dir() {
    [[ -d $1 && ! -L $1 ]] || die "missing directory $1"
    [[ $(stat -c %u "$1") == 1000 ]] || die "$1 is not owned by UID 1000"
    (( (8#$(stat -c %a "$1") & 8#022) == 0 )) || die "$1 is group/world writable"
}

check_release() {  # $1 release, $2 sha
    local dir=$pilot/$1
    [[ $1 =~ ^pilot-[A-Za-z0-9_-]{1,72}$ ]] || die 'release must be named pilot-<something>'
    [[ $2 =~ ^[0-9a-f]{64}$ ]] || die 'expected the capture.py SHA256 (64 lowercase hex characters)'
    protected_dir "$pilot"
    protected_dir "$dir"
    protected_dir "$dir/dependencies"
    [[ -f $dir/capture.py && ! -L $dir/capture.py && $(stat -c %u "$dir/capture.py") == 1000 ]] \
        || die 'capture.py missing, a link, or not owned by UID 1000'
    (( (8#$(stat -c %a "$dir/capture.py") & 8#022) == 0 )) || die 'capture.py is group/world writable'
    [[ $(sha256sum "$dir/capture.py" | cut -d' ' -f1) == "$2" ]] || die 'capture.py SHA256 mismatch'
    echo "capture.py sha256 ok: $2"
    runuser -u "$account" -- env PYTHONPATH="$dir/dependencies" "$python" -B -c \
        'import depthai, numpy, PIL; print("imports ok: depthai", depthai.__version__, "numpy", numpy.__version__, "Pillow", PIL.__version__)' \
        || die 'dependency import failed'
    runuser -u "$account" -- env PYTHONPATH="$dir/dependencies" "$python" -B -c \
        'import ast, sys; ast.parse(open(sys.argv[1]).read()); print("capture.py parses")' "$dir/capture.py" \
        || die 'capture.py does not parse'
}

current_release() {  # the release directory the live drop-in runs
    local line
    [[ -f $drop && ! -L $drop ]] || die "expected the existing pilot drop-in $drop"
    line=$(grep -E '^ExecStart=.+/capture\.py( |$)' "$drop" | tail -n 1) || die "no capture.py ExecStart in $drop"
    [[ $line =~ $pilot/(pilot-[A-Za-z0-9_-]{1,72})/capture\.py ]] || die "ExecStart does not run a release under $pilot"
    echo "${BASH_REMATCH[1]}"
}

render() {  # $1 release, $2 sha, $3 backup; rewrites only the release directory
    local old
    old=$(current_release)
    printf '%s\n' "# Owned by switch-capture-release.sh: $1 (capture.py sha256 $2)." \
        "# Previous drop-in: $3" \
        "# Rollback: sudo bash $script rollback $3"
    grep -v '^#' "$drop" | sed -e "s#$pilot/$old/#$pilot/$1/#g"
}

install_dropin() {  # $1 = file with the exact new drop-in bytes
    local tmp=$drop_dir/.90-victoria-jpeg-pilot.conf.new
    install -m 0644 -o root -g root "$1" "$tmp"
    sync "$tmp"
    mv -f "$tmp" "$drop"  # rename(2): systemd sees the old or the new file, never a partial one
    sync "$drop_dir"
}

restore() {  # $1 backup
    [[ -f $1 && $1 == "$backups"/* ]] || die "unknown backup $1"
    install_dropin "$1"
    systemctl daemon-reload
    systemctl restart "$unit"
    wait_healthy 120 || die "restored $1 but oak-den is not healthy; inspect: journalctl -u $unit -n 100"
    echo "rolled back to $1"
}

case ${1:-} in
    check)
        [[ $# == 3 ]] || die 'usage: check RELEASE CAPTURE_SHA256'
        check_release "$2" "$3"
        echo "current release: $(current_release)"
        systemctl is-active --quiet "$unit" || echo "warning: $unit is not active" >&2
        echo "current health: $(probe /health | cut -c1-200)"
        echo '--- drop-in that switch would install ---'
        render "$2" "$3" "$backups/90-victoria-jpeg-pilot.conf.before-$2-<timestamp>"
        ;;
    switch)
        [[ $# == 3 ]] || die 'usage: switch RELEASE CAPTURE_SHA256'
        check_release "$2" "$3"
        [[ $(current_release) != "$2" ]] || die "$2 is already the running release"
        systemctl is-active --quiet "$unit" || die "$unit must be active before switching"
        install -d -m 0755 -o root -g root "$backups"
        backup=$backups/90-victoria-jpeg-pilot.conf.before-$2-$(date +%Y%m%dT%H%M%S)
        cp -p "$drop" "$backup"
        sync "$backup"
        new=$(mktemp)
        trap 'rm -f "$new"' EXIT
        render "$2" "$3" "$backup" > "$new"
        grep -q "$pilot/$2/capture.py" "$new" || die 'rendered drop-in does not run the new release'
        install_dropin "$new"
        echo "switched $drop (backup $backup)"
        echo "ROLLBACK: sudo bash $script rollback $backup"
        systemctl daemon-reload
        systemctl restart "$unit"
        if ! wait_healthy 120; then
            echo 'new release did not become healthy; rolling back automatically' >&2
            restore "$backup"
            exit 1
        fi
        echo "depth correction: $(probe /calibration)"
        echo "active. Rollback stays available: sudo bash $script rollback $backup"
        ;;
    rollback)
        [[ $# == 2 ]] || die 'usage: rollback BACKUP_FILE'
        restore "$2"
        ;;
    *)
        die 'usage: check RELEASE SHA256 | switch RELEASE SHA256 | rollback BACKUP_FILE'
        ;;
esac
