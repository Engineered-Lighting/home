#!/bin/sh
# Is Home Assistant alive? Exit non-zero if not.
#
# All de-flapping, exponential re-page backoff and the RECOVERED notice come
# from the existing ntfy pipeline (OnFailure=ntfy-alert@, threshold in
# /etc/ntfy-alert.conf) -- this script only needs an honest exit code.
#
# Probes GET /auth/providers: it is served by Core's event loop (a wedged Core
# times out), needs no credentials, and is never a failed-auth event to HA's
# http.ban. The view has requires_auth = False and no @log_invalid_auth, so
# HA never calls process_wrong_login for it. Do not probe /api/ here: without
# a token that is a 401 on every run, and with one a revoked token becomes a
# failed login. Only a 200 whose body lists providers counts as reachable.
#
# Addresses come from the unit's EnvironmentFile (/etc/default/ha-reachable,
# host-local, not in git): HA_LAN_URL, HA_TAILSCALE_URL, LAN_GATEWAY.
set -u

: "${HA_LAN_URL:?set HA_LAN_URL in /etc/default/ha-reachable}"
: "${HA_TAILSCALE_URL:?set HA_TAILSCALE_URL in /etc/default/ha-reachable}"
: "${LAN_GATEWAY:?set LAN_GATEWAY in /etc/default/ha-reachable}"

# Planned-maintenance inhibitor: `touch /run/ha-maintenance` before restarting
# HA so deliberate downtime doesn't page.
[ -e /run/ha-maintenance ] && { echo "maintenance inhibitor set -- skipping"; exit 0; }

code=000
probe() {
    out=$(curl -s --noproxy '*' -m 8 -w '\n%{http_code}' "${1%/}/auth/providers" 2>/dev/null)
    code=$(printf '%s\n' "$out" | tail -n 1)
    [ "$code" = 200 ] && printf '%s\n' "$out" | grep -q '"providers"'
}

if probe "$HA_LAN_URL"; then
    echo "HA reachable via LAN"
    exit 0
fi

if probe "$HA_TAILSCALE_URL"; then
    echo "HA reachable via Tailscale (LAN path down)"
    exit 0
fi

# Blame isolation: if our own gateway is unreachable too, this is our LAN, not
# HAOS -- and ntfy could not deliver a page anyway. Log distinctly so a later
# RECOVERED notice is not misread as HAOS having been down.
if ! ping -c1 -W2 "$LAN_GATEWAY" >/dev/null 2>&1; then
    echo "LOCAL NETWORK DOWN (gateway unreachable) -- not blaming HAOS" >&2
    exit 1
fi

echo "FATAL: Home Assistant unreachable on both LAN and Tailscale (gateway is up; last HTTP code $code)" >&2
exit 1
