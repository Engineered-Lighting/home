# Qwen3.8 Capability Roadmap — Story Groups A–F

> Companion to `docs/QWEN38-MIGRATION.md`. Sequenced crawl → walk → run by
> value ÷ effort, with the flywheel first because it compounds every later
> evaluation. Every slice reuses an existing service; new surface only where
> unavoidable and named. Each slice ships behind a default-OFF toggle with
> its own eval note. Ordering principle: offline/reversible first;
> containment-flag changes, ACTIVATION_PATHS, and actuation last.

**Hard gates before anything here ships:**
- The migration soak has exited clean (see migration doc).
- **V1**: live Frigate recording state verified (repo example says
  `record.enabled: false`; ~36 GB in `/media/frigate` suggests otherwise —
  everything in group A hinges on the answer). If recording is off, enabling
  it is a standalone privacy + disk decision (D8), never a roadmap side
  effect. Also verify the face-recognition per-camera overrides: the repo
  example nests the driveway exclusion under `dining_room` (inverted vs the
  documented policy).
- **D3 (identity policy)** hard-gates A1, E1, and the evening digest: one
  written policy over the four current stances (labeler prompt forbids
  identity; ambient/world-state names people ≥0.70 confidence indoors by
  design; perception bridge forbids driveway identity; user do-not-track
  flags). The tool path has a real hole today — `find_clips` carries
  `sub_label` (names) into model context unconditionally regardless of
  camera — so enforcement lands at the tool layer with a regression fixture
  per surface, not only in the bridge.

---

## Crawl (weeks 1–3 post-soak)

**F0 — self-labeling flywheel (FIRST).** Point the video-labeler at prod
vLLM. NOT config-only, despite the README: `MIN_VLM_FREE_VRAM_GB = 22.0`
(`videolabeler/jobs/prelabel.py:41`) hard-fails against a remote backend at
util 0.80 (~10 GB free) — a small reviewed change parameterizes/skips the
guard when `VLM_BASE_URL` is remote. Route via the metrics-sidecar
`127.0.0.1:8000` (vLLM is not host-published); person-crop payloads then
traverse the live `/trace` surface — covered by the D11 acknowledgment.
Nights-only lane (shares `--max-num-seqs 4` with prod). Prelabels flow into
the existing timeline editor accept/reject + Wilson-bound calibration
(`labels.py` bulk gate: lower bound ≥ 0.95 on deciles 8–9). **Rule: every
engine-model change under the frozen served name mandates a
`PROMPT_VERSION` bump before the next prelabel lane** (the `done_key`
cannot distinguish models behind `qwen3-vl-30b`), and the F0 lane joins the
migration rollback quiesce list. Walk-stage payoff: labeler suggestions
grow the intelligence held-out set toward the 50-audited-item prompt gate.

**A0 — repo compose sync.** `image: 4 → 8` closing the
`DESCRIBE_CLIP_MAX_FRAMES=8` contradiction (live already runs 8).

**E0 — guardianship recap (read-only).** Daily recap over existing live
labeler rows + the read-only `soak-review` endpoint, delivered via ntfy —
only after Phase-0 verifies ntfy is self-hosted/LAN-only; payload rule: no
names, no images otherwise. No intelligence containment flags are touched
(they are contract-test-pinned literals; changing them is a reviewed
policy + test change, deferred to Run).

## Walk (months 1–2)

**A1 — past-event describe ("did the package come?").** New
`POST /describe_event {event_id, question?}` in the **vision-sidecar** (it
already has ffmpeg, RTSP creds, the vLLM client): validate `has_clip` +
duration ≤ 60 s → fetch Frigate `clip.mp4` (≤ 50 MB — vLLM 0.20.2 has an
open >2 GB-video segfault, #46589) → `ffmpeg fps=2, scale=768` → ≤ 8 frames
evenly resampled → multi-image call with real timestamps. Token math
(research-derived, verify from `usage.prompt_tokens`): ~1 token per
32×32 px ⇒ ~576/768² frame, ~900/1280×720; video parts are consumed in
frame PAIRS (`temporal_patch_size 2`), a 20 s clip @2 fps ≈ 6–18k tokens
depending on scale. `sub_label` stripped / naming suppressed for outdoor
cameras at the tool layer (D3), fixture-pinned.
EOC tool added to the **live subentry** via
`tools/patch-subentry-function-tools.py` — mechanism: it edits the
archaeology literal at `const.py:645` (not the containment export); the
dry-run diff must show only `describe_event`; never flip
`MODEL_TOOL_CATALOG_ENABLED`.
**App/web landing (seven touchpoints, not "an allowlist entry"):** gateway
POST regex + metadata content-type + the negative-assert test tables +
base-path split; the vision POST compatibility route is env-gated OFF by
default → browser/mobile exposure is owner decision **D12**; app-side the
perception-card tool filter is a closed literal list (needs the new name +
`resultFamily` entry + a `/latest` ring or inline payload — `describe_clip`
writes no ring, so there is no precedent to inherit) + a sim-mode
short-circuit.
Unlocks: package query (find_clips → describe_event) and the evening-digest
crawl (hierarchical: per-event captions → one digest call, a scheduled
automation — no new service).

**Why-did-lights (story A).** Join live `/trace` + the Living-Lights shadow
JSONL + the anticipator debug topic (all existing logs) → one narrate call.
No new services.

**B1 — activity-shaped rooms (report-only).** Labeler activity labels →
the belief-engine socket (`living_lights_belief_engine.yaml`, default-OFF,
single-writer append contract) → the `ACTIVITY_PROFILES` seam in the
lighting generator (targets already defined: cooking 100 … napping 3), in
shadow. Blocked on F0 label quality AND authoring the three-ontology
mapping table (video-labeler 23-class / intelligence 13-value / lighting
7-value — no mapping exists today).

**C1 — multi-step voice.** Run the DOC-S58..S68 planning contracts live;
raise scenario `max_tool_calls` 6→10; add 2–3 multi-turn golden scenarios.
**Gated on the consumer-timeout retune row in the migration doc** (the 30 s
processing guard, 90 s room-binding TTL, and 6/8 s perception timeouts all
mis-fire on longer chains — the room-binding expiry is a wrong-room
actuation path). This is where the dense 27B must beat the 4B-revert
failure mode ("turn off my lights everywhere" → chitchat).
Cross-modal investigation: a golden scenario over existing tools
(world_state + grounded_look + get_history in one pass); Run extends it
with Frigate audio events (bark/glass/doorbell classifiers already
configured) → find_clips → describe_event correlation.

**A2 — native-video trial.** `video: 0 → 1` behind
`VIDEO_INPUT_MODE=native`. NOT an offline experiment: changing the mm limit
shrinks the KV pool via the startup profiling reservation and needs an
engine restart → a maintenance-window change with the KV floor re-assert
(or a temporary reduced-util engine). Base64 `video_url` part first (URL
refs were rejected by this build for images — P9 precedent). Ships only if
it beats frame-extraction on a 10-event judged A/B (temporal-order
accuracy) or on measured token/latency cost; the frame path is the
permanent fallback.

## Run (month 3+, each gated)

**A3 — retroactive visual search** ("find when I left with the blue
duffel"): Frigate CLIP semantic search (exists) for recall + describe_event
re-rank for precision; new embedding infrastructure only if this measurably
underperforms.

**B2 — anticipation actuation.** Shadow → a single pilot zone behind the
existing default-OFF boolean, Travel Mode remaining the final blocker.
Trajectory-with-intent: the spatial-tracker's metric tracks (an existing,
richer motion signal) or low-rate VLM heading labels on active tracks via a
new MQTT topic consumed alongside `camera_edges` (the addon's hot-reload
seam). Departure choreography: away-sweep + door-close + carrying-detection
signal, propose-only first. The kinematic anticipator is not rewritten; the
VLM never actuates lights directly — signals flow through the
classifier/pilot machinery only.

**C2 — standing conditional intents (propose-only).** The model drafts
automation YAML as a preview artifact; the owner confirms and applies via
existing human flows. `add_automation` stays triple-disabled (catalog off +
deny-lists + fail-closed runtime handler — verified untouched by this
design). Post-ceremony: migrate to the template-keyed
`engagement.initiatives` pattern (`allowed_surfaces`, `template_key` — the
governed slot standing intents naturally occupy).

**D — Home Agent synergy (HARD-GATED on activation-ceremony completion).**
No model-worker slot exists in the repo (grep-verified); a reviewed design
comes first (shape precedents: the `worker_maintenance_state` singleton +
the `memory_transactions` governance envelope). Then: visit understanding
(arrival-with-bags vs drop-off → typed visit candidates through
preview/confirm) and preference archaeology (recurring manual corrections →
typed preference candidates). The model NEVER writes memory; every
people-data slice names its consent gate. Zero edits to
`stack/services/home-agent-*`, `stack/home-agent-deploy/**`, or the other
ACTIVATION_PATHS from this workstream, ever.

**E1 — guardianship v2 (report-only).** Anomaly narration (driveway
loitering via Frigate events → describe_event narrative → ntfy + snapshot;
no outdoor naming — enforced at the tool layer per D3). Left-running
detection (stove on + kitchen empty 40 min → optional confirm frame →
ntfy). Physical response stays behind the future Safety Kernel.

**Wild cards (unscheduled).** Monthly house documentary (batch job over
labeled clips — zero risk, a good soak exercise). Workshop copilot
(hour-scale video = the Run stage of A's hierarchical summarization —
last). Gesture vocabulary revival (its capture tool is deny-listed —
needs a propose-only design first).

## Not-doing list

No `add_automation` / free-text standing intents (the model-action
kill-switch is intentional and untouchable) · no conversation-tee
re-enable (requires the authenticated redesign its lock comment demands) ·
no vLLM version bump bundled with anything · no fp8 KV outside the
garble-gated cell · no cloud video egress (ntfy payload rule included) ·
no ACTIVATION_PATHS edits · no `qwen_*` schema-namespace rename (stable
API contract, not a model name) · no Frigate-recording decision as a side
effect · no new embedding microservice in v1 · no physical guardianship
response · no host image builds · no lighting actuation from raw VLM
output (classifier/pilot machinery only).

## Open owner decisions referenced here

- **D3** identity policy (precondition for A1/E1/digest) — includes fixing
  the example-config inversion and per-surface regression fixtures.
- **D8** Frigate recording/retention (if V1 finds recording off).
- **D11** `/trace` retention acknowledgment covering F0 prelabel payloads,
  G2 replay, and matrix image traffic.
- **D12** browser/mobile exposure of `describe_event` (the vision POST
  route is env-disabled by default; widening it is a deliberate decision).
