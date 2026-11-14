#!/bin/bash
# The night probe (night_probe.py), run with sudo by the camera's service account:
#
#   sudo bash ~/victoria-camera-pilot/night-probe-run.sh <release> [lights]
#
# The probe needs the camera to itself, so this stops oak-den, runs the probe as that account with the camera's venv,
# and starts oak-den again however the probe ends (a failure, Ctrl-C or a closed terminal included). The takes stay
# in the account's private folder; it prints their folder.
set -euo pipefail
release=${1:?usage: sudo bash night-probe-run.sh <release> [lights]}
lights=${2:-low,off,reading,on}
account=${SUDO_USER:?run it with sudo from the service account}
home=$(getent passwd "$account" | cut -d: -f6)
pilot=$home/victoria-camera-pilot
if [[ ! -f $pilot/$release/capture.py || ! -f $pilot/night_probe.py ]]; then
    echo "no $release/capture.py or night_probe.py in $pilot" >&2
    exit 2
fi
out=$pilot/private/night-$(date +%Y%m%d-%H%M%S)
trap 'systemctl start oak-den; echo "oak-den: $(systemctl is-active oak-den)"' EXIT
systemctl stop oak-den
sudo -u "$account" "$home/victoria-camera/venv/bin/python" -I "$pilot/night_probe.py" \
    --release "$pilot/$release" --out "$out" --lights "$lights"
echo "takes: $out"
