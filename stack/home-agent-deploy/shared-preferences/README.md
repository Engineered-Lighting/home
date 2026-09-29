# Private shared-preference services

`compose.json` is a separate, opt-in Compose project. It adds two preference
listeners, two identity listeners and one linking coordinator. It does not
replace Core, BFF, the web gateway or Lab, and it does not run migrations.

Before startup, provide a reviewed environment file containing every required
variable. No production addresses, image tags or resource budgets are defaulted.
Use the immutable Core image produced by the approved release workflow.

Each service needs its own pre-created directories, accessible to UID 10001:

- `*_SECRETS`: mounted read-only at `/run/secrets`; contains only that service's
  API database URL, knowledge key, service token and dedicated profile secrets.
  Never mount the global secret directory or administrator database credentials.
- `*_CONFIG`: mounted read-only at `/config`; includes `listener.json` and TLS
  material, matching the existing role-specific profile loader.
- `*_JOURNALS`: mounted writable at `/journals`; use separate encrypted durable
  storage for each service. Include journals and their recovery keys in the
  reviewed backup and retention procedure before enabling writes.

The listener address in each profile must match its reserved `*_IP` on the
existing API network. Profiles reference container paths, not host paths.
Database URLs must resolve the existing PostgreSQL service on its private
network. There are no host-published ports. BFF routes require private TLS and
dedicated service credentials; browser origins remain separately provisioned.

Apply `core-pool-limits.json` as the last override in the existing Core project's
reviewed Compose command. It bounds the main API, ingest and worker pools to two
connections each without overflow, and the API operator pool to two. Existing
three binding/relationship/owner pools remain at two each. The legacy service
total is therefore 14. This requires the new image; older images ignore these
settings. Recreate existing services without overlapping old/new instances and
verify the live settings before admitting the new services.

The new five-service configuration is bounded to another 14 connections:
Echo preferences 2, Victoria preferences 1, each identity service 3, coordinator
5. This includes auxiliary role pools. With one instance of each service, the
total is 28, leaving nine ordinary slots plus three PostgreSQL reserved slots
on the inspected 40-connection database. Admit maintenance jobs against the
nine-slot remainder; no overlapping migration, restore or duplicated runtime
is implicitly admitted. Verify actual process count and role limits at startup.
The observed eight live connections are not evidence of peak capacity, and
these limits still require live latency/error acceptance.

Validate resolved configuration without starting services:

```sh
docker compose --env-file /absolute/reviewed.env -f compose.json --profile shared-preferences config --quiet
```

Validate current host health, compatible schema 0047, installed permissions,
verified backup and rollback artifacts before a coordinated startup. Start only
the explicitly admitted service names with `up -d --no-build --no-deps`.
The five live services (`echo-preferences`, `echo-identity`, `victoria-identity`,
`link-coordinator`, `victoria-link`) restart `unless-stopped`, after live
acceptance on 2026-09-28 and a sustained resource check. `victoria-preferences`
has no client yet, so it stays staged with `restart: no`. Inspect TLS and
authenticated operation results after any restart.

Rollback stops these new services and revokes their new credentials while
retaining journals, current data and deletion history. It must not restore an
older database. Live acceptance passed on 2026-09-28; see `docs/SHARED-PREFERENCES-RELEASE-2026-09-28.md`.

## Internal TLS renewal

The private listeners use 90-day EC P-256 leaves, each with one IP SAN, issued
by the retained internal CA (`authority/ca.crt`, CN "Home Shared Preferences
Internal CA", valid to 2027-09-28). `renew_internal_tls.py` reissues them from
that CA with the same subject, SAN and key usage. It never creates, replaces or
re-signs the CA. Leaves as inspected on 2026-09-29, under `prepared-20260928/`:

| Leaf (`--leaf`) | Directory | SAN | Owner, modes | Expires (UTC) | Probed from |
| --- | --- | --- | --- | --- | --- |
| `echo-preferences` | `echo-preferences/config` | 172.23.0.31:9448 | root:10001, dir 0750, files 0440 | 2026-12-27 12:50 | BFF |
| `victoria-preferences` (staged) | `victoria-preferences/config` | 172.23.0.32:9448 | root:10001, dir 0750, files 0440 | 2026-12-27 12:50 | not running |
| `echo-identity` | `echo-identity/config` | 172.23.0.33:9448 | root:10001, dir 0750, files 0440 | 2026-12-27 12:50 | BFF |
| `victoria-identity` | `victoria-identity/config` | 172.23.0.34:9448 | root:10001, dir 0750, files 0440 | 2026-12-27 12:50 | victoria-link |
| `link-coordinator` | `link-coordinator/config` | 172.23.0.35:9448 | root:10001, dir 0750, files 0440 | 2026-12-27 12:50 | BFF |
| `victoria-link-ingress` | `victoria-bff/config/ingress-tls` | 172.23.0.36:9451 | 1000:1000, dir 0700, crt 0444, key 0400 | 2026-12-27 20:09 | BFF |
| `lighting` (when deployed) | `lighting/config` | 172.23.0.37:9448 | as issued by `generate_link_profiles.py` | — | BFF |

The Core leaves carry keyUsage `digitalSignature`. The ingress and lighting
leaves, issued by `generate_link_profiles.py`, also carry `keyEncipherment`.
Each keeps its own policy. Renewal preserves each file's owner, group and
mode. Clients trust the CA, not the leaves: the BFF through
`echo-bff/config/internal-ca.crt` and victoria-link through
`victoria-bff/config/internal-ca.crt`, both via `NODE_EXTRA_CA_CERTS`. So a
leaf renewal never touches a client or restarts the BFF.

### Commands

All four commands take `--root /srv/home-agent/shared-preferences/prepared-20260928`.

- `check-expiry` is read-only. It exits 10 if any of these holds:
  - a live leaf expires within 30 days
  - the CA has fewer than 120 days left
  - a required leaf is missing
  - a client trust copy differs from `authority/ca.crt`
  - a leaf no longer verifies against the CA or its reviewed policy
  - a change has been on disk for more than a day without a passing probe
  - a leftover `.server.*.renew` file shows an interrupted change
- `renew --leaf L` refuses, changing nothing, in any of these cases:
  - the CA key does not match the CA certificate
  - the CA has fewer than 120 days left, so a new leaf plus the warning window
    could outlive it
  - any trust copy differs from the CA
  - the current leaf does not verify against the CA, has drifted from the
    reviewed policy, or does not match its key
  - the previous change to that leaf still awaits a restart and probe

  Otherwise it does the following:
  - issues a fresh key and a 90-day leaf in a root-only work directory on the
    encrypted volume
  - saves the displaced pair and a receipt to
    `tls-renewal/<leaf>/<stamp>-renew/` (root 0700; files 0400)
  - stages `.server.key.renew` and `.server.crt.renew` beside the live files,
    with the live owner and mode
  - renames them over the live files
  - records a restart-pending marker

  It prints the rollback stamp and never prints key material.
- `probe --leaf L` runs a TLS handshake with `node` inside the listener's real
  client container, which trusts the CA through `NODE_EXTRA_CA_CERTS`. It
  succeeds only if the client authorizes the served leaf and the leaf's SHA-256
  equals the on-disk file. Then it clears the marker. If the listener is
  stopped, it clears the marker, because the listener loads the file at its
  next start.
- `rollback --leaf L --stamp S` restores a saved pair byte-for-byte with its
  original owner and mode. It saves the displaced pair as a new `-rollback`
  stamp, so a rollback can itself be reversed. It refuses a saved leaf that has
  expired, fails to verify, or does not match its receipt.

Until a probe confirms a change, `check-expiry` uses the displaced leaf's
expiry, because the running listener still serves it. `tls-renewal/` is outside
the shared-runtime backup, and that is intended: the live leaf in each `config/`
directory is backed up, and saved leaves are needed only until a probe passes.

### Install the tool and expiry alert

Install only from the reviewed merge commit, and record the digests.
`home-agent-internal-tls-expiry.timer` runs `check-expiry` daily at about
09:20 local time. The check runs in a read-only sandbox and never reads a
private key. Its results are handled differently:

- **Warnings** (exit 10) leave the unit successful. `ExecStopPost=+` reruns the
  check and pages the warning lines through `ntfy-send` (tag
  `internal-tls-expiry`), once a day until they clear. The unit never enters
  the failed state for a warning, because the Lab worker preflight refuses GPU
  work while any unit outside its allowlist is failed.
- **Real errors** fail the unit: an unreadable or unsafe layout, a CA that
  fails to load, or a crash. `OnFailure=ntfy-alert@%n.service` pages, as for
  `observer-health`, and Lab GPU work stops until someone investigates. Do not
  add this unit to the Lab allowlist.
- `ntfy-alert-reset` clears the failure streak after each run that is not a
  failure.

Only these host alert helpers run outside the sandbox (`+`), because they write
`/run/ntfy-alert` and the ntfy spool. The journal names each warning.

```sh
cd /opt/home/home-github            # checkout at the reviewed merge commit
reviewed=<merge commit sha>
test "$(git rev-parse HEAD)" = "$reviewed"
src=stack/home-agent-deploy
tool=/usr/local/libexec/home-agent/shared-preferences/renew_internal_tls.py
sudo install -m 0555 -o root -g root "$src/shared-preferences/renew_internal_tls.py" "$tool"
for unit in home-agent-internal-tls-expiry.service home-agent-internal-tls-expiry.timer; do
  sudo install -m 0644 -o root -g root "$src/operator/systemd/$unit" /etc/systemd/system/
done
check() { test "$(git rev-parse "$reviewed:$1")" = "$(sudo git hash-object "$2")" || { echo "digest mismatch: $1" >&2; return 1; }; }
check "$src/shared-preferences/renew_internal_tls.py" "$tool"
check "$src/operator/systemd/home-agent-internal-tls-expiry.service" /etc/systemd/system/home-agent-internal-tls-expiry.service
check "$src/operator/systemd/home-agent-internal-tls-expiry.timer" /etc/systemd/system/home-agent-internal-tls-expiry.timer
receipt=/srv/home-agent/config/internal-tls-renewal-install.sha256
sudo sh -c "{ echo '# reviewed $reviewed'; cd / && sha256sum ${tool#/} \
  etc/systemd/system/home-agent-internal-tls-expiry.service \
  etc/systemd/system/home-agent-internal-tls-expiry.timer; } > $receipt"
sudo chmod 0600 "$receipt"
sudo systemctl daemon-reload
sudo systemctl start home-agent-internal-tls-expiry.service     # must succeed
sudo journalctl -u home-agent-internal-tls-expiry.service -n 5 --no-pager
sudo systemctl enable --now home-agent-internal-tls-expiry.timer
```

### Renew (before 2026-12-27; the alert starts about 2026-11-27)

Run this in one Lab-acknowledged window: message the Perception Lab session
first. Complete the AGENTS.md pre-operation checks (`systemctl
is-system-running`, healthy containers, storage headroom, temperatures, no new
kernel faults). Then set:

```sh
T=/usr/local/libexec/home-agent/shared-preferences/renew_internal_tls.py
R=/srv/home-agent/shared-preferences/prepared-20260928
sudo python3 -I "$T" check-expiry --root "$R"   # CA > 120 days, no trust drift
```

**1. Core listeners, one at a time.** Order: `echo-preferences`,
`echo-identity`, `victoria-identity`, `link-coordinator`. Restarting a Core
listener does not end any browser session. Requests in flight during the few
seconds of restart can fail and be retried. Move to the next leaf only after
`probe` prints `"outcome": "serving"`.

```sh
L=echo-preferences
sudo python3 -I "$T" renew --root "$R" --leaf "$L"     # note rollback_stamp
sudo docker restart --time 20 "home-shared-preferences-$L-1"
sudo docker logs --since 2m "home-shared-preferences-$L-1"   # listening, no TLS or key errors
sudo python3 -I "$T" probe --root "$R" --leaf "$L"
```

**2. `victoria-preferences`.** It is staged and not running. Run `renew` and
then `probe`. The probe reports `listener_not_running`. Do not start it.

**3. `victoria-link-ingress`, last, at a time the owner chooses.**

> **Restarting victoria-link or the BFF ends account-linking use for every
> existing session.** The page still shows the owner signed in, but linking
> and preference authority no longer work. The owner must **Sign out** and sign
> in again at `echo-agent` and at `victoria-agent`; a reload is not enough.
> Restarting the Core listeners (identity, coordinator, preferences) has no
> such effect. Leaf renewal never restarts the BFF.

```sh
sudo touch /run/tailscale-origin-hold          # keep the Victoria cert helper from restarting it mid-sign-in
sudo python3 -I "$T" renew --root "$R" --leaf victoria-link-ingress
sudo docker restart --time 20 home-shared-preferences-victoria-link-1
sudo docker logs --since 2m home-shared-preferences-victoria-link-1   # "Victoria linking listeners ready"
sudo python3 -I "$T" probe --root "$R" --leaf victoria-link-ingress   # from the BFF
sudo python3 -I "$T" probe --root "$R" --leaf victoria-identity        # victoria-link is its client
sudo systemctl start home-agent-victoria-link-egress-verify.service    # egress contract still holds
```

The owner then signs out and in at both origins. Afterwards, remove the hold
with `sudo rm -f /run/tailscale-origin-hold`. If a Victoria browser-certificate
restart is pending, this one restart covers both.

**4.** Run `check-expiry` again. It must exit 0 with no warnings.

### Rollback

The displaced leaf stays valid until its original expiry, so a rollback is
safe until then. Use the stamp that `renew` printed; `sudo ls "$R/tls-renewal/$L"`
lists all stamps. `rollback` prints the container to restart as `restart`.

```sh
sudo python3 -I "$T" rollback --root "$R" --leaf "$L" --stamp <stamp>
```

Then:

- **Core leaves:** `sudo docker restart --time 20 "home-shared-preferences-$L-1"`,
  then `probe`.
- **`victoria-preferences`:** no restart; `probe` only. `docker restart` would
  start the staged service.
- **`victoria-link-ingress`:** repeat the whole of step 3 for
  `home-shared-preferences-victoria-link-1`: the hold file, the restart, both
  probes and the egress check. The owner signs out and in again afterwards.

If `renew` fails before its first rename, the live files are unchanged and no
staged file or marker remains. If it is interrupted between the key and
certificate renames, the pair is torn and `.server.crt.renew` is left behind
(`check-expiry` reports it). The listener keeps serving from memory. Do not
restart it; run `rollback` with the printed stamp first. `renew` ignores
hang-ups and ^C while it renames, so a dropped SSH session cannot cause this.

### CA rotation (separate procedure; not automated)

Renewal refuses from about 2027-05-31, when the CA has fewer than 120 days
left, and the alert starts warning at that point. Before then, rotation needs
its own reviewed change, because every client's trust must change:

1. Create a new CA in a new root-only directory, keeping the old one.
2. Put an old-plus-new bundle in both BFF `internal-ca.crt` copies and every
   Core `config/ca.crt`. Restart the BFF and victoria-link; this ends linking
   sessions, so the owner signs out and in again.
3. Reissue each leaf from the new CA, one listener at a time, probing each.
4. After every leaf has moved, remove the old CA from the bundles, restart
   again, and retire the old authority.

Until that change updates them, `renew` and `check-expiry` treat any bundle or
new CA as trust drift and refuse or warn. Nothing rotates the CA implicitly.

## Migration 0031 -> 0047

`operator/shared_preferences_migration.py` migrates live Core from
`0031_relationship_uniqueness_e5r` to `0047_personal_pref_authority_v1` and
nothing else. It runs the image's fixed `phase3-migrate-personal-preferences`
entrypoint once; it never builds, pulls, starts dependencies, applies grants
or restores a database. Install it into a root-owned path; never run it from
the mutable checkout. The digest comparison closes the copy race:

```sh
cd /opt/home/home-github
expected="$(git hash-object stack/home-agent-deploy/operator/shared_preferences_migration.py)"
sudo install -d -m 0755 -o root -g root /usr/local/libexec/home-agent/shared-preferences
sudo install -m 0555 -o root -g root \
  stack/home-agent-deploy/operator/shared_preferences_migration.py \
  /usr/local/libexec/home-agent/shared-preferences/shared_preferences_migration.py
installed="$(sudo git hash-object \
  /usr/local/libexec/home-agent/shared-preferences/shared_preferences_migration.py)"
test "$installed" = "$expected"
```

Every input must be an absolute root-owned path with no group/world-writable
component (including the Compose file's directories). The receipt directory is
`root:root 0700`. `core-runtime-0047.json` must pin `--core-image` for all three
Core roles with readiness `0047`; `core-runtime-0031.json` is the reviewed
current runtime, used only if the migration fails before commit. Run `check`
first, then `run` with identical arguments within the two-hour off-host backup
window:

```sh
OVERRIDES=/absolute/reviewed/overrides
set -- \
  --env /srv/home-agent/config/home-agent.env \
  --compose-file /opt/home/home-agent/stack/home-agent-compose.yml \
  --pool-limits "$OVERRIDES/core-pool-limits.json" \
  --runtime-override "$OVERRIDES/core-runtime-0047.json" \
  --rollback-override "$OVERRIDES/core-runtime-0031.json" \
  --core-image sha256:<64-hex verified Core image ID> \
  --receipt-dir /srv/home-agent/shared-preferences/migration-receipts
sudo python3 -I /usr/local/libexec/home-agent/shared-preferences/shared_preferences_migration.py check "$@"
sudo python3 -I /usr/local/libexec/home-agent/shared-preferences/shared_preferences_migration.py run "$@"
```

Both actions hold the exclusive activation lock and require: Core ready at
0031 with rollout authorized and current restore gate and worker maintenance;
healthy Core roles and PostgreSQL; an off-host receipt for the newest full
pgBackRest backup copied within two hours; a successful maintenance restore
receipt; a present, labelled Core image; and `HOME_AGENT_RUN_MIGRATIONS` not `1`.
`check` exits 0 or 3 (blocked) without changing anything.

`run` stops worker, ingest and API, migrates through the `migrate` service
bounded to 0.5 CPU, 512 MiB and 64 PIDs, then starts only the three roles with
the pool limits and 0047 runtime and waits up to 180 s for readiness at 0047.
It writes a `0400` receipt `shared-preferences-migration-<UTC>.json`. Exit 0 is
`migrated`. Exit 4 (`failed_rolled_back`) means the migration transaction
rolled back, the source revision was verified and Core is ready again at 0031.
Exit 5 needs attention: after `migrated_core_unready` never start the 0031
image or restore the database; fix forward, or use the owner-approved reviewed
`alembic downgrade 0031_relationship_uniqueness_e5r` only before source
registration or CONNECT grants. After `migration_state_unverified` Core roles
remain stopped until the live revision is verified. `failed_source_unready`
means the schema stayed at 0031 but the restarted 0031 runtime is not ready.

## Explicit cross-home lighting

Design: `docs/CROSS-HOME-LIGHTING.md`. The private `lighting` listener
(`app.lighting_server`, Core image, command `lighting-api`) is defined in
`lighting.json`, an override used only when deploying it, so existing
`compose.json` commands keep working without lighting variables:

```sh
docker compose --project-name home-shared-preferences --env-file <commissioning.env> \
  -f compose.json -f lighting.json --profile shared-preferences \
  up -d --no-build --no-deps --pull never lighting
```

It listens on `172.23.0.37:9448` (TLS leaf issued by the retained internal CA),
reaches PostgreSQL as `home_agent_lighting`, and reaches each home's light-only
endpoint through its Tailscale Serve port (LA `:10000`, Victoria `:10001`) over
the `home-agent_lighting-egress` bridge (`ha-light-egr0`, `172.27.0.10`), which
the `lighting` firewall profile confines to exactly those two ports. It starts
with `restart: no` until a sustained check.

Order, in one Lab-acknowledged window:

1. Each home: install the `home_agent_edge` lighting block with its own
   64-hex secret in `secrets.yaml` and an allowlist of real lights; restart HA.
   Verify an unsigned request to `/api/home_agent_edge/lighting/v1/inventory`
   returns 401.
2. Stage `lighting/secrets/` (root-owned, readable by UID 10001):
   `credential` (identical to `echo-bff/secrets/lighting_credential`),
   `database_url` (`home_agent_lighting` with `sslmode=verify-full` like the
   other private roles), `action_key`, `consent_key`, `journal_key` (distinct,
   64 hex) and `<home>_home_secret` (identical to that home's HA secret).
   Create empty `lighting/config` and `lighting/journals` (UID 10001, 0700).
3. As the owner in the `migrate` operator service:
   `python -m app.lighting_permissions prepare`, then
   `activate --password-file <staged>`; `status` must show one login role.
4. `generate_link_profiles.py --root <prepared> --tailnet <tailnet> --lighting echo,victoria`.
   It adds the Echo BFF's `lighting` transport, which changes the write-once
   `echo-bff/config/link.json`: move the old file into the rollback set after
   review first. It also writes `lighting/config/listener.json` and the leaf.
5. Apply the `lighting` firewall profile, start `lighting`, and check TLS and
   admission from a client container.
6. Recreate the Echo BFF so it loads the new link profile. This ends linking
   use of current sessions: the owner signs out and in again at `echo-agent`.
7. The owner allows lighting once in the Agent panel, then accepts in Home.

Rollback: stop `lighting`, `lighting_permissions deactivate`, remove the
firewall profile, and restore the previous Echo link profile and BFF image.
Journals and grants are retained; nothing restores the database.
