#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/.." && pwd)"
service_name="${HOME_APARTMENT_ASSET_SERVICE_NAME:-home-apartment-assets}"
node_bin="${NODE_BIN:-$(command -v node || true)}"

if [[ -z "${node_bin}" ]]; then
  echo "node was not found on PATH. Install Node.js before running this script." >&2
  exit 1
fi

install_user="${SUDO_USER:-$(id -un)}"
install_group="$(id -gn "${install_user}")"
asset_host="${HOME_APARTMENT_ASSET_HOST:-0.0.0.0}"
asset_port="${HOME_APARTMENT_ASSET_PORT:-5190}"
asset_origin="${HOME_APARTMENT_ASSET_ORIGIN:-*}"
unit_path="/etc/systemd/system/${service_name}.service"
tmp_unit="$(mktemp)"

cleanup() {
  rm -f "${tmp_unit}"
}
trap cleanup EXIT

{
  printf '[Unit]\n'
  printf 'Description=Engineered Lighting Home apartment asset server\n'
  printf 'After=network-online.target tailscaled.service\n'
  printf 'Wants=network-online.target tailscaled.service\n\n'
  printf '[Service]\n'
  printf 'Type=simple\n'
  printf 'User=%s\n' "${install_user}"
  printf 'Group=%s\n' "${install_group}"
  printf 'WorkingDirectory=%s\n' "${repo_root}"
  printf 'Environment=HOME_APARTMENT_ASSET_HOST=%s\n' "${asset_host}"
  printf 'Environment=HOME_APARTMENT_ASSET_PORT=%s\n' "${asset_port}"
  printf 'Environment=HOME_APARTMENT_ASSET_ORIGIN=%s\n' "${asset_origin}"
  printf 'ExecStart=%s %s\n' "${node_bin}" "${repo_root}/tools/serve-apartment-assets.mjs"
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
