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

Install the non-secret environment outside the user-writable source checkout and
calculate the versioned policy digest. The installed file and each parent stay
root-owned and non-writable by the operator account:

```sh
cd /opt/home/home-github/stack
sudo install -d -o root -g root -m 0750 /srv/home-agent/config
sudo install -o root -g root -m 0600 home-agent.env.example \
  /srv/home-agent/config/home-agent.env
sha256sum home-agent-deploy/policy/home-agent-mvp-v1.json
sudoedit /srv/home-agent/config/home-agent.env
```

Fill the installed `home-agent.env`, then generate independent secrets. The
helper refuses to overwrite an existing set. It stores a root-only mode-0600 master set under
`/srv/home-agent/secrets/master` and materializes distinct mode-0400 copies
under `runtime/<service>` with the numeric owner of that unprivileged
container:

```sh
sudo sh home-agent-deploy/bootstrap-secrets.sh /srv/home-agent/secrets
```

For an existing deployment created before the isolated rollout writer, do not
rerun bootstrap and do not construct a database URL in shell history. Add the
new credential pair as one atomically published root-only directory, then let
the helper rematerialize service copies:

```sh
sudo sh home-agent-deploy/add-rollout-role-secrets.sh \
  /srv/home-agent/secrets
```

The additive helper refuses a partial or existing rollout set and never prints
the password or URL. Run normal preflight afterward; preflight never generates
credentials.

Create the independent ledger directory, then perform its only permitted
initialization. This command refuses either existing ledger file; never use it
as recovery from missing or damaged ledger state:

```sh
sudo install -d -m 0700 -o 10001 -g 10001 /srv/home-agent/erasure-ledger
sudo docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml --profile operator run --rm ledger-init
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
/srv/home-agent/tls/server.crt
/srv/home-agent/tls/server.key
/srv/home-agent/tls/client-ca.crt
```

Install the client certificate, client key, CA certificate, and a separate copy
of the master `edge_token` on the HA host. All HA-side key/token files must be
mode 0600.

Configure `pgbackrest.conf` from
`stack/home-agent-deploy/pgbackrest.conf.example` and store it outside the
source checkout as `/srv/home-agent/config/pgbackrest-local.conf` on the
encrypted mapper. The active deployment topology is explicitly local and
egress-free.

For a standalone Ubuntu deployment, use:

```dotenv
HOME_AGENT_BACKUP_TOPOLOGY=local
HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT=/srv/home-agent/durable/pgbackrest-repository
HOME_AGENT_PGBACKREST_SPOOL_ROOT=/srv/home-agent/durable/pgbackrest-spool-local-v1
HOME_AGENT_BACKUP_MIN_FREE_KIB=2097152
```

Configure encrypted `repo1` as a POSIX repository at the container path
`/repository`. The host repository must be an absolute, dedicated UID/GID-999
mode-0700 directory on the approved encrypted mapper. It must not be PostgreSQL
data, the archive spool, runtime/session state, or the independent erasure
ledger, and none of those paths may be nested inside it. Local topology needs
no FormD, NAS, or SFTP host; those systems are optional backup destinations,
not Home Agent runtime dependencies.

This local repository is deliberately a **same-host, same-volume recovery
anchor**. It can recover from a damaged PostgreSQL cluster or an operator
mistake, and it supports a new verifiable WAL/backup baseline. It does not
survive loss, theft, destruction, or failure of the Ubuntu host, encrypted
volume, or its keys. Treat operation without an independently replicated
repository as degraded durability, not as completion of the plan's off-host
backup requirement.

`pgbackrest.sftp-legacy.conf.example` is retained only for a supervised,
offline recovery of the retired repository. Active preflight rejects that
topology; running PostgreSQL and the backup gate receive neither SFTP keys nor
an external backup network. A future off-host copier needs its own reviewed
failure-domain and credential boundary.

Local operation requires the AES-256-CBC repository cipher, a fresh
topology-specific UID/GID-999 mode-0700 pgBackRest spool, and
`pg1-user=home_agent_backup`. Preflight mounts
that role's separate mode-0400 password only into the backup gate. PostgreSQL
uses the deployment-owned SCRAM HBA file even for existing clusters, so the
backup container never receives or impersonates the bootstrap owner. Its SQL
authority is limited to read-only settings (not live query statistics) and the
four PostgreSQL 17 backup/WAL control functions required by pgBackRest. Keep
`repo1-bundle=y`; it reduces small-file operations without changing the
repository cipher or retention rules. The startup backup also refuses to run
unless the shared encrypted volume has at least the configured reserve plus
three times current PGDATA free; this prevents a same-volume backup from
turning low disk space into a host incident. The 2-GiB setting is a
non-lowerable floor; the gate preserves at least ten percent of the filesystem
when that is larger.

Install the reviewed prebuilt images before preflight; never build or move an
image tag after it has been attested. Record the output of the following command
as `HOME_AGENT_PGBACKREST_IMAGE_ID` in the installed environment:

```sh
sudo docker image inspect --format '{{.Id}}' \
  engineered-lighting/home-agent-postgres:17.10
sudoedit /srv/home-agent/config/home-agent.env
```

Run the strict preflight as root because the Compose secret source directories
are deliberately not traversable by an ordinary host account. It verifies
absolute, mutually non-nested paths, `/dev/mapper` mounts,
master files, exact per-service ownership/modes, pgBackRest ownership, policy
digest, the rendered Compose configuration, required files, and the
unprivileged mTLS ingress configuration; it starts no application service:

```sh
sudo sh home-agent-deploy/preflight.sh \
  /srv/home-agent/config/home-agent.env
```

## Hosted immutable web image handoff

The Ubuntu deployment host is import-only for the Home Agent BFF and browser
origin. Never run Docker or Compose build/pull operations for those images on
that host. The only deployable artifact is
`home-agent-web-images-<source-commit>` from the `deployable-images` job of
`.github/workflows/home-agent-web-boundary.yml` after a successful `main`
`push` or `workflow_dispatch` run on a GitHub-hosted runner. A passing pull
request or feature-branch gate is not a deployable build.

On a trusted administration workstation, select the exact workflow run and
40-character `main` source commit, confirm the run's workflow name, conclusion,
event, branch, and head SHA, and download only the exact
`home-agent-web-images-${SOURCE_COMMIT}` artifact. Require only these five
files: the two image archives, `manifest.json`, `SHA256SUMS`, and
`provenance.sigstore.json`. Verify every attested subject:

```sh
for subject in \
  home-agent-bff-linux-amd64.tar.gz \
  home-agent-origin-linux-amd64.tar.gz \
  manifest.json \
  SHA256SUMS
do
  gh attestation verify "$subject" \
    --bundle provenance.sigstore.json \
    --repo Engineered-Lighting/home \
    --signer-workflow \
      Engineered-Lighting/home/.github/workflows/home-agent-web-boundary.yml \
    --source-ref refs/heads/main \
    --source-digest "$SOURCE_COMMIT" \
    --deny-self-hosted-runners
done
sha256sum --strict --check SHA256SUMS
```

Then validate `manifest.json` as an exact schema: repository, source ref,
source commit, `linux/amd64` platform, pinned Node index and Linux AMD64 base
digests, the exact BFF/origin source and deployment tags, archive digests, and
immutable top-level image IDs must all match. The complete validator and exact
download/import commands are in
`stack/home-agent-deploy/agent-origin/README.md`. Retain the complete read-only
bundle as deployment/rollback evidence, transfer it through the approved
administrative channel, and do not load, unpack, start, or otherwise execute
either image on the workstation.

On Ubuntu, store the same bundle in a root-controlled directory on the
encrypted volume, repeat the checksum and exact-manifest checks, and archive
the existing BFF/origin images and IDs before loading either verified tarball.
After loading, compare both source-tag top-level IDs with the manifest. Only
after both match may the `:local` deployment tags move. Set
`HOME_AGENT_BFF_IMAGE_ID` to the verified BFF ID and
`HOME_AGENT_WEB_IMAGE_ID` to the verified origin ID before preflight. Preflight
always requires the running BFF's exact
`engineered-lighting/home-agent-bff:local` configuration reference. Candidate
mode runs before BFF recreation, so it validates the new ID's syntax but permits
the old running content ID; post-recreate normal and `--require-origin`
preflight require the running BFF's top-level Docker image ID to match the
reviewed value. A mutable tag alone is never provenance or deployment approval;
any failed check leaves the current tags and services untouched.

## WAL continuity and staged start

Validate the dedicated Compose project without building or pulling a replacement
image:

```sh
sudo docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml config
```

Do **not** issue an all-service `compose up` on an existing cluster when a WAL
segment is still `.ready`, the archive topology has changed, or the repository
has no verified baseline. For this one-time WAL repair, with PostgreSQL and
every pgBackRest writer stopped:

1. Copy the exact pending WAL segment into a root-only incident-evidence
   directory on the encrypted mapper and record its SHA-256. Never rename the
   live marker, create `.done`, run `pg_archivecleanup`, or reset WAL.
2. Create a fresh topology-specific repository and spool. Preserve the retired
   repository/config/spool read-only for forensic recovery; never reuse its
   queue state.
3. Under the repository lock, run `stanza-create --no-online`, synchronously
   push the preserved segment with `--no-archive-async`, retrieve the same
   segment into tmpfs with `archive-get`, and require an exact SHA-256 match.
4. Start only PostgreSQL. Require the live `.ready` marker to become `.done`
   through the real configured `archive_command`, then retrieve and hash the
   segment again. A fabricated marker or a current snapshot is not proof.
5. Hold the same boot ID, healthy PostgreSQL, increasing uptime, low resource
   use, and an intact archive for three minutes before advancing.

The current recovery segment is
`0000000100000000000000F5`; its evidence copy and recorded digest are operator
inputs, not values to rediscover or replace. Start PostgreSQL only after the
offline push/get proof:

```sh
sudo docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml up -d --no-deps --no-build --pull never postgres
```

After the archive proof and stability hold, run each transition separately and
inspect its exit/readiness before continuing. `docker compose run` intentionally
omits `--build` and uses only already installed images:

```sh
sudo docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml run --rm --no-deps --pull never provision-roles
sudo docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml run --rm --no-deps --pull never backup-gate
sudo docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml run --rm --no-deps --pull never migrate
sudo docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml run --rm --no-deps --pull never grant-runtime
```

Take and verify a second full backup after migration; only that post-migration
label is eligible for the restore acceptance drill. Then start `core-api`, hold
and inspect it, followed separately by `core-worker`, `core-ingest`,
`edge-ingress`, and finally `bff`. Do not advance a failed or degraded stage.
An HA-dependent stage may remain stopped while Home Assistant is unavailable.

The normal migration container requires
`HOME_AGENT_EXPECTED_DB_REVISION=0006a_worker_lease_arbitration` and refuses any
other target. It never runs `alembic upgrade head`; revisions 0007 through 0012
remain dormant Phase 3 groundwork and cannot be crossed by normal startup. A
post-migration exact-revision check rejects any old descendant database that
would otherwise make Alembic assume the inserted 0006a hotfix had already run;
rebuild that non-production database rather than downgrading it in place.

Startup ordering is enforced:

```text
PostgreSQL ready
→ runtime roles provisioned
→ dedicated backup role authenticated
→ pgBackRest stanza/check/full encrypted local repo1 backup
→ transactional migration
→ least-privilege grants
→ API / ingest / worker
→ mTLS Edge ingress and OAuth BFF
```

PostgreSQL uses the immutable official 17.10 Bookworm image index configured in
`/srv/home-agent/config/home-agent.env`; no database or Core port is published. A pgBackRest failure
prevents the semantic roles from starting. Raw runtime observations live only
under `HOME_AGENT_RUNTIME_ROOT`; sealed OAuth session/revocation state lives
only under `HOME_AGENT_SESSION_ROOT`. Neither path belongs in the durable
PostgreSQL or selected pgBackRest repository source.

Do not run `tools/run-home-agent-e1-postgres-gate.py`, any E1/E2 disposable
PostgreSQL gate, image build, stress test, or AI/GPU workload on the Ubuntu
service host. Those gates are quarantined to the reviewed hosted workflow or a
separate disposable test machine. Normal preflight, migration, backup, restore,
and staged service startup do not require that runner.

Check readiness without printing environment variables or secrets:

```sh
sudo docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml ps
sudo docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml logs --tail=100 \
  backup-gate migrate grant-runtime core-api core-ingest core-worker bff edge-ingress
```

## Isolated database restore drill

After installing and attesting the digest-labeled PostgreSQL/pgBackRest image, run the staged
restore drill for one explicit completed backup label. For a supervised legacy
recovery, direct libssh2 restore is not an acceptance path: stage the encrypted
repository with native host-key-pinned OpenSSH SFTP. For active `local` mode,
the drill takes the deployment's exclusive repository lock, validates the exact
production PostgreSQL mounts, and stages only the encrypted repository into a
separate root-owned scratch tree. PostgreSQL may remain online because its
archive command and every reviewed backup writer honor that same lock. An
unreviewed container mounting production data or the repository fails closed.
In either topology the drill never targets live PostgreSQL data and verifies,
restores, and boots only isolated scratch data with `network_mode=none` and no
published ports.

```sh
sudo bash home-agent-deploy/operator/isolated_restore_drill.sh \
  /srv/home-agent/config/home-agent.env \
  20260711-220110F
```

The drill must pass revision, seven-schema, page-checksum, schema-dump, clean
shutdown, and offline checksum validation. Detailed prerequisites, failure
retention, and cleanup guards are documented in
`stack/home-agent-deploy/operator/RESTORE-DRILL.md`.
A successful `pgbackrest check` or full backup alone is not restore evidence.
The operator script reads `HOME_AGENT_BACKUP_TOPOLOGY`; its legacy branch is
offline recovery tooling and is never accepted by active deployment preflight.
Local topology does not weaken or remove the isolated-restore acceptance gate.

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

The browser Agent host serves only `/home-agent/*` and `/api/agent/*`. It
returns 404 for `/native-oauth-client`; native OAuth metadata and typed native
routes belong only to the separately configured native web-gateway origin. The
browser host has no gateway Basic-login, legacy UI, legacy proxy, health, or
websocket surface. The static Agent bundle has no parent-path dependencies.
Requests carrying an Origin must exactly match the configured browser Agent
origin. On every other host, browser Agent static/session/API routes and OAuth
metadata return 404, even with valid gateway Basic or HA Bearer credentials.
This prevents same-origin legacy UI script execution from reading Agent CSRF
state or mutating semantic memory.

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

The dedicated native web-gateway host publishes `/native-oauth-client` without
Basic auth; that sub-10 KiB document contains the exact HA client-metadata
redirect link and no application/session data. Exact typed native Agent routes
also bypass gateway Basic because HA Bearer and Basic share the `Authorization`
header. The bearer is preserved to the BFF, which immediately validates HA
`whoami`. Native typed routes remain usable during browser-origin migration,
but they never gain browser cookie/session semantics. Every non-native HTTP
route on the native host is denied before legacy authentication, proxy
selection, or static fallback. Every websocket upgrade on the native host is
independently rejected before authentication, route lookup, or legacy
websocket proxying. Browser Agent routes on legacy hosts are also denied.

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
  up -d --no-deps --no-build --pull never --force-recreate bff
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

The former container-local bootstrap used the retired per-item People verifier
and is disabled with the rest of that protocol. Do not run
`operator/provision_identity.py` as a workaround. HA-user-to-person binding
remains unavailable until the atomic finalizer has committed reviewed People
and the binding ceremony has a replacement read-only, receipt-bound lookup.
Tracker binding, explicit parent facts, and the private Itaipava locality also
remain disabled pending their separate reviewed flows. Do not use implicit
legacy `parent` labels as `parent_of` facts.

The operator-only `principal_binding_candidate_staging.py` compiler may be
exercised with synthetic canonical fixtures while live services remain offline.
It re-verifies the exact review bundle, separate finalization signature, and
reconstructed source snapshot before resolving one UUIDv7 Person-projection
receipt. That receipt is the sole permitted selector; never substitute a name,
alias, HA UUID, principal UUID, actor claim, or legacy `me` label. The receipt
choice is still only an operator nomination input: this compiler does not prove
who supplied it or that they were authorized, and it never establishes `me`.

Treat its result as private, in-process, and non-portable. A future consumer
must re-submit the raw artifacts, source rows, and exact selector, then prove a
fresh authenticated HA subject, current conflict-free graph, current privacy /
retrieval / erasure state, and a single-use private confirmation inside the
future serializable binding transaction. Until then every authority, binding,
write, location-memory, and greeting flag stays false and the result remains
`non_deployable`, `capability_disabled`, and `coverage_unproven`. It has no live
invocation command, API route, database writer, or Compose service.

In particular, neither browser nor native BFF accepts direct parent
confirmation. The pure `app.parent_confirmation` module compiles exactly two
server-staged candidates into a private digest-bound preview and verifies one
inseparable two-edge intent, but deliberately returns
`capability_disabled`, `authoritative=false`, and `commit_ready=false`. It has
`enables_writes=false` as well and uses a dormant-only contract version. It has
no API, store, database, BFF, native, or browser integration. Before either
explicit parent fact can be enabled, a later reviewed schema and serializable
commit kernel must use a new deployable contract version, consume a fresh
authenticated confirmation, and commit both edges atomically. Client-supplied
parent/child UUIDs are not authority. Until that full flow and its live
adversarial tests are deployed, parent role expansion remains unresolved. The
dormant compiler authenticates no receipt and accepts no client confirmation
time; its verified preview still requires a new private authenticated gesture,
single-use artifact consumption, and transaction-time validity inside the
future commit kernel.

The operator-only `parent_confirmation_staging.py` compiler may be exercised
with synthetic canonical fixtures while live services remain offline. It
re-verifies both the identity review signature and the separate finalization
signature, then derives all signed legacy `parent` labels; callers cannot name
or omit a candidate. Exactly two distinct People rows that are not archived in
the supplied signed snapshot and have no signed blocking privacy directive are
required. Current person/privacy/retrieval state remains unverified. The output
proves only in-process signed-source verification and remains
`non_deployable`, `capability_disabled`, and `coverage_unproven`. Do not copy it
into Core configuration or treat the Python object/private payload as portable
proof. A future consumer must re-verify the original signed artifacts and source
rows. Never use it as proof of current retrieval state, erasure completeness,
Marcelo's HA binding, a private confirmation gesture, or an atomic parent-fact
commit. It has no live invocation command or Compose service.

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

Do not run the `identity-migration` Compose profile. Its sequential per-item
HTTP apply path is retired and the service exits with status 78 before reading
its database credential or mounted legacy database. Core keeps the former
operator capability and mutation paths only as authenticated tombstones; they
return `capability_disabled` without parsing submitted identity content or
opening a database transaction.

The legacy SQLite Identity Store remains read-only semantic evidence. Its
separate operational Frigate enrollment bridge may continue to maintain
unreviewed recognition mappings, but those mappings are not semantic People or
relationship authority. Do not copy legacy data into Core, restore the old
writer, or use the retired HTTP endpoints as a migration workaround.

Reviewed People, aliases, privacy directives, recognition bindings, status,
and legacy relationship candidates may enter Core only through the atomic
`SERIALIZABLE` finalizer after its writer-freeze, erasure, rollout, and operator
review gates are deployed. Until then, keep the finalizer dormant and the live
deployment pinned to record-only revision 0006a.

### Dormant E4 legacy writer-freeze ceremony

Do not perform this ceremony merely because the installer exists. It is an
irreversible cutover prerequisite and requires a separately reviewed E4
activation. Home Assistant must be stopped so the installer can obtain an
exclusive SQLite lock. From a reviewed checkout, run:

```text
python3 ha-config/extended_openai_conversation/freeze_legacy_identity_semantics.py \
  --database /config/extended_openai_conversation/identity.db
```

The network-free installer opens an existing database in read/write mode (it
will not create one) and holds the same lifetime OS lock used by the integration.
It requires a clean WAL checkpoint, exact pinned schema, clean foreign keys,
zero pending writebacks, and consistent legacy Frigate alias/enrollment links.
Case-folded recognition-name collisions are rejected even when SQLite would
otherwise treat the spellings as distinct.
Before the first fence, it scrubs contentful audit snapshots and conversation
links, runs `VACUUM` with secure deletion, checkpoints again, and then installs
the exact generation-2 trigger and marker contract. It performs blocked-write
probes, post-commit integrity checks, removes clean WAL/SHM/journal sidecars,
fsyncs the main database and final directory state, and reopens the immutable
main file read-only without creating a plaintext verification copy.
Re-running the same verified generation is idempotent. A partial marker,
missing/modified or extra trigger, schema drift, malformed/noncanonical marker,
conflicting pending-cutover marker, newer generation, active reader/writer,
dirty outbox, inconsistent recognition link, hardlinked database/witness,
residual journal, or failed probe aborts without auto-repair.

Before its first irreversible SQLite mutation, the ceremony atomically writes
and fsyncs a mode-`0600` witness beside the database:
`identity.db.e4-cutover-witness`. This witness is deliberately outside SQLite
and must be preserved independently when restoring `identity.db`. If a crash
or old-database restore leaves the witness without the exact generation-2
database fence, normal HA startup and `legacy_migration_only` both fail closed.
Stop HA and rerun the same ceremony to complete the witness-first state; never
delete, roll back, or restore an older copy of the witness. A database-only
backup is therefore not a complete E4 boundary backup.

After the explicit fence, every legacy semantic table and both fence metadata
rows reject insert, update, delete, and replacement. HA opens that database
query-only, guards every legacy admin read, and performs a full revalidation
whenever the database, journal sidecars, or witness fingerprint changes.
Recognition polling, when separately enabled, writes only the opaque
Frigate identifier to a separate operational database. It never creates a
semantic identity, alias, display label, or prompt context; legacy
`do_not_track`, ignored, and do-not-identify subjects are rejected at ingress.
The operational database has a random persistent instance marker; every
operation checks the opened handle and a fresh read-only connection through
the visible path against the exact marker and schema before mutation.
The private legacy inspector receives only privacy-filtered bucket names and
bounded capture counts; crop filenames and crop bytes never cross that route.
After the physical fence, operational recognition persistence is disabled
unless the caller supplies a current deterministic Core privacy decision;
legacy privacy may veto but cannot authorize it. Privacy changes synchronously
purge newly blocked opaque rows, and every periodic Frigate poll reapplies the
current policy before network processing, including when the network bridge is
disabled. Private/silent records remain absent from normal UI, prompts, and
initiatives.

The HA-admin-only identities response reports boundary state
`legacy_frozen_pending_cutover`. This means only that the legacy authority is
frozen: it does **not** promote or claim Home Agent Core authority. The People
surface has no identity/face cache, suppresses prior-principal data during
credential changes before effects run, fails read-only while boundary state is
unknown, and says “legacy read-only · cutover pending.” Caller-provided
`actor` labels never authorize the pre-fence compatibility path; that narrow
unique-`me` pronoun edit is derived from the authenticated HA administrator and
exists only before this explicit ceremony.

The ceremony alone is not an E4 activation receipt. Core promotion still
requires the reviewed PostgreSQL migration/finalization transaction, privacy
and erasure checks, operator stop/backup evidence, and the separately signed
cutover receipt. Until those gates pass, remain
`legacy_frozen_pending_cutover`.

### Dormant E5a current database-authority verifier

Revision `0015_current_authority_e5a` adds only the database-local,
content-free verifier
`operations.evaluate_current_identity_semantic_authority(uuid) RETURNS
varchar(32)`. Production remains pinned to
`0006a_worker_lease_arbitration` in `record_only`; E5a has no deployment
command, API or BFF route, UI or native surface, principal-binding write, or
live activation path.

The sole permitted session boundary is
`session_user=home_agent_binding_operator` with
`current_user=home_agent_identity_authority_kernel`. The caller must already
be in a writable `SERIALIZABLE` transaction on a non-recovery database. A null
or non-UUIDv7 promotion ID fails with
`identity_authority_status_input_invalid` (`22023`), an invalid role boundary
fails with `identity_authority_status_role_invalid` (`42501`), and an invalid
transaction boundary fails with
`identity_authority_status_transaction_invalid` (`25000`).

Because a successful call holds the semantic-write fence, its exact migration
run row, and the ledger head until the surrounding transaction commits, the
binding-operator role is limited to eight connections and carries 15-second
statement/idle-transaction, 5-second lock, and 30-second total-transaction
limits. The verifier reapplies those limits transaction-locally before taking
application-data locks. The role-level limits are installed only by the
post-E3 dormant cutover-role ceremony; the base role provisioner preserves the
historical shape pinned by the E1-E3 migration chain.

While holding the semantic fence, exact run row, and mutable ledger head, the
verifier revalidates one E4 promotion and its exact append-only E3/E4
finalization, admission, writer-freeze, privacy, and projection evidence. It
then checks monotone erasure-ledger continuity from the promotion's admitted
head through the current database head, plus exact-run erasure impacts and
subject retrieval blocks. Its only result values are:

- `current_database_authority`
- `promotion_missing`
- `promotion_invalid`
- `ledger_state_missing`
- `ledger_regressed`
- `ledger_diverged`
- `ledger_continuity_invalid`
- `erasure_impact_present`
- `subject_retrieval_blocked`

`current_database_authority` is deliberately narrow. It means that the
historical E4 promotion remains current against the database erasure overlay.
It does not authenticate a Home Assistant user, prove that the external
encrypted erasure ledger is currently available, re-evaluate current privacy
directives, prove that the external legacy writer remains frozen, or reverify
the private E4 document and its signature. E5a structurally rechecks the
immutable stored graph and exact E4 verifier-bundle marker under the pinned
catalog contracts.

Do not call E5a as a standalone readiness probe or treat its categorical result
as a binding receipt. Dormant E5b adds only the database-local principal/person
binding kernel described below; it invokes E5a and commits the binding in the
same writable `SERIALIZABLE` transaction. E5b does not authenticate Home
Assistant identity or expose a live confirmation path. Revision 0017 adds the
separately gated E5c adapter boundary. Its hosted PostgreSQL 17 acceptance
passed in workflow run `30329873575` at commit `74ffb07`; the live record-only
deployment remains disabled until an explicit operator rollout.

### Dormant E5b principal-binding kernel

Revision `0016_principal_binding_e5b` adds the database-only function
`identity.commit_authenticated_principal_binding_e5b(uuid,varchar,varchar,uuid,uuid,uuid,uuid,uuid)`.
It is still dormant: production remains pinned to
`0006a_worker_lease_arbitration` in `record_only`, and there is no API, BFF,
OAuth, Home Assistant, native, web, Compose, or operator activation path.

Only `session_user=home_agent_binding_committer` with
`current_user=home_agent_identity_binding_kernel` may enter the function.
The kernel owner is a separate `NOLOGIN`, `NOINHERIT`, `NOBYPASSRLS`,
connection-limit-zero role. Its owner-only provisioning ceremony is additive
at revision `0015_current_authority_e5a`; it grants no callable capability.
Grant replay keeps the function, nested E5a call, kernel role, and receipt
catalog quarantined unless the exact E3, E4, E5a, and E5b catalog manifests
match their reviewed digests.

The caller must open a writable `SERIALIZABLE` transaction and call E5b before
performing any write or row-locking operation in that transaction. E5b rejects
an already assigned transaction ID, takes the global semantic write fence
before resolving the proposal or the unique identity-semantics promotion, and
then invokes E5a. E5a reacquires the fence and holds its current-authority locks
through the outer commit. A prior plain `SELECT` cannot be proven at the
database function boundary, so E5c uses a separate commit-only login and calls
E5b immediately on a fresh transaction rather than reusing the staging
connection.

One successful call consumes an exact, independently staged proposal. It
recomputes the person snapshot, stage receipt, and proposal digests; requires
one promoted primary People lineage; rechecks current privacy directives,
edge privacy blocks, current erasure authority, and the active principal
graph; and creates the principal, confirmation artifact, binding, artifact
registry row, and normalized authority receipt atomically. The receipt records
only typed IDs, hashes, policy versions, lineage, transaction ID, and
timestamps. It contains no display name, token, utterance, coordinate, or
camera content.

Confirmation nonce commitments are globally single-use across every
confirmation-artifact purpose, preventing cross-domain gesture replay.
Idempotent retry is exact: the promotion, authenticated HA user, proposal
digest, nonce, and all four caller-supplied output IDs must match the committed
receipt graph. A changed ID, stale or blocked subject, drifted lineage,
non-current E5a result, or malformed graph fails closed without returning
content.

E5b does not authenticate the supplied HA user ID. Never expose it directly to
a client or general service credential.

### Gated E5c authenticated binding adapter

Revision `0017_authenticated_binding_e5c` is the distinct E5c activation
boundary. Its migration is denial-only: it revokes the E5b caller surface, and
the pinned grant replay may then restore only the two internal kernels plus
`USAGE` on `identity` and `EXECUTE` on the exact E5b function for
`home_agent_binding_committer`.

The API uses three independent database credentials:

- the ordinary API credential for authenticated subject reads;
- `home_agent_binding_operator` for reviewed proposal staging and an opaque
  proposal-ID lookup;
- `home_agent_binding_committer` for one commit-only kernel call.

The committer receives no table, sequence, broad function, schema-creation, or
kernel-membership privilege. Its first SQL in a writable `SERIALIZABLE`
transaction is the E5b call. The proposal UUID is resolved and committed in
separate database transactions, and E5b rebinds it to the fresh HA user ID and
proposal digest before any write.

The BFF obtains a fresh authenticated Home Assistant `whoami` result for every
binding route. Client-supplied actor, person, and principal identifiers are
ignored or rejected. Confirmation accepts only the exact proposal digest and a
random UUIDv4 gesture nonce. The adapter derives stable, distinct UUIDv7
authority-receipt, principal, confirmation-artifact, and binding IDs so
serialization retries and safe request replay cannot create a second graph.

The Core route fails before parsing the body unless its reviewed readiness
revision is exactly `0017_authenticated_binding_e5c` and both isolated
credentials are configured. A successful identity binding leaves location
memory and travel greetings off. Production remains pinned to
`0006a_worker_lease_arbitration` in `record_only`. The hosted E5c gate passed
in workflow run `30329873575`; an explicit operator rollout is still required.

### Dormant E5j Phase 3 evidence preflight

E5j supersedes E5i's placeholder receipt boundary. It keeps the read-only
operator diagnostic and separates database-restore evidence from erasure-gate
evidence; neither proof may claim the other. E5j still does not install the
missing E4 activation contract. Run the preflight only from the exact reviewed
checkout:

```sh
cd /opt/home/home-agent-integration-test
sudo python3 \
  stack/home-agent-deploy/operator/phase3_activation_preflight.py
```

The diagnostic accepts no arguments, opens no database connection, and invokes
only fixed `docker exec`/`docker inspect` reads. It calls the existing
operator-only Phase 2 and Phase 3 Core diagnostics from inside the Core API
container, reads pgBackRest `info --output=json`, checks five required
container health states plus the healthcheck-free edge ingress's exact
`running` state, and binds hosted acceptance to the clean checkout's exact Git
commit. It never runs Compose, Alembic, `psql`, a backup, a restore, or a
rollout writer.

Exit status `0` means only that the non-authoritative preflight packet is
complete. Status `3` means canonical admission blockers remain, and `78` means
the probe could not establish a trusted read boundary. Every JSON result keeps
`authoritative=false`, `enables_writes=false`,
`changes_rollout_mode=false`, and `runs_migrations=false`, including a passing
result.

The following optional root-owned, mode-`0600`, single-link receipts are
recognized beneath `/srv/home-agent/config`:

- `phase3-restore-drill-e5j.json`, written only after the isolated database
  restore, schema dump, clean shutdown, and page-checksum ceremony passes for
  the exact newest completed full backup;
- `phase3-erasure-current-e5j.json`, written only after the existing Core
  restore gate reports that the database epoch exactly equals the current
  independent erasure-ledger head;
- `phase3-off-host-backup-e5j.json`, for a checksum-verified encrypted copy of
  that same backup at an operator-controlled off-host destination; and
- `phase3-source-acceptance-e5j.json`, binding the target revision, clean source
  commit, hosted PostgreSQL/web runs, and the future reviewed activation
  contract.

The guarded restore drill now creates only the restore receipt. It deliberately
does not claim erasure replay or erasure currency. After that receipt exists,
the separate read-only erasure ceremony is:

```sh
cd /opt/home/home-agent-integration-test
sudo python3 \
  stack/home-agent-deploy/operator/phase3_evidence_receipts.py \
  erasure-current
```

That command uses the pinned local image with `--pull never`, runs only Core's
existing `restore-verify` command, requires its exact content-minimized result
to match the current UID/GID-10001 ledger head, and atomically writes the
second receipt. It does not replay a lagging ledger. A restored database that
needs replay remains quarantined until the separately governed
`restore-replay` recovery ceremony succeeds; then `erasure-current` may be
rerun.

E5j has no writer for the off-host PostgreSQL or source-acceptance receipts
because their required evidence does not yet exist: no operator-controlled
off-host database-backup destination is configured, and the E4 activation
contract is not installed. Those blockers therefore cannot be cleared by E5j.
The active pgBackRest topology may remain `local`; later off-host durability
will be represented by its separate label-bound receipt rather than forcing
the retired SFTP topology back into production.

Production remains pinned to `0006a_worker_lease_arbitration` in `record_only`.
Even after the 500-event evidence threshold is met, an actual Phase 3
deployment still requires a reviewed off-host writer, the E4 activation and
source-acceptance contracts, a rollback ceremony, and an explicit operator
decision.

The complete hosted E1–E5j PostgreSQL 17 authority gate passed in workflow run
`30394033276` at source commit `2686286`. This acceptance does not authorize a
production migration or create any missing live receipt.

The subsequent live read-only E5j evaluation at accepted documentation commit
`c913eb3` found all required containers ready, the encrypted local backup
repository healthy at full label `20260728-102702F`, and 220 of 500 qualifying
record-only events. The remaining blockers were the event threshold, an
off-host database-backup receipt, an isolated restore receipt, a separate
current erasure-gate receipt, and reviewed activation-source admission. No
receipt writer, restore, migration, restart, or rollout mutation was run.

### Dormant E5k activation source plan

E5k content-addresses the activation-relevant source that passed the hosted
E1–E5j PostgreSQL gate. It is not the missing activation executor and cannot
create the source-acceptance receipt:

```sh
cd /opt/home/home-agent-integration-test
python3 \
  stack/home-agent-deploy/operator/phase3_activation_source_plan.py
```

The verifier accepts no arguments and runs fixed, read-only Git commands only.
It requires the current clean checkout to descend from the latest accepted
source-pack commit, then proves that every tracked activation path is
byte-for-byte and mode-for-mode unchanged from that hosted PostgreSQL run. It
rejects symlinks,
non-regular Git modes, missing objects, path traversal, duplicate/disordered
tree entries, source drift, an unrelated checkout, and an unavailable accepted
commit.

A trusted result still exits `3` and always reports
`source_acceptance_receipt_issuable=false`, `authoritative=false`,
`enables_writes=false`, `runs_migrations=false`, and
`changes_rollout_mode=false`. The result also names the five deliberate
remaining stops:

- no root-controlled Phase 3 activation sequencer is installed;
- normal grant replay still rejects the absent E4 activation contract;
- the identity finalizer service remains non-callable;
- the semantic cutover service remains non-callable; and
- no off-host PostgreSQL backup writer is installed.

E5k does not weaken any of those boundaries. Its purpose is to prevent the
future executable ceremony from silently changing or omitting an already
hosted-tested dependency.

The complete hosted E1-E5k PostgreSQL 17 authority gate passed in workflow run
`30394892306` at source commit
`ba9edab82261000761c71760833ed97d81bb90f9`. This acceptance verifies E5k's
read-only source-plan contract; it does not authorize a production migration
or create a source-acceptance receipt.

The subsequent live read-only E5k evaluation at documentation commit
`66f50de` matched all 86 activation-source entries to source-pack digest
`4ce9ee5cab3e2e43883a54b63adc377fe2116ac47ee0e96a635e776fbca2bff5`.
The companion E5j preflight found all required production containers ready,
the rollout still `record_only`, and 220 of 500 qualifying events. The source
plan retained all five deliberate executor/durability stops and issued no
receipt. No service, database, migration, or rollout state was changed.

### Dormant E5l fixed migration entrypoints

E5l installs exact image entrypoints for the five reviewed Phase 3 schema
checkpoints:

- `phase3-migrate-finalizer` targets `0013_identity_finalizer_e3`;
- `phase3-migrate-current-authority` targets
  `0015_current_authority_e5a`;
- `phase3-migrate-authenticated-binding` targets
  `0017_authenticated_binding_e5c`;
- `phase3-migrate-parent-authority` targets
  `0018_parent_relationship_e5d`; and
- `phase3-migrate-parent-status` targets `0021_parent_status_e5h`.

These are image entrypoints, not a production activation procedure. They
accept no target or extra argument, reject `HOME_AGENT_RUN_MIGRATIONS=1`, run
the exact Alembic revision followed by the existing exact-revision guard, and
have no service in `home-agent-compose.yml`. The ordinary `migrate` role
remains restricted to `0006a_worker_lease_arbitration`.

The checkpoints preserve the required ordering. The E4 cutover and current
authority roles are provisioned after 0013 and before 0015; the binding kernel
role is provisioned after 0015 and before 0017; Marcelo's authenticated
principal binding is completed at 0017; and the parent relationship kernel
role is provisioned at 0018 before the final 0021 migration. No operator should
invoke any E5l entrypoint until a later root-controlled sequencer validates all
E5j evidence receipts, installs the grant activation contract, and provides
tested forward-recovery and rollback behavior.

The complete hosted E1-E5l PostgreSQL 17 authority gate passed in workflow run
`30395870684` at source commit
`74c751628f0559452215897d1fd251e7277f0f51`. The E5k source verifier is bound to
that accepted source pack and now reports the fixed migration entrypoints as
installed. The sequencer, grant activation contract, identity finalizer
executor, identity cutover executor, and off-host backup writer remain absent.

The subsequent Ubuntu verification at source-plan commit `be02ad3` substituted
`echo` for both Alembic and Python, then proved all five role-to-revision
routes. The live source verifier matched 86 entries at digest
`9862ecd4b6d0368d6324106b376a619224d12bab1855149bbb7223c944d58f73`.
No database connection, container restart, migration, receipt write, or
rollout change occurred.

### E5m source admission and grant arming

E5m adds a root-only sequencer with four commands:

```sh
cd /opt/home/home-agent-integration-test
sudo python3 \
  stack/home-agent-deploy/operator/phase3_activation_sequencer.py status
sudo python3 \
  stack/home-agent-deploy/operator/phase3_activation_sequencer.py admit-source
sudo python3 \
  stack/home-agent-deploy/operator/phase3_activation_sequencer.py arm-grants
sudo python3 \
  stack/home-agent-deploy/operator/phase3_activation_sequencer.py disarm-grants
```

`admit-source` writes the existing exact E5j source-acceptance receipt only
when the E5k verifier matches the current checkout to its hosted-tested source
pack, the five fixed E5l entrypoints are present, and the E5m grant contract is
installed. The receipt binds the current clean commit, accepted PostgreSQL
run, accepted web-boundary run, target revision, and installed contract.

`arm-grants` cannot write its root-owned, mode-0600, single-link permit until
the full E5j preflight has no blockers. The permit is not a secret and grants
no authority by existence alone. Only the operator-profile
`grant-phase3-activation` service receives it and a distinct materialized
owner-password copy. The service has no port or application network, copies
the permit into a root-owned tmpfs, and runs the reviewed grant contract with
logging disabled.

At revisions 0017 through 0021, `apply-grants.sh` now validates the exact
permit path, root ownership, mode, link count, and fixed value before its first
ACL statement. Normal revision-0006a grant replay is unchanged. Revisions
0014 through 0016 retain their earlier explicit E4 activation stop.

E5m publishes the deterministic activation sequence for review but keeps
`migration_executor_enabled=false`, `runs_migrations=false`,
`enables_writes=false`, and `changes_rollout_mode=false`. The finalizer,
legacy-writer freeze, cutover, staged migration executor, and off-host writer
remain separate required milestones.

### E5n identity authority executors

E5n installs two content-minimized, operator-profile execution surfaces:

- `identity-finalize` calls only
  `operations.finalize_reviewed_identity_migration(bytea,uuid)`.
- `identity-cutover` calls only
  `operations.commit_reviewed_identity_cutover(bytea,uuid)`.

Each accepts one bounded JSON request on private stdin containing the exact
contract, a UUIDv7 admission ID, and a canonical base64 document. The document
is never accepted through argv or environment variables and the service emits
only the resulting UUID receipt. Each connection is pinned to its dedicated
database role, uses a complete `SERIALIZABLE` transaction, disables parameter
logging on errors, and retries only complete transactions for `40001`,
`40P01`, or `55P03`.

Installing these entrypoints does not make either authority callable in the
normal deployment. Provisioning continues to expire both disposable logins at
1970, the services have no application network or port, and no role-activation
helper is installed yet. Do not manually alter `VALID UNTIL`; the later
root-controlled ceremony must bind role activation, the one-time admission,
executor invocation, immediate re-expiry, and zero-session verification.

### E5o encrypted OneDrive copy

E5o adds `operator/off_host_backup_writer.py`. It accepts no arguments and is
fixed to the rclone remote `home-agent-offhost:HomeAgent/pgBackRest`. Configure
that remote as a dedicated OneDrive connection; do not reuse a broad personal
rclone configuration.

One-time preparation on the Ubuntu host:

```sh
sudo install -d -o root -g root -m 0700 \
  /srv/home-agent/secrets/offhost-rclone \
  /srv/home-agent/durable/off-host-export-e5o
sudo install -o root -g root -m 0600 /dev/null \
  /srv/home-agent/secrets/offhost-rclone/rclone.conf
sudo rclone config \
  --config /srv/home-agent/secrets/offhost-rclone/rclone.conf
```

Create exactly one remote named `home-agent-offhost`, select Microsoft
OneDrive, and restrict its purpose to Home Agent backup storage. The OAuth
browser step is the only interactive part. Afterward, confirm the config is a
root-owned, single-link mode-0600 regular file and store its recovery material
separately from the Ubuntu disk.

Install the fixed destination descriptor from the reviewed example:

```sh
sudo install -o root -g root -m 0600 \
  stack/home-agent-deploy/off-host-backup-destination.e5o.example.json \
  /srv/home-agent/config/off-host-backup-destination-e5o.json
```

Confirm the target is owned `root:root`, mode `0600`, with one hard link. Then
run from the clean accepted checkout:

```sh
cd /opt/home/home-agent-integration-test
sudo python3 \
  stack/home-agent-deploy/operator/off_host_backup_writer.py
```

The writer gets the latest healthy full-backup label from the E5j preflight,
holds the repository coordination lock exclusively, structurally validates the
repository, and creates a deterministic tar archive on the encrypted volume.
Only that encrypted repository archive is uploaded. The raw observation spool,
live PostgreSQL directory, erasure ledger, pgBackRest cipher passphrase, and
Home Assistant credentials are never read.

After upload, the writer streams the remote archive back through rclone and
compares byte count and SHA-256. Only an exact match publishes
`/srv/home-agent/config/phase3-off-host-backup-e5j.json`. The local staging
archive is removed on success or failure. A successful copy is not a substitute
for the separate restore and erasure-current receipts.

### E5t bounded migration executor

E5t installs `operator/phase3_migration_executor.py`, but does not arm or run
it. The executor accepts only these five exact commands:

```sh
sudo python3 stack/home-agent-deploy/operator/phase3_migration_executor.py \
  migrate-finalizer
sudo python3 stack/home-agent-deploy/operator/phase3_migration_executor.py \
  migrate-current-authority
sudo python3 stack/home-agent-deploy/operator/phase3_migration_executor.py \
  migrate-authenticated-binding
sudo python3 stack/home-agent-deploy/operator/phase3_migration_executor.py \
  migrate-parent-authority
sudo python3 stack/home-agent-deploy/operator/phase3_migration_executor.py \
  migrate-parent-status
```

Every invocation requires root on Linux, a clean hosted-tested source pack, a
root-owned E5m permit armed within the preceding four hours, the shared
activation lock, and all application-facing Core/BFF/Edge services stopped.
It verifies the exact source revision with the migration guard, invokes one
fixed `phase3-migrate-*` image entrypoint with `--no-deps`, then verifies the
exact target revision. It cannot accept an arbitrary revision, build or pull
an image, stop or start a service, or promote rollout mode.

Do not invoke it on the live deployment yet. The E5j preflight must first pass,
the later admission and disposable-role ceremonies must be installed and
hosted-tested, and the complete activation sequence must receive a separate
operator review. The source-plan blocker for those later boundaries remains.

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

`GET /v1/operator-rollout/phase3-readiness` is available only to the offline
operator bearer paired with the bootstrap credential. It is deliberately a
non-authoritative revision-0006a diagnostic: it rechecks the live database
revision, accepts no query parameters or request body, reads no identity or
fact rows, and exposes no names, IDs, timestamps, digests, counts, or private
content. Its only live checks are the exact configured mode and the existing
rollout gate's categorical shadow-predecessor result. Only exact `shadow` mode
with gate code `authorized` reports an authorized predecessor; `record_only`
does not.

The response always returns `authoritative=false`, `enables_writes=false`, and
`ready_to_advance=false`. Revision 0006a cannot prove a People migration
manifest/completion receipt, privacy cutover, legacy semantic-write freeze,
unique `me` identity, or the staged atomic two-parent confirmation protocol,
so the corresponding blockers remain fixed. Do not use this diagnostic to
change rollout configuration or enable semantic writes. An authoritative
Phase 3 v1 gate requires a future reviewed schema migration and implementation.
The endpoint is intentionally absent from BFF, Agent-origin, browser, and
native allowlists.

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

PostgreSQL forces an archive switch at least every five minutes and the startup
gate takes a full encrypted backup into local repo1. Schedule daily full backups
and a monthly isolated restore. A local full backup establishes the standalone service's
recovery baseline, but it does not satisfy the off-host RPO or total-host-loss
gate; add an independent destination when one becomes available.
Never mount `HOME_AGENT_RUNTIME_ROOT`, `HOME_AGENT_SESSION_ROOT`, or the
erasure ledger into the PostgreSQL backup job. Replicate the encrypted ledger
and its head separately. A second directory on the same encrypted volume is
not an independent ledger replica.

A restored database must remain isolated from BFF/Edge until every later
erasure epoch has been replayed and verified. With only PostgreSQL and its
internal network running, invoke:

```sh
sudo docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml \
  --profile operator run --rm restore-replay
```

The dedicated erasure role replays the independent encrypted ledger
idempotently. API and ingest readiness remain quarantined for a missing,
stale, ahead, or divergent head and while an erasure receipt is still pending.
Never initialize a replacement ledger during recovery or bypass this gate. If
the Ubuntu volume and its only ledger copy are lost together, a restored
database cannot be declared erasure-safe.

Install the scheduled surface only after the first local full backup and WAL
retrieval proof. Never execute root timers from the mutable Git checkout:

```sh
sudo install -d -o root -g root -m 0755 /usr/local/libexec/home-agent
sudo install -o root -g root -m 0555 \
  home-agent-deploy/operator/local_backup.sh \
  /usr/local/libexec/home-agent/local-backup.sh
sudo install -o root -g root -m 0555 \
  home-agent-deploy/backup-gate.sh \
  /usr/local/libexec/home-agent/backup-gate.sh
sudo sh -c 'cd / && sha256sum \
  usr/local/libexec/home-agent/local-backup.sh \
  usr/local/libexec/home-agent/backup-gate.sh \
  srv/home-agent/config/pgbackrest-local.conf \
  > /srv/home-agent/config/local-backup-operator.sha256'
sudo chown root:root /srv/home-agent/config/local-backup-operator.sha256
sudo chmod 0600 /srv/home-agent/config/local-backup-operator.sha256
sudo install -o root -g root -m 0644 \
  home-agent-deploy/operator/systemd/home-agent-local-backup.service \
  home-agent-deploy/operator/systemd/home-agent-local-backup.timer \
  /etc/systemd/system/
```

Set `HOME_AGENT_PGBACKREST_IMAGE_ID` to the reviewed local image's immutable
`sha256:` ID and keep the installed-operator manifest path from the example
environment. The non-persistent timer runs one process-limited full backup each
day, retaining four full recovery boundaries so archived WAL does not accumulate
for weeks. It invokes the immutable image directly with pull policy
`never`, never parses checkout Compose, and holds both backup serialization and
repository locks. Confirm the service result and `pgbackrest info` before
enabling the timer; it never runs E1/E2, builds, or model work.

After the installed image ID and manifest are final, reload systemd and run the
bounded oneshot once. Review its result and journal, confirm the new completed
full in `pgbackrest info`, and only then enable the non-persistent timer:

```sh
sudo systemctl daemon-reload
sudo systemctl start home-agent-local-backup.service
sudo systemctl show home-agent-local-backup.service \
  -p Result -p ExecMainCode -p ExecMainStatus
sudo journalctl -u home-agent-local-backup.service -n 100 --no-pager
sudo systemctl enable --now home-agent-local-backup.timer
sudo systemctl list-timers home-agent-local-backup.timer --no-pager
```

The service is intentionally not independently enableable at boot. If the
oneshot, WAL retrieval, or `pgbackrest info` verification fails, do not enable
the timer.

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
  OAuth registration, certificates, independently replicated backup and
  erasure-ledger destinations, seven-day observation, and human confirmations
  are operator work, not source changes. FormD or a NAS may provide those
  destinations later, but neither is required to run the standalone service in
  explicitly degraded-durability mode.

These are release gates, not optional follow-ups.
