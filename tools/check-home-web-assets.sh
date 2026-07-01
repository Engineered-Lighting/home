#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/.." && pwd)"
assets_dir="${HOME_WEB_APARTMENT_ASSETS_DIR:-${repo_root}/app/data/apartment}"

fail() {
  echo "home web asset check failed: $*" >&2
  exit 1
}

require_file() {
  local name="$1"
  local min_bytes="${2:-1}"
  local path="${assets_dir}/${name}"
  [[ -f "${path}" ]] || fail "missing ${path}"
  local bytes
  bytes="$(wc -c < "${path}")"
  [[ "${bytes}" -ge "${min_bytes}" ]] || fail "${path} is too small (${bytes} bytes)"
  printf 'ok %-16s %s bytes\n' "${name}" "${bytes}"
}

[[ -d "${assets_dir}" ]] || fail "missing asset directory ${assets_dir}"

require_file "points.ply" 1000000
require_file "frame.json" 100
require_file "floor.json" 100
require_file "manifest.json" 100
require_file "seed-model.json" 1000

if [[ -f "${assets_dir}/apartment.spz" ]]; then
  require_file "apartment.spz" 1000000
else
  require_file "apartment.ply" 1000000
fi
if [[ -f "${assets_dir}/apartment.mobile.ply" ]]; then
  require_file "apartment.mobile.ply" 1000000
else
  printf 'warn %-16s missing; mobile photo mode will use the full scan only\n' "apartment.mobile.ply"
fi

if [[ -f "${assets_dir}/mesh.glb" ]]; then
  require_file "mesh.glb" 1000000
else
  require_file "collision.glb" 100000
fi
