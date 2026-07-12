# Dedicated Home Agent web origin

This deployment is a separate process, container, network edge, and Compose
project from `web-gateway`. Its immutable image contains and serves exactly
four built files below `/home-agent/` and an explicit allowlist of browser BFF routes below
`/api/agent/`. It has no legacy UI, Basic-auth route, generic proxy, native
bearer route, health URL, websocket upgrade, HA bearer, or Core credential.

The process has no per-request logger. OAuth callback query strings are passed
directly to the BFF but are never interpolated into startup, error, access, or
application logs. Docker's bounded `local` log contains only fixed lifecycle
messages. The source tests exercise a canary authorization code and state and
fail if either reaches console output.

## Before deployment

The origin intentionally remains undeployed until all of these checks pass:

1. The committed `app/src/home-agent/{index.html,api.js,panel.js,panel.css}`
   bundle is current; these are the only files copied from the named build
   context into the immutable origin image.
2. `home-agent-bff-1` is healthy on the existing internal
   `home-agent_api-net` Docker network.
3. `HOME_AGENT_WEB_PUBLIC_ORIGIN` exactly equals the BFF's
   `HOME_AGENT_ALLOWED_ORIGINS` and `HOME_AGENT_OAUTH_CLIENT_ID`.
4. `HOME_AGENT_OAUTH_REDIRECT_URI` is that origin plus
   `/api/agent/auth/callback`.
5. The published address is exactly `127.0.0.1`; no LAN, wildcard, or Docker
   host address is accepted for rollout.
6. `home-agent_api-net` exists with Docker's `Internal` flag set to `true`.
   The origin joins no other network, so it has no default Internet egress.
7. The legacy gateway has no `HOME_WEB_AGENT_ORIGINS` setting; browser Agent
   routes must exist only in this dedicated process.
8. The current Tailscale Serve configuration has been exported so adding port
   8443 can be rolled back without disturbing the existing 443 and 10000
   listeners.

Run the hermetic service tests:

```sh
cd stack/services/home-agent-origin
npm run check
npm test
```

Render the deployment without changing live state:

```sh
cd stack/home-agent-deploy/agent-origin
cp home-agent-origin.env.example home-agent-origin.env
# Set the exact production tailnet origin in the private env file.
docker compose --env-file home-agent-origin.env -f compose.yml config
```

The rendered `ports` entry must begin with `127.0.0.1:`. The origin must have
exactly one network, resolving to `home-agent_api-net` (or the explicitly
reviewed equivalent), and this check must print `true`:

```sh
docker network inspect home-agent_api-net --format '{{.Internal}}'
```

## Staged deployment

Do not expose Tailscale until the loopback stage is healthy:

```sh
docker compose --env-file home-agent-origin.env -f compose.yml up -d --build
docker compose --env-file home-agent-origin.env -f compose.yml ps
curl --fail-with-body \
  -H 'Host: home-app.example.ts.net:8443' \
  http://127.0.0.1:8096/home-agent/index.html >/dev/null
```

Verify `/`, `/healthz`, `/proxy/ha/api/states`, `/native-oauth-client`,
`/api/agent/native/v1/snapshot`, and a query-bearing static asset all return
404. Verify a mismatched `Host` returns 404 and a mismatched `Origin` returns
403. No generic legacy endpoint may return 2xx, 3xx, or an authentication
challenge.

Save the complete current Serve configuration before adding the listener:

```sh
sudo install -d -m 0700 /srv/home-agent/config/tailscale
sudo tailscale serve get-config \
  /srv/home-agent/config/tailscale/pre-agent-origin.json --all
sudo tailscale serve --bg --https=8443 http://127.0.0.1:8096
sudo tailscale serve status
```

The status must describe 8443 as `TLS terminated, tailnet only` and preserve
the existing 443 and 10000 handlers byte-for-byte. Never use Funnel.

From a tailnet client, verify the exact public origin and repeat the negative
path tests. Then send a deliberately invalid callback canary without an OAuth
cookie and confirm the canary occurs in none of these outputs:

```sh
since=$(date --iso-8601=seconds)
canary="callback-log-canary-$(date +%s)"
curl -sS -o /dev/null \
  "https://home-app.example.ts.net:8443/api/agent/auth/callback?code=$canary-code&state=$canary-state"
docker compose --env-file home-agent-origin.env -f compose.yml logs --since "$since" origin \
  | grep -F "$canary" && exit 1 || true
sudo journalctl -u tailscaled --since "$since" --no-pager \
  | grep -F "$canary" && exit 1 || true
```

This canary cannot be exchanged because it has no state-bound initiation
cookie. Do not use a real authorization code in diagnostics.

Only after these checks pass should a browser begin a real HA OAuth flow. The
first acceptance flow must verify the callback URL disappears through the
no-referrer redirect, the session cookie is `Secure`, `HttpOnly`,
`SameSite=Strict`, and no callback query appears in container, systemd,
Tailscale, browser console, metrics, or crash output.

## Rollback

Restore the saved complete Tailscale configuration rather than using
`tailscale serve reset`, which would also remove the legacy UI and HA HTTPS
handlers:

```sh
sudo tailscale serve set-config \
  /srv/home-agent/config/tailscale/pre-agent-origin.json --all
docker compose --env-file home-agent-origin.env -f compose.yml down
```

The BFF may remain loopback-only and healthy during rollback. Do not point
port 8443 at `web-gateway` and do not add the Agent routes to port 443.
