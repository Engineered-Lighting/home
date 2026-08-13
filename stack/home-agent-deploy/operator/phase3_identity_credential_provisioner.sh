#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 0 ]] || {
  printf '%s\n' 'identity credential provisioner accepts no arguments' >&2
  exit 64
}
[[ $(id -u) -eq 0 ]] || {
  printf '%s\n' 'identity credential provisioner requires root' >&2
  exit 77
}

readonly install_root=/usr/local/libexec/home-agent-identity-signing
readonly manifest="$install_root/installed.sha256"
[[ -f "$manifest" ]] || exit 78
(
  cd "$install_root"
  sha256sum --check --status "$manifest"
) || exit 78

exec /usr/bin/python3 "$install_root/phase3_identity_credential_provisioner.py"
