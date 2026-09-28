#!/bin/sh
# Refresh the Let's Encrypt certificate that a tailnet origin node exports to a
# TLS-terminating container (raw `serve --tcp` passthrough). Content-free: it
# never prints key material, only the outcome.
#
# Usage: refresh-origin-cert.sh <instance>
# Reads /etc/tailscale-origin/<instance>.env:
#   TS_ORIGIN_CERT_DIR          absolute directory receiving browser.crt/browser.key
#   TS_ORIGIN_CERT_OWNER        uid:gid that must read them (e.g. 1000:1000)
#   TS_ORIGIN_RESTART_CONTAINER optional container to restart after a change
# A present /run/tailscale-origin-hold file (an open owner sign-in window)
# defers the restart; the new files are installed and the restart is reported
# as pending, never forced.
set -eu
umask 077

instance="${1:-}"
case "$instance" in
  ''|*[!a-z0-9-]*) echo "origin certificate refresh: invalid instance" >&2; exit 64 ;;
esac
env_file="/etc/tailscale-origin/${instance}.env"
[ -f "$env_file" ] && [ ! -L "$env_file" ] || { echo "origin certificate refresh: missing $env_file" >&2; exit 78; }
# shellcheck disable=SC1090
. "$env_file"
: "${TS_ORIGIN_CERT_DIR:?origin certificate refresh: TS_ORIGIN_CERT_DIR unset}"
: "${TS_ORIGIN_CERT_OWNER:?origin certificate refresh: TS_ORIGIN_CERT_OWNER unset}"
case "$TS_ORIGIN_CERT_DIR" in /*) ;; *) echo "origin certificate refresh: cert dir must be absolute" >&2; exit 78 ;; esac
[ -d "$TS_ORIGIN_CERT_DIR" ] && [ ! -L "$TS_ORIGIN_CERT_DIR" ] || { echo "origin certificate refresh: cert dir missing" >&2; exit 78; }

socket="/run/tailscale-origin-${instance}/tailscaled.sock"
name="$(/usr/bin/tailscale --socket="$socket" status --json | /usr/bin/python3 -c \
  'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
case "$name" in
  "${instance}".*.ts.net) ;;
  *) echo "origin certificate refresh: node name does not match instance" >&2; exit 78 ;;
esac

work="$(mktemp -d "${TS_ORIGIN_CERT_DIR}/.refresh.XXXXXX")"
trap 'rm -rf "$work"' EXIT
/usr/bin/tailscale --socket="$socket" cert --cert-file "$work/browser.crt" --key-file "$work/browser.key" "$name" >/dev/null 2>&1 || {
  echo "origin certificate refresh: tailscale cert failed for $instance" >&2; exit 75; }
/usr/bin/openssl x509 -in "$work/browser.crt" -noout -checkend 1209600 >/dev/null || {
  echo "origin certificate refresh: issued certificate expires within 14 days" >&2; exit 75; }

if cmp -s "$work/browser.crt" "$TS_ORIGIN_CERT_DIR/browser.crt" 2>/dev/null &&
   cmp -s "$work/browser.key" "$TS_ORIGIN_CERT_DIR/browser.key" 2>/dev/null; then
  echo "origin certificate refresh: $instance unchanged"
  exit 0
fi

chown "$TS_ORIGIN_CERT_OWNER" "$work/browser.crt" "$work/browser.key"
chmod 0444 "$work/browser.crt"
chmod 0400 "$work/browser.key"
mv -f "$work/browser.key" "$TS_ORIGIN_CERT_DIR/browser.key"
mv -f "$work/browser.crt" "$TS_ORIGIN_CERT_DIR/browser.crt"
echo "origin certificate refresh: $instance installed new certificate"

container="${TS_ORIGIN_RESTART_CONTAINER:-}"
[ -n "$container" ] || exit 0
if [ -e /run/tailscale-origin-hold ]; then
  echo "origin certificate refresh: restart of $container pending (owner window hold)"
  exit 0
fi
if [ "$(/usr/bin/docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || true)" = "true" ]; then
  /usr/bin/docker restart --time 20 "$container" >/dev/null
  echo "origin certificate refresh: restarted $container"
fi
