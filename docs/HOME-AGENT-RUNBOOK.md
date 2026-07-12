# Home Agent Core deployment runbook

This runbook deploys the greenfield Home Agent beside the legacy GPU stack. It
does not extend or mount Intelligence, and it does not enable model-originated
physical actions.

The repository implementation is deliberately fail-closed. Live acceptance
still requires the named operator confirmations, a seven-day record-only run,
and a successful isolated restore drill. Do not work around a failing gate by
adding broad proxy routes or mounting legacy databases.

## What is implemented

- Dedicated PostgreSQL-backed Core roles (`api`, `ingest`, `worker`) with
  bitemporal semantic records, encrypted locators, evidence roots, deterministic
  memory previews, scoped forgetting, and role-aware credentials.
- A 24-hour/100 MB row-encrypted runtime SQLite spool that is mounted outside
  the durable backup path.
- HA Edge with reviewed subscriptions, privacy filtering, encrypted delivery
  spool, epoch/sequence delivery, mTLS, application bearer, Core-synchronized
  entity/user privacy blocks, and explicit gaps.
- HA authorization-code BFF with opaque strict cookies, CSRF/Origin checks,
  browser-bound one-time state, HA revocation revalidation, restart-safe
  AES-GCM-sealed sessions, authority-side logout/expiry revocation, and a narrow
  semantic route set. HA 2026.7.1 does not enforce PKCE; the boundary does not
  claim protection from ignored verifier parameters.
- A separate `/home-agent/` surface for consent, typed preview, explicit commit,
  and status, plus a Windows-native authorization-code transport with a
  pre-bound, one-time-state loopback callback isolated in its own Tauri window.
  The deployed browser and native clients contain no initiative list, claim,
  or presentation path. Initiative domain logic remains isolated future work,
  not an exposed capability.
- Legacy containment: browser secrets/history are purged, model action tools
  are recursively denied, Intelligence is loopback/read-only with generated
  memory and capture off, and contentful metrics tracing is off.
- Reviewed People cutover with typed aliases, recognition bindings, executable
  privacy/status projection, non-authoritative relationship candidates, and
  independently receipted whole-person auto-expiry.

## Phase 0: operational containment

These are live administrative actions and are not performed by a source-tree
change. Complete and record them before connecting persistent context:

1. Revoke and rotate the HA LLAT, stack token, bridge/S2S token, external API
   key, gateway credential, and every reused bearer.
2. Install the new desktop/web build once so its startup migration removes old
   localStorage credentials and private content.
3. Confirm a model request cannot dispatch native services, scripts,
   composites, REST/file actions, lighting overrides, or gesture capture.
4. Keep Intelligence bound to loopback and leave
   `INTELLIGENCE_READ_ONLY=1`, `INTELLIGENCE_MEMORY_ENABLED=0`, multimodal ring
   and pilot off, and contentful metrics tracing off.
5. Keep every `HOME_WEB_ENABLE_LEGACY_*_PROXY` flag off, including
   `HOME_WEB_ENABLE_LEGACY_HA_PROXY`. Merely configuring `HOME_WEB_HA_TARGET`
   does not enable its broad WebSocket/service/camera/conversation proxy; with
   the flag off, the legacy Home web UI shows a quarantine page. Use the native
   Home Assistant app for device control. Video Labeler must
   use a mode-0600 `video_labeler_token`, bind `127.0.0.1`, and be reached only
   through an authenticated admin tunnel. Its container must use the dedicated
   mode-0600 `/etc/home-ai-voice/video-labeler.env`, a read-only root, dropped
   capabilities, and no Docker socket. Vision, model/chat, metrics, S2S,
   and spatial research endpoints stay loopback-bound unless a reviewed host
   address and firewall allowlist are configured.
6. Apply host default-deny firewall policy. Permit TCP 8443 only from the HA
   host to `HOME_AGENT_EDGE_BIND_ADDR`; never publish PostgreSQL or Core.
7. Run the local containment tests:

   ```sh
   npm run test:security
   npm run test:native-agent-security
   node tools/run-web-gateway-stack-token-tests.js
   (cd stack/services/home-agent-bff && npm test)
   ```

## Encrypted storage and secrets

Create and mount the approved LUKS volume first. The exact block device and key
source are operator decisions; do not paste recovery keys into `.env`, shell
history, tickets, or this repository.

`HOME_AGENT_SESSION_ROOT` is a separate UID-1000 mode-0700 runtime directory
for the BFF's sealed SQLite session ledger. Keep it on an encrypted
`/dev/mapper` mount but exclude it from off-host backups, just like
`HOME_AGENT_RUNTIME_ROOT`. The ledger exists to survive a process restart long
enough to revoke HA refresh tokens; it is not durable household knowledge.

`HOME_AGENT_ERASURE_LEDGER_ROOT` is a fourth, independent UID-10001
mode-0700 directory on encrypted storage. It must not be inside the PostgreSQL,
runtime, or session roots. Replicate its encrypted `ledger.jsonl` and
`ledger.head.json` together to an operator-controlled destination independent
of pgBackRest; PostgreSQL backups intentionally cannot roll this ledger back.
Install the commit-marker-ordered replication timer using
`stack/home-agent-deploy/operator/ERASURE-LEDGER-BACKUP.md`; its rclone
credential and staging directory must also remain on the encrypted mapper.

Copy the non-secret environment template and calculate the versioned policy
digest:

```sh
cd /opt/home/home-github/stack
cp home-agent.env.example home-agent.env
sha256sum home-agent-deploy/policy/home-agent-mvp-v1.json
```

Fill `home-agent.env`, then generate independent secrets. The helper refuses to
overwrite an existing set. It stores a root-only mode-0600 master set under
`/etc/home-agent/secrets/master` and materializes distinct mode-0400 copies
under `runtime/<service>` with the numeric owner of that unprivileged
container:

```sh
sudo sh home-agent-deploy/bootstrap-secrets.sh /etc/home-agent/secrets
```

For an existing deployment created before the isolated rollout writer, do not
rerun bootstrap and do not construct a database URL in shell history. Add the
new credential pair as one atomically published root-only directory, then let
the helper rematerialize service copies:

```sh
sudo sh home-agent-deploy/add-rollout-role-secrets.sh \
  /etc/home-agent/secrets
```

The additive helper refuses a partial or existing rollout set and never prints
the password or URL. Run normal preflight afterward; preflight never generates
credentials.

Create the independent ledger directory, then perform its only permitted
initialization. This command refuses either existing ledger file; never use it
as recovery from missing or damaged ledger state:

```sh
sudo install -d -m 0700 -o 10001 -g 10001 /srv/home-agent/erasure-ledger
sudo docker compose --env-file home-agent.env -f home-agent-compose.yml \
  --profile operator run --rm --build ledger-init
```

Do not point Compose at the master files or make them group/world-readable.
Each service-specific directory contains only the files that service needs,
and Compose mounts those files individually. The Core worker does not receive
the durable knowledge-encryption key. To rotate a value, update its protected
master file and rerun preflight to atomically refresh the per-service copies
before restarting only the affected services.

The BFF session-encryption key is the exception to blind rotation: first log
out/drain all BFF sessions, allow the revocation janitor to clear pending rows,
and verify the session database is empty. Retain the old key until then. Losing
it while rows remain would prevent HA-side refresh-token revocation.

Create a private CA, server certificate with the Edge hostname/IP in its SAN,
and a dedicated HA Edge client certificate. Install these server-side files:

```text
/etc/home-agent/tls/server.crt
/etc/home-agent/tls/server.key
/etc/home-agent/tls/client-ca.crt
```

Install the client certificate, client key, CA certificate, and a separate copy
of the master `edge_token` on the HA host. All HA-side key/token files must be
mode 0600.

Configure `pgbackrest.conf` from
`stack/home-agent-deploy/pgbackrest.conf.example` using the separately approved
off-host SFTP destination, independently verified SHA-256 host-key fingerprint,
and backup cipher secret. Store it outside the repo on the encrypted mapper.
Generate a dedicated key pair there, set `HOME_AGENT_PGBACKREST_SFTP_KEY` and
`HOME_AGENT_PGBACKREST_SFTP_PUBLIC_KEY` to those files, and install only the
public key on the non-admin, key-only SFTP account. The private key is mounted
read-only at `/run/pgbackrest-sftp/id_ed25519`; neither key belongs in the
source checkout or a container image.
Preflight sets it to `root:999` mode 0640 so PostgreSQL and the UID/GID-999
backup gate can read it without making the repository credential writable; it
also enforces the encrypted mount, SFTP host-key pin, AES-256-CBC repository
cipher, dedicated key mounts, and a UID/GID-999 mode-0700 pgBackRest spool. Keep
`pg1-user=home_agent_backup`: preflight mounts that role's separate mode-0400
password only into the backup gate. PostgreSQL uses the deployment-owned SCRAM
HBA file even for existing clusters, so the backup container never receives or
impersonates the bootstrap owner. Its SQL authority is limited to read-only
settings (not live query statistics) and the four PostgreSQL 17 backup/WAL
control functions required by pgBackRest.
Keep `repo1-bundle=y` as well: new backups are grouped into bundle files to
reduce small-file SFTP operations and improve restore resilience without
changing the repository cipher or retention rules.

Run the strict preflight as root because the Compose secret source directories
are deliberately not traversable by an ordinary host account. It verifies
absolute, mutually non-nested paths, `/dev/mapper` mounts,
master files, exact per-service ownership/modes, pgBackRest ownership, policy
digest, the rendered Compose configuration, required files, and the
unprivileged mTLS ingress configuration; it starts no application service:

```sh
sudo sh home-agent-deploy/preflight.sh ./home-agent.env
```

## Build and start

Validate the dedicated Compose project, then start it:

```sh
sudo docker compose --env-file home-agent.env -f home-agent-compose.yml config
sudo docker compose --env-file home-agent.env -f home-agent-compose.yml build
sudo docker compose --env-file home-agent.env -f home-agent-compose.yml up -d
```

Startup ordering is enforced:

```text
PostgreSQL ready
→ runtime roles provisioned
→ dedicated backup role authenticated
→ pgBackRest stanza/check/full encrypted off-host backup
→ transactional migration
→ least-privilege grants
→ API / ingest / worker
→ mTLS Edge ingress and OAuth BFF
```

PostgreSQL uses the immutable official 17.10 Bookworm image index configured in
`home-agent.env`; no database or Core port is published. A pgBackRest failure
prevents the semantic roles from starting. Raw runtime observations live only
under `HOME_AGENT_RUNTIME_ROOT`; sealed OAuth session/revocation state lives
only under `HOME_AGENT_SESSION_ROOT`. Neither path belongs in the durable
PostgreSQL or off-host backup source.

Check readiness without printing environment variables or secrets:

```sh
sudo docker compose --env-file home-agent.env -f home-agent-compose.yml ps
sudo docker compose --env-file home-agent.env -f home-agent-compose.yml logs --tail=100 backup-gate migrate grant-runtime core-api core-ingest core-worker bff edge-ingress
```

## Isolated database restore drill

After building the digest-labeled PostgreSQL/pgBackRest image, run the staged
restore drill for one explicit completed backup label. Direct libssh2 SFTP
restore is not an acceptance path: the operator stages the encrypted repository
with native host-key-pinned OpenSSH SFTP, then verifies, restores, and boots it
locally with `network_mode=none` and no published ports.

```sh
sudo bash home-agent-deploy/operator/isolated_restore_drill.sh \
  /srv/home-agent/config/home-agent.env \
  20260711-220110F
```

The drill must pass revision, seven-schema, page-checksum, schema-dump, clean
shutdown, and offline checksum validation. Detailed prerequisites, failure
retention, and cleanup guards are documented in
`stack/home-agent-deploy/operator/RESTORE-DRILL.md`.

## Web and OAuth routing

Provision a dedicated private HTTPS name such as
`agent.home.example.internal`. It must not be the origin that serves the large
legacy Home UI. Register that exact origin as the HA OAuth client and the exact
`https://agent.home.example.internal/api/agent/auth/callback` redirect. Set only
the dedicated origin in `HOME_AGENT_ALLOWED_ORIGINS`.

Do not serve this origin from `web-gateway`, even through its hostname boundary.
Deploy the separate fail-closed service in
`stack/home-agent-deploy/agent-origin/compose.yml`. Its immutable image copies
only the four built Agent assets, reaches `bff:8097` only through the existing
IPv4-only internal Core API network, and has no Docker host port or external
route. Tailscale Serve terminates TLS and targets the origin's reviewed static
internal address; it is the only permitted ingress. The BFF remains
loopback-only for native compatibility; never change its bind to a LAN or
wildcard host address.

The complete preflight, staged exposure, callback-log canary, acceptance, and
rollback procedure is in
`stack/home-agent-deploy/agent-origin/README.md`. Save the existing complete
Tailscale Serve configuration before adding the Agent listener so rollback
preserves the legacy port-443 UI and port-10000 HA HTTPS handlers.

HA Core 2026.7.1 neither documents nor enforces PKCE for this flow. The BFF is
a confidential server-side *token-handling boundary*, not an OAuth confidential
client: HA supplies no client secret. The authorization code is protected by
continuous TLS to the exact dedicated callback, random one-time state bound to
an HttpOnly `__Host-` initiation cookie, immediate server-side exchange, and a
no-referrer redirect into an opaque HttpOnly session. Ensure every proxy and
observability layer redacts or excludes the callback query string; preserving a
request-target access log fails the authentication gate. Reassess and adopt
enforced PKCE when HA implements it.

The Agent host serves only `/home-agent/*`, `/api/agent/*`, and the fixed
`/native-oauth-client` metadata page. It has no gateway Basic-login, legacy UI,
legacy proxy, health, or websocket surface. The static Agent bundle has no
parent-path dependencies. Requests carrying an Origin must exactly match the
configured Agent origin. On every other host, browser Agent static/session/API
routes and OAuth metadata return 404, even with valid gateway Basic or HA
Bearer credentials. This prevents same-origin legacy UI script execution from
reading Agent CSRF state or mutating semantic memory.

The Agent host relies on HA OAuth/BFF authentication, not the legacy gateway
Basic cookie. Open `https://agent.home.example.internal/home-agent/`; `/agent`
in the legacy web UI opens the configured dedicated origin in a separate
`noopener` tab, so the two JavaScript realms never share an opener. Browser
logout revokes its HA refresh token before the BFF deletes the local sealed
envelope. If HA is temporarily unavailable, the cookie is cleared and the
session remains fail-closed as a sealed retry row. Idle and absolute expiry use
the same bounded revocation janitor. A BFF restart reloads valid and pending
sessions while deliberately invalidating in-flight authorization state.

Startup rejects insecure/malformed browser origins, an OAuth callback outside
the exact allowed origin/path, credentialed/query-bearing HA or Core URLs, and
non-root service URLs. HA is HTTPS except explicit loopback/test use. All BFF
HA/Core fetches use redirect-error mode so credentials and semantic bodies
cannot follow a 3xx response.

The host firewall also applies when the BFF calls this host's own Tailscale HA
listener. Keep `home-agent_bff-public` pinned to the reviewed bridge, `/24`, and
single BFF source address in Compose, then follow the existing-network migration
and boundary procedure in `stack/home-agent-deploy/bff-egress/README.md`.
The reconciler checks Tailscale node identity, DNS, Docker
membership/hardening, default-deny UFW posture, the exact UFW rule, and an
anonymous `405` token-endpoint probe. It also owns the sole first IPv4 `INPUT`
jump for the BFF bridge: that chain accepts only the reviewed source,
destination, protocol, and port before dropping every other host-directed
packet from the bridge. This makes later broader accepts unreachable and makes
an earlier bypass a contract failure. The root-owned deployment environment
also pins the expected Tailscale IPv4. A reviewed `/etc/ufw/after.init` hook
reinstalls that exact first-hop guard synchronously across UFW start, stop,
reload, and flush lifecycle handling, without waiting for Docker, DNS, or the
periodic verifier. Never replace this with a subnet-wide allow or the
unencrypted HA LAN endpoint. Docker cannot retrofit IPAM or bridge options onto
the old named network; stop/remove only BFF, prove the network is empty, recreate
it, apply and verify the new boundary, then retire the exact old commented UFW
rule as the documented procedure requires.

The legacy Intelligence proxy and contentful metrics proxy remain disabled
unless explicitly re-enabled with their containment override variables; they
are never Core retrieval sources.

### Windows native OAuth

The packaged Windows Agent window uses a different native OAuth redirect from
the browser BFF. Configure the non-secret native endpoints in the Windows
process environment exactly as documented in
`docs/HOME-AGENT-NATIVE-OAUTH.md`. The `HOME_NATIVE_AGENT_BFF_URL` value is the
private HTTPS web-gateway origin, never the deployment's loopback HTTP BFF.
Set `HOME_AGENT_NATIVE_PUBLIC_ORIGIN` in the Ubuntu deployment environment to
that exact same origin. It must also match the dedicated web-gateway
`HOME_WEB_NATIVE_AGENT_ORIGIN` entry. Do not copy the browser
`HOME_WEB_AGENT_ORIGINS`, the browser
`HOME_AGENT_OAUTH_CLIENT_ID` or the separate Agent-web
`HOME_AGENT_WEB_PUBLIC_ORIGIN`: native proofs use a distinct audience, and the
deployment preflight rejects a missing, non-canonical, non-HTTPS, or browser-
equal native origin. It also rejects the native origin if it appears anywhere
in the browser `HOME_AGENT_ALLOWED_ORIGINS` set, including as a secondary
origin.

The native flow is not PKCE-protected because HA ignores verifier parameters.
Its supported boundary pre-binds the exact loopback listener before opening the
trusted system browser, requires one random state exactly once, validates the
callback Host/path/query, and exchanges the bearer code immediately in Rust.
The code and tokens never enter the webview. Treat a hostile browser extension
or local process that can read callback URLs as outside this canary's threat
model; leave native login disabled on an untrusted Windows profile.

The dedicated Agent host publishes `/native-oauth-client` without Basic auth;
that sub-10 KiB document contains the exact HA client-metadata redirect link
and no application/session data. Exact typed native Agent routes also bypass
gateway Basic because HA Bearer and Basic share the `Authorization` header.
The bearer is preserved to the BFF, which immediately validates HA `whoami`.
Native typed routes remain usable during origin migration, but they never gain
browser cookie/session semantics. Non-Agent routes on the Agent host are
denied, and browser Agent routes on legacy hosts are denied.

### Native installation registry

Native OAuth proves the HA user, but the BFF also requires an independently
enrolled Windows installation for every native semantic request. The registry
authority is the root-owned mode-`0600`
`$HOME_AGENT_SECRETS_DIR/master/native_installations.json` file on the verified
encrypted mapper. A missing, malformed, empty, revoked, key-mismatched, or
HA-user-mismatched record contains native semantics while leaving browser OAuth
and record-only ingest available.

Use the configured deployment environment and fixed operator tool; never edit
the registry directly:

```sh
cd /opt/home/home-github/stack
ENV_FILE=/srv/home-agent/config/home-agent.env
set -a
. "$ENV_FILE"
set +a
REGISTRY="${HOME_AGENT_SECRETS_DIR:?}/master/native_installations.json"
```

Fresh deployments created by `bootstrap-secrets.sh` already contain an empty
schema-v1 registry and must not run this command. On an existing deployment
that predates native attestation, run this one-time initialization only when
the registry is absent, then continue to preflight:

```sh
printf '%s\n' '{"action":"initialize"}' | \
  sudo python3 home-agent-deploy/operator/manage_native_installations.py "$REGISTRY"
```

On the trusted Windows client, copy only `installation_id` and `public_jwk` from
the native-only **Public installation enrollment material** card backed by
`native_auth_status`; the private installation key remains in Windows
Credential Manager. The selectable card has no clipboard API or enrollment
network action, never appears in browser sessions, and does not mean enrollment
is complete. Independently verify the exact HA user UUID through authenticated
HA `whoami`. Then start the writer without putting reviewed identity material
in the command line, shell history, or a temporary file:

```sh
sudo python3 home-agent-deploy/operator/manage_native_installations.py "$REGISTRY"
```

Paste one bounded JSON object on stdin, then send EOF. Field names and values
must have this exact shape; `kid` must equal `installation_id`, and `x`/`y` are
the 32-byte base64url P-256 coordinates returned by `native_auth_status`:

```json
{"action":"enroll","installation":{"installation_id":"<uuid-v4>","ha_user_id":"<verified-ha-user-uuid>","status":"active","public_key_jwk":{"kty":"EC","crv":"P-256","x":"<x>","y":"<y>","kid":"<same-uuid-v4>"}}}
```

To revoke an installation, run the same writer and provide this object on
stdin, then EOF:

```json
{"action":"revoke","installation_id":"<enrolled-uuid-v4>"}
```

Enrollment is idempotent only for the exact existing record. A UUID cannot be
rebound, and a revoked record cannot be reactivated. Revoke a lost, reinstalled,
reassigned, or replaced client and enroll its newly generated UUID/key instead.

If the Windows installation credential is corrupt or lost, complete this exact
recovery order:

1. Revoke the old registry UUID and force-recreate the BFF container as below.
2. Explicitly delete the Windows Credential Manager target
   `EngineeredLighting.HomeAgent/installation-attestation/v1`.
3. Restart the signed app so it generates a new installation UUID and key.
4. Verify the new public card and enroll its UUID/JWK offline for the exact HA
   user UUID, then force-recreate the BFF container again.

There is no automatic key rotation, re-enrollment, reactivation, or registry
replacement path.

On the first initialization, continue through the normal preflight and initial
Compose startup below. After any registry change on a running deployment,
rematerialize the unprivileged read-only BFF copy and recreate the BFF so it
reads the new registry at process startup:

```sh
sudo sh home-agent-deploy/preflight.sh "$ENV_FILE"
sudo docker compose --env-file "$ENV_FILE" -f home-agent-compose.yml \
  up -d --no-deps --force-recreate bff
curl -fsS "http://127.0.0.1:${HOME_AGENT_BFF_PORT:-8097}/healthz"
```

Require `native_attestation_configured:true` and a healthy BFF without printing
the registry. An initialized empty registry satisfies configuration health but
correctly authorizes no native installation. Revocation takes effect only after
the BFF container is force-recreated; a plain restart retains the old
bind-mounted inode and is insufficient. Treat container replacement as part of
the revocation transaction.

## HA Edge

Copy `ha-config/home_agent_edge/` into HA's custom-components location using the
repository's HA deployment process, restart HA, then create the integration.
Configure:

- the exact `person`, active `device_tracker`, and reviewed zone entity IDs;
- blocked entity/user IDs implementing privacy directives;
- the HTTPS `/v1/ingest/envelopes` Edge endpoint;
- the same mTLS origin's exact `GET /v1/ingest/privacy-policy` endpoint;
- server CA, client certificate/key, spool encryption key, and bearer-token
  file paths;
- an encrypted local spool path, 24-hour age, and 100 MB size;
- conversation content hard-disabled (there is no runtime text toggle).

The raw spool path must be on encrypted runtime storage excluded from Home
Assistant and off-host backups. The integration rejects `/config`, `/backup`,
and `/share`; its privacy-safe `/tmp/home_agent_edge/runtime.sqlite` default is
ephemeral. For restart/replay durability, mount a dedicated host directory at
that path without adding it to a backup set. Keep the separate spool key file
mode 0600. Loss of the runtime mount is a coverage gap, never permission to
reconstruct or retain coordinates elsewhere.

Do not select cameras, broad state wildcards, scripts, scenes, switches, or
action events. Verify restart, duplicate, reordering, source-switch, malformed
input, and overflow tests before record-only observation begins.

Before semantic cutover, every ignored/do-not-track person must have every
tracked HA entity and HA user UUID present in the Edge static blocked lists.
Core synchronizes reviewed bindings dynamically and rejects racing/unknown-user
events, but it cannot infer an HA entity that was never reviewed or bound.

## Explicit identity and locality confirmation

First call HA's authenticated view and record the returned HA UUID without the
access token:

```text
GET /api/home_agent_edge/whoami
```

Then run the container-local interactive bootstrap. It reads the route-scoped
service and operator bearers plus the bootstrap secret from mounted files,
never prints them or coordinates, and does not enable either location opt-in:

```sh
sudo docker compose --env-file home-agent.env -f home-agent-compose.yml exec core-api \
  python /operator/provision_identity.py
```

The deployed workflow currently supports only the exact
HA-user-to-Marcelo binding ceremony. Tracker binding, explicit parent facts,
and the private Itaipava locality remain disabled pending their separate
reviewed flows. Import reviewed stable UUIDs when available. Do not use
implicit legacy `parent` labels as `parent_of` facts.

In particular, neither browser nor native BFF accepts direct parent
confirmation. Before either explicit parent fact can be enabled, Core must
stage the complete exact candidate set, render a private digest-bound preview,
and commit both reviewed edges atomically from one authenticated confirmation.
Client-supplied parent/child UUIDs are not authority. Until that flow and its
adversarial tests are deployed, parent role expansion remains unresolved.

After binding, `/home-agent/` may show the two stored location-preference
booleans during record-only or shadow operation, but only as a rollback privacy
surface. Precise-location retention, visit projection, teaching, and both
opt-ins remain disabled until a separately authorized canary. If an enabled
value survived a rollback, browser and native clients retain no visit or
principal identifiers from the snapshot and offer only a direction-fixed
Disable control. Travel greetings also have no deployed list, claim, or
presentation path; a separate reviewed initiative-capability gate is still
required. A later canary place-teaching UI must still require a current
supported visit and a second explicit preview confirmation.

## Reviewed legacy Identity Store migration

Use the one-shot `identity-migration` Compose profile only after the legacy
Identity Store writer is stopped and its SQLite WAL has been checkpointed.
Verify that neither `<database>-wal` nor `<database>-journal` exists; the tool
also refuses to open a source with either sidecar present. Do not copy the
legacy database into Core, the repository, or a backed-up directory.

Grant the fixed migration UID read access to the stopped source file without
making it group/world-readable, then start the profile with an absolute source
path:

```sh
sudo setfacl -m u:10001:r-- /private/path/identity.sqlite
sudo env HOME_AGENT_LEGACY_IDENTITY_DB=/private/path/identity.sqlite \
  docker compose --env-file home-agent.env -f home-agent-compose.yml \
  --profile operator run --rm identity-migration
```

At the prompt, enter `/legacy/identity.sqlite`. With the default answers the
tool performs a counts-and-digests-only review and sends no Core writes. It
opens SQLite with `mode=ro` and `query_only`, validates schema version 1, and
installs a SQLite authorizer that permits only the selected identity, alias,
enrollment, role, and relationship columns. It cannot read notes, preferences,
generated content, face-crop metadata, change-log snapshots, or pending-write
payloads.

Apply mode requires `HOME_AGENT_ROLLOUT_MODE=shadow`. Before collecting any
item confirmation or issuing any write, it fetches Core's authenticated
`GET /v1/operator-rollout` contract and accepts only the exact typed response
`{mode: shadow, source: deployment_policy, semantic_people_writes: true,
persistent_memory_writes: false, ingest_projection: true}`.
It then fetches the fixed authenticated `GET /v1/operator-capabilities`
contract, requires an exact confirmation for the review digest and every item,
and calls only the typed People migration routes. Stable UUID import requires the reviewed source
digest; a retry is idempotent only when every projected field and provenance
value match exactly. The profile receives a dedicated operator-audience token
and bootstrap token, but no BFF service token, database URL, knowledge key,
runtime spool key, or PostgreSQL network. It mounts the legacy database
read-only, requires a private TTY, and uses Docker logging driver `none`.

Aliases, Frigate recognition bindings, privacy directives, archived status,
and explicit relationship candidates use exact typed endpoints. `ignored` and
legacy `do_not_identify` import only a suppressed placeholder plus the blocking
directive; aliases, recognition bindings, and roles are skipped. A legacy
`parent` classification or relationship remains a non-authoritative candidate
with unknown perspective. No direct parent-confirmation endpoint exists in
Core or either BFF. A safety-critical directive failure stops later dependent
operations for that person.

Optional review artifacts contain counts, stable UUIDs, source digests,
endpoint requirements, and a plan digest only. The tool creates them
exclusively on POSIX storage with mode 0600 and refuses an existing path.
Remove the temporary source ACL after review:

```sh
sudo setfacl -x u:10001 /private/path/identity.sqlite
```

Run the synthetic safety suite before live review:

```sh
python -m unittest discover -s stack/home-agent-deploy/operator/tests -v
```

## Record-only, shadow, and canary gates

Every fresh deployment starts with `HOME_AGENT_ROLLOUT_MODE=record_only`.

Keep Edge/Core in record-only operation for at least seven days and until 500
qualifying identity-redacted location-transition envelopes have been observed.
Controlled journeys remain an informational diagnostic and cannot authorize
live advancement until a separately reviewed pre-canary consent mode exists.
Confirm:

- duplicates/replays preserve one stable visit identity;
- gaps and snapshot recovery never manufacture an arrival;
- exact raw coordinates are absent from PostgreSQL logs and off-host backups;
- location persistence and visit projection are absent throughout record-only
  and shadow, even if a stale enabled preference row survived rollback;
- authenticated opt-out remains available in every rollout mode, while the
  clients expose no non-canary enable control;
- private initiatives are absent from web snapshots and every deployed client;
  no bearer-authenticated list, claim, or presentation route is accepted;
- a tracker switch opens conflict rather than silently merging evidence;
- stale or insufficient fixes never create a specific property anchor.

The read-only operator endpoint
`GET /v1/operator-rollout/phase2-readiness` is the canonical counter for this
gate. It requires both the operator bearer and offline bootstrap credential and
returns the fixed `phase2-record-only-gate-v3` JSON contract. The observation
window begins at the first qualifying redacted transition envelope's database
ingest time; older conversation, snapshot, or other irrelevant headers cannot
age the gate. Advancement counts only accepted location-related `state_changed` or
`location_fix` envelopes with continuous or Recorder-reconstructed coverage
whose durable header proves identity, HA context, source event ID, and raw
payload were suppressed. Conversation metadata, raw-retained location, and
headers suppressed because the retention worker was unavailable never advance
this gate. Startup snapshots, snapshot-only recovery, coverage gaps, unknown
coverage, duplicates, and quarantine rows never advance it.

The endpoint never discovers or auto-counts visits. To inspect a controlled
journey, repeat a paired `controlled_principal_id=<uuid>` and
`controlled_journey_id=<uuid>` query parameter. Selection is an operator
attestation held only for that request; it is not persisted. Core counts the
selected journey only when RLS resolves that exact principal/visit pair and
the visit:

- was created within the current observation window after explicit
  `location_memory` consent (the consent row's timestamp must be no later than
  the visit creation time, so later consent cannot qualify old evidence);
- is departed, has sufficient coverage, and contains at least ten minutes of
  stable dwell;
- begins at a continuous `device_tracker` transition root from exactly one
  Edge stream; and
- has neither a snapshot/gap envelope nor a gap/unknown/snapshot-only coverage
  interval overlapping its observed range.

An empty journey query therefore reports zero journeys even if visit rows
exist. A qualifying journey is still informational and never changes
`ready_to_advance`. This keeps the default-off consent path intact: record-only
advances only through 500 redacted envelope headers. `ready_to_advance=true`
requires the seven-day window, the 500-envelope threshold, a current fenced
worker-maintenance proof, and the deployment still being in `record_only`; the
endpoint cannot change rollout mode. The response exposes only a categorical
worker status, not its instance ID, timestamps, retention counts, or errors.

After reviewing a ready v3 response, create a JSON object with a new random
UUIDv4/UUIDv7 `operator_request_id` and the response's exact values renamed to
`expected_rule_version`, `expected_policy_version`,
`expected_policy_digest`, and `expected_input_digest`. Feed that content-free
object to the isolated operator-profile writer:

```sh
cd /opt/home/home-github/stack
docker compose --env-file /srv/home-agent/config/home-agent.env \
  --profile operator run --rm -T rollout-authorize < /root/shadow-request.json
```

The one-shot is the only service that receives the `home_agent_rollout`
database credential. It has no API, port, API network, bearer, knowledge key,
spool key, or ledger key; Core API is SELECT-only on receipts. Before writing,
it verifies the exact migration, independent erasure-ledger head, and normal or
warning storage budget. It then recomputes readiness and snapshots the current
worker proof inside the serializable receipt transaction. An exact retry
returns the same receipt, while a reused
request UUID, policy drift, rule drift, or evidence drift fails closed. The
public receipt stores random IDs, mode transition, policy/rule/input digests,
worker kernel version, worker success sequence, worker proof digest, and
timestamps. The database-only snapshot also retains the random worker instance
ID and maintenance-success timestamp needed to verify that proof. All fields
are content-free—never names, entities, payloads, or coordinates. No online
authorization endpoint exists, and the one-shot never changes
`HOME_AGENT_ROLLOUT_MODE`.

Core health exposes the locked resource budget. Durable-volume free space is
`warn` at 20%, suspends optional API mutations at 15%, and enters
privacy-essential/read-only degraded mode at 10%; ingest then receives 507 and
HA Edge retains or expires its bounded spool with explicit gaps. Health also
alerts above 1,000 location events in 24 hours or 100 MiB of location payloads
in seven days. These thresholds are deployment policy, not environment-tunable
model inputs.

Health and authenticated snapshots report effective capability, not merely a
stored preference. In record-only and shadow, `persistent_memory`,
`location_retention`, `location_visits`, and `preference_opt_in` are
`disabled`; `preference_opt_out` remains `enabled`. The authenticated snapshot
still returns the two stored booleans for revocation, but returns no visit
metadata. Only canary may report location retention/visits as consent-gated or
effective.

Only after the receipt is durably committed, stop Core, set
`HOME_AGENT_ROLLOUT_MODE=shadow`, rerun `preflight.sh`, and restart. Every Core
role recomputes the stable first-500 evidence digest and refuses startup if the
receipt is missing or mismatches the policy, rule, or evidence. Shadow is
bounded by append-only envelope headers: the ingest role cannot update, delete,
or truncate accepted envelopes, and a database trigger independently rejects
those mutations while preserving the separate erasure role. Canary is also
schema-disabled until a future reviewed migration opens that transition.
Shadow is
the only mode authorized for reviewed People/privacy migration and semantic
cutover; persistent memory and presentation remain disabled. Confirm Marcelo's
HA binding, verify each privacy directive across ingress/retrieval/initiatives/
export, freeze legacy semantic writes, and retain the reviewed migration report.
Both explicit parent facts remain a later shadow milestone and may proceed only
through the server-staged, digest-bound, atomic confirmation flow described
above. The former caller-supplied single-edge Core route has been removed.

Canary remains unavailable in this slice. It requires its own future durable
`shadow` to `canary` authorization, and no endpoint currently issues that
receipt. Setting `HOME_AGENT_ROLLOUT_MODE=canary` now makes every Core role fail
startup. A later separately reviewed canary-authorization design must bind the
shadow acceptance evidence without weakening this gate. Return to `shadow` or
`record_only` on any failed gate; never reactivate legacy semantic authority.

Initiative presentation is not part of the supervised canary. Per-install
attestation narrows the remaining native typed calls but does not authorize a
new initiative capability or its presentation policy. Deployed browser/native
clients have no list or claim method and the BFF accepts no initiative route.
Core may retain isolated future-domain tests for freshness, deduplication, and
one-time claims; those tests are not deployment authority.

An unresolved teaching anchor remains `needs_confirmation` with
`location_unresolved`. The private preview shows the complete digest-bound
locator summary and visible resolved parent names, but its Confirm control is
disabled. Wait for two anchor-eligible fixes and create a fresh preview; never
convert the locality-only transaction into a guessed property.

Only then run the supervised Itaipava teaching, correction, scoped-forgetting,
crash-boundary, backup, and restore scenarios. Physical actions, active-room
perception, learning, Atlas/V-JEPA, and media migration must continue returning
`capability_disabled`.

The canonical 24-scenario acceptance inventory is
`tests/home_agent/itaipava_golden_scenarios.json`. Run
`python tests/home_agent/test_repository_contract.py` to validate its exact
scenario set, test-node references, and disabled-capability handlers. A
manifest entry may be only `covered` or `capability_disabled`; the repository
contract rejects hidden `gap` entries. The app-closed journey is exercised by
an Edge-to-Core PostgreSQL replay harness. Retrieved-memory injection and
context compaction are exercised by the deterministic governed replay compiler,
which excludes untrusted text and keeps model/action contexts disabled.

## Backup and restore gate

WAL archive timeout is five minutes and the startup gate takes a full encrypted
off-host backup. Schedule incremental backups and a monthly isolated restore.
Never mount `HOME_AGENT_RUNTIME_ROOT`, `HOME_AGENT_SESSION_ROOT`, or the
erasure ledger into the PostgreSQL backup job. Replicate the encrypted ledger
and its head separately.

A restored database must remain isolated from BFF/Edge until every later
erasure epoch has been replayed and verified. With only PostgreSQL and its
internal network running, invoke:

```sh
sudo docker compose --env-file home-agent.env -f home-agent-compose.yml \
  --profile operator run --rm restore-replay
```

The dedicated erasure role replays the independent encrypted ledger
idempotently. API and ingest readiness remain quarantined for a missing,
stale, ahead, or divergent head and while an erasure receipt is still pending.
Never initialize a replacement ledger during recovery or bypass this gate.

## Known gated work

- Typed correction and retraction use preview/confirm bitemporal transactions;
  direct database edits remain unsupported.
- The reviewed importer and typed People/privacy routes are implemented.
  `do_not_track`, `ignored`, `silent`, `private`, archived, and due-auto-expiry
  gates are executable. Auto-expiry scrubs identity-linked semantic records,
  exact place locators, durable headers, and keyed runtime-spool subjects, then
  remains `ledger_pending` until the independent erasure ledger is durable.
  Edge-spool deletion remains an explicit external residual until separately
  verified or its hard 24-hour TTL expires.
- Native Tauri OAuth/Windows Credential Manager source transport is implemented
  and compile-tested, but live acceptance still requires a signed Windows
  release build, exact HA client registration/metadata verification, a real
  Credential Manager login-refresh-restart-logout test, and the operator's
  private HTTPS gateway configuration, plus explicit verification that the
  deployed HA authorization-code behavior still matches the documented
  no-PKCE limitation and the native-machine threat model is acceptable.
  Semantic relationship/presence query routes remain on the exact native
  allowlist. Initiative list/claim methods are absent from the deployed client
  and BFF pending a separate initiative-capability and rollout review.
- Live credential rotation, firewall application, LUKS/key provisioning, HA
  OAuth registration, certificates, off-host repository credentials, seven-day
  observation, and human confirmations are operator work, not source changes.

These are release gates, not optional follow-ups.
