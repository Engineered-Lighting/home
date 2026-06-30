#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/.." && pwd)"
service_name="${HOME_WEB_SERVICE_NAME:-home-web-gateway}"
node_bin="${NODE_BIN:-$(command -v node || true)}"

if [[ -z "${node_bin}" ]]; then
  echo "node was not found on PATH. Install Node.js before running this script." >&2
  exit 1
fi

install_user="${SUDO_USER:-$(id -un)}"
install_group="$(id -gn "${install_user}")"
install_home="$(getent passwd "${install_user}" | cut -d: -f6)"
auth_file="${HOME_WEB_AUTH_FILE:-${install_home}/.config/EngineeredLightingHome/web-auth.json}"
assets_dir="${HOME_WEB_APARTMENT_ASSETS_DIR:-${repo_root}/app/data/apartment}"
unit_path="/etc/systemd/system/${service_name}.service"
tmp_unit="$(mktemp)"

cleanup() {
  rm -f "${tmp_unit}"
}
trap cleanup EXIT

sudo install -d -o "${install_user}" -g "${install_group}" "$(dirname -- "${auth_file}")"

{
  printf '[Unit]\n'
  printf 'Description=Engineered Lighting Home web gateway\n'
  printf 'After=network-online.target tailscaled.service\n'
  printf 'Wants=network-online.target tailscaled.service\n\n'
  printf '[Service]\n'
  printf 'Type=simple\n'
  printf 'User=%s\n' "${install_user}"
  printf 'Group=%s\n' "${install_group}"
  printf 'WorkingDirectory=%s\n' "${repo_root}"
  printf 'Environment=HOME_WEB_HOST=%s\n' "${HOME_WEB_HOST:-127.0.0.1}"
  printf 'Environment=HOME_WEB_PORT=%s\n' "${HOME_WEB_PORT:-5181}"
  printf 'Environment=HOME_WEB_AUTH_REQUIRED=%s\n' "${HOME_WEB_AUTH_REQUIRED:-0}"
  printf 'Environment=HOME_WEB_AUTH_FILE=%s\n' "${auth_file}"
  printf 'Environment=HOME_WEB_HA_TARGET=%s\n' "${HOME_WEB_HA_TARGET:-http://192.168.0.125:8123}"
  printf 'Environment=HOME_WEB_METRICS_TARGET=%s\n' "${HOME_WEB_METRICS_TARGET:-http://192.168.0.100:8092}"
  printf 'Environment=HOME_WEB_VLLM_TARGET=%s\n' "${HOME_WEB_VLLM_TARGET:-http://192.168.0.100:8000}"
  printf 'Environment=HOME_WEB_VISION_TARGET=%s\n' "${HOME_WEB_VISION_TARGET:-http://192.168.0.100:8091}"
  printf 'Environment=HOME_WEB_INTELLIGENCE_TARGET=%s\n' "${HOME_WEB_INTELLIGENCE_TARGET:-http://192.168.0.100:8095}"
  printf 'Environment=HOME_WEB_SUPERVISOR_TARGET=%s\n' "${HOME_WEB_SUPERVISOR_TARGET:-http://home-app.taild52a15.ts.net:8093}"
  printf 'Environment=HOME_WEB_STACK_TOKEN_FILE=%s\n' "${HOME_WEB_STACK_TOKEN_FILE:-/opt/home-ai-voice/.env}"
  printf 'Environment=HOME_WEB_S2S_TARGET=%s\n' "${HOME_WEB_S2S_TARGET:-http://192.168.0.100:8094}"
  printf 'Environment=HOME_WEB_TRACKER_TARGET=%s\n' "${HOME_WEB_TRACKER_TARGET:-http://192.168.0.100:8098}"
  printf 'Environment=HOME_WEB_VIDEO_LABELER_TARGET=%s\n' "${HOME_WEB_VIDEO_LABELER_TARGET:-http://192.168.0.100:8099}"
  printf 'Environment=HOME_WEB_FRIGATE_TARGET=%s\n' "${HOME_WEB_FRIGATE_TARGET:-http://192.168.0.125:5000}"
  printf 'Environment=HOME_WEB_APARTMENT_ASSETS_DIR=%s\n' "${assets_dir}"
  printf 'ExecStart=%s %s\n' "${node_bin}" "${repo_root}/web-gateway/server.mjs"
  printf 'Restart=always\n'
  printf 'RestartSec=3\n'
  printf 'NoNewPrivileges=true\n\n'
  printf '[Install]\n'
  printf 'WantedBy=multi-user.target\n'
} > "${tmp_unit}"

sudo install -m 0644 "${tmp_unit}" "${unit_path}"
sudo systemctl daemon-reload
sudo systemctl enable --now "${service_name}.service"
sudo systemctl status --no-pager --lines=20 "${service_name}.service"
