# Dedicated Home Agent web origin

This deployment is a separate process, container, network edge, and Compose
project from `web-gateway`. Its immutable image contains and serves exactly
four built files below `/home-agent/` and an explicit allowlist of browser BFF
routes below `/api/agent/`. It has no legacy UI, Basic-auth route, generic
proxy, native bearer route, health URL, websocket upgrade, HA bearer, or Core
credential.

The origin has no Docker host port and joins only `home-agent_api-net`, an
IPv4-only `internal: true` network with no external gateway. Tailscale Serve connects
from the host directly to the origin's reviewed static address. The network's
dynamic range is disjoint from that address, preventing restart drift and
allocator collision.

The process has no per-request logger. OAuth callback query strings pass to
the BFF but are never interpolated into startup, error, access, or application
logs. Docker's bounded `local` log contains only fixed lifecycle messages.

## Locked network contract

The production defaults are:

```text
API subnet:       172.23.0.0/16
Gateway:          172.23.0.1
Dynamic range:    172.23.128.0/17
Agent origin:     172.23.0.10
Serve target:     http://172.23.0.10:8096
```

The origin and gateway are outside the dynamic range. Changing any value
requires a reviewed update to both environment files, the preflight evidence,
and the Serve target. Never select an address from `IPRange`.

Run the hermetic tests before touching the live network:

```sh
cd stack/services/home-agent-origin
npm run check
npm test
cd ../../../stack/home-agent-deploy/agent-origin
python3 -m unittest -v test_network_contract.py
```

Render both Compose projects and verify the origin has `ipv4_address` but no
`ports`, and the base `api-net` has `internal: true` plus exact IPAM:

```sh
docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f ../../home-agent-compose.yml config >/tmp/home-agent-base.rendered.yml
docker compose --env-file home-agent-origin.env -f compose.yml config \
  >/tmp/home-agent-origin.rendered.yml
```

## One-time network pinning

An existing auto-allocated `home-agent_api-net` must be recreated once to add
the disjoint `IPRange`. This operation may touch only the stateless `core-api`,
`bff`, and `origin` containers. PostgreSQL, ingest, worker, edge ingress, and
the BFF session directory remain running or preserved.

Before the maintenance window:

1. Copy `home-agent-origin.env.example` to a private `home-agent-origin.env`
   and set the exact production HTTPS origin.
2. Add the three `HOME_AGENT_API_*` values from `home-agent.env.example` to
   `/srv/home-agent/config/home-agent.env`.
3. Confirm the BFF session bind remains `/srv/home-agent/sessions`.
4. Run the read-only candidate preflight. It rejects overlap with host routes,
   Tailscale routes, and every other Docker network before network removal:

   ```sh
   python3 network_contract.py \
     --base-env /srv/home-agent/config/home-agent.env \
     --origin-env home-agent-origin.env --candidate-only
   ```

5. Record `docker compose ps` and `docker network inspect home-agent_api-net`.
6. Confirm no container outside `core-api`, `bff`, and `origin` is attached to
   `home-agent_api-net`.

Then recreate only this network and its three consumers:

```sh
# Stop/remove the separate origin first so the external network can be removed.
docker compose --env-file home-agent-origin.env -f compose.yml down

cd ../..
docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml stop bff core-api
docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml rm -f bff core-api
docker network rm home-agent_api-net

# Recreate only the stateless API boundary. --no-deps leaves PostgreSQL and
# every ingest/worker/edge container untouched.
docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml up -d --no-deps core-api bff
```

Wait for both containers to become healthy. The network preflight must now
pass before starting the origin:

```sh
cd home-agent-deploy/agent-origin
python3 network_contract.py \
  --base-env /srv/home-agent/config/home-agent.env \
  --origin-env home-agent-origin.env
docker compose --env-file home-agent-origin.env -f compose.yml up -d --build
python3 network_contract.py \
  --base-env /srv/home-agent/config/home-agent.env \
  --origin-env home-agent-origin.env --require-origin
```

The origin preflight fails if the network is not internal, IPAM differs, the
reviewed address is dynamic/occupied, or the Serve target differs by even its
scheme, address, port, path, query, or fragment.

Every candidate and live preflight also requires the private origin setting,
the base BFF allowed origin/client ID/callback, and the running BFF container's
three OAuth values to match exactly. It reports only a fixed pass/fail reason;
it never prints environment contents or credentials.

## Pre-exposure validation

Obtain the target from the validated contract rather than retyping it:

```sh
target=$(python3 network_contract.py \
  --base-env /srv/home-agent/config/home-agent.env \
  --origin-env home-agent-origin.env --require-origin --print-serve-target)
test "$target" = "http://172.23.0.10:8096"
```

From the Ubuntu host, the reviewed target must serve the Agent bundle when the
exact public Host is supplied:

```sh
curl --fail-with-body -H 'Host: home-app.example.ts.net:8443' \
  "$target/home-agent/index.html" >/dev/null
```

Verify `/`, `/healthz`, `/proxy/ha/api/states`, `/native-oauth-client`,
`/api/agent/native/v1/snapshot`, and a query-bearing static asset all return
404. A mismatched Host must return 404 and a mismatched Origin must return 403.

Prove the container can reach the BFF but has no LAN or Internet route:

```sh
docker compose --env-file home-agent-origin.env -f compose.yml exec -T origin \
  node -e "fetch('http://bff:8097/healthz').then(r=>{if(!r.ok)process.exit(1)})"
docker compose --env-file home-agent-origin.env -f compose.yml exec -T origin \
  npm run verify-network
```

The verifier first rejects any IPv4 default route, independently proves the
BFF health endpoint is reachable, and then requires both external probes to
fail specifically with `ENETUNREACH` or `EHOSTUNREACH`. Connection success,
`ECONNREFUSED`, `ECONNRESET`, timeout, or any suppressed/unknown error fails the
gate.

The `--require-origin` preflight additionally inspects the exact Compose
container. It requires one network attachment, no host `PortBindings` or
published port, no IPv6 address, expected Compose labels/image/user, a read-only
root, all capabilities dropped, `no-new-privileges`, no runtime mounts, and the
reviewed memory/PID limits.

From a separate LAN host, both the Ubuntu LAN address on port 8096 and the
Docker-internal address on port 8096 must be unreachable. For example, on
Windows both commands must report `TcpTestSucceeded : False`:

```powershell
Test-NetConnection 192.168.0.100 -Port 8096
Test-NetConnection 172.23.0.10 -Port 8096
```

## Tailscale Serve exposure

Tailscale 1.98.3's `serve get-config --all` does **not** capture the node-level
TCP handlers used here; it may return only a version object. It is not a valid
backup or rollback mechanism. Save the actual baseline from `status --json`:

```sh
sudo install -d -m 0700 /srv/home-agent/config/tailscale
sudo tailscale serve status --json \
  | sudo install -m 0600 -o root -g root /dev/stdin \
      /srv/home-agent/config/tailscale/pre-agent-origin.status.json
sudo tailscale serve status
```

Before proceeding, the human-readable and JSON status must show exactly the
existing tailnet-only handlers:

```text
443   -> 127.0.0.1:5181 (TLS terminated TCP)
10000 -> 192.168.0.125:8123 (TLS terminated TCP)
```

Use the preflight-produced target and add only the scoped HTTPS listener:

```sh
sudo tailscale serve --bg --https=8443 "$target"
sudo tailscale serve status --json
sudo tailscale serve status
```

The result must retain 443 and 10000 byte-for-byte and add only tailnet-only
8443 targeting `http://172.23.0.10:8096`. Never use Funnel or `serve reset`.

From a tailnet client, repeat all positive and negative path tests. Send a
deliberately invalid callback canary without an OAuth cookie and confirm its
code/state occur in neither origin logs nor `tailscaled` journal output. Do not
use a real authorization code in diagnostics.

Only after those checks pass may a browser begin one supervised HA OAuth flow.
Verify the callback URL disappears through the no-referrer redirect, cookies
are `Secure`, `HttpOnly`, `SameSite=Strict`, and no callback query appears in
container, Tailscale, browser console, metrics, or crash output.

## Rollback

Remove only the Agent listener; never reset all Serve state:

```sh
sudo tailscale serve --https=8443 off
sudo tailscale serve status --json
docker compose --env-file home-agent-origin.env -f compose.yml down
```

The status must match the saved baseline for ports 443 and 10000. If either
legacy handler is missing, reconstruct it explicitly; never use `reset`:

```sh
sudo tailscale serve --bg --tls-terminated-tcp=443 tcp://127.0.0.1:5181
sudo tailscale serve --bg --tls-terminated-tcp=10000 tcp://192.168.0.125:8123
sudo tailscale serve status
```

The pinned internal network may remain: Core and BFF operate normally on it,
and the session bind is preserved. If network recreation fails before they are
healthy, leave 8443 off, correct the IPAM/environment contract, and repeat only
the targeted `core-api`/`bff` recreation. Do not restart PostgreSQL, ingest,
worker, edge, or legacy device-control services as part of this rollback.
