#!/bin/sh
# Refresh the Let's Encrypt certificate that a tailnet origin node exports to a
# TLS-terminating container (raw `serve --tcp` passthrough). Content-free: it
# never prints key material, only the outcome.
#
# Usage: refresh-origin-cert.sh <instance>
# Reads /etc/tailscale-origin/<instance>.env:
#   TS_ORIGIN_CERT_DIR          absolute root-owned directory (root:<group> 0750)
#                               receiving browser.crt/browser.key
#   TS_ORIGIN_CERT_GROUP        numeric group allowed to read them (e.g. 1000)
#   TS_ORIGIN_RESTART_CONTAINER optional container to restart after a change
#
# Staging happens only in this node's root-only state directory, never in a
# directory another user can write. A new certificate leaves a persistent
# restart-pending marker that is cleared only by a successful restart. A present
# /run/tailscale-origin-hold (an open owner sign-in window) defers the restart;
# a restart pending for more than 7 days fails the unit so OnFailure alerts.
set -eu
umask 077
trap 'exit 143' TERM INT HUP

instance="${1:-}"
case "$instance" in
  ''|*[!a-z0-9-]*) echo "origin certificate refresh: invalid instance" >&2; exit 64 ;;
esac
env_file="/etc/tailscale-origin/${instance}.env"
[ -f "$env_file" ] && [ ! -L "$env_file" ] || { echo "origin certificate refresh: missing $env_file" >&2; exit 78; }
# shellcheck disable=SC1090
. "$env_file"
: "${TS_ORIGIN_CERT_DIR:?origin certificate refresh: TS_ORIGIN_CERT_DIR unset}"
: "${TS_ORIGIN_CERT_GROUP:?origin certificate refresh: TS_ORIGIN_CERT_GROUP unset}"
case "$TS_ORIGIN_CERT_GROUP" in ''|*[!0-9]*) echo "origin certificate refresh: numeric group required" >&2; exit 78 ;; esac
case "$TS_ORIGIN_CERT_DIR" in /*) ;; *) echo "origin certificate refresh: cert dir must be absolute" >&2; exit 78 ;; esac
dir="$TS_ORIGIN_CERT_DIR"
[ -d "$dir" ] && [ ! -L "$dir" ] || { echo "origin certificate refresh: cert dir missing" >&2; exit 78; }
# The destination and every parent must be root-owned and not writable by
# anyone else, so no other user can redirect what root writes.
p="$dir"
while :; do
  [ ! -L "$p" ] || { echo "origin certificate refresh: symlink in cert path" >&2; exit 78; }
  owner="$(stat -c %u "$p")"; mode="$(stat -c %a "$p")"
  [ "$owner" = 0 ] || { echo "origin certificate refresh: cert path not root-owned" >&2; exit 78; }
  case "$mode" in *[2367][0-7]|*[0-7][2367]) echo "origin certificate refresh: cert path writable by others" >&2; exit 78 ;; esac
  [ "$p" = / ] && break
  p="$(dirname "$p")"
done

state="/var/lib/tailscale-origin/${instance}"
[ -d "$state" ] && [ ! -L "$state" ] && [ "$(stat -c %u "$state")" = 0 ] || {
  echo "origin certificate refresh: node state directory missing" >&2; exit 78; }
pending="$state/restart-pending"

socket="/run/tailscale-origin-${instance}/tailscaled.sock"
name="$(/usr/bin/tailscale --socket="$socket" status --json | /usr/bin/python3 -c \
  'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
case "$name" in
  "${instance}".*.ts.net) ;;
  *) echo "origin certificate refresh: node name does not match instance" >&2; exit 78 ;;
esac

work="$(mktemp -d "$state/.refresh.XXXXXX")"
trap 'rm -rf "$work"' EXIT
/usr/bin/tailscale --socket="$socket" cert --cert-file "$work/browser.crt" --key-file "$work/browser.key" "$name" >/dev/null 2>&1 || {
  echo "origin certificate refresh: tailscale cert failed for $instance" >&2; exit 75; }
/usr/bin/openssl x509 -in "$work/browser.crt" -noout -checkend 1209600 >/dev/null || {
  echo "origin certificate refresh: issued certificate expires within 14 days" >&2; exit 75; }

if cmp -s "$work/browser.crt" "$dir/browser.crt" 2>/dev/null &&
   cmp -s "$work/browser.key" "$dir/browser.key" 2>/dev/null; then
  echo "origin certificate refresh: $instance unchanged"
else
  # Mark first: if anything below fails, the next run still restarts.
  [ -e "$pending" ] || date +%s > "$pending"
  install -o root -g "$TS_ORIGIN_CERT_GROUP" -m 0440 "$work/browser.key" "$dir/.browser.key.new"
  install -o root -g "$TS_ORIGIN_CERT_GROUP" -m 0444 "$work/browser.crt" "$dir/.browser.crt.new"
  mv -f "$dir/.browser.key.new" "$dir/browser.key"
  mv -f "$dir/.browser.crt.new" "$dir/browser.crt"
  echo "origin certificate refresh: $instance installed new certificate"
fi

container="${TS_ORIGIN_RESTART_CONTAINER:-}"
if [ -z "$container" ]; then
  rm -f "$pending"
  exit 0
fi
[ -e "$pending" ] || exit 0
if [ -e /run/tailscale-origin-hold ]; then
  since="$(cat "$pending" 2>/dev/null || echo 0)"
  case "$since" in ''|*[!0-9]*) since=0 ;; esac
  if [ $(( $(date +%s) - since )) -gt 604800 ]; then
    echo "origin certificate refresh: restart of $container pending for over 7 days" >&2
    exit 1
  fi
  echo "origin certificate refresh: restart of $container pending (owner window hold)"
  exit 0
fi
if [ "$(/usr/bin/docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || true)" = "true" ]; then
  /usr/bin/docker restart --time 20 "$container" >/dev/null
  echo "origin certificate refresh: restarted $container"
fi
# Not running: it loads the current files when it next starts.
rm -f "$pending"
