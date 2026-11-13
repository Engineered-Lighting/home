#!/usr/bin/env bash
# den-live's installer on the LA host (live den view, step 4). install_la.py, the owner's paste on the workstation,
# copies it with the release tarball and the private inputs into /dev/shm/den-live-install/ and runs it there:
#
#   sudo bash /dev/shm/den-live-install/install.sh <the tarball's sha256> [<the ssh host, for the undo lines>]
#
# Each step is safe to run again: one already done is checked, not redone.
#  1. Holds den-live-release.tar.gz to the sha256 given, and itself to the release's own copy.
#  2. Unpacks it to /opt/den-live/releases/<first 12 of the sha256>/ and checks every file against SHA256SUMS.
#  3. Holds the two models (YOLO26s, RTMPose-m), the pose model's manifest and rtmlib's wheel to their pinned sha256,
#     and copies the models to /opt/den-live/models/.
#  4. Builds the detector's venv in /opt/den-live/venvs/<first 12 of requirements.lock's sha256>/ with exactly the
#     lock's versions, wheels only, each with --no-deps: CPU torch from PyTorch's CPU index, rtmlib from its wheel in
#     the release, the rest from PyPI. Then it holds the venv to the lock as the detector does, and loads the models
#     once through the detector's own checks.
#  5. Writes /etc/den-live/config.json and the unit's drop-in, from settings.json and the running Home gateway. With
#     the Qwen ask set up (settings.json's asks block), the drop-in also allows Home Assistant's one address and loads
#     its token as a credential.
#  6. Installs the three private inputs root-only in /etc/den-live/private/, and the unit. With the Qwen ask set up, a
#     fourth: ha-token.txt; without it, an old one is removed.
#  7. Points /opt/den-live/current at this release and (re)starts den-live, then waits for its stream to answer.
# It prints words only, never a token or a coordinate, and ends with its undo lines. Any stop, an unexpected one too
# (the ERR trap), prints why, the undo lines and, once this run has switched releases, the line back to the old one.
set -Eeuo pipefail
umask 022

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
want="${1:-}"
ssh_host="${2:-}"
previous=""
release=""
base=/opt/den-live
etc=/etc/den-live
unit=/etc/systemd/system/den-live.service
dropin_dir=/etc/systemd/system/den-live.service.d
python=/usr/bin/python3.12
gateway=home-web-gateway.service
stream_url=http://127.0.0.1:8111/api/den-live/v1/homes/den/state
yolo_sha=646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b
pose_sha=5c0a4bf67953e6d2ac43ce15e77dc9d5d354ae18430a47d2c5963a7bc5683e3c
manifest_sha=1bd8ad0a3828efc9d5b86b603ceee0639d3e7d674db98dfc3d85baa291740585
wheel=rtmlib-0.0.16-py3-none-any.whl
wheel_sha=ba932b25f7d264d7ec4837545c5e7c392001f48b53e22e514cb1497882389eaa
work=""

say() { printf '%s\n' "$*"; }

on_la() {
  # One line the owner can run: on the workstation's PowerShell through ssh when the host is known, else on LA itself.
  if [[ -n "${ssh_host}" ]]; then
    printf '  ssh -t %s "%s"\n' "${ssh_host}" "$1"
  else
    printf '  %s\n' "$1"
  fi
}

undo() {
  say "Undo, to stop den-live and keep it from starting:"
  on_la "sudo systemctl disable --now den-live"
  say "Undo everything, also its files, settings, private inputs and state:"
  on_la "sudo systemctl disable --now den-live; sudo rm -rf ${unit} ${dropin_dir} ${etc} ${base} /var/lib/den-live /var/lib/private/den-live; sudo systemctl daemon-reload"
}

back_line() {
  if [[ -n "${previous}" && -n "${release}" && "${previous}" != "${release}" ]]; then
    say "Back to the release before this one:"
    on_la "sudo ln -sfn ${previous} ${base}/current; sudo systemctl restart den-live"
  fi
}

refuse() {
  trap - ERR
  say "den-live install: refused: $*"
  back_line
  undo
  exit 2
}

# Any command that fails unexpectedly stops here, with its line, never silently.
trap 'refuse "stopped at line ${LINENO}: ${BASH_COMMAND:0:160}"' ERR

cleanup() {
  if [[ -n "${work}" ]]; then
    rm -rf -- "${work}"
  fi
}
trap cleanup EXIT

sha_of() {
  # Empty for a file that isn't there: callers compare, so this never fails itself.
  { sha256sum -- "$1" 2>/dev/null || true; } | cut -d' ' -f1
}

pinned() {
  [[ -f "$1" && "$(sha_of "$1")" == "$2" ]]
}

pip_step() {
  # pip_step <words> <pip install arguments...>: pip's output goes to a log; on failure its last lines are shown.
  local what="$1"
  shift
  if ! "${venv}/bin/python" -m pip install --no-input --disable-pip-version-check --no-cache-dir --no-deps "$@" >>"${work}/pip.log" 2>&1; then
    tail -n 6 "${work}/pip.log"
    refuse "pip couldn't install ${what} (its last lines are above)"
  fi
}

# ---- 1. what was copied ----------------------------------------------------------------------------------------
[[ "${want}" =~ ^[0-9a-f]{64}$ ]] || refuse "give the tarball's sha256, 64 hex characters"
[[ "${EUID}" -eq 0 ]] || refuse "run it with sudo"
tarball="${here}/den-live-release.tar.gz"
[[ -f "${tarball}" ]] || refuse "den-live-release.tar.gz isn't beside install.sh"
[[ "$(sha_of "${tarball}")" == "${want}" ]] || refuse "the tarball's sha256 isn't the one given"
for name in room-model.jsonl camera-token.txt register-receipt.json settings.json; do
  [[ -s "${here}/${name}" ]] || refuse "${name} isn't beside install.sh"
done
[[ -x "${python}" ]] || refuse "LA has no ${python}: the detector's wheels are Python 3.12's"
"${python}" -c 'import ensurepip, venv' >/dev/null 2>&1 || refuse "Python 3.12's venv module is missing (Ubuntu's python3.12-venv package)"
# The Qwen ask: settings that set it up and its token come together or not at all. The drop-in's LoadCredential follows
# the settings, so a token missing beside them would stop the unit from starting.
if "${python}" -c 'import json, sys; sys.exit(0 if "asks" in json.load(open(sys.argv[1], encoding="utf-8")) else 1)' "${here}/settings.json"; then
  [[ -s "${here}/ha-token.txt" ]] || refuse "settings.json sets the Qwen ask up, but ha-token.txt isn't beside install.sh"
elif [[ -e "${here}/ha-token.txt" ]]; then
  refuse "ha-token.txt is beside install.sh, but settings.json doesn't set the Qwen ask up"
fi
# Your labels from the PC: settings that bring them and the file come together, as the token does (the drop-in's
# LoadCredential follows the settings).
if "${python}" -c 'import json, sys; sys.exit(0 if json.load(open(sys.argv[1], encoding="utf-8")).get("labels_seed") is True else 1)' "${here}/settings.json"; then
  [[ -s "${here}/room-labels.json" ]] || refuse "settings.json brings your labels, but room-labels.json isn't beside install.sh"
elif [[ -e "${here}/room-labels.json" ]]; then
  refuse "room-labels.json is beside install.sh, but settings.json doesn't bring labels"
fi
work="$(mktemp -d)"
say "The tarball matches its sha256 (${want:0:12})."

# ---- 2. the release --------------------------------------------------------------------------------------------
release="${base}/releases/${want:0:12}"
install -d -m 0755 "${base}" "${base}/releases" "${base}/venvs" "${base}/models"
if [[ ! -f "${release}/SHA256SUMS" ]]; then
  rm -rf -- "${release}.part"
  install -d -m 0755 "${release}.part"
  tar -xzf "${tarball}" -C "${release}.part" --no-same-owner --no-same-permissions
  rm -rf -- "${release}"
  mv -T -- "${release}.part" "${release}"
fi
if ! (cd -- "${release}" && sha256sum --quiet --strict --check SHA256SUMS); then   # it names any file that differs
  refuse "a file in ${release} isn't the one the release lists; remove that folder and paste again"
fi
cmp -s -- "${BASH_SOURCE[0]}" "${release}/stack/services/den-live/install.sh" || refuse "install.sh beside the tarball isn't the release's own"
observe_tree="$(find "${release}/trees" -mindepth 1 -maxdepth 1 -type d -name 'observe-*' -print -quit)"
[[ -n "${observe_tree}" ]] || refuse "the release has no observe tree"
say "Release ${want:0:12}: every file matches its SHA256SUMS."

# ---- 3. the models ---------------------------------------------------------------------------------------------
for pair in "yolo26s.pt:${yolo_sha}" "rtmpose-m-body7.onnx:${pose_sha}" "artifact.json:${manifest_sha}"; do
  name="${pair%%:*}"
  sha="${pair#*:}"
  pinned "${release}/models/${name}" "${sha}" || refuse "the release's ${name} isn't the pinned one"
  if ! pinned "${base}/models/${name}" "${sha}"; then
    install -m 0644 -- "${release}/models/${name}" "${base}/models/${name}"
  fi
done
pinned "${release}/wheels/${wheel}" "${wheel_sha}" || refuse "the release's ${wheel} isn't the pinned one"
say "Models: YOLO26s and RTMPose-m match their pinned sha256 (646f8bc3, 5c0a4bf6)."

# ---- 4. the detector's venv ------------------------------------------------------------------------------------
lock="${release}/tools/den-detect/requirements.lock"
venv="${base}/venvs/$(sha_of "${lock}" | cut -c1-12)"
if [[ ! -f "${venv}/.complete" ]]; then
  say "Building the detector's venv: about 400 MB from PyPI and PyTorch's CPU index, a few minutes..."
  rm -rf -- "${venv}"
  "${python}" -m venv "${venv}"
  grep -E '^[A-Za-z0-9._-]+==' "${lock}" >"${work}/all.txt"
  grep -Ev '^(pip|torch|torchvision|rtmlib)==' "${work}/all.txt" >"${work}/pypi.txt"
  grep -E '^(torch|torchvision)==' "${work}/all.txt" >"${work}/torch.txt"
  pip_step "pip itself" "$(grep -E '^pip==' "${work}/all.txt")"
  pip_step "the lock's packages from PyPI" --only-binary=:all: --index-url https://pypi.org/simple -r "${work}/pypi.txt"
  pip_step "CPU torch" --only-binary=:all: --index-url https://download.pytorch.org/whl/cpu -r "${work}/torch.txt"
  pip_step "rtmlib from its wheel" --no-index "${release}/wheels/${wheel}"
fi
if ! "${venv}/bin/python" -B -c 'import sys; sys.path.insert(0, sys.argv[1]); import detect
wrong = detect.unlocked(detect.lock()["versions"], detect.installed())
for line in wrong[:8]:
    print("  " + line)
sys.exit(1 if wrong else 0)' "${release}/tools/den-detect"; then
  rm -f -- "${venv}/.complete"
  refuse "the venv isn't requirements.lock's (the differences are above)"
fi
say "Loading the detector once, with its own checks (about half a minute)..."
install -d -m 0700 "${work}/detector"
ready="$("${venv}/bin/python" -B "${release}/tools/den-live/detect_worker.py" --tools-root "${observe_tree}" \
  --state "${work}/detector" --models "${base}/models" </dev/null 2>"${work}/detector.log" || true)"
if [[ "${ready}" != *'"ready":true'* ]]; then
  say "  ${ready:0:300}"
  tail -n 5 "${work}/detector.log" || true
  refuse "the detector didn't load (its words, if any, are above)"
fi
touch "${venv}/.complete"
say "Detector venv: requirements.lock's versions; the models load."

# ---- 5. the settings -------------------------------------------------------------------------------------------
pid="$(systemctl show -p MainPID --value "${gateway}" 2>/dev/null || true)"
install -d -m 0755 "${etc}" "${dropin_dir}"
if ! "${python}" -B "${release}/tools/den-live/run_service.py" --write-config --settings "${here}/settings.json" \
  --gateway-pid "${pid:-0}" --venv-python "${venv}/bin/python" --models "${base}/models" \
  --config "${etc}/config.json" --dropin "${dropin_dir}/10-local.conf"; then
  refuse "the settings couldn't be written (why is above)"
fi

# ---- 6. the private inputs and the unit ------------------------------------------------------------------------
install -d -m 0700 -o root -g root "${etc}/private"
for name in room-model.jsonl camera-token.txt register-receipt.json; do
  install -m 0600 -o root -g root -- "${here}/${name}" "${etc}/private/${name}"
done
install -m 0644 -- "${release}/stack/services/den-live/den-live.service" "${unit}"
say "Private inputs: room model, camera token and step 2's receipt, root-only in ${etc}/private."
# The Qwen ask's Home Assistant token, only when install_la.py brought one (the settings' asks block, and the drop-in's
# LoadCredential for it, come with it). Without one, an old copy goes, so the ask is off and nothing stale stays.
if [[ -s "${here}/ha-token.txt" ]]; then
  install -m 0600 -o root -g root -- "${here}/ha-token.txt" "${etc}/private/ha-token.txt"
  say "The Qwen ask: Home Assistant's token, root-only in ${etc}/private. It asks only while Frigate sees someone seated."
else
  rm -f -- "${etc}/private/ha-token.txt"
  say "The Qwen ask: off (no Home Assistant token came with this install)."
fi
# Your labels from the PC, when install_la.py brought them: a root-only seed the service copies into its own state once,
# only while it has no labels (run_service.seed_labels). Without one, an old seed goes; the service's own labels stay.
if [[ -s "${here}/room-labels.json" ]]; then
  install -m 0600 -o root -g root -- "${here}/room-labels.json" "${etc}/private/room-labels.json"
  say "Your labels: brought from the PC. den-live takes them only if it has none yet."
else
  rm -f -- "${etc}/private/room-labels.json"
fi

# ---- 7. this release, live -------------------------------------------------------------------------------------
previous="$(readlink -f "${base}/current" 2>/dev/null || true)"
ln -sfn -- "${release}" "${base}/current.part"
mv -Tf -- "${base}/current.part" "${base}/current"
systemctl daemon-reload
systemctl enable --quiet den-live.service
systemctl restart den-live.service
say "den-live started; waiting for its stream (the detector takes about half a minute)..."
answered=""
for _ in $(seq 1 45); do
  sleep 2
  if ! systemctl is-active --quiet den-live.service; then
    journalctl -u den-live.service -n 8 -o cat --no-pager || true
    refuse "den-live stopped (its last lines are above)"
  fi
  answered="$("${python}" -c 'import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=3) as reply:
        print("yes" if json.loads(reply.read()).get("people") else "")
except Exception:
    pass' "${stream_url}" || true)"
  if [[ "${answered}" == "yes" ]]; then
    break
  fi
done
journalctl -u den-live.service -n 6 -o cat --no-pager || true
if [[ "${answered}" == "yes" ]]; then
  say "den-live is live: its people stream answers on 127.0.0.1:8111, for the Home gateway's den-live route."
else
  say "den-live is running, but its stream hadn't sent a people event after 90 s; its lines are above."
fi
back_line
undo
