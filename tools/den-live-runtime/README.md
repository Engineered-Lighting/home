# Bounded live Den runtime preparation

This is an opt-in source preparation for the existing Home Apartment experience,
with Victoria selected. It does not install a service, access a camera, prepare a
model or deploy the context runtime merely by importing a module. The existing LA
Home HTTPS entry and registered assets remain the presentation surface. LA Qwen
and heavier model work retain their admitted model transport; fresh local spatial
measurements/protection and the sole context worker are Victoria-owned.

Actual target operation still requires the owner-local source/config/schema,
registration, workload/model grants, independently established measurement bounds,
current host/resource/fault checks and applicable development qualification. The
unchanged capture/development limits are not automatically compatible with an
incumbent process that already contains other models. Missing preparation remains
`resident_models_not_provisioned`; no model is downloaded or second camera opened.

## Fixed composition and authority

The existing managed controller composes `ManagedProducerComposition.create`
from `stack/services/perception-lab/live_supervised_composition.py` with
`compose_supervisor` from this directory. The managed root supplies the existing
controller capture scope, occupancy, qualified control clock, private authority
files and resource preflight. Configuration cannot select Python modules,
commands, factories, model endpoints or new camera owners.

`home-live-runtime-config-1` has exactly `schema,version,session,worker_config,
node_runtime,archive_file,archive_limits,source_bundle_digest`. Version is `1`.
`worker_config` is the existing fully commissioned worker configuration with its
private worker `token_file`; the live journal endpoint is derived from the actual
created graph. `node_runtime` and `archive_file` are explicit absolute paths.
Optional `assessment_file` is an absolute private candidate-output path or null;
a graph with an assessment recorder requires it, and export failure stays incomplete.
`archive_limits` contains `max_records,max_bytes,min_free_bytes`. The archive
parent already exists and remains owner-controlled. Source bundle, worker module
hashes, source/schema/configuration and registration pins must agree before use.

The `session` object is `home-live-session-config-1`, version `1`, with exactly
`session_ref,configuration_digest,mode,max_runtime_s,max_cycles,interval_s,
drain_timeout_s` in addition to schema/version. The configuration digest names
the acquisition configuration; the worker configuration has its own independent
digest. Modes are `controlled`, `development`, `live_shadow_unqualified` or `qualified`; qualified start
additionally requires a current qualification receipt in the trusted preflight.
Producer-only `development` starts no lighting worker; its `worker_config` may
be null. It retains private candidates and source transactions, with ordinary
worker replay explicitly not applicable. It supplies no focused-output qualification.
`live_shadow_unqualified` attaches real observations to the required worker and
recorder without claiming qualification or physical authority. Its missing work,
registration and protection inputs still refuse lighting. It is never relabeled
as an authored controlled run.
Bounds remain at most 120 seconds/24 acquisition cycles, interval at least 0.5
seconds and drain timeout at most 10 seconds. They are refusal ceilings, not a
quality or sustained-operation claim.

The additive `home-managed-den-mount-2` consumes an already recording incumbent
Lab session through `lab_live_runtime.py`. The original Lab owner retains model,
camera and Store lifecycle ownership. Exact activation, worker readiness, model
and media generations, source grants and original receipt metadata must agree.
An original metadata sidecar is durable before publication. Den Stop detaches
this bounded consumer; it does not stop the original Lab session.

For controllers that start before a Lab session exists, the fixed protected
`home-den-lab-bootstrap-1` file names one absolute mount file, one operator-token
hash and the Victoria room scope. Existing authenticated operator Start supplies
the exact already-prepared session/configuration identity. It cannot select a
file, host, model or factory. The mount binds once, and controller shutdown keeps
the deferred guard installed while preparation unwinds. No input is admitted at
boot or before recorder/worker readiness.

Start returns `starting` promptly. Preparation runs off the request thread so
status/stop remain usable while a native loader unwinds. Native cancellation may
remain unconfirmed; late completion cannot start a new worker or camera cycle.
One start identity does not authorize another session or hidden epoch rotation.

Startup order is fixed:

1. Trusted preflight and independently authorized existing model preparation.
2. One idle factory-owned journal; no source step yet.
3. Exclusive fsynced causal archive READY; journal attaches its recorder before
   cursor zero can advance; private write-only recorder RPC becomes ready.
4. One actual Node worker, with awaited durable recorder hooks.
5. Bounded progressing acquisition. Home reads committed results independently.

Stop prevents source admission, quiesces native work, durably withdraws source
support, and asks the worker to consume through the exact withdrawal cursor and
settle any unknown commit. Then the recorder writes its terminal disposition and
drains. Only the exact typed `home-acquisition-drain-1` receipt permits graph
finalization; no publication occurs after recording drains. Original resolver
buffers remain available until that point. Unconfirmed worker/native/recording
outcomes stay incomplete and retain their reason. Existing camera chat, perceiver,
Lab and occupancy automation remain independent.

## Existing Home route and secure bridge

`web-gateway/home-live-context-bridge.mjs` exports an inert
`createHomeLiveContextBridge(config)`. `await start()` returns its real loopback
`service_url`; `status()`, `invalidate()` and awaited `close()` own its lifetime.
The existing Home context gateway still accepts only its loopback upstream. The
bridge alone connects to the fixed existing Victoria TLS controller origin, using
endpoint-specific CA trust and a new narrow read/intent bearer. No browser
credential or arbitrary header is forwarded.

Its exact `home-live-context-bridge-1` configuration is `schema,version,binding,
upstream,ca_file,token_file,listen_port,deadline_ms`; version `1`. `binding` contains
the full `scope` and immutable `manifest_ref`. The upstream is one fixed HTTPS
origin. `ca_file` and `token_file` are explicit protected local paths. The listener
always binds `127.0.0.1`; port zero discovers a private ephemeral port. Internal
deadline is explicit, 100–12000 milliseconds. These fields are server-owned,
never selected by browser input. Startup check mode must not start the bridge.

`ContextEdge` in `live_context_edge.py` dispatches the exact public context family
before the unchanged Lab authorizer. It independently verifies the new context
credential and immutable binding, then forwards to the fixed supervisor-owned
graph address. The journal checks the narrow grant again. Allowed operations are
snapshot/events/catalog reads, intent submission and attributed intent lookup.
Producer publications, revocation, worker commits/cuts/checkpoints, media, evidence
requests and generic remote paths remain refused. Ordinary non-context routes
retain the incumbent Lab application and authentication.

The existing Home gateway mounts immutable live options before application
services. Home switching/logout/source generation changes must invalidate old
in-flight reads. The browser draws committed, locally leased execution only.
Bridge or LA failure can make the existing UI unreachable; it does not transfer
context authority or require local geometry to wait for LA semantics.

## Owner commands after reviewed provisioning

`operator.py` contacts only the fixed existing controller HTTPS origin. Its
separate operator credential is not a browser, context-read or Lab grant. The
`SessionControlEdge` admits only bounded start/stop/status/read-results commands;
the page gains no source-start authority.

```text
python -B tools/den-live-runtime/operator.py start --config ABSOLUTE_OPERATOR_CONFIG.json --id retained-start-id
python -B tools/den-live-runtime/operator.py status --config ABSOLUTE_OPERATOR_CONFIG.json --id status-id
python -B tools/den-live-runtime/operator.py stop --config ABSOLUTE_OPERATOR_CONFIG.json --id retained-stop-id
python -B tools/den-live-runtime/operator.py read-results --config ABSOLUTE_OPERATOR_CONFIG.json --id results-id
```

The exact `home-live-operator-config-1` fields are `schema,version,upstream,
ca_file,token_file,session_ref,configuration_digest,timeout_s`; version `1`, HTTPS
origin only and timeout at most 15 seconds. Mutations are not automatically
retried; retain the exact ID/body and inspect status after an uncertain response.
`read-results` returns the closed runtime/recording disposition and invokes the
real ordinary replay after the session is terminal. It does not calculate
independent perception accuracy; the producer assessment tool owns that report.

These commands do not work against an unprovisioned target. They do not ask the
owner to assemble Python objects or supply absent measured bounds.

## Causal archive and replay

`archive.py` owns one exclusive, finite, fsynced hash-chain file. Journal hooks
record original envelopes/publications/receipts plus resolved original receipt
metadata before commit, then the actual commit/abort result. Worker hooks record
actual received cuts, tick clocks, restore/cold-start operations, prepared outputs
and verified durable commit results. The worker uses a private write-only
loopback recorder capability; lost recorder ACK retries preserve exact identity.
Secret credential fields and raw household media are refused. The archive still
contains private household geometry/context and stays owner-local; agents receive
only permitted aggregates. Original source recording does not enable camera-media
recording or a new third-party egress route.

`replay.cjs` checks all source/module/config pins, chain/terminal integrity,
transaction outcomes, original event and receipt completeness. It then constructs
the **actual** worker and calls its restore/coldStart/applyCut/tick APIs in recorded
causal order, comparing full decisions, scenes, checkpoints, allocation/execution
and commit bodies. It never accepts a matching hash as a substitute for executing
the reducer/controller. Missing receipts, rehashed forged outputs, unknown effects
or recorder loss return incomplete. Ordinary replay remains distinct from the
later diagnostic bypasses and independently measured held-out acceptance.

```text
node tools/den-live-runtime/replay.cjs ABSOLUTE_PRIVATE_ARCHIVE.jsonl
```

Rollback stops only this opt-in composition, retains exact journals/configs/
archives/attempts and returns to prior separately stored service state. Do not
reset the shared checkout, delete failed attempts, reuse a fresh graph against an
old journal, bypass held-out contamination, or claim physical illumination.

## Controlled workstation verification

```text
node --test web-gateway/home-live-context-bridge.test.mjs
python -B -m unittest discover -s tools/den-live-runtime -p test_runtime.py -q
python -B -m unittest test_live_context_edge -q
```

Run the last command from `stack/services/perception-lab`. Tests use temporary
authored records, existing worker classes, actual loopback HTTP/TLS and ordinary
replay. They require local socket permission. No Home host, camera, HA/model API,
private household input, package install or deployment is contacted. A Windows
test may set `OPENSSL` to an existing supported binary; no certificate is installed
in a trust store. Passing these tests proves controlled software behavior only.
