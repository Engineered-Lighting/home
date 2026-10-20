# Shared intelligence contracts

This is the local implementation workspace for the
[accepted plan](../../docs/VICTORIA-LA-SHARED-AI-PLAN.md). It includes executable
state machines, durable contract stores and a disabled gateway harness. It does
not replace the deployed Apartment/V-JEPA, Intelligence or governed Core systems.
No component runs automatically. A real snapshot model transport is included,
but no production authority verifier, token-budget authority, engine-completion
attestor, HA action executor or cross-home credential is bundled.

## Run the deterministic tests

The camera protocol accepts version 2 preview metadata with mandatory timing
generation and exposure duration. Frame IDs include generation and sequence and
survive the existing gateway/observation contracts without changing deadlines.
Legacy version 1 IDs remain distinct. Timing generation also flows through the
camera health cache and serialized runtime. A changed generation invalidates
occupancy context, rejects mismatched frames and prevents late results from
being stored or published while retaining upstream execution accounting.
The current Windows workstation suite ran 536 tests: 519 passed and 17
dependency/platform cases skipped. `timing-generation-verification.json` binds
the run to 83 source/fixture/runner files, including 22 real HTTP/TLS snapshot
connector tests. Actual timing authority and live acceptance
remain open.
`inference_service.py` joins the provisioned TLS listener and dispatcher with
single-use startup, initially closed admission and a bounded component drain.
Its six local assembly tests use synthetic model/authority providers; production
commissioning and process supervision remain required.
Earlier aggregate source receipts are historical after these protocol changes.

Optional legacy chat support now shares the snapshot controller queue and
dispatcher. `legacy_chat.py` supplies real bounded JSON/SSE transport and requires
independent token/terminal authorities; default executable ingress remains
disabled. Manifest version 3 adds complete consumer deadline and policy-digest
maps, preserving version 2 semantics and the Victoria 15-second pilot cap.
See the [cutover map](../../docs/VICTORIA-LA-INFERENCE-CUTOVER-MAP.md) for supported
request forms, local evidence and remaining production gates.

`PrivateTLSIngress` adds bounded mutual TLS with a registered certificate/site
registry; `DispatchPump` explicitly drives accepted snapshots through the same
queue. Both require provisioned inputs and do not enable admission. See
[private service lifecycle](../../docs/VICTORIA-LA-PRIVATE-INFERENCE-SERVICE.md)
for assembly/shutdown requirements and local TLS evidence.

`host-clock-linux-verification.json` separately covers 15 passing tests and one
Windows-only skip on the verified local `formd-wsl` workstation. That run
exercises the publisher's Linux-only cases, including real localhost UDP,
protected atomic output and acceptance by the actual camera receipt reader.
It neither contacts a production NTP source nor establishes operational accuracy.

Schema 3 additionally accepts an exact-frame causal receipt from the camera
owner. It checks nonce/device order, bracket midpoint, exposure and uncertainty,
and the hash of the actual JPEG. Its `c<generation>-s<sequence>` identity remains
distinct from mapped frames. The runtime requires version 2 host-clock evidence
with matching boot and an adequate charged host-error bound; it adds LA error
once and revalidates after durable writes and before storage/publication. Version
1 clock evidence remains reserved for the mapped path. Evidence-version changes
invalidate cached health even if the authority returns to its earlier version.
An installed timing authority and physical camera acceptance are still missing.

From the repository root on the workstation:

```powershell
npm run test:shared-home
npm run test:home-connections
node --test tools/run-home-agent-connection-tests.cjs
node --test stack/services/home-agent-bff/test/*.test.mjs
python -m unittest discover -s tools/victoria-camera -p test_capture.py -v
```

The Python contract suite uses SQLite and fake model/identity/governance backends.
Its HTTP tests bind only loopback; no test calls either home. The positive codec
test requires Pillow and explicitly skips if unavailable. Camera tests also need
NumPy. Dependencies are not installed by the test runner. Camera hardware is not
required. Production decoding fails closed if Pillow is missing. The marker-only
JPEG fixtures use an explicitly injected fake decoder, never the default codec.

On this workstation, the system Python lacks NumPy. The camera suite was run
with the app's bundled Python 3.12.14, NumPy 2.3.5 and Pillow 12.3.0; the shared
contract suite also runs with the system Python 3.14.5. This verifies local source,
not the Jetson's pinned NumPy 2.2.6/ARM64 deployment environment.

## Components and boundaries

### One-shot camera-host clock input

After commissioning, the Linux entry point is
`python -m home_shared.host_clock_service --config <protected-config> --once`.
Use `--check-config` instead for an offline ownership/schema check. Neither
command installs or enables a daemon; `--once` sends at most one probe and
never adjusts the clock. The configuration requires the exact hostname, UID,
an absolute `host-clock.json` output in an existing protected directory,
`minimum_reference_interval_seconds`, and all `NtpClockConfig` fields.

The publisher persists its attempt before sending. A server refusal or crash
with unresolved outcome keeps that reference blocked for operator review; it
does not silently retry in a later process. Successful output uses the original
sample's expiry. Only the camera-host receipt is produced: this is not yet the
aggregate timing socket service or Pi/LA/Frigate evidence. The
[pilot policy proposal](../../docs/VICTORIA-HOST-CLOCK-PILOT-POLICY.md) remains
uncommissioned; there is deliberately no usable example with guessed bounds.

### Snapshot model transport

The snapshot connector requires a `SnapshotInputAuthority` bound to the exact
manifest/model/engine epoch and serialized request body. It must establish the
input-token bound before any model request; observed usage afterward is only an
additional check. A separately commissioned `CompletionAttestor` supplies opaque
terminal evidence for the admission controller to verify. HTTP success, socket
closure and a model response ID do not establish engine termination.
Use `compose_snapshot_gateway` to bind the connector to its admitted controller
and durable running reservation. Neither authority has a permissive default.
The current response profile is nonstreaming, bounded Content-Length JSON with
the exact model alias, one assistant text choice and `finish_reason=stop`.
Tool calls and non-null reasoning output are rejected. This does not yet provide
the required compatibility routes for existing LA streaming consumers. Exact
pinned server behavior and lifecycle evidence remain deployment gates.

`EngineReceiptStore` persists the complete internal-request set before engine
dispatch. The trusted engine hook must use `register_for_dispatch` and dispatch
only when its returned decision permits it; duplicates and recovered pending
registrations never permit another dispatch. Its cleanup writer is restricted
to the in-process cleanup authority. `EngineReceiptReader` is a read-only local
view, and `EngineReceiptAttestor` binds its durable result to the gateway ledger,
manifest, epoch, model and request/body digests. The attestor implements only
terminal verification, not deployment admission. Provision a protected local
database and retain its old epochs for reconciliation.

`vllm_engine_hook.install_engine_hook` and `vllm_worker_cleanup` now supply an
explicit, source-pinned engine/worker integration. They observe scheduler free,
await true engine idle, synchronize the worker device and verify request cleanup
before writing a receipt. No startup wiring or model deployment is enabled.
`vllm_api_hook.install_api_hook` supplies the corresponding signed request binding,
randomized-ID registration and final-output wait for stock JSON/SSE handling.
Configure the gateway backend's optional `dispatch_authority` with the same
protected manifest/epoch/key as the API authority. The API hook always requires
the signature; an unconfigured legacy transport cannot use the hooked API.
Signature replay is rejected by durable engine registration. Neither installer
is invoked automatically. Startup composition, installed configuration support,
input-token authority and GPU validation remain required; see the
[lifecycle review](../../docs/VICTORIA-LA-VLLM-LIFECYCLE-REVIEW.md).

### Component status

| Component | Implemented behavior | Remaining production connection |
| --- | --- | --- |
| `adapter.py`, `runtime.py` | Clean-session reconciliation, occupancy/deadline state machines, serialized transport callbacks, one camera/gateway worker, live publication and independent retention | Commissioned health/clock provisioning and live acceptance |
| `service_config.py`, `service.py` | Strict static provisioning, offline config inspection and actual service composition; Linux ARM64 Orin Nano/hostname checks, protected credential/state paths, pinned dependencies and safe shutdown ownership | Reviewed installation/supervision, resource limits, credentials, trust, state allocation and Jetson acceptance; nothing is installed or enabled automatically |
| `timing_client.py` | Bounded authenticated Linux Unix-socket client, peer UID/GID and protected-path validation, nonblocking exact-snapshot evidence cache | Actual commissioned timing authority and measured camera/Pi/LA clock and Frigate collection evidence; this client cannot create receipts |
| `ntp_clock.py`, `host_clock_service.py` | Real bounded one-shot NTP exchange and protected Linux camera-host receipt publication; original expiry, lifetime lock, durable polling floor and refusal history | Reviewed reference/rate/drift policy and actual Jetson commissioning; aggregate Pi/LA and Frigate timing authority remains separate |
| `health.py` | Fixed authenticated loopback camera probe and provisioned HTTPS Frigate stats probe; bounded worker, nonblocking expiring cache and observer-owned continuity | Narrow health credentials, endpoint/CA, pinned source version, measured stats-age policy and trusted clock-evidence producer |
| `mqtt_transport.py` | Actual pinned Paho TLS client, fixed subscriptions/output ACLs, clean sessions, honest retained/duplicate flags, bounded queues, explicit reconnect and socket-abort LWT | Broker TLS/credentials/ACLs, broker packet-size limit and actual Pi/Jetson acceptance |
| `protocol.py` | Fixed Frigate topics, camera-header decoding, versioned gateway serialization and result validation, non-retained observation/availability output | Approved upstream gateway and commissioned source timing |
| `camera_client.py` | Bearer-authenticated HTTP GET to fixed `127.0.0.1:8090/frame.jpg`, bounded JPEG/header validation, capture-session/freshness checks, one fetch and an absolute deadline covering I/O and decoding | Same-Jetson installation, credential provisioning, commissioned clock map and service ownership; no caller-selected URL or redirect |
| `admission.py` | Immutable resource contract, one scheduler, process/engine locks, FULL-synchronous ledger, reserve before dispatch, restart-indeterminate accounting, priority for queued interactive work | Validated manifest, enforced process limits, complete route protection and trusted policy/health/terminal evidence |
| `gateway.py` | Strict versioned image API, credential-bound site/device/camera, full bounded decode, exact expiry, default-closed HTTP harness | Verified cancellation/lookup, private TLS ingress and existing LA OpenAI streaming compatibility |
| `openai_backend.py` | Fixed-endpoint nonstreaming HTTP/TLS snapshot connector, real JPEG decode, pre-dispatch input-token evidence and once-bound durable admission checks | Commissioned pinned token processor/profile, independent engine terminal attestor and exact pinned model-server compatibility validation |
| `gateway_client.py`, `submissions.py`, `rejections.py` | Actual verified HTTPS/mTLS client; stable ingress binding; metadata-only journal committed before POST; restart lookup without retransmission; exact-digest durable rejection receipts | Real admitted gateway, ingress/certificate provisioning and recovery acceptance |
| `authority.py` | Issuer-qualified identity links/grants, generation revocation, shared-speaker limits, deterministic lighting intent, immutable envelopes and durable per-operation accounting | Fresh HA/owner verification, governed Core identity transactions and authenticated per-home brokers |
| `coordinator.py` | User input creates immutable lighting intent before retrieval; read-only interactions cannot execute; per-home results and unknown outcomes stay separate | Authenticated Home/BFF request routes and signed broker transport; default broker map is empty |
| `memory.py` | Source-gated federation and version-one staging personal-memory contract with candidates, confirmations, optimistic corrections, tombstones and derived invalidation | Existing Core privacy/transaction/erasure integration and reviewed deployed-source connectors |
| `observations.py`, `observation_sink.py` | Separate Victoria source store, independent retention, tombstones and backup/restore contracts; accepted results persist with versioned model/configuration/clock provenance before publication; legacy unknown provenance remains unknown | Approved recovery/deletion-history verifier, storage commissioning, governed retrieval integration and deployment |
| `media.py` | Complete-GOP RAM ring, byte/time limits and truthful partial clip selection | Authenticated RTSP demuxer/muxer, bounded unfinished GOP, commissioned PTS mapping, quotas and recording |
| `operations.py` | Explicit local source hashes, preservation-gap reporting, restore checks and positive pilot evaluation | Real source/runtime manifests, measurements, backup coverage and rollout receipts |
| `home-connection-registry.js`, existing browser Agent API/panel | Provisioned connection factory, stable issuer IDs separate from HTTPS transport; actual Echo BFF session verification, cancellation and private-context clearing; deterministic packaging in existing panel bundle | Dual-issuer linking, reviewed Victoria provisioning, separate Victoria/native sessions and pinned workflow acceptance |
| `ha-config/custom_components/shared_home_context/` | Inactive HA consumer with fixed MQTT topics, retained-message rejection, live availability, absolute/monotonic expiry and a one-second timer | Broker ACLs, explicit HA configuration and live HA/restart acceptance |

The isolated browser Agent now uses the canonical connection registry for its
actual Echo session. BFF returns trusted issuer/site metadata; only a verified
`home-assistant:echo` / `echo` session installs the browser's CSRF and principal.
The current HTTPS origin supplies transport, not identity. The registry is bundled
into existing `panel.js`, preserving the origin's four-asset allowlist and CSP.
The visual home selector and legacy HomeServices remain separate; no Victoria
endpoint, fallback or grant is provisioned. Client checks are UX isolation, not
server authorization.

Session changes, logout and authorization denial cancel pending work and clear
private context. The panel checks the accepted authority generation after every
refresh await, so a later denial cannot restore an earlier private snapshot.
Logout is single-flight. Uncertain delivery keeps a local fence and retains its
original CSRF only for explicit logout retry. If BFF has cleared the cookie while
HA revocation remains queued, retry may return 401; the owner can explicitly start
a new sign-in. Refresh cannot silently restore access, and starting OAuth neither
installs a principal nor erases the old revocation record. Native IPC remains the
existing typed path. The [browser verification receipt](../../docs/VICTORIA-LA-SHARED-AI-BROWSER-VERIFICATION.json)
records component tests and real loopback BFF integration; live HTTPS/native
workflow acceptance remains separate.

The staging identity/memory SQLite stores are contract harnesses. They cannot be
configured as a production Core replacement. A real implementation must transact
through existing governed owner, privacy, precise-place and erasure gates. No
normalization adapter writes legacy databases.

The existing Core and BFF now implement a separate, real Echo ingress boundary.
Trusted provisioned `home-assistant:echo` / `echo` metadata is independent of the
HA transport URL; browser headers cannot choose it. Unknown, partial, duplicate
or contradictory claims are rejected before the legacy principal resolver.
Credentialed Edge and service routes enforce the same claim boundary. Existing
credentialed clients omitting both claims retain their trusted Echo binding.
Victoria is refused by this legacy authority; cross-home linking and grants are
not enabled. Provisioned labels do not prove which HA installation an endpoint
serves: its endpoint and trust mapping still require review.

The existing BFF session database migrates atomically to schema 2, preserving
encrypted tokens and pending revocations while binding principal metadata and
browser origins to Echo. Legacy schema and original encryption-key authentication
are checked before migration; wrong keys, contradictory identities and unknown
schemas fail closed. LAN/Tailscale transport changes do not change issuer identity.
Changing the persisted browser-origin set needs a reviewed migration. Native
authority workflow acceptance remains required despite no Rust changes.

### Governed identity schema compatibility

The production Core also has an additive, isolated 0032 storage migration for
issuer-qualified proofs, links, exact source grants and revocation history.
It is initially inaccessible and cannot be selected by runtime Settings. This
is separate from the SQLite harness and does not enable linking. See the
[integration record](../../docs/VICTORIA-LA-SHARED-AI-IDENTITY-INTEGRATION.md)
for the implemented boundaries and remaining authentication/kernel/restore gates.

`app/identity_capabilities.py` in the existing Core service admits HTTP adapters
only at explicitly reviewed schema revisions. It does not authorize deployment,
change rollout/restore checks, replay grants or relax startup's exact database
revision check.

| Existing capability | Reviewed configured revisions |
| --- | --- |
| Principal binding confirmation | 0017 only; grant replay deliberately quarantines this kernel at later revisions |
| Parent proposal, confirmation and recovery | 0021, 0027, 0028, 0029, 0030, 0031; their kernel signatures and explicit grants persist |
| Owner person creation and owner relationship attestation | 0030 and 0031; repaired kernel roles and current vocabulary are required |

The source contains full revision IDs, never a numeric range or a `>=` comparison.
Unknown/future revisions stay disabled. Onboarding also requires an installed
parent adapter before advertising confirmation. A later shared-owner migration
must review these capabilities individually. Function existence is insufficient:
`apply-grants.sh` can revoke a retained function's authority.

Cross-home linking still needs a separate governed ceremony and authenticated
per-issuer proof ingress. It must retain the existing owner/person and receipt
lineage without routing Victoria into legacy `Principal` endpoints. New tables
must use separate migration metadata: adding them to the current global metadata
would cause migration 0001 to create future tables prematurely. Role grants,
privacy fences, revocation generations and restore replay of unlink tombstones
are required parts of that integration. Token refresh or a new whoami request
alone cannot prove fresh authentication to either home.

The HA component's public sensor state is the short value `current`; the caption
is an untrusted attribute, excluded from recorder persistence. Its raw diagnostic
is hidden and disabled by default. No HA configuration has been changed. The
vendored consumer is generated from the canonical tested state machine; after
changing that contract, run `python tools/shared-home/build_ha_consumer.py` and
verify `python tools/shared-home/build_ha_consumer.py --check`. The test suite uses
fake HA APIs for deterministic lifecycle checks, not a live HA instance.

## Local gateway harness

```powershell
$env:PYTHONPATH = 'tools/shared-home'
python -m home_shared.gateway --help
python -m home_shared --help
```

Use the gateway's default loopback listener only for workstation development.
Readiness is closed without an installed trusted verifier and backend. There is
no `--force`, policy bypass or production-enable flag. The compatibility route
returns unavailable until an actual reviewed adapter exists; do not repoint any
LA clients to this harness.

The metadata ledger contains request digests and execution reservations, not
images or conversation text. A timeout, closed socket or crashed process does not
prove engine completion. Indeterminate work retains capacity, and a new process
starts closed. Positive terminal proof from the exact engine epoch is required
before explicit re-admission. No automatic engine restart is implemented.

## Victoria wire lifecycle

1. Establish a clean MQTT session with an output availability LWT. Await all exact
   subscription acknowledgements before publishing the single permitted input
   command, `frigate/onConnect`.
2. Supply fresh independent camera/Frigate health identity. Reconcile every five
   seconds and on count hints. Missing `objects` means unknown; empty means zero,
   whose 60-second uninterrupted confirmation starts at receipt.
3. Call `tick` every second with monotonic and UTC clocks. After debounce, take a
   scheduled work item. Its 15-second deadline is immutable, including queueing.
4. Use `CameraClient.fetch(work, expected_capture_session=..., cross_host_uncertainty=...)`
   to fetch the existing camera owner's `/frame.jpg` on fixed loopback. The
   bearer-protected `X-Victoria-Frame-Metadata` header must carry the commissioned
   clock-map receipt. The client must run on that same Jetson. It never follows
   redirects or retries. A timed-out worker retains its fetch permit until it
   exits, including if decoding has not returned.
5. `inference_payload` binds the selected frame to active work. Source mapping and
   cross-host uncertainty bounds are summed, must fit within one second, and are
   included in the conservative three-second selection-age bound. The gateway
   independently binds the payload to its provisioned credential.
6. Poll request status without resubmitting. Only a matching successful terminal
   result can become an observation. Failed terminal work clears local activity;
   running/unknown work remains accounted. A site/generation/frame mismatch or
   expired result never publishes.
7. Call `ObservationSink(adapter, store).finish(...)` on the serialized adapter
   callback path. It validates the terminal result, persists a source-bound
   record and returns the fixed non-retained publication tuple. Storage failures
   propagate without a publication or new inference. Duplicate terminal delivery
   is ignored. This is not a durable MQTT outbox: a crash after storage but before
   publication may lose the live caption; history must not replay into context.
   Versioned provenance persists model/configuration revisions, the trusted camera
   clock-map revision, combined uncertainty and original deadline/validity. Old
   records remain readable without inventing missing provenance.
8. Publish the returned observation once and live availability every five
   seconds. A same-session LWT invalidates even if its generation was bound before
   later health reconciliation. HA and Home independently enforce 180-second
   absolute validity and receipt-relative guards. Monotonic remaining lifetime
   prevents clock rollback from reviving expired context.

Frigate 0.18 does not provide a producer epoch, snapshot nonce or source ordering
guarantee in this interface. An unflagged older snapshot delivered during a later
window can be indistinguishable from a current response. These observations are
untrusted descriptions and **never trigger physical actions**. A future physical
policy needs a different reviewed evidence/authority capability. The behavior is
grounded in the [pinned dispatcher](https://raw.githubusercontent.com/blakeblackshear/frigate/v0.18.0/frigate/comms/dispatcher.py)
and [activity manager](https://raw.githubusercontent.com/blakeblackshear/frigate/v0.18.0/frigate/camera/activity_manager.py).

## Evidence and rollout

`source-manifest` hashes only explicitly supplied local source paths. It does not
claim to preserve the deployed dirty checkout, snapshot live databases, capture
keys or establish rollback readiness. `preservation-check` reports missing review
receipts and never authorizes maintenance or deployment.

Pilot evaluation includes rejection, timeout and expiry in the denominator;
zero delivered observations cannot pass. Capture timestamps must be compatible
with fresh selection. At least 20 overlapping interactive requests must deliver
valid results, so a failed load cohort cannot hide in a large successful idle
cohort. The LA p95/error helper reports criterion comparisons only: measured
workload matching, sample sufficiency and the 24-hour/one-hour/24-hour/seven-day
windows remain required external evidence. Local tests are not a pilot receipt.

The follow-up [ingestion source receipt](../../docs/VICTORIA-LA-SHARED-AI-INGESTION-VERIFICATION.json)
records the camera-client and storage-binding checks. Earlier receipts preserve
their original source state; they must not be used to certify subsequent edits.
The [runtime source receipt](../../docs/VICTORIA-LA-SHARED-AI-RUNTIME-VERIFICATION.json)
records the later transport/runtime/provenance integration and its 258 passing
workstation tests, including real localhost TLS/mTLS and Paho MQTT packet tests.
The newer [rejection/health receipt](../../docs/VICTORIA-LA-SHARED-AI-RECOVERY-VERIFICATION.json)
records 317 passing workstation tests, with zero skips, after durable rejection,
bounded metadata storage and exact-snapshot health timing changes. Its real local
HTTP/TLS fixtures use synthetic timing/resource authorities, not live evidence.
The [commissioning gate handoff](../../docs/VICTORIA-SHARED-AI-COMMISSIONING-GATES-2026-09-26.md)
tracks equipment evidence separately. This implementation does not close its live
transport commissioning, clock, health, authority, model or deployment gates.
The newer [service verification receipt](../../docs/VICTORIA-LA-SHARED-AI-SERVICE-VERIFICATION.json)
records static provisioning, service composition, authenticated timing IPC and
the real Core/BFF Echo boundary. The final shared
suite ran 385 tests: 383 passed and
two native Linux tests (filesystem preservation and Unix-socket peer credentials)
were skipped on Windows. The full BFF suite passed
53 tests; the focused Core issuer/bootstrap/maintenance suite passed 52. Linux
peer-credential acceptance, the timing producer and live commissioning remain open.
This run includes the timing-denial and invalid-receipt recovery fixes. Source
hashes remained unchanged during testing and were checked again when the receipt
was saved. Earlier receipts certify only their recorded source states.
A client timeout never releases gateway reservations. An ordinary HTTP error or
404 cannot close ambiguous work. Durable gateway rejection receipts now allow
fresh future work after a proven pre-admission rejection; their recovery contract
is described below.

All synthetic manifests in tests are deliberately unrelated to host hardware.
Do not use them as deployable caps. No live LA stage proceeds under the present
named-host quarantine. Partial preservation copied 66 source files (497,291 bytes)
to private `.recovery/echo-20260926T095217046977Z`, with six skipped files and
manifest SHA-256 `2a63664ce8dd1ee0d4f8a0f12fb57ed5b1980e1c7d1942486d18d2824adfcd7c`.
The [preservation report](../../docs/VICTORIA-LA-SOURCE-PRESERVATION-2026-09-26.md)
records independently verified copied bytes and all excluded paths.
This is not a consistent data/media backup or a restorable release; Stage 0
still requires reconciliation, complete runtime artifacts and verified recovery.
Stage 1 still requires supervised clock,
power, cooling, storage, reboot and transport commissioning. See the saved plan's
coverage and receipt tables for all other blocked integrations.

## Runtime integration and verification

`VictoriaRuntime.step()` runs on one owner thread at least once per second. It
consumes `MqttTransport` events, takes a cached `HealthSample`, reconciles Frigate,
selects a camera frame, commits `SubmissionJournal.prepare`, submits through
`GatewayClient`, polls status and persists through `ObservationSink` before
publication. A missing, expired or untrusted health/clock source cannot be
replaced with a configuration boolean. The launcher composes these actual clients,
but no daemon or commissioned timing authority has been deployed.

Run `python -m home_shared.service --config <reviewed-config.json> --check-config`
with `PYTHONPATH=tools/shared-home` for offline structural/trust-file checks. It
opens no sockets or stores and reports `deployment_ready: false`. Running without
that flag requires the provisioned Linux ARM64 Orin Nano hostname, pinned runtime
dependencies, protected files and an existing owner-only state directory. No
example test values are deployable credentials, resource caps or clock evidence.
The launcher neither creates system services nor installs dependencies.

`TimingClient` connects only to
`/run/victoria-shared-home-timing/authority.sock`, checking protected ancestors and
Linux peer UID/GID. Framed JSON is bounded to 16 KiB and binds authority, request
and exact stats digest. Its single worker has a two-second deadline and keeps its
permit until it exits. Missing evidence keeps inference closed. The external
authority must measure and attest timing; neither a config file nor this client
can self-certify clock or collection accuracy.

`HealthProbe` installs the timing receipt validator once before polling. The
client rejects missing, replaced or late validation policy and checks every
receipt against the provisioned scope, boot, source version and timing budget
before publishing it. Denial, invalid receipts and IPC failure advance an
invalidation generation; a subsequent successful refresh cannot resurrect the
old health cache. An unresolved newer lookup may retain only the original valid
sample through its unchanged expiry, without replacing the queued digest.

Construct the journal using `GatewayClient.gateway_identity`, which binds the
exact endpoint, CA contents, device, camera registry and configuration revision.
Its explicit row and free-space bounds require deployment commissioning. Keep
the journal outside disposable cache storage. Restart can only look up unresolved
IDs; it never resends media. Unknown lookup results leave that source closed.
`GatewayNotSent` proves only local failure before a network worker launched;
it does not classify an HTTP error or timeout as an unaccepted request.

### Durable rejection recovery

The gateway atomically records `rejected_capacity`, `rejected_closed`,
`rejected_expired`, `rejected_invalid_image` or `rejected_superseded` only when
the same durable ledger has never accepted that request. Existing accepted or
rejected records win unchanged, and reuse with changed content fails. Rejection
tombstones do not expire or become dispatchable. New accepted and rejected rows
share an explicitly provisioned ledger-record limit and the manifest's free-space
floor. Missing storage policy or exhausted capacity supplies no new receipt;
existing record lookup and terminal reconciliation remain available. Ledger
failure, an uncertain commit or failed readback cannot produce a rejection receipt.

Resource manifests now use schema **2** and require `max_ledger_records` (integer
1–1,000,000). Schema-1 input fails closed and must be re-reviewed with an explicit
metadata budget. Test values are synthetic; they supply no production limit.

An authenticated terminal response includes a versioned `rejection` object with
the provisioned device, canonical digest of the original complete request and
matching reason. The source journal compares that digest to its pre-POST entry;
it never trusts the HTTP status code alone. An HTTP 202 response can describe a
durable rejection, so inspect the contract's status/outcome. Rejections count as
failed opportunities, produce no observation and do not resend the rejected
image. Later scheduled work gets a new request identity and a fresh frame.

If the POST reply is lost, authenticated GET can recover the same rejection from
the ledger after restart without stored image bytes. A plain 429, 503 or 404, a
wrong device/digest, or a malformed receipt remains ambiguous. An accepted
indeterminate reservation cannot be converted to a rejection. Rejected metadata
is part of the gateway's durable recovery set; its growth and storage reserve
must be included in the production manifest before admission.

### Independent health and clock evidence

Construct `HealthProbe` with explicit `HealthProbeConfig`, a trusted
`ClockEvidenceProvider` and `StatsTimingEvidenceProvider`.
Call `poll()` before each runtime tick and pass `read`
as the runtime's health reader. Poll starts at most one network worker and attempts
once per second. Read is nonblocking. The single worker retains its slot until it
actually exits, including after a timeout or stop; repeated ticks cannot create
unbounded DNS/TLS workers. Stop aborts the current socket and invalidates the cache.

Camera health uses the existing bearer-protected `127.0.0.1:8090/health`. Frigate
health uses only the provisioned HTTPS hostname/port and `/api/stats`, with a
dedicated bearer and explicit CA/SAN validation. There is no HA bearer, caller URL,
automatic login, proxy, redirect or retry. Responses have bounded headers/body and
strict JSON/framing checks. The configured detector, `den_oak` processing, camera
session and process identities must all be present and valid.

Frigate 0.18's stats emitter refreshes roughly every 15 seconds. A successful
five-second reconciliation or fresh HTTP receipt does not refresh those stats.
The probe requests nginx cache bypass but still checks `service.last_updated`,
including integer timestamp quantization and commissioned Pi clock uncertainty.
Crucially, the pinned producer stamps this value after potentially blocking
telemetry collection: it is a packaging timestamp, not proof of when processing
metrics were sampled. The probe therefore also requires fresh trusted timing
evidence bound to the exact stats snapshot and source/boot/policy. Its validated
collection-lag bound is charged to the age budget. A configuration number, HTTP
RTT or the packaging timestamp alone cannot satisfy this requirement. Stock stats
without independently established timing evidence remain unavailable; a reviewed
timestamped relay or producer instrumentation must supply that evidence.

An offline [pinned Frigate producer overlay](../victoria-frigate-timing/README.md)
now records raw whole-collection BOOTTIME and wall/monotonic brackets. It is not
installed and does not yet supply this authority's receipts. Counter-read timing
does not establish underlying counter-update age, camera liveness or UTC error;
the existing fail-closed requirements remain in force.

Provision explicit maximum stats age (at most 30 seconds) and collection-lag
policy based on validated producer behavior. This diagnostic policy does not relax the three-second camera
evidence/frame bound, 15-second request deadline or MQTT reconciliation rules.
Camera ages include elapsed request time. Reads expire cached evidence using its
original timestamps, monotonic deadlines and clock receipt; clock jumps fail closed.

The clock provider must supply independently validated camera source mapping and
separate Pi/LA timing uncertainty, bound to provisioned host identities, boot IDs,
policy revision and validation receipt. Source plus each relevant cross-host
uncertainty must fit within one second. A typed object defines the contract; it
does not itself prove a measurement. No provider that guesses uncertainty or
self-certifies NTP status is bundled. The actual commissioned producer and its
reviewed provenance remain installation prerequisites.

When a new exact-snapshot timing lookup is genuinely pending, an already
validated continuous health cache may remain usable only until its original
expiry. Pending work never refreshes timestamps, receipts or budgets. New camera
or process identity, backward source time, negative health, denial or timeout
invalidates evidence; startup without prior valid evidence remains unavailable.
Independent review found that cached health could outlive an explicit timing
denial in one read path. That correction and its fresh verification are pending;
the preceding test count must not be read as acceptance of the corrected source.

Commissioning reports a pinned-source concern with Mosquitto's authentication
plugin ordering: successful HTTP authorization may bypass built-in ACL checks,
packet-size settings are global, and credentials may also work on plaintext
1883. No broker changes were made. TLS-only least privilege and packet isolation
remain an unresolved design gate, beyond merely supplying credentials; the
commissioning source-linked report must establish the reviewed solution.

Camera/PID changes, backward source time or uptime, failed probes and expired
evidence invalidate observer continuity. Its token belongs to this probe session,
not to Frigate: it is never represented as a durable producer epoch or atomic
MQTT snapshot nonce. A zero detector frame rate alone is not a health failure;
stationary scenes can legitimately require no fresh detection.

The pinned sources are Frigate's
[stats implementation](https://raw.githubusercontent.com/blakeblackshear/frigate/v0.18.0/frigate/stats/util.py),
[emitter](https://raw.githubusercontent.com/blakeblackshear/frigate/v0.18.0/frigate/stats/emitter.py)
and [nginx configuration](https://raw.githubusercontent.com/blakeblackshear/frigate/v0.18.0/docker/main/rootfs/usr/local/nginx/conf/nginx.conf).
Authenticated camera-scoped Frigate access is broader than a health-only API;
commission a narrow reviewed relay if that capability boundary is required. An
internal unprotected Frigate port is not an acceptable replacement.

Runtime shutdown is nonblocking by default. If `close()` returns false, keep the
process/journal lock until its I/O worker ends. MQTT stop aborts the socket for its
offline LWT and discards outgoing state before explicit reconnection. Independent
pruning continues while inference is unavailable. A crash between storage and
publication may lose a live caption, never replay historical context.

The optional transport dependency is hash-pinned in `requirements-mqtt.txt`.
For local verification it was installed without dependencies into
`.tmp/shared-home-test-deps`; no global installation occurred. The default test
runner does not install packages. To include the real Paho tests:

```powershell
python -m pip install --require-hashes --no-deps --target .tmp/shared-home-test-deps -r tools/shared-home/requirements-mqtt.txt
python -c "import sys,runpy; sys.path.insert(0,r'.tmp/shared-home-test-deps'); runpy.run_path('tools/shared-home/run_tests.py',run_name='__main__')"
```

This workstation used bundled Python and an approved ordinary-host execution
because sandbox Windows ACLs could not read the isolated installed dependency.
The runner gives its fake engines a fresh shared temporary directory per run,
including child processes; production OS-lock logic is unchanged. Missing Paho
skips the three socket tests and cannot count as transport acceptance. Public
test certificates are exclusively for ephemeral localhost fixtures and must never
be provisioned to a real service.

The [implementation status](../../docs/VICTORIA-LA-SHARED-AI-IMPLEMENTATION-STATUS.md)
tracks the original stage exits and next unresolved integrations.

Input preflight integration: `FixedInputAuthority` uses the fixed private
`/shared-home/input-bound` route installed explicitly with `install_input_route`.
It requires exact serialized bodies and durable admission identities. Version 2
signed preparation tokens cannot execute inference; execution tokens carry the
validated input limit, checked again after actual stock preprocessing. This code
has no startup side effects and is not deployed. Pin/provision the complete runtime,
protect the signing key, charge preprocessing resources, and complete actual model
parity/terminal-cleanup acceptance before enabling it.

The local vLLM extension is packaged by `pyproject.toml`. Its required guarded
entry point is `python -m home_shared.vllm_launch serve <reviewed CLI arguments>`,
with `HOME_SHARED_VLLM_LAUNCH` naming a protected absolute launch descriptor.
Use the existing pinned vLLM environment; this package does not install a model
runtime or alter its dependencies. Preserve any plugin allowlist and include
`home_shared_admission`. See the lifecycle review for provisioning and outstanding
runtime acceptance. The launcher never creates a ledger, key, epoch or admission
approval and rejects reuse of an engine launch marker.
