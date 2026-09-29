# Shared preferences release receipt — 2026-09-28

Status: **shared preferences live and accepted in authenticated Home** (desktop), 2026-09-28.
Phone-size (375×812) acceptance is still pending.

## Internal TLS expiry alert installed — 2026-09-29 ~07:26 UTC

The expiry check from #170 and #171 (merge commit `7b661cf6`) is installed on LA. The leaves were not renewed.

- **Pre-operation checks:**
  - The system was `degraded` only because of four failed units: the two superseded off-host backup units,
    `observer-recover` and `observer-health`.
  - `observer-health` has been failing since 2026-09-20. Its watched Lab unit `vjepa-live-prototype` has refused every
    start since the 2026-09-20 12:16 PDT global OOM kill of vLLM. That was reported to the Lab session for inspection.
  - Home Agent containers were up, the encrypted volume had 58 GB free, and Tctl was 59 °C. The only kernel faults in
    this boot were the 09-17 and 09-20 vLLM OOM kills.
- **Source:** the clean `/opt/home/reviewed-main`, moved to `7b661cf6` (detached). `/opt/home/home-github` has
  uncommitted changes and was not used.
- **Installed:**
  - `/usr/local/libexec/home-agent/shared-preferences/renew_internal_tls.py` (root 0555)
  - `/etc/systemd/system/home-agent-internal-tls-expiry.{service,timer}` (0644)
  - Each blob digest matched the merge commit. The receipt is `/srv/home-agent/config/internal-tls-renewal-install.sha256`
    (root 0600), listing tool `b0b01b0f…`, service `0a021f0f…` and timer `13b538f1…`.
- **First run, in the unit's sandbox:** `Result=success`, exit 0, no warnings.
  - All six leaves are `ok` with 89 days left; `lighting` is absent.
  - The CA has 364 days left, sha256 `d3e2b4b2…`.
  - The run wrote no `tls-renewal/` store, sent no alert and touched no container.
- **Timer:** enabled, running daily around 09:20 PDT.
  - Warnings exit 10 and page through `ntfy-send` without failing the unit, because the Lab worker preflight refuses GPU
    work while any unit is failed.
  - Real errors fail the unit and page through `OnFailure`.
- **Failed units:** unchanged by the install. The Lab session was notified before and after, with no collision.

## Live acceptance — ~22:40–23:05 UTC

The owner typed every credential and made every confirmation. Claude checked server state between steps.

**Account linking**
- The owner signed in fresh at `echo-agent` and `victoria-agent`, then completed the ceremony: pairing, Victoria code handoff,
  LA re-authentication, Victoria verification, account review and confirmation.
- Core recorded one consumed ceremony, one link receipt and one active owner link.

**Preference sharing**
- The owner confirmed sharing in the Agent panel.
- The consent journal was written, and Core shows active `memory.read` and `personal_memory.write` source grants for both `echo`
  and `victoria`.

**Acceptance flow in Home** (`https://home-app.taild52a15.ts.net`) — all six steps passed:
1. "Remember that I prefer warm lighting in the evening." A review card appeared and was explicitly confirmed.
2. With the viewed home switched to Victoria, "What lighting do I prefer in the evening?" returned warm, labeled for both homes.
3. "Actually, I prefer neutral lighting in the evening." Confirmed against the exact revision.
4. After reloading Home, retrieval returned neutral.
5. "Forget my evening lighting preference." Success was reported only after durable deletion.
6. Retrieval from both homes returned no preference.

After acceptance:
- `/run/tailscale-origin-hold` was removed, so certificate refresh and restarts resumed.
- The five private services had zero restarts, and Core, BFF and Origin were healthy.

The Lab session was told when each window opened and closed.

UX follow-up: confirmation currently opens a separate Agent-origin window. The owner judged this unacceptable for Home chat, so an
in-chat confirmation is being built separately.

## Fixes found during the live sitting

Each fix below merged to main, and its signed image was verified with `gh attestation verify`, imported with the cross-store
identity verifier and deployed.

- **#151** `64b5fb49`. **Start account linking** always returned 503. The Echo session path re-verified the HA subject without a
  fetch client. BFF `sha256:69b75820…` was deployed and `HOME_AGENT_BFF_IMAGE_ID` updated. The network preflight and both
  firewall profiles passed.
- **#153** `32e5356a`. The Agent panel and the Victoria code page rejected valid 60 s expiries whenever the browser clock ran
  behind the server. The owner's PC was about 135 ms behind LA, and the Windows Time service was stopped. A 30 s skew allowance
  was added. Origin `sha256:cd2a0e36…` was deployed (`HOME_AGENT_WEB_IMAGE_ID` updated), and victoria-link now runs BFF
  `sha256:42ebcecf…` (`commissioning-w3-32e5356.env`). The Echo BFF was left untouched so the owner's session survived.
- **#154** `cc58f333`. Issuance failed with `shared_link_key_not_admitted`: nothing had ever written
  `privacy.shared_link_key_admission`, whose kernels require an active `shared-link-v1` row. `app.shared_link_key_admission`
  was added.
  - It ran once as the owner in the `migrate` operator service on the deployed Core image `5bb2eb55`. Its two imports are
    byte-identical at the merge commit.
  - The reviewed module was mounted read-only at its sha256 `380d0fa6…`, with the commitment key copied 0400.
  - Result: `admitted`, then `status` reported `matches: true` (`shared-link-20260928`, revision 1). No service restarted.
- **Restored sessions.** Any BFF or victoria-link restart makes existing sign-ins unusable for linking, even though the page still
  shows them signed in. The owner must **Sign out** and sign in again; a reload is not enough.

## Browser cutover (W3) — ~20:10 UTC

**Backup coverage before any link or consent write**
- A new root-only rclone remote (the owner authorized OneDrive) and `home-agent-shared-runtime-backup`, installed at the reviewed
  digest.
- Epoch `20260928T200727Z`:
  - 96 members, including 3 journals that passed `integrity_check`.
  - CMS AES-256-GCM to a recipient whose private key stays on the owner's workstation.
  - Remote download sha256 matched.
  - A workstation stream-decrypt reproduced `archive_sha256`.
- Daily timer enabled.

**Rollback set** (`prepared-20260928/rollback-w3/`)
- env files, primary Serve JSON, old image IDs (`3f5917f` BFF, `7f38a940` Origin)
- old `:local` tag targets
- BFF session DB, copied with the BFF stopped
- the previous egress helper and UFW hook

**Configuration**
- `home-agent.env`: allowed origins, OAuth client and redirect set to `https://echo-agent.taild52a15.ts.net`; Victoria HA URL
  and egress keys added. The native origin is unchanged and no longer in the browser set.
- The Origin public origin moved likewise.
- The reviewed egress helper and UFW hook are installed at the verified digest. The BFF profile verified unchanged.

**Services**
- Link profiles and a Victoria ingress leaf (IP SAN .36) were generated.
- The `home-agent-bff:local` and `home-agent-origin:local` deployment tags now point at the verified `5091abb7` and `553e6178`
  images. `HOME_AGENT_BFF_IMAGE_ID` and `HOME_AGENT_WEB_IMAGE_ID` were updated. Origin and BFF were recreated and are healthy;
  the BFF runs with the link runtime.
- victoria-link is running (`Victoria linking listeners ready`).
- The victoria-link egress contract was applied: its guard sits in the leading INPUT block beside the BFF guard. Both profiles
  verify, and the in-container token-endpoint probe passed. A verify timer is enabled.
- Serve: `echo-agent:443` over HTTPS to the Origin; `victoria-agent:443` as raw TCP to `172.23.0.36:9450`.

**Home**
- Gateway `HOME_WEB_AGENT_ORIGINS` set to echo-agent.
- The reviewed shell patch applied on the pinned base hashes, and the two new scripts were added. Backups are under
  `code/home/.shared-preferences-w3-backup-20260928`.
- The gateway restarted at 13:12 PDT.

**Verification**
- `network_contract.py --require-origin` passed, and `preflight.sh` passed.
- echo-agent serves Agent assets (200); an unauthenticated session request is refused (401).
- Victoria sign-in serves its page and rejects forwarded headers (400) and a wrong Host (403).
- Owner Chrome: Home boot is complete, `HG_AGENT_ORIGIN` is echo-agent, `HomePersonalMemory`/registry/camera query are
  present, and there are no console errors.
- `home-app:8443` retired. The primary node is left with 443, 10000, 10001 and 8100.

## Migration 0031 → 0047 and private services (W2) — ~19:56 UTC

Merged: PR #146 `5039be24` (tailnet origin nodes), #147 `5bb2eb55` (role activation and startup admission), #148 `5bd165b2`
(deploy wiring). Each had an independent review; its findings were fixed before merge.

**Signed Core image**
- Built by `main` run `36473868728`. Attestations verified against commit `5bb2eb55`, the signer workflow and GitHub-hosted
  runners.
- Checksummed in root-only `/srv/home-agent/image-bundles/5bb2eb55…/core/`.
- Imported through the cross-store identity verifier: local image `sha256:065463f0…`. Running images were unchanged during
  the import.

**Tailnet origin nodes**
- `echo-agent` and `victoria-agent` are installed from pinned checkout `/opt/home/reviewed-main`, with digests verified.
- The owner approved both; key expiry is off.
- The Victoria browser certificate is exported to root-owned `victoria-link-tls/`, valid to 2026-12-27, with a daily refresh
  timer.

**Backup before migration**
- Fresh local full backup `20260928-195502F` (58 MB, 2,141 files).
- Off-host copy checksum-verified at 19:55:51 UTC.

**Migration** (`shared_preferences_migration.py`)
- Preflight passed with no blockers.
- Outcome `migrated` in 19 s: worker, ingest and API stopped; migrate; restart on `065463f0` at readiness 0047.
- Receipt: `prepared-20260928/receipts/shared-preferences-migration-20260928T195605Z.json`.
- Checkpoint: readyz ready, migration = expected = 0047, rollout authorized, restore/outbox/worker maintenance current.

**Provisioning**, run through the pinned `migrate` service
- Source registration verified, with no personal grants.
- Preference API permissions active.
- `shared_preference_roles activate`: six logins active with their expected limits and exact function grants, and no sessions
  at activation.
- The staged passwords were exposed only through a temporary uid-10001 read-only mount, deleted afterwards.

**Private services** (commissioning env copy pinned to `065463f0`)
- echo-identity `.33`, victoria-identity `.34`, link-coordinator `.35` and echo-preferences `.31` started in sequence.
- All are running after 35 s with no restarts, at about 83 MiB each of their 512 MiB caps.
- TLS verifies against the internal CA; unauthenticated proof POST returns 401 on the identity services.
- No errors in their logs, and legacy Core, BFF and Origin are healthy.
- victoria-preferences stays staged.

**Owner anchor:** a count-only check found exactly one eligible, unblocked binding.

## Victoria HA HTTPS origin (W1) — 18:3x UTC

Handoff to Claude Code. Pre-operation inspection at 18:07 UTC:

- LA failed units are only the documented `observer-recover` and the two earlier off-host attempts. Containers are healthy.
  - The attempts are the transient units `home-agent-preference-offhost-20260928.service` (05:33 PDT) and
    `home-agent-preference-offhost-20260928-r2.service` (05:38 PDT). Both ran `off_host_backup_writer.py`, which exited
    78 when its input contract refused.
  - The later successful off-host copy of backup `20260928-123053F` at 12:41 UTC
    (`/srv/home-agent/config/phase3-off-host-backup-e5j.json`) superseded them.
  - Nothing uses those units now. Their failed state is kept as incident history, per AGENTS.md: no reset-failed to get a
    green status.
- No new kernel faults since 16:20 UTC. The encrypted volume has 59 GiB free, and the maximum temperature is 64 °C.
- Core readyz: schema 0031, rollout authorized, restore/worker/outbox current.

The Lab session acknowledged windows W1–W3 with no collision.

Victoria HA (2026.9.3):

- A whoami-only `home_agent_edge` component is installed. It is added with a bare YAML key, so there is no config entry and no edge stream.
- A `configuration.yaml` backup was taken first.
- The restart was clean, add-ons are running, and unauthenticated whoami returns 401. Before the change it returned 404.

LA Serve adds only `10001 → tcp://192.168.1.60:80` (TLS-terminated TCP). It was baselined to
`/srv/home-agent/config/tailscale/pre-victoria-ha-10001.status.json`, and 443/10000/8100/8443 are unchanged.

A tailnet client verified `https://home-app.taild52a15.ts.net:10001` with a Let's Encrypt certificate: manifest 200, whoami
401, auth providers 200. The owner also disabled node key expiry for `home-app` (`KeyExpiry` is now null).

Planning found blockers that remain before activation:

- The Agent origin must move off the `home-app` hostname (`echo-link-runtime.mjs:24-29`). The new Agent origin will be
  `echo-agent`, and Victoria sign-in will be `victoria-agent`.
- The private listener login roles need function grants.
- Consent and coordinator startup admission race the worker heartbeat.
- The installed Home shell lacks the preference assets and hooks.

The plan is `C:\Users\Marcelo\.claude\plans\pasted-content-id-2d66-take-over-warm-scott.md`.

## Restore approval resolved — 16:26 UTC

The owner explicitly approved one additional supervised restore drill. The
repaired operator completed it successfully with backup `20260928-123053F`:
2,127 files and 7,396 blocks checked, zero bad checksums. The temporary restored
database used no network, shut down cleanly, and left no matching restore
containers. Production PostgreSQL remained online and all required services
were healthy afterward; the kernel scan found no new matching faults.

The root-owned `/srv/home-agent/config/maintenance-restore-drill.json` records
completion at `2026-09-28T16:26:01.414023Z` with schema
`0031_relationship_uniqueness_e5r`. The scope invocation was
`5fe5dfc70c4d4e03a8905ecc4f57ac9c`. This resolves the earlier rejected repeat and
receipt failure recorded below; it does not itself authorize semantic writes.

Owner Tailscale administration sign-in was also verified. Distinct Victoria
service/hostname configuration still needs completion. No schema migration,
account link, sharing grant or preference write has occurred.

At approximately 13:21 UTC, Core worker, ingest and API were replaced sequentially
with the verified release image below. Each passed its readiness check. The
database remained exactly `0031_relationship_uniqueness_e5r`; API, worker and
ingest now use pool size two with no overflow. BFF, Origin and PostgreSQL
container identities were unchanged. Post-operation inspection found all
required containers healthy, no new matching kernel faults, and five observed
database connections. This is short functional evidence, not sustained-load
acceptance. The encrypted `prepared-20260928/core-0031-upgrade-receipt.json`
records the deployment; `core-0031-rollback.json` retains the previous image.

Before migration, that rollback remains compatible with 0031. After migration,
retain a 0047-compatible Core while closing the new private capabilities; do not
restore the old image or database as a routine application rollback.

PR #145 passed the hosted PostgreSQL authority gate and Core candidate smoke
test, then merged. Its two maintenance helpers were installed with verified
checksums and original copies at 13:24 UTC. The maintenance receipt is separate
from the legacy activation receipt and accepts only reviewed schemas 0031/0047.
The first repeat attempt was rejected before execution. The owner subsequently
approved the additional drill, which passed at 16:26 UTC as recorded above.

At 13:27 UTC the upgraded Core services remained healthy, with no newly matched
kernel faults. The five private listener configurations and both BFF credential/
journal directories are prepared but inactive. New-key/journal backup coverage,
the final BFF listener configuration, migration, source permissions and live
acceptance remain unfinished. Existing Echo session keys were preserved.

Both prior owner prerequisites are now satisfied: additional-drill approval
and Tailscale administration sign-in. Distinct Victoria HTTPS provisioning and
the remaining deployment work still need completion. No deployment window is
currently active; coordinate the next replacement with the Lab owner.

Release source: `e7a7d62de560a200f641281f19db1b4526f5b390` (merged PR #144).
Hosted web run `36424600309` and Core/PostgreSQL run `36424600299` succeeded.
The workstation verified every artifact subject against the exact main commit,
repository, signer workflow and GitHub-hosted runner. Exact manifests and archive
checksums passed locally and again in encrypted host storage.

Imported images were verified by the existing cross-store identity verifier:

| Component | Immutable local image ID |
| --- | --- |
| Core | `sha256:b8b0975453a00299e8b1c21f1877927231797bd7845358633952ddf4817fabc8` |
| BFF | `sha256:5091abb730b2058ba0b2ffc341d65acfd9ba07c77eec11ec47a4b907548ae031` |
| Origin | `sha256:553e617844190b3b26752b322cd4ad8e866c6bbbf474ef1304372197ac181f52` |

The signed config digests differ from these local containerd manifest digests;
the verifier checked source tags, config bytes, revision labels and rootfs IDs.
The root-owned receipt is under the release's encrypted image-bundle directory
as `import-identity-receipt.json`. Deployment tags and running images were not
changed. Existing Core, BFF and Origin remained healthy after import.

All five prepared listener profiles (two preference, two identity, one linking
coordinator) passed their actual image's profile loader and TLS key/certificate
loading as UID 10001. Checks ran sequentially in network-disabled, read-only,
resource-limited containers. No database connection or listener startup occurred.

Pre-operation host inspection at 13:07 UTC found required containers healthy,
59 GiB free on the encrypted volume, and no matching new kernel faults since
00:00 UTC. Known historical observer and earlier backup-attempt failures were
retained. The other chat reported no deployment collision; live Lab changes
remain outside this release's replacement scope.

## Remaining work

- Repeat the six-step acceptance at 375×812.
- After a sustained resource check, move the five private services (`echo-identity`, `victoria-identity`, `link-coordinator`,
  `echo-preferences`, `victoria-link`) from `restart: no` to a restarting policy, through a reviewed compose change.
- In-chat preference confirmation, to replace the separate Agent window.
- Internal TLS leaves expire 2026-12-27.
  - The expiry alert is installed (2026-09-29, above) and pages from about 2026-11-27.
  - Renew before 2026-12-27 with the installed `renew_internal_tls.py`, following the shared-preferences README
    (Internal TLS renewal). Put the victoria-link ingress leaf last, in an owner-chosen window with sign-out and sign-in.
  - CA rotation needs its own reviewed change before about 2027-05-31.

Lighting and travel defaults remain inactive milestones.
