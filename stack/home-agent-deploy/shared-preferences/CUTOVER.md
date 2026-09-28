# Shared-preference browser cutover

This moves the Echo Agent origin off `home-app`, activates the account-linking
runtime in the BFF, and starts the Victoria linking service. Run it in one
Lab-acknowledged window, and complete it before any owner sign-in. Every
BFF or victoria-link restart cancels linking and preference authority for
restored sessions.

## Prerequisites

- Schema 0047 migration receipt present. `echo-identity`, `victoria-identity`,
  `link-coordinator` and `echo-preferences` are running and verified.
  `victoria-preferences` stays staged.
- Imported, identity-verified BFF (`HOME_AGENT_SHARED_BFF_IMAGE`) and Origin
  (`HOME_AGENT_SHARED_ORIGIN_IMAGE`) images.
- The `echo-agent` and `victoria-agent` tailnet origin nodes are approved, with
  key expiry disabled (`../tailnet-origins/README.md`). The Victoria browser
  certificate has been exported to the root-owned `victoria-link-tls/`
  directory, which victoria-link mounts read-only at `/tls`.
- Victoria HA is served at `home-app.<tailnet>.ts.net:10001` and its unauthenticated whoami returns 401.
- The shared-runtime backup has run once and its restore check passed.
- The updated egress helper and lifecycle hook are installed with verified digests.

## 1. Profiles and TLS

```sh
sudo python3 -I /usr/local/libexec/home-agent/shared-preferences/generate_link_profiles.py \
  --root /srv/home-agent/shared-preferences/prepared-20260928 --tailnet taild52a15.ts.net
```

It writes `echo-bff/config/link.json` and `victoria-bff/config/link.json` with
fixed endpoints only. It also issues the Victoria ingress leaf (IP SAN
`172.23.0.36`) from the retained internal CA. Existing profiles are accepted
only when byte-identical.

## 2. Rollback set, captured before anything changes

Record the running BFF `3f5917f…` and Origin `7f38a940…` image IDs. Save
root-only copies of:

- `/srv/home-agent/config/home-agent.env` and the Origin env
- `tailscale serve status --json`
- the BFF session database under `HOME_AGENT_SESSION_ROOT`, including any
  `-wal`/`-shm` files, taken with the BFF stopped

The new BFF pins its browser origins into that database on first open. Never
start the new image with interim or old origins.

## 3. Root-owned environment

These values move to the new origins. The reviewed `network_contract.py` and
`preflight.sh` read them from the env files, not from Compose overrides:

| File | Key | Value |
| --- | --- | --- |
| `home-agent.env` | `HOME_AGENT_ALLOWED_ORIGINS` | `https://echo-agent.<tailnet>.ts.net` (only; per the runbook) |
| `home-agent.env` | `HOME_AGENT_OAUTH_CLIENT_ID` | `https://echo-agent.<tailnet>.ts.net` |
| `home-agent.env` | `HOME_AGENT_OAUTH_REDIRECT_URI` | `https://echo-agent.<tailnet>.ts.net/api/agent/auth/callback` |
| `home-agent.env` | `HOME_AGENT_VICTORIA_HA_URL` and egress keys | see `../bff-egress/README.md` |
| `home-agent-origin.env` | `HOME_AGENT_WEB_PUBLIC_ORIGIN` | `https://echo-agent.<tailnet>.ts.net` |
| gateway `web-gateway.env` | `HOME_WEB_AGENT_ORIGINS` | `https://echo-agent.<tailnet>.ts.net` |

`HOME_AGENT_NATIVE_PUBLIC_ORIGIN` must not appear in the browser allowed set.

**Known impact:** Home's People map reads the Agent household same-origin
through the gateway. That read works today only because the Agent cookie on
`home-app:8443` also reaches `home-app:443`. After the move it returns 401. People
then shows its sign-in fallback, which links to the household in the Agent.
The owner accepted this for now; a compliant bridge follows preference
acceptance.

## 4. Replace Origin, then BFF, then start victoria-link

```sh
cd /opt/home/home-agent/stack
env HOME_AGENT_SHARED_ORIGIN_IMAGE=sha256:553e617… docker compose \
  --env-file home-agent-deploy/agent-origin/home-agent-origin.env \
  -f home-agent-deploy/agent-origin/compose.yml \
  -f home-agent-deploy/shared-preferences/origin-shared-link.json \
  up -d --no-deps --no-build --pull never origin
env HOME_AGENT_SHARED_BFF_IMAGE=sha256:5091abb… \
    HOME_AGENT_ECHO_BFF_ROOT=/srv/home-agent/shared-preferences/prepared-20260928/echo-bff \
  docker compose --env-file /srv/home-agent/config/home-agent.env -f home-agent-compose.yml \
  -f home-agent-deploy/shared-preferences/core-pool-limits.json \
  -f <core-runtime-0047.json> -f home-agent-deploy/shared-preferences/bff-shared-link.json \
  up -d --no-deps --no-build --pull never bff
docker compose --env-file <commissioning.env> -f home-agent-deploy/shared-preferences/compose.json \
  --profile shared-preferences up -d --no-deps --no-build --pull never victoria-link
```

Then:

- Apply the victoria-link egress contract (`../bff-egress/README.md`).
- Enable Serve on the origin nodes: `echo-agent` `--https=443`, `victoria-agent` `--tcp=443`.
- Set the gateway `HOME_WEB_AGENT_ORIGINS` and apply the reviewed Home shell
  delta. Restart `home-web-gateway` after messaging Lab.

The delta is `home-shell-preference.patch`. It adds only the connection-registry
tag and the `home-personal-memory.js` loader entry to `index.html`, the
preference launch card to `home-events.jsx`, the `HomePersonalMemory`
run/reset hooks to `home-app.jsx`, and the People sign-in fallback in
`home-people.jsx`. That fallback now links to the Agent origin, where the
household remains viewable (the owner accepted this interim behavior). Every Lab hunk in the installed files is
kept.

It was generated against these installed base files. Apply it only if they
still match; otherwise regenerate it against the new base:

```text
f08145ad101c6517b481eacadb9be10f29aee8bf03cf9d4a3b888c5a80c2be65  app/src/index.html
181e19b39233f7a744dd268e106a32e085d2308f1090a506c0bd0abde8042dfb  app/src/home-events.jsx
8698d37fe21dbbd1d8ac64b67251b3a93b67c82119f8783a80633868a28ae952  app/src/home-app.jsx
d6f325bc28cf74ce98ab455f6335f7f2af0cdd87ab6d3dd81632f2b2c7e482a5  app/src/home-people.jsx
```

```sh
cd /home/marcelo-lima/code/home
sha256sum -c <pinned list>
cp -a app/src/{index.html,home-events.jsx,home-app.jsx} <root-only backup dir>/
patch -p1 --dry-run < <reviewed>/home-shell-preference.patch
patch -p1 < <reviewed>/home-shell-preference.patch
git -C <reviewed checkout> show <commit>:app/src/home-personal-memory.js > app/src/home-personal-memory.js
git -C <reviewed checkout> show <commit>:app/src/home-connection-registry.js > app/src/home-connection-registry.js
```

## 5. Verify, then retire `home-app:8443`

- `agent-origin/network_contract.py --require-origin`, `preflight.sh`, and
  `firewall_contract.py verify` for both profiles.
- An unauthenticated GET `/api/agent/auth/session` on echo-agent reports
  `personal_memory_enabled`, and the Home origin is listed.
- victoria-link logs show `Victoria linking listeners ready`. A TLS probe to
  `172.23.0.36:9451` succeeds. The HA token endpoint is reachable from the
  container, well under 10 s.
- Negatives:
  - a callback canary never appears in logs
  - cookies are `Secure; HttpOnly; SameSite=Strict`
  - a wrong Host or a forwarded header on victoria-agent is rejected
- In the browser, `window.HomePersonalMemory` exists and `HG_AGENT_ORIGIN` is
  echo-agent. Camera chat, HA chat and Lab still work.

Then run `sudo tailscale serve --https=8443 off` on the primary node. Never use
`reset`.

## Rollback

While victoria-link is still running, disable
`home-agent-victoria-link-egress-verify.timer` and run `firewall_contract.py
remove --profile victoria-link` (its live check needs the running container).
Then stop victoria-link and turn off the origin-node Serve handlers. Restore the
saved env files and the `:8443` handler. Recreate the old Origin `7f38a940…`
and old BFF `3f5917f…`, putting back the saved session database if the new
image opened it. Revert the gateway env and shell delta. Private Core services
and schema 0047 stay as they are.
