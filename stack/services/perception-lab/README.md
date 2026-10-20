# Perception Lab controller

This service is a site-scoped experiment and recording controller. It does not
grant Home Assistant action authority. Importing it does not contact cameras,
start FFmpeg, download artifacts, load models, or call a cloud provider.

`portable_export.archive_chunks` builds a portable TAR stream from the existing
stable metadata snapshot and registered media. It does not create a disk spool.
Chunks are at most 64 KiB; metadata pages retain their existing bounded size.
The caller must supply a live authorization/cancellation callback and a transfer
budget. Revocation, source deletion, snapshot changes or byte-integrity failure
abort the stream. Media records name their archive paths and preserve MIME types,
calibration and timing metadata; encoded segments are not transcoded.

Consumers must require `COMPLETE.json`, TAR completion and every media length/hash
before accepting a package. Labels retain reviewed/model-generated distinctions;
an archive is not automatically a training-approved dataset. Exported external
copies cannot be remotely erased by deleting the controller session. This builder
is now exposed through an authenticated service/gateway archive route; browser
and native download/save clients and the Lab export CTA remain to be connected.

`GET /sessions/{id}/archive?generation=...&snapshot_id=...` requires read and
export capabilities and a finalized snapshot obtained through metadata export.
The service permits one active transfer. Home's gateway streams it separately
from JSON with bounded writes/backpressure, repeated authority/credential checks,
one transfer per site, a 64 GiB ceiling and a 30-minute transfer timeout. Partial
responses are destroyed on failure. Client-side site/connection cancellation,
portable package verification and native save acceptance remain separate work.

The fixed local specialist registry also supports `YoloXWorker`, pinned to the
inspected LA YOLOX-L artifact and RTMLib implementation. Provision its profile
with `scope: full_frame_detector`, score/NMS thresholds 0.45, RTMLib 0.0.16 and
ONNX Runtime 1.23.2. This route includes person detections and does not reproduce
the installed object service's person/hand crops, merging or tracking. Returned
confidence is unavailable, not 1.0. Explicit loading and measured admission are
still prerequisites; binding a worker never loads it.

## Source ownership and admission

`app.py` provides the `/api/perception-lab/v1` FastAPI routes; `controller.py`
orchestrates explicitly injected runtime hooks. `store.py` owns the durable
SQLite ledger and immutable media objects. `recorder.py` accepts bounded sampled
numeric data and contains an opt-in stream-copy supervisor. `artifacts.py`
records exact checkpoint/runtime declarations without claiming that they are
present, verified, or loaded.

The camera pipeline in `tools/victoria-camera/capture.py` remains the sole
DepthAI/USB owner. The Lab may consume its registered encoded relay and numerical
outputs. It must not create another DepthAI pipeline. Existing latest numerical
bundles are sampled media, not a full-rate archive.

`create_app(settings, runtime, store)` accepts an explicit runtime implementing
`capabilities()`, `start_session(session)`, `stop_session(session)`,
`switch(session, request)`, and `run_job(job)`. Optional hooks are `live(session)`
and `cancel_job(job)`. A missing hook blocks its operation; it never substitutes
fixture results. Active work with an uncertain execution/stop receipt becomes
`indeterminate`, retaining the camera/resource reservation. An operator/runtime
must reconcile it before retry or media deletion. Restart does not prove a
remote process stopped.

## Provisioning

`RemoteHTTP(receiver, grants)` is an optional ASGI ingress for exactly two POST
routes: `/v1/specialist/execute` and `/v1/specialist/lookup`. Grants contain unique
token SHA-256 digests, source `site_id`, and exact `cameras`. It derives the source
from the credential and verifies the envelope against it before receiver calls.
No browser CORS, model-management route, unscoped proxy or startup command exists.
Deploy only behind reviewed TLS with trusted ASGI scheme handling; do not trust
caller-supplied forwarded headers. Bound the upstream server's HTTP headers too.

`RemoteTransport` accepts a fixed HTTPS origin and credential callback, verifies
TLS through the standard client, and uses only those two routes with no retry or
redirect following. Transport failures remain unproven completion. Requests are
bounded to 6 MiB for execution and 32 KiB for lookup; responses to 256 KiB.
The ingress serializes requests independently of GPU admission. Cancellation
retains its slot until the local receiver task finishes. ASGI lifespan shutdown
closes admission and waits up to 30 seconds for idle; it reports failure and
retains storage if work has not drained. With explicit `owns_store=True`, only
a successful drain closes the dedicated receiver ledger. Abrupt loss still leaves
durable execution indeterminate and cannot authorize retry.

The trusted wrapper can call `replace_grants(...)` to atomically replace grants;
this advances a revision checked after upload and before result delivery. It has
no HTTP management route. Revocation does not cancel already executing inference
or erase its terminal receipt. Persistent grant provisioning, TLS deployment,
global resource admission and hardware acceptance remain separate dependencies.

`RegisteredSpecialists` accepts explicitly provisioned `RemoteSpecialist` adapters
alongside local workers, so A/B dispatch can select different hosts without a
fallback. The remote adapter resolves the same `RegisteredImage` pixels as local
workers, encodes lossless PNG without incidental metadata, and binds encoded and
decoded-BGR hashes to the complete pinned profile and paired request. Its media
authorization callback runs before decoding and again before dispatch. A durable
dispatch reference precedes sending bytes; repeated execution is refused.
Terminal reconciliation sends only the saved reference, never image bytes.

The transport is injected and must enforce workload admission, source permissions,
bounded authenticated transfer, remote exact-profile execution, and terminal
lookup. No HTTP endpoint, worker daemon, credentials or LA authorization is supplied
by this adapter. A compatible receiver and measured remote execution remain open.
Use only approved profile contents (no secrets); no caller-provided URLs are read.

`RemoteReceiver` supplies the durable receiving execution core for provisioned
Keypoint R-CNN, YOLOX, Ultralytics and RTMPose workers. Bind `source_site` from authenticated
ingress identity, never from request JSON. A dedicated owned ledger retains
site/request/digest/terminal proofs without copying a source home's live state.
PNG dimensions, encoded bytes and decoded BGR hashes must agree before execution.
Duplicate completed requests return saved proof; uncertain or interrupted requests
cannot execute again, including after receiver recreation. No model is loaded by
construction. The worker must already be explicitly loaded and admitted.

The receiver core itself has no listener; `RemoteHTTP` supplies the optional
authenticated ASGI boundary described above. Provisioned
authorization must enforce source/camera grants, clock bounds, resource admission
and workload authorization. The ingress must reject oversized bodies before JSON
parsing. Remote composite execution, terminal repair after uncertain
worker failure, ledger retention and actual networking still require integration.

For RTMPose, pass an explicit registered `crop_loader` to `RemoteSpecialist`.
Use the same resolver for a local pose arm's `RegisteredSpecialists.crop_loaders`
binding to compare identical proposals. The envelope includes bounded, frame-bound
crop metadata and its digest; unexpected label fields are rejected. This does not
run the source detector or schedule its completion before paired pose dispatch.

For live dependency execution, provision one `SharedDetectorCrops` instance as
both arms' crop resolver. It serializes one admitted source detector per exact
frame/configuration, persists the resulting proposals, then releases the waiting
pose arms. It requires an active paired request and a fresh recording, never runs
from catalog inspection, and never substitutes an empty crop set for failure.
Uncertain/crash-left detector stages are not repeated by another arm or resolver.
The shared stage is a distinct event with its own provenance; neither arm gains
independent evidence merely by consuming it. Its detector residency/execution must
be included in global admission and measured latency. Current support uses the
existing YOLOX-L, Keypoint, Ultralytics and HumanArt adapters.
The HumanArt detector is available as `HumanArtWorker`, pinned to the inspected
101,400,344-byte artifact and human-mode threshold 0.7. It inherits the registered
YOLOX dispatch route, including shared crops and remote execution. Use
`minimum_confidence=None`: this runtime returns boxes without scores. The complete
RTMW3D tracker/scheduler/pose pipeline remains separate work, and weight-license
provenance plus SDK/hardware validation remain required before preparation.

`RTMW3DWorker` now provides the installed pose stage separately: pinned 369 MB
ONNX artifact, 288×384 crops and 133-joint records. It uses the shared RTMPose
execution contract and can consume the same source crop resolver locally or via
`RemoteSpecialist`. Predicted hip-relative Z is labeled in model units and is
not stereo or room XYZ. Initial overlay edges cover only the shared 17 body
joints. The full incumbent tracker/scheduler and measured SDK parity are still
not established by this adapter.

For HA context, provision `ha_sources={camera_id: HAIngestor(...)}` against the
controller-owned store and pass it to `compose_runtime`. A trusted site adapter
calls `runtime.ingest_ha(session_id, snapshot_id=..., observed_at=..., states=...)`
with an exact allowlisted entity/state mapping. Snapshot acquisition time must be
validated by that adapter; HA `last_changed` is not an acquisition timestamp.
Refresh unchanged states with new snapshot IDs while recording. Missing entities
are unknown; attributes are not accepted. Reusing an ID with different content,
out-of-order or stale snapshots, and writes after stop/shutdown are rejected.
The ledger supplies identical provenance to live and saved reasoning. No public
write endpoint, HA credentials, network polling or action capability is added.
Live transport and clock validation remain provisioning requirements. Recipe arms
may declare `ha_evidence: false` to withhold HA from reasoning while preserving
the recording. Omission preserves the current shared-HA behavior. Activation
records bind that policy to live reasoning and saved Qwen A/B requests; Qwen
baseline retains shared HA. The current catalog does not yet expose a dedicated
HA-ablation recipe. Changing a recipe requires rebinding its reviewed digest.

One-lever recipes: `experiments.json` declares a `baseline` of shared evidence
families (`ha`, `ambient`, `qwen_scene` granted; `caption` withheld;
`temporal_encoder`, `spatial` not provisioned) and every recipe a `lever`
(`paths`, `note`, and a named `coupled` set when several paths move together).
`evidence_policy.validate_catalog` runs at catalog load and proves each recipe's
arms differ exactly at its lever paths; absent per-arm grants (`ha_evidence`,
`caption_evidence`, `source_policy.{family}`) mean the baseline default. The
recipe digest covers the lever; the activation records the resolved family
statuses (`source_policy`), the lever and `baseline_sha256`.

Ambient context (`ambient_context.py`) is computed once per scheduler tick from
the offer cutoff, the JEV config's `site_timezone` and the newest HA snapshot
carrying each entity bound to `daylight`/`weather_condition`: part of day, day
type, an hour label, daylight and weather, all bucketed enums. It is identical in
both arms, never a record, never refreshes visual freshness, and joins the
packet's shared context block (inside `state.semantic.context`) with per-family
coverage and a `shared_context_digest` over the non-lever families. It is
supplied only while the household policy grants `ambient-context-1` (coverage
`not_granted` when the zone is set but the grant is absent). A granted zone
that cannot load (no tzdata) is coverage `timezone_unavailable`: the clock
facts are `unknown` and daylight/weather still apply. JEV readiness reports
`zone_loaded: true/false`. `daylight`/`weather_condition` ride this grant: they
feed only the ambient block, never device state.

HA device facts and their household grants (`jev_evidence.FACT_GRANTS`):

| Fact | Household policy key | Value |
|---|---|---|
| `person_present`, `computer_active`, `screen_playback` | none (pre-existing) | — |
| `owner_home` (person entities only, home/away) | `owner_presence` | `owner-presence-1` |
| `den_audio_playback`, `den_light`, `computer_input_recency` | `device_context` | `den-devices-1` |

The grants are read per offer tick. A bound fact whose grant is absent is
dropped from both arms' packets for that tick and marked `not_granted` in the
context block's `ha_facts` (one status per bound device fact: `supplied`,
`stale`, `not_granted`, or the arm's HA family status). Dispatch continues with
the rest of the evidence; the egress gate still refuses a packet that carries
an ungranted fact. HA coverage is `supplied` only when a snapshot known at the
cutoff carries at least one bound, granted device fact; an HA record left
without a bound fact is dropped from the packet, never sent empty.

Cloud provenance is opaque: `evidence_roots`, `frame_id` and `capture_epoch`
leave only as `root-`/`frame-`/`epoch-` plus 24 hex of their sha256 (stable, so
shared roots still reveal correlation; the ledger keeps the originals), and the
gate refuses anything else. `site_id` and `camera_id` are safe as sent: the gate
requires them to equal the household policy's own `site_id`/`camera_ids`.
`session_id` is the controller's uuid4 and must equal the request's session.
`id` is a ledger event id or a code-built digest id (`[A-Za-z0-9_:-]`, no prose).

Continuous Qwen scene (`qwen_live.py`, `qwen_scene.py`): with a `qwen_live`
block in the live config (managed controller only) each recording runs one
perceiver pump beside the caption pump. It sends only while the recording
records, the session's reasoning is enabled and the household policy lists
`lab-live-scene-1` in `structured_sources` (a per-tick kill switch); it stops
with the other readers, before the reasoner closes. Per offer tick every arm
whose recipe grants `qwen_scene` gets the same shared `qwen.observation`
record: the `qwen-scene-projection-1` enum projection (latest facts plus a
code-built 60/120 s history), never visual, never a scheduling key (a new
receipt alone never causes a JEV call) and marked
`correlated_with: same_camera_visual`. `jev_evidence.project` copies it only
when every string is a projection token and it holds no number.
`state.semantic.qwen_scene` carries it as `calibration: unvalidated`,
`independent_confirmation: false`, and `state.semantic.corroboration` lists per
activity the same-camera signals (detector/pose relations, Qwen, camera
occupancy) against the independent ones (PC input recency, den lamp, den audio)
with `independent_support` none/one/multiple (rule `corroboration-3`). PC input
never counts for `computer_work` by itself: `computer_work.pc_input` is
`pc_input_under_15s_with_camera_cue` (the only case listed as independent: a v3
`under_15s` bucket plus a camera cue at the PC, meaning Qwen `gaze_target`
`screen`, `typing_visible` `yes` or `screen_facing_subject` `yes`, a wrist
near the keyboard or mouse, or `head_toward` `monitor_side`), `pc_input_recent_but_not_observed_at_pc`,
`pc_input_recent_but_conflicts_with_held_object`, `pc_input_not_recent` or
`pc_input_unknown`. `computer_work.conflicts` names a book or phone that Qwen
sees in hand. `state.semantic.static_scene_objects` (rule `static-scene-1`)
tags objects as background: those whose box stays put (IoU >= 0.8) across at
least 3 distinct frames of the arm's own packet (the newest 8 are compared), and
those seen with no person box or while `person_present` is `off`. An object that
a wrist or the face is near is never tagged. Tagged labels move from
`detected_objects` to `background_objects` and are never activity cues. A
latest-only arm packet has one frame, so only the no-person rule applies there.
Phone relations also fire within one phone length of the phone box (`basis`
`within_phone_length`), and `phone_near_face_in_image` covers a phone within
one phone length of a supported nose, eye or ear. Each posed observation carries
a `head` block (rule `head-direction-1`, computed from the one pose's supported
face joints and shoulders; words only). Its `yaw` is `toward_image_left`,
`toward_image_right`, `forward` or `unknown`: the nose offset from the ear
midpoint (or the one visible ear, since ears closer than 0.1 shoulder widths
are one ear) reaches 0.4 shoulder widths. Its `pitch` is `down` or `level`:
`down` when the nose sits less than 0.4 shoulder widths above the shoulder
line. `support` is `both_ears`, `one_ear`, `eyes_only` or `insufficient`. With
the JEV provisioning key `"monitor_side": "image_right"` (or `"image_left"`) it
also carries `head_toward`: `monitor_side`, `lap_or_hands`, `elsewhere` or
`unknown`. Under rule `corroboration-3`, `head_toward` `monitor_side` is a
camera cue at the PC. The v6 wire carries the same words in `now.head`,
`now.head_toward` and a per-segment storyline `head_toward`.

Desk-seat position (rule `desk-seat-1`, wire `den-facts-7` only) is a word for
where the one person in view is, computed from the detector's person box and a
private per-site region in the JEV provisioning file, for example
`"desk_seat": {"edge": "image_right", "edge_margin_frac": 0.03,
"min_width_frac": 0.35, "min_top_frac": 0.08}`. The box must touch that image
edge (within `edge_margin_frac` of the width), be at least `min_width_frac` of
the width wide and have its top at least `min_top_frac` of the height below the
image top (seated, not standing). `now.position` is then `desk_seat`, else
`elsewhere_in_room`, or `unknown` without exactly one person box. The box and
region never leave. `desk_seat` is a detector line for `computer_work` (so a
Qwen screen cue makes `camera_line` `detector_and_qwen`) and a camera cue at the
PC for `pc_input_role`, unless the debounced detector cue is a book or phone
near the hand. `den-facts-7` is its own household grant in
`structured_sources`; `"wire": "den-facts-7"` needs `desk_seat`, and setting
`"wire": "den-facts-6"` again rolls back (the region is then ignored). Each
registered wire schema's digest is pinned in the tests. Coverage is the projection status
(`supplied`/`stale`/`invalid`), `not_granted` without the household grant, or
`not_provisioned` without a pump. `/live` returns `qwen_live_status`.

Room facts (rule `room-facts-1`, `room_facts.py`, wire `den-facts-8` only) are
placement-agnostic words computed from each arm's person box and keypoints, a
2 Hz depth sample shared by both arms, and the saved den site map (the live
config's `"room_facts": {"enabled": true, "site_map_file": ...}`):
`now.position`, `looking_at` (head pose, not eye gaze) with its stability,
`hands_near`, `posture` and `motion`, a `room_storyline`, and `room_line` /
`room_conflicts` in `support` from the `jev_wire.ROOM_SUPPORT` table. The
controller writes one `room.facts` ledger event per paired detection; the
den-facts-8 scheduler offers the newest per arm as shared context and the
policy re-derives it. Only fixed category words leave. Without a current 3D
fact, `desk_seat` (optional on den-facts-8) is the position fallback
(`position_method` `image_edge_rule`). `/live` returns `room_facts_status` only
while room facts are provisioned. Switch-on: `docs/DEN-ROOM-MAPPING-RUNBOOK.md`
section 7.

```json
"qwen_live": {
  "transport_file": "/etc/perception-lab/qwen-live-transport.json",
  "artifact_sha256": "<LA Qwen artifact sha256>",
  "runtime_revision": "<LA runtime revision>",
  "enabled": true,
  "interval_seconds": 4, "frames": 3, "span_seconds": 3, "max_per_minute": 15,
  "horizon_seconds": 120, "timezone": "America/Vancouver"
}
```

The transport file uses `qwen_transport.load_config` (mTLS to the LA
live-scene consumer) with `deadline_seconds` 5-8. Absent block or
`"enabled": false`: no pump, `qwen_scene` coverage `not_provisioned`.

`ha_clients={camera_id: HAStateClient(...)}` connects the source to recording-owned
polling. Construction and catalog/replay reads do not poll. The client accepts a
provisioned HTTPS origin and credential callback, reading only one to eight exact
entity endpoints at one-second intervals after the previous acquisition completes.
There is no backlog, redirect following or service-call route. Responses are
bounded to 64 KiB per entity; attributes are discarded. The acquisition interval
is bounded to two seconds for accepted evidence and explicitly non-atomic.
Stop/reaper/shutdown cancel polling and discard late results; `ha_status` reports
the source state. Existing facts expire on failure rather than being refreshed.
An HA source file with `"entity_list": true` reads the scoped
`perception_lab_source` view's own allowlist (e.g. its Den area) at session
start and then requires an explicit `entities` narrowing list (one to eight exact
ids); listed ids outside it are never requested. The component (0.2.1) admits
`sensor`, `select`, `input_select`, `input_number` and `number` entities to area
mode only by explicit `include`, never by area membership, because HASS.Agent
publishes free text (active window, logged-in user) as plain sensors.

This uses Home Assistant's documented [GET state endpoint](https://developers.home-assistant.io/docs/api/rest/)
(checked 2026-09-26). No endpoint or bearer is supplied by default. Provision a
reviewed local authenticated HTTPS ingress; the existing Victoria plain HTTP
address is not silently accepted. A HA token is not made read-only merely because
this client only issues GETs; credential/ingress restrictions need separate review.
Acquisition time describes when HA was read, not the physical sensor update time.
Actual network, HA load, clock and field-session validation remain outstanding.

An HA source file may declare `derived.computer_active` (`computer_active_v1`)
from two allowlisted entities: a session state and an ISO-8601 last-input time.
Each snapshot records `on` only for a configured unlocked value with input no
more than `max_idle_seconds` (30–3600) before acquisition; a last-input time
more than 5 s in the future, or any unknown input, is `unknown`. The fact keeps
its input ids and rule locally, is independent of the camera and has the same
snapshot validity as other HA facts. JEV binds it only as `computer_active`.

`derived.computer_input_recency` uses the same session and last-input entities
without `max_idle_seconds`. Its optional `"rule"` key selects the buckets.
`"computer_active_v2"` is the default when the key is absent, with buckets
`under_60s`, `1_to_5_min`, `over_5_min` and `locked`. `"computer_active_v3"` has
buckets `under_15s`, `15_to_60s`, `1_to_5_min`, `over_5_min` and `locked`. Bounds
are inclusive. Both versions use the same 5 s clock-skew rule, and an unusable
input is `unknown`. HASS.Agent publishes `lastactive` on its sensor update
interval, so the recorded idle time is never shorter than the true one:
`under_15s` can be missed but never invented, as long as that interval is 15 s
or less. Check the LastActive sensor's update interval in HASS.Agent. With a
longer interval, `under_15s` appears only just after each publish. Only
`under_15s` with a camera cue can support `computer_work`.

```json
"derived": {"computer_active": {"session_entity": "sensor.formd_t1_sessionstate", "unlocked_values": ["Unlocked"],
                                "last_active_entity": "sensor.formd_t1_lastactive", "max_idle_seconds": 60},
            "computer_input_recency": {"session_entity": "sensor.formd_t1_sessionstate", "unlocked_values": ["Unlocked"],
                                       "last_active_entity": "sensor.formd_t1_lastactive", "rule": "computer_active_v3"}}
```

A reviewed Python service wrapper can now pass
`create_app(settings, runtime_factory=lambda ledger: compose_runtime(ledger, ...))`.
The factory receives the app-owned SQLite ledger; clean lifespan shutdown drains
its workers and releases that same store. An inert factory failure releases the
owned store. Factories must perform no capture, model loading or network work
during construction; configuration cannot name arbitrary modules to execute.

`composition.compose_runtime` connects one registered Victoria OAK source to the
stream-copy recorder, authenticated preview/numerical clients and ingestors,
paired executor, optional JEV provider, and optional Qwen saved-analysis executor.
Supply exact `StreamSource` instances, the camera credential callback, validated
receiver clock bounds, profile resolver and common-admission clients from the
reviewed wrapper. The helper supplies no credentials, resource admission or
household cloud authorization. It refuses an Echo site and multiple OAK sources
because its camera clients address one Jetson-local owner. Other-site composition
and complete incumbent pipelines remain separate work.

Live JEV cadence is a controller setting, independent of the recipe: the live
config key `reasoning_min_offer_interval_s` (seconds, 1-10, default 2.0) is the
per-arm minimum interval between scheduler offers. An arm is offered only for
new admitted camera evidence; a newer output inside the interval is deferred and
its newest evidence is offered when the interval ends. JEV admission budgets
(dispatch spacing, per-minute and per-session calls) are unchanged. The managed
controller re-reads the live config per session.

With the v6 facts wire the scheduler uses the facts-key cadence (plan §6.1/6.2),
live config `reasoning_cadence` (`mode` `facts_key`, the default, or
`new_evidence` for the rule above; `keepalive_s` 10, `debounce_s` 2,
`hold_max_s` 12, `gate_keepalive_s` 10). Each tick computes each arm's facts
key locally (`JevProvider.facts_key`, the digest of the state a request would
carry). An arm is offered when its key changed since its last offer (at most
once per max(interval, debounce)), or re-offered on an unchanged key after the
keepalive while the camera-derived occupancy gate reads occupied. While that
gate reads vacant or unknown for longer than its hold, nothing is requested:
the arm answers locally with a `reasoning.gate` record (`code_gate`,
`insufficient_evidence`, reason `occupancy_vacant|occupancy_unknown`). Skips
are counted in `reasoning.scheduler` (`facts_unchanged`, `keepalive_not_due`,
`no_person`, plus the earlier reasons), with `offer_reasons` and
`local_abstains`. A delivered answer is recorded with `facts_key` and
`prediction.valid_through` (arrival + `hold_max_s`); its facts-bound validity
ends earlier, as a `reasoning.validity` record, when the key changes, a newer
answer or local abstention arrives, or occupancy reads vacant. The strict
frame-TTL `valid_until` is unchanged. Every delivered answer and local
abstention also steps the per-arm smoother (`jev_smoothing.Smoother`, the same
filter the offline replay runs) into a `reasoning.smoothed` record (run
`jevA-smooth`). These three kinds are local only: no JEV request, packet or
sidecar reads them. Findings keeps the strict metrics and adds
`metrics_valid_facts` per JEV run, the smoothed runs, and a `jev_metrics`
summary (`coverage`, `coverage_valid_facts`, `coverage_smoothed`, and likewise
for exact match and false-confident seconds). The timeline carries
`valid_through_ms` on JEV results and a `jev_smoothed` strip (contract revision
3). `python jev_cadence_replay.py LEDGER SESSION_PREFIX` checks all three
offline against reviewed labels (read-only, no calls; not shipped).

Three optional levers in the private JEV config, all off by default and each a
registered name: `questions` (`v6`, the default `den-activity-8-facts`; `v8`,
`den-activity-10-facts`, the same set with one computer-work wording change
naming the desk seat with counted PC input, `den-facts-7` only; or `v7`,
`den-activity-9-fanout`: a Choice with structured `what`/`not_for`/`examples`
criteria plus seven single-condition Nouls, fused in code by
`jev_wire.fuse` veto gates that only ever turn an answer into
`insufficient_evidence`, with the raw answers and the gate outcome kept in
`diagnostics.fusion`); `facts_key` (`facts-key-1`, the default, or
`facts-key-2`, a coarse key that ignores storyline spans and earlier segments,
the derived support lists, Qwen `visible_basis`/posture/history and
detector/pose coverage flicker, and holds a hand cue lost for under 10 s); and
`smoothing` (a `jev_smoothing.PARAMETER_SETS` name, default `jev-smoothing-1`).
`questions` and `facts_key` need the v6 wire. The policy accepts either
registered question set by digest, unchanged.

`python jev_replay_calls.py --ledger SNAPSHOT --replay-db REPLAY --config
JEV_CONFIG --session PREFIX --questions v6|v7|v8 [--wire den-facts-6|den-facts-7]
[--desk-seat REGION.json] [--from-s S --to-s S]` is the bounded paid replay
(plan §7.1; operator tool, not shipped). It re-asks JEV at a recorded session's
own cutoffs through the controller's `JevProvider` and `LabJevPolicy`
unchanged; the household policy must list `jev-replay-1` (and the replayed
wire, `den-facts-6` or `den-facts-7`) and be enabled. `--wire` defaults to the
config's wire and `--desk-seat` to its `desk_seat`; `--from-s`/`--to-s` limit
calls to a session-time window. The ledger is read-only (use a snapshot copy); reservations,
admissions, `reasoning.request` events and a response cache keyed by packet
sha256 go to the separate replay database, which also enforces a hard cap of
600 admissions across runs. `--dry-run` runs every policy check without
admission or transport; `--repeat N` re-sends N cached packets once
(repeatability probe).

While a session records, the controller writes one content-free
`controller.load` receipt every 5 s: process CPU as a percentage of one core
(`time.process_time` deltas, all threads), RSS, per-phase server-side timings
(`live_handler_ms`, `live_runtime_ms`, `live_room_ms`, `graph_handler_ms`,
`reasoning_offer_ms`, `reasoning_build_ms`, `reasoning_send_ms`: count, p50, p95,
max), storage accounting counters for the interval (including the entries the
out-of-band walk visited) and expiry-loop upkeep failures, counted by step. Each
upkeep step is guarded: one error cannot stop duration limits, reconciliation or
load receipts. `/live` returns the latest
as `controller_load` {`cpu_pct_1core`, `rss_mb`, `live_handler_ms_p50`,
`graph_handler_ms_p50`, `reasoning_build_ms_p50`}.

For pre-staged model files, pass `artifact_preparation=ArtifactPreparation(...)`
using an `ArtifactRegistry` attached to this same app-owned ledger. The wrapper
provisions the cache root, device-specific relative file bindings and positive
verification byte budget. The factory attaches a bounded preparation job worker;
only an explicit preparation request hashes the files. A successful receipt means
stored, not runtime validated, loaded or warmed. Unknown artifact IDs are blocked
before file access. Downloads, compilation and residency admission are not supplied
by this verifier, and `keep_ready` remains blocked without residency support.

This is a composition entrypoint, not deployment authorization. Source admission
flags do not establish fresh operational health or lift host quarantine. The
default `app:create_app` below remains non-executing without a reviewed wrapper.

`profile_resolver.RegisteredPairs` can supply the `resolve_profiles` callback.
The wrapper binds a catalog recipe's canonical SHA-256 and exact A/B profiles,
then supplies a read-only readiness checker for the complete recipe. That checker
must consider source/HA/map/reasoner prerequisites as well as the workers, and
return the same recipe digest and profiles only when ready. This helper does not
implement resource admission, prepare a model, or substitute a profile. Execution
still requires admission at dispatch. Unbound recipes remain blocked; a failed
switch preserves the active pair and recording, with the specific blocker in the
controller result.

`config.example.json` intentionally contains no credential grants or admitted
stream sources. `PERCEPTION_LAB_CONFIG` must point to an explicitly provisioned
configuration. The factory may be launched by a reviewed service wrapper as
`uvicorn app:create_app --factory --host 127.0.0.1`; the default factory has no
execution runtime and operational calls remain blocked. No install/deploy script
is provided or invoked by this change.

Provision separate Lab bearer credentials with at least 32 characters and store
only their SHA-256 digests in `token_grants`. Each grant contains `subject`,
`site_id`, `token_sha256`, and `capabilities`. Supported scopes are `read`,
`record`, `review`, `run`, `prepare`, `export`, `delete`, and producer-only
`ingest`. A site's storage cannot be reopened as the other site. The optional
`X-Home-Lab-Site` header must match the configured site. Use authenticated TLS
transport outside loopback; bearer authentication is not transport encryption.
Keep configuration and storage private to the service account. No face identity
or inferred household identity authorizes these endpoints.

`developer_fixture_mode` is an explicit provenance marker. It does not grant
access, bypass admission, invent a runtime, or start cameras. Tests inject fake
runtimes explicitly and use temporary storage.

## Storage and lifecycle

Defaults are 64 GiB total Lab storage, 2 GiB free-filesystem headroom, a 30-minute
session maximum, 16 MiB per immutable media object, and 4 MiB per HTTP media read.
Both database and media/staging files count toward storage usage. Existing Lab
sessions remain until explicitly deleted; there is no age expiry or automatic
eviction. These rules do not change production snapshot/clip retention.

Quota checks do not rescan the Store's own trees per write. Every check adds:

- **Counted immediately, by the running total:** files only the Store writes,
  meaning `media/` (objects, and partial files left by a failed write),
  `staging/` directories after their recorder has terminated, and the
  `.controller.lock` file. Every Store write and delete path (media, segment
  imports, media deletion, erasure) updates the total under the ledger lock.
- **Counted immediately, by measurement at every check:** SQLite's own files
  (`stat`); each active recorder's staging directory (FFmpeg writes it; the
  monitor passes its own fresh measurement); and **every other entry under the
  root**, such as `derived/` written by `tools/extract-lab-detector-inputs.py`
  or any operator file. This last walk is bounded to 4,096 entries
  (`MAX_UNOWNED_ENTRIES`). Beyond that, admission is refused as
  `storage_unbounded` rather than estimated. Keep out-of-band content small, or
  move it out of the storage root.
- **Counted only at the next reconciliation:** a file created, grown or removed
  inside `media/` or `staging/` by anything other than the Store. That is
  drift. Reconciliation is a full scan of those trees at controller startup,
  at session start (before admission), at session stop, and every 60 s on the
  expiry thread (`STORAGE_RECONCILE_INTERVAL_S`).

Drift is corrected, counted and logged without content on the
`perception_lab.storage` logger. A corrected total over quota fails closed:
every admission is refused until usage is back within quota. A scan that fails
also fails closed until a scan succeeds: a link under the root refuses as
`unsafe_storage`, and a permission or I/O error as `storage_scan_failed` (503),
counted and logged with the exception class only. Quota semantics are
unchanged: no write is admitted that would exceed it.

SQLite uses WAL and FULL synchronous writes. Object writes use a private partial
file, fsync, atomic rename, then a manifest insert. Crash-orphaned objects/partial
files remain on disk and count toward quota; this implementation does not silently
delete or publish them. Recovering such files requires an explicit integrity
review. The session ledger records source failures and uncertain recovery.

Explicit session deletion requires the current session revision, a stopped or
otherwise conclusively inactive recorder, and no running/uncertain analysis.
It removes recorded immutable media, increments its generation, and retains
annotations and the deletion audit. **This is media deletion, not full personal
data erasure.** A complete account/session purge is not implemented.

## Recording and playback contracts

`POST /sessions/{id}/samples` requires the `ingest` scope. Its body contains
bounded immutable numerical bytes; `X-Perception-Sample` contains base64url JSON
metadata. Required fields are `site_id`, `camera_id`, `capture_epoch`, `stream`,
`sequence`, `source_timestamp_seconds`, `source_clock`, `timestamp_semantics`,
and `format`. Unknown clock uncertainty stays unknown. Receipt time never
refreshes source freshness. Duplicate source identities require identical bytes
and metadata; conflicting replays fail.

`depth_u16_le` requires matching width/height/byte count, millimetres, invalid
value 0, `alignment_frame: rectified_left`, and `calibration_id`. `numeric_npz`
accepts bounded numeric NPY members with no pickle/object/structured arrays,
encrypted members, archive paths, or unbounded expansion. Calibration references
are provenance, not a claim that physical depth or geometry has been validated.
Lossless depth and transformations support reproducible point reconstruction;
copying full point clouds is not mandatory. Camera movement, metric references,
and RGB/depth reprojection still require explicit geometric acceptance.

`StreamCopySupervisor` starts only a configured/admitted `StreamSource`, whose
codec and codec validation ID must be provisioned. It uses FFmpeg with argument
arrays, no shell, no transcoding, and no inherited credential-bearing output.
HLS fMP4 initialization and completed `.m4s` fragments are committed separately
from a bounded local event playlist. The manifest links each fragment to its
initialization object and includes registered RFC 6381 codec, duration, sequence,
and encoded-stream timeline. Temporary/unclosed fragments remain unpublished.

The encoded timeline is relative to the encoded sequence, **not validated camera
exposure or session observation time**. Numerical association remains unavailable
until a commissioned clock/PTS bridge exists. No adjacent arrival times or
generated FFmpeg PTS imply RGB-D synchronization. Playback may show video without
claiming synchronized depth overlays. Actual codec/MSE behavior has not been
validated against the deployed stream by these deterministic tests.

Staging has reserved headroom, periodic size monitoring (every 0.2 s: this
directory's bytes plus the Store's running total, no storage scan), bounded
segment imports, and process termination on limit/error. A Stop that moves the
session to `stopping` before the recorder's own stop signal (the managed pair's
asynchronous stop) finalizes with the stop reason Controller.stop recorded as
`recording.stop_requested` (`user_stop`), not as `recording_state_changed`.
An in-process stop also reports that recorded reason (`controller_shutdown`,
`quota_exceeded`, `duration_limit`); only an unrecorded stop reports
`user_stop`. A filesystem quota is still necessary
for a strict hard bound against an unexpectedly fast external writer between
checks. Live NVMe, thermal, CPU, and capture-coexistence acceptance is pending.

## API and evidence

- `GET /capabilities`, `GET/POST /sessions`, `GET /sessions/{id}` and `/live`.
- `POST /sessions/{id}/stop`, `/switch`, and `/bookmarks`. Switch accepts either
  one `{arm, profile_id}` or an atomic `{experiment_id}`, plus `request_id`.
- `PUT/DELETE /sessions/{id}/annotations/{annotation_id}` uses optimistic
  `expected_revision`. New labels use 0. `origin` and `reviewed` are distinct;
  a human-origin label is not implicitly reviewed ground truth.
- `POST /jobs`, `GET /jobs`, `POST /jobs/{id}/cancel`, and `POST /diagnostics`.
  Request IDs are content-bound. Desired work is not reported as running.
- `GET /sessions/{id}/media?cursor=0&limit=100` returns a bounded manifest page,
  `next_cursor`, generation and total count. Follow pages to enumerate all media.
  `GET /sessions/{id}/media/{media_id}?generation=N` reads bounded immutable byte
  ranges and checks content integrity. Opaque IDs never accept filesystem paths.
  `GET /sessions/{id}/media/{media_id}/descriptor?generation=N` reauthorizes one
  manifest item without bytes. Clients should retain only a bounded replay window,
  not accumulate every numerical-frame descriptor in memory.
- `GET /sessions/{id}/events?after_id=0&limit=1000` pages the append-only ledger.
  Session detail intentionally contains a bounded first event/media page.
- `GET /sessions/{id}/export` returns a JSON descriptor, not a downloaded archive.
  Its media references remain authenticated, site/session/generation bound.
- `DELETE /sessions/{id}` explicitly deletes recorded media as described above.
- `GET /sessions/{id}/findings` scores only explicitly reviewed human intervals
  against typed prediction records. Observation events are not activity labels.
  Diagnostic predictions are unscored; predictions must declare session-relative
  seconds, availability and expiration. Missing data returns insufficient data.
  Its `auto_results` is the session's stored automatic result (below), or null.

About 30 s after a recording stops, the controller's maintenance thread stores
one `results.auto_score` event (`lab_scoring`): the live JEV answers per arm
and activity (coverage, exact@coverage, abstention, false-confident seconds,
false positives, time to first correct) against the saved session labels. If
there are none, it scores them against draft labels made from the bookmarks
(each bookmark's tag or text until the next). Draft labels are unreviewed and
live only in that local event. They are never annotations or a model input.

`lab_regression.py` (operator tool, not in the release) builds the regression
set (the annotations table plus the private
`~/perception-lab/private/regression/manifest.json`), scores live answers and
`jev_replay_calls.py` replay jobs, and gates a candidate against a baseline on
their common time with the explicit tolerances in `GATE_DEFAULTS`. An activity
whose data comes from one session is marked LOW (low confidence). It is still
gated, but one session does not show that a change holds across the room. An
activity of the set that the candidate did not cover leaves the gate
INCOMPLETE. `replay` dry-runs the whole set and estimates the cost first. It
makes paid calls only with `--yes` and within `--max-calls` (default 400).

Runtime output events are separate from jobs and labels. The controller exposes
`perception.output`, `runtime.output`, and `analysis.output` as stored outputs;
only typed activity predictions can be scored. An inference result, a preference
prediction and a hypothetical recommendation do not call lights or other devices.

## Validation

Run `python -m unittest discover -s tests -v` from this directory in an environment
containing the pinned requirements/dev requirements. Core tests use only the
standard library. `test_api.py` uses real FastAPI/Starlette in-process HTTP via
TestClient. Tests create only temporary fixture data and never invoke FFmpeg,
DepthAI, model inference or remote hosts. Root-level reasoning/geometry/runtime
test modules have separate test discovery if present.

The tests cover authorization/site boundaries, session idempotency and crash
uncertainty, annotation conflicts, quota preservation, replay identities,
depth/NPZ validation, immutable ranges/generations/integrity, fMP4 playlist
validation and closed-fragment manifests, explicit cancellation, and blocked
runtime behavior. They do not establish live recording, physical accuracy,
accelerator coexistence, provider quality, or production readiness. LA quarantine
and deployment/admission requirements in repository AGENTS.md remain in force.
# Analysis composition (not deployment authorization)

### First RTSP-only recording

For the first recording/replay/label trial, use `capture_mode: "encoded_only"`
in the schema-1 capture configuration and omit `camera_credential_file` and
`receiver_clock` entirely. Keep `settings_file`, the registered loopback RTSP
`source` with validated codec, and all three `gate_references`. Configure the
camera's modalities as `["rgb"]`. Controller authentication, storage and source
admission still apply. The existing RTSP reader credentials, if required, belong
in the protected source configuration; this mode only removes the JPEG API token.

This creates no JPEG/numerical polling and no inference. It does not require the
separate camera pilot to activate or a camera restart. Playback is on the encoded
timeline; no RGB/depth synchronization or source exposure time is asserted.
The browser and Tauri still need their authenticated Lab endpoint provisioning.

The provisioned service can compose `SequentialAnalysis(store, profile, executor)`,
`AnalysisJobs(store, analysis, RegisteredAnalysisInputs(store))`, and
`ServiceRuntime(..., analysis_jobs=jobs)` before passing that runtime to
`create_app`. The executor must use the common admission boundary and return
matching epoch/artifact/request terminal receipts; it is not a direct model URL.
The profile resolver must return an already admitted pair. Construction does not
start capture, download artifacts, or load models.

Analysis runs independently of browser navigation with one active and one pending
job. Application shutdown must close the job dispatcher before closing its store,
then reconcile still-active external requests; closing the UI is not cancellation.
Unknown terminal status preserves reservations and blocks subsequent work.

The store holds an OS lifetime lock on `.controller.lock` before opening SQLite
or running crash recovery. Use a local filesystem with working OS file locks;
network filesystem semantics have not been validated. The lock file is never
deleted as a takeover mechanism. A second process fails closed. Recovery marks
possibly dispatched work indeterminate and cancels/interrupts unsent work without
replay. It never resets an engine or infers provider completion.

`ServiceRuntime.close(timeout=5)` closes new dispatch, cancels unsent work and
waits for local database users to finish. The ASGI lifespan waits for the expiry
thread and recording stop attempts, then requests this drain. The owned store is
closed only with a positive drain receipt. Otherwise it and its lock remain
held until safe teardown/process exit; `app.state.shutdown_drained` is false.
An injected store remains the caller's responsibility. Local worker completion
is distinct from remote execution completion: durable uncertain reservations
survive a clean local shutdown. Windows ownership behavior is tested locally;
Linux `flock` behavior remains a target-platform commissioning check.

The registered input loader currently requires captured JPEG/PNG RGB samples with
verified source-clock mapping. Encoded session video needs a separately admitted
frame extraction path. Missing mapped frames block analysis; packaging time and
unverified video PTS are never substituted. Provider execution, frame extraction,
terminal reconciliation, and hardware acceptance remain incomplete.

## Recording-only commissioning

Spatial publication remains separately gated. `room_evidence.publish_room`
requires exactly one registered `depth_u16_le` source per point, matching site,
camera, capture epoch, frame identity, observation time, calibration and source
coordinate frame. Its metadata must identify validated geometry with a depth
validation receipt and verified clock mapping with a mapping ID. Existing sampled
bundle views deliberately report unvalidated geometry/unverified clocks and
therefore cannot satisfy this boundary. Do not relabel those views to enable a
recipe: a commissioned depth consumer must derive new traceable measurements
from actual calibration and timing evidence. Model-predicted Z is never a substitute.

`capture_app:create_capture_app` is the fixed recording-only ASGI entry point.
After independent commissioning and workload approval, an operator can provision
an absolute `PERCEPTION_LAB_CAPTURE_CONFIG` path with this structure (illustrative
values are not commissioning receipts):

```json
{
  "schema_version": 1,
  "settings_file": "/etc/perception-lab/settings.json",
  "camera_credential_file": "/etc/perception-lab/camera-token",
  "source": {
    "id": "oak",
    "camera_id": "den",
    "url": "rtsp://127.0.0.1:8554/REVIEWED_STREAM",
    "codec": "avc1.640028",
    "codec_validation_id": "REPLACE_WITH_CODEC_RECEIPT"
  },
  "receiver_clock": {
    "validation_id": "REPLACE_WITH_CLOCK_RECEIPT",
    "uncertainty_seconds": 0.1
  },
  "gate_references": {
    "technical": "REPLACE_WITH_TECHNICAL_REVIEW",
    "health_resource": "REPLACE_WITH_CURRENT_HEALTH_AND_RESOURCE_REVIEW",
    "workload_authorization": "REPLACE_WITH_AUTHORIZATION"
  }
}
```

Settings use the existing `Settings` schema, exactly one registered Victoria
camera, non-fixture mode, absolute storage and provisioned bearer hashes. Protect
both files with service-account-only access. The camera token is read on each
request so replacing it does not require persisting it in the Lab database.
RTSP credentials, if required by the existing relay, belong only in the protected
configuration; never print its contents or commit it. The recorder hands the
source URL to FFmpeg on stdin as a one-entry concat list, never in argv, which
every local user can read; the display-only RTSP preview and live video
(`PERCEPTION_LAB_DISPLAY_RTSP=1`) still pass it in argv. The source is restricted to
loopback port 8554 and FFmpeg to `/usr/bin/ffmpeg`. Preview/bundle clients retain
their fixed camera-owner loopback endpoint. No arbitrary module import is allowed.

The future supervised launch, from this service directory with approved installed
dependencies, is:

```sh
PERCEPTION_LAB_CAPTURE_CONFIG=/etc/perception-lab/capture.json \
  python -m uvicorn capture_app:create_capture_app --factory --host 127.0.0.1 --port 8788 --workers 1
```

This command has **not** been executed on hardware. Single-worker ownership is
required. The existing authenticated Home ingress still needs separate reviewed
provisioning; a loopback listener alone does not connect browser or Tauri. No
model, camera or recording starts just by opening the app. Recording begins only
through an authenticated recording request. Gate references are an operator
review trail, not automatic health validation or an authorization bypass.

For the capture/storage/replay stage, reviewed Python provisioning may call
`compose_runtime(..., recording_only=True)` with the existing `store`, one
admitted `StreamSource`, camera credential callback and validated receiver clock.
Omit `resolve_profiles`, specialist workers/executors, JEV, Qwen, policy and
artifact-preparation bindings. Supplying any of them is rejected; this mode is
explicit, never an automatic fallback from a failed inference configuration.

Construction performs no capture. An explicit recording request starts the
existing encoded stream-copy recorder and sampled preview/numerical pumps.
Capabilities report `recording_only_commissioning` for inference; sessions record
the same reason, and no paired runner or reasoning scheduler is activated.
Stopping and replay/export use the normal session lifecycle. HA observations may
be separately provisioned through the existing bounded source/client bindings.

This is a commissioning mode, not completion of the Lab's comparison workflow.
It requires the same camera/codec, clock, storage, health and authorization gates
as other real recording. No ready source, credential, capacity measurement or
workload authorization is supplied by selecting the mode. Move to a separately
reviewed paired runtime only after model and resource admission is available.

## Metadata exports

`GET /sessions/{sid}/export` requires the export capability and a finalized
recording. It returns `perception-lab-session-v2` with `snapshot_id`, record counts,
up to 200 records/approximately 1 MiB per page, and `next_cursor`. Continue with
`snapshot_id`, `cursor`, and optional `limit`. Annotation/job/event/media changes,
deletion or controller restart invalidate an ongoing snapshot with HTTP 409.
Snapshot IDs are consistency checks, not bearer capabilities.

The Home button collects every page before downloading. A mixed, incomplete or
oversized collection produces an error, not a partial successful export. Browser
collection is capped at 64 MiB of encoded record data and 10,000 pages. Larger
metadata collections need a future streaming export path. Each media descriptor
retains its registered ID, generation, digest and metadata; the download contains
**no media bytes** and is not a portable video archive. Future training needs an
authorized media/dataset packaging path and explicit dataset review.

Current/deleted annotation revisions and complete annotation events are retained.
Only current reviewed human labels are marked `reviewed_human`; reviewed model
labels remain `unverified`. Nothing is automatically promoted to training data.
Exports themselves are not persisted by the service; source deletion invalidates
their references. A user-downloaded file is a separate local copy outside server
deletion control.

# Local specialist bindings

`compose_runtime` can receive `local_workers=[...]` instead of a
`specialist_executor`. These must be explicitly constructed KeypointWorker,
YoloXWorker, UltralyticsWorker or RTMPoseWorker instances. RTMPose additionally requires a
`crop_loaders` mapping from its exact profile ID to a registered crop resolver.
Construction/routing does not download, load, warm or evict models. An admitted
preparation path must load the workers separately; per-worker admission callbacks
remain mandatory. Remote workers still require the provisioned executor route.

The fixed registry rejects duplicate/unknown profiles and changed manifests,
and uses the controller-owned RegisteredImage reader. It does not introduce
arbitrary renderer paths, model URLs or device fallback. This wiring is not a
global resource scheduler and does not lift hardware/workload gates.

`DetectorPoseWorker` is an explicitly provisioned local composite accepted by
the same registry. It binds one detector, one RTMPose worker and a versioned crop
policy into a digest used as the composite artifact identity. Both workers must
already be loaded through admitted preparation. The composite admission callback
must reserve the whole pipeline; per-stage worker callbacks remain mandatory.

Detection runs before pose within one durable paired request. Stage receipts are
stored as `perception.stage` diagnostics, not separate independent reasoning
evidence. Selected boxes, objects, masks and skeletons retain common person IDs.
Only completed chains publish combined evidence. Expiry between stages or any
uncertain stage leaves the parent unresolved; reconnect/retry does not rerun it.
Missing person proposals never become a full-frame pose fallback. YOLOX uses an
explicit `minimum_confidence=None` policy because it does not return scores;
other detectors require a numeric threshold. Shared-crop estimator-only recipes
still use the separate registered crop resolver; this composite compares complete
detector-plus-pose pipelines and does not assert shared-arm crop equivalence.


### Registered numerical surface reader

`RegisteredDepth.surface` reads one registered `depth_u16_le` object and returns
an uncertain visible-patch estimate in rectified-left camera metres. It requires
trusted calibration matching dimensions, camera, mount revision and physical
validation receipt, plus verified observation timestamps. Its input ROI must
already belong to rectified-left coordinates; passing RGB detections directly
is rejected. Output uncertainty includes patch extent and sensor error, not a
probability or anatomical-joint accuracy claim. A trusted downstream consumer
must associate arm/track/evidence roots before calling `publish_room`.

Current sampled archive metadata remains unvalidated. This reader does not
commission it, perform RGB-left association, or automatically start spatial
inference. Workstation tests use synthetic numerical arrays only.

For a real browser decoder check without FFmpeg or household footage, run the
existing workstation `tools/serve-perception-lab-fixture.py` and open
`http://127.0.0.1:18766/codec-fixture`. Click Generate to create four seconds of
canvas-only H.264 MP4, then Play and seek in both directions. This uses Home's
actual player but an in-memory transport; it is not camera, gateway or native
acceptance. No camera/microphone permissions or media uploads are needed. Stop
the server after testing. Unsupported MediaRecorder codecs are reported honestly.

## Known limitations

- TODO (packet v6): the cloud packet still carries raw epoch times
  (`observation_cutoff`, each record's `observed_at`, `available_at`,
  `valid_until`). They reveal wall-clock time beyond the ambient buckets and
  are what JEV is weakest at. Packet v6 replaces them with code-computed age
  and validity buckets; until then they remain as sent today. v6
  (`den-facts-6`, `jev_wire.py`) is built and policy-checked in shadow only
  (`"shadow_wire": "den-facts-6"` in the JEV provisioning file records a local
  `reasoning.shadow` event per v5 request). `"wire": "den-facts-6"` in the
  same file makes v6 the live wire (it needs the household `den-facts-6`
  structured-source grant; the shadow is then off). Without it, v5 is sent
  under a pre-send budget: over about 22k estimated tokens (0.70 tokens per
  canonical byte) the oldest window records are trimmed (never the latest
  frame's; `budget_trimmed` is recorded locally), and a packet still over 26k
  is refused locally as `request_over_budget`.
- A provider HTTP 400/401/403/404/413/422 that arrives with a body is a
  refusal before execution (`provider_rejected_<status>`, `failed`). Other
  statuses, including 429 and 5xx, timeouts and lost connections stay
  `indeterminate`, and the status code is stored in the request result. An
  indeterminate request blocks every later JEV send, in every session and
  across restarts, until `LiveReasoning.reconcile` records terminal proof;
  TypeSafe documents no request lookup or idempotency key, so no lookup is
  provisioned.
  `python jev_replay.py LEDGER --audit UTC-7 SESSION` replays recorded
  sessions read-only (fidelity, v6 sizes, simulated calls, v6 policy audit).
- The Qwen scene facts are an unvalidated model interpretation of the same
  camera; `corroboration` counts consistency, not accuracy.
