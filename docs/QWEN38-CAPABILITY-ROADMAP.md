# Qwen3.8 Capability Roadmap — Story Groups A–F

> Companion to `docs/QWEN38-MIGRATION.md`. Sequenced crawl → walk → run by
> value ÷ effort, with the flywheel first because it compounds every later
> evaluation. Every slice reuses an existing service; new surface only where
> unavoidable and named. Each slice ships behind a default-OFF toggle with
> its own eval note. Ordering principle: offline/reversible first;
> containment-flag changes, ACTIVATION_PATHS, and actuation last.

**Hard gates before anything here ships:**
- The migration soak has exited clean (see migration doc).
- **V1 — ANSWERED 2026-08-16 (Phase 0). Recording is OFF, and it blocks A1
  as designed.** All five cameras (`living_room`, `dining_room`, `kitchen`,
  `back_door`, `driveway`) carry `record: enabled: false`; there is no
  global `record:` block. `/media/frigate/recordings` is **empty (4 KB)** —
  the 34.4 GB is `clips/`, and it holds **0 `.mp4` files against 202,373
  `.jpg`** plus `.webp`. Those are event *snapshots* (`snapshots.enabled:
  true`, `bounding_box: true`), not clips. The "~36 GB suggests recording is
  on" hypothesis is refuted: disk usage never distinguished the two.
  **Consequence:** Frigate builds event clips from recording segments, so
  `has_clip` is false for every event and there is no `clip.mp4` to fetch.
  A1's first validation step can never pass today, and A1 + the package
  query + the evening digest + A3 + E1's narrative all inherit the block.
  Enabling recording remains a standalone privacy + disk decision (**D8**;
  the HA media disk has 802 GB free), never a roadmap side effect. See
  "A1 — snapshot-first correction" below for the unblocked slice.
- **Face-recognition overrides — CORRECTED 2026-08-16.** An earlier
  same-day entry here claimed face recognition was "not configured at all".
  **That was wrong**: it came from `/addon_configs/ccab4aaf_frigate/`, which
  is a **stale, inactive** addon directory (Apr 28, 3 KB, cameras on a
  192.168.251.x subnet that no longer exists). The config in force is
  **`/addon_configs/ccab4aaf_frigate-fa/`** (Jul 10, 8.8 KB), and the
  authority is Frigate's own `GET /api/config`.
  Running truth: **face recognition is ENABLED** (`model_size: small`,
  `recognition_threshold: 0.85`, `unknown_score: 0.8`, `min_faces: 2`) on
  `living_room`, `dining_room`, `kitchen`, `workshop` — and **explicitly
  disabled on `driveway`**. The documented policy is therefore correctly
  implemented live; the inversion is a repo-example-only defect. Also
  running: `semantic_search: enabled` (the CLIP index A3 assumes), `lpr`
  and `genai` off.
  Names still reach model context by a second route — this stack writes
  `sub_label`s back to Frigate itself (`frigate_sync.py`,
  `identity_store.py`) — so D3 enforcement at the tool layer is still
  required and `find_clips` still carries `sub_label` unconditionally
  (confirmed live in `functions/frigate.py`).
- **Camera roster — CORRECTED.** The live cameras are `living_room`,
  `dining_room`, `kitchen`, `workshop`, `driveway`. There is **no
  `back_door`** camera; that name came from the stale file.
- **Camera uptime — RESOLVED 2026-08-16.** `living_room` and `dining_room`
  were found offline (`camera_fps: 0.0`, TCP refused on :554); the owner
  reconnected them and all five now stream at ~10 fps. Keep the lesson:
  `enabled: true` in the config means CONFIGURED, not STREAMING, and a
  capacity estimate taken off the config list alone was wrong by 40% here.
  Cross-check `GET /api/stats` for `camera_fps` before sizing anything.
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
labeler rows + the read-only `soak-review` endpoint, delivered via ntfy.
**Phase 0 answered the precondition: ntfy is the PUBLIC `https://ntfy.sh/`**
— no self-hosted instance exists, and an unlisted topic is the only access
control. The payload rule therefore binds at its strict setting: **no names,
no images, ever**, for E0, for the soak alarms, and for E1. Current senders
are already text-only. Self-hosting ntfy is the prerequisite for relaxing
this, and it is not on this roadmap. No intelligence containment flags are touched
(they are contract-test-pinned literals; changing them is a reviewed
policy + test change, deferred to Run).

## Walk (months 1–2)

**A1 — past-event describe ("did the package come?").**

> **UNBLOCKED by D8 (owner decision, 2026-08-16): recording is being
> enabled with a 48 h rolling window — see group G.** Once G1 lands,
> `has_clip` becomes true and the clip design below works as written.
> Ship order is unchanged and still starts with the snapshot slice:
> **A1a snapshot-first** needs no video, can be built while recording
> accumulates, and shares every app/web touchpoint with the clip path —
> so A1b becomes a backend swap rather than a new story.
>
> **Snapshot-first rationale (Phase 0, 2026-08-16).** The clip-based design
> below *was* blocked — with recording off there is no `clip.mp4` and
> `has_clip` is false for every event (see V1). Do **not** wait on D8 to
> ship the story. Frigate holds **202k event snapshots** with boxes already
> drawn, retained for months, on a disk with 802 GB free. A snapshot-first
> `describe_event` — single frame, `GET /api/events/<id>/snapshot.jpg`, no
> ffmpeg, no RTSP, no 50 MB fetch, no exposure to the >2 GB-video segfault
> (#46589) — answers "did the package come?" today and is strictly smaller,
> cheaper, and safer. It also needs no new token math: one 768² frame ≈ 576
> tokens against the multi-thousand-token clip path.
> Ship order: **A1a snapshot-first (unblocked now)** → D8 decision → **A1b
> clip path (the design below) only if D8 enables recording**. The seven
> app/web touchpoints, the D3 `sub_label` stripping, and the EOC subentry
> tool addition are identical for both and are done once in A1a.

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
⚠ **Phase 0: the live subentry caps `max_function_calls_per_conversation`
at 3.** Raising only the harness number tests a chain production cannot
execute. The live option must be raised in the same slice (a subentry edit,
so `tools/patch-subentry-function-tools.py` territory — dry-run diff first),
and the raise interacts with every timeout in the retune row below: three
extra tool round-trips is exactly what pushes a turn past
`processingGuardMs` (30 s) and the 90 s room-binding TTL.
**Gated on the consumer-timeout retune row in the migration doc** (the 30 s
processing guard, 90 s room-binding TTL, and 6/8 s perception timeouts all
mis-fire on longer chains — the room-binding expiry is a wrong-room
actuation path). This is where the dense 27B must beat the 4B-revert
failure mode ("turn off my lights everywhere" → chitchat).
Cross-modal investigation: a golden scenario over existing tools
(world_state + grounded_look + get_history in one pass); Run extends it
with Frigate audio events (bark/glass/doorbell classifiers already
configured) → find_clips → describe_event correlation.

**A2 — native-video trial.** Unblocked once group G1 lands (D8 enables
recording); until then there is no video on disk to trial against.
`video: 0 → 1` behind `VIDEO_INPUT_MODE=native`. NOT an offline experiment: changing the mm limit
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
loitering via Frigate events → describe_event narrative → ntfy, **text
only**; no outdoor naming — enforced at the tool layer per D3). ⚠ The
original "ntfy + snapshot" would push camera imagery to public ntfy.sh —
cloud egress of camera imagery, which the not-doing list bars. Ship the
narration without the image, or self-host ntfy first. Left-running
detection (stove on + kitchen empty 40 min → optional confirm frame →
ntfy). Physical response stays behind the future Safety Kernel.

**Wild cards (unscheduled).** Monthly house documentary (batch job over
labeled clips — zero risk, a good soak exercise). Workshop copilot
(hour-scale video = the Run stage of A's hierarchical summarization —
last). Gesture vocabulary revival (its capture tool is deny-listed —
needs a propose-only design first).

## Group G — rolling video buffer + nightly distillation (owner-directed)

**The idea.** Record continuously with a 48-hour rolling window. Every
night between 03:00 and 08:00, the VLM reads what accumulated and writes
durable *observations*; the video then ages out at 48 h. Memory survives,
footage does not. This is the roadmap's hierarchical-summarisation story
(A1 → digest → A3) with a real substrate under it.

**G1 — enable recording (owner-run, reversible).** `record.enabled: true`
plus a 48 h retention policy. The plumbing already exists: every camera's
`record` role is already bound to its main `stream1`, so this is a config
flip, not a re-architecture.

⚠ **Edit the RIGHT file: `/addon_configs/ccab4aaf_frigate-fa/config.yaml`.**
The sibling `/addon_configs/ccab4aaf_frigate/` is stale and inactive;
editing it changes nothing and looks like it worked. Verify every change
against `GET /api/config` on the running service, which is what
`tools/qwen38-frigate-summary.py` reads.

Current global record block already carries `alerts`/`detections` with
`pre_capture: 5, post_capture: 5, retain: {days: 10, mode: motion}`. A 48 h
continuous buffer means `record.enabled: true` with
`retain: {days: 2, mode: all}`; leaving the per-event 10-day retention as-is
would keep motion segments far longer than the buffer, which is a different
policy than the one being chosen — decide both numbers together.

✅ **MEASURED 2026-08-16 — no recording needed to get the number.** Rather
than enable recording and wait an hour, each main stream was pulled through
go2rtc for 30 s with `ffmpeg -c copy`, which is byte-for-byte what Frigate
writes (Frigate stream-copies; it does not re-encode).

All five cameras, measured after the owner brought `living_room` and
`dining_room` back online:

| camera | resolution | bitrate | per hour |
|---|---|---|---|
| living_room | 1920×1080 h264 | 1.26 Mbps | 0.53 GB |
| dining_room | 1920×1080 h264 | 1.21 Mbps | 0.51 GB |
| workshop | 1920×1080 h264 | 1.21 Mbps | 0.51 GB |
| driveway | 1920×1080 h264 | 1.19 Mbps | 0.50 GB |
| kitchen | 2304×1296 h264 | 1.15 Mbps | 0.48 GB |

- **Combined: 2.53 GB/hour = 61 GB/day.**
- **48 h buffer = 121 GB.** With 2× headroom for daylight and motion on
  variable-bitrate H.264, **~243 GB worst case = 30% of the 802 GB free.**

The earlier 215–430 GB planning estimate was **~3× too high**: these
cameras encode at ~1.2 Mbps, not the 2–4 Mbps assumed. Disk is not a
constraint — **7 days of all five is ≈ 420 GB** and still fits. Retention
is now a privacy question, not a storage question.

⚠ Caveat: 20–30 s samples taken overnight. Re-measure across a full day
before choosing any window longer than a week.

⚠ **The storage goes on the HA box, and the new SSD is not needed for it.**
Frigate writes to `/media/frigate` on the HA machine, which has **802 GB
free** — comfortably more than 48 h needs. The 2 TB NVMe on the AI box is
already mounted at **`/srv/data` with 1.6 TB free** (it holds 217 GB of
ollama models). Pointing Frigate at it would mean NFS-mounting AI-box
storage into HA and putting continuous recording on the network path — a
well-known source of Frigate segment corruption. **Record locally on HA;
give the AI-box SSD the job it is actually good for: the durable
observation store and any clips pulled for parsing.** Those are small and
permanent, which is the opposite of video.
- Cameras sit on an isolated subnet (192.168.251.x) reachable only from the
  HA box via go2rtc — the AI box cannot pull RTSP directly and must go
  through Frigate's API. Verified 2026-08-16.

**G2 — nightly distillation lane (03:00–08:00).**

⚠ **Parsing "all the stored video" is not feasible, and is not what you
want.** 48 h across 5 cameras is 240 camera-hours ≈ 13 M frames. Measured
on the live incumbent, one 1280×720 frame costs **0.32 s** end-to-end
(913 prompt tokens, production path); the candidate is 2.4–2.6× slower on
decode, so budget ~0.8 s/frame. The 5-hour window, at 50% duty to leave
production responsive, buys roughly **11,000 frames a night**. Brute force
is off by three orders of magnitude.

⚠ **"Parse every Frigate event" also does not fit — and measuring showed
why.** Live event rates (6-hour sample, all five cameras streaming):

- **6,620 events in 6 hours** (>10,000/day, the API cap). But merged into
  distinct moments they collapse to **12 scenes**. Frigate emits one event
  per tracked object and re-detects stationary ones continuously.
- **49% of all events are furniture**: `cup` 2,319, `bottle` 274, `sink`
  230, `suitcase` 139, `chair` 49 — in 6 hours. The kitchen camera is
  detecting a cup on the counter over and over. (This is the same
  "cup-misfire" the migration doc's G4 negatives already name, now
  quantified: it is not an edge case, it is half the event stream.)
- Actual activity: `car` 3,234, `person` 155 in 6 hours.

Costed against the measured 0.32 s/frame incumbent and ~0.8 s/frame
candidate, at one frame per 10 s of scene:

| lane | scenes/day | footage/day | frames/day | candidate cost |
|---|---|---|---|---|
| every event merged | 24 | 2,908 min | 17,449 | **233 min** ✗ |
| activity classes only | 108 | 2,385 min | 14,308 | **191 min** ~ |
| **person-anchored** | 212 | 1,004 min | 6,025 | **80 min** ✓ |

**The lane is person-anchored, not event-anchored.** Static-object labels
are excluded outright; vehicles enter only as arrival/departure
transitions, never as parked-car persistence, which is what inflates the
"activity" row into near-continuous footage.

1. **Person-anchored pass (primary).** ~212 scenes/day, ~1,000 min of
   footage, one frame per 10 s ⇒ **≈ 6,000 frames ⇒ ~80 min on the
   candidate.**
2. **Ambient sweep (gap coverage).** One frame per camera per 5 min:
   1,440 frames ⇒ **~19 min.** Catches what the detector missed.
3. **Hierarchical roll-up.** Per-scene captions → per-camera hourly
   summaries → one nightly narrative. A handful of text-only calls.

**Total ≈ 100 min inside a 5-hour window** — roughly a third of the budget,
which leaves room for a busy day, for the candidate being slower than
projected, and for production staying responsive. **Each segment is parsed
once, ~24 h after recording, leaving a second 24 h of margin before it ages
out**, so a failed night is recoverable rather than lost.

⚠ Re-derive these counts after a week of five-camera uptime. `living_room`
and `dining_room` were offline for most of the sampled window (10 and 13
events against driveway's 3,495), so indoor activity is under-represented
here and the person-anchored lane will grow.

**G3 — the observation store.** Writes go to a new, **non-authoritative**
observations store on `/srv/data`, NOT to Home Agent memory.

⚠ **This is the one place the request collides with a standing guardrail.**
Group D's rule is absolute: *the model never writes memory.* Nightly
distillation is the model writing down what it saw, so it must land
somewhere that is explicitly not memory: append-only, timestamped,
queryable, and clearly marked as model-generated observation. Promotion
from observation to durable memory stays human- or ceremony-governed,
exactly as group D specifies. That keeps the capability and the guardrail
both intact; collapsing the two would quietly hand the model the write path
the kill-switch exists to deny.

**G4 — privacy consequences, stated plainly.** This is a real posture
change and should be adopted with eyes open, not as a side effect:
- Continuous recording now covers **indoor** rooms (living room, kitchen,
  dining room), not just approaches.
- **"Wiped after 48 h" no longer means "forgotten."** The observations are
  the point, and they outlive the footage indefinitely. The retention
  decision is therefore about the observation store, not the video.
- **D3 identity policy binds the observations**, not just the tool layer:
  no naming on outdoor cameras, do-not-track flags honoured, and a
  regression fixture per surface. Frigate's own face recognition is off
  (verified), but this stack writes `sub_label`s back itself, so names can
  still reach a caption.
- Nothing here is exported: the observation store is local, and the ntfy
  payload rule (no names, no images — ntfy is public) is unchanged.

**G5 — sequencing.** Recording (G1) is independent of the model migration
and can be enabled as soon as the one-camera measurement lands; doing it
early means a real corpus exists by the time the candidate does. The
**nightly lane (G2) must not start before soak exit** — it is a large new
GPU consumer sharing `--max-num-seqs 4` with production, and it joins the
migration rollback quiesce list alongside F0 and the ambient loop.

## Not-doing list

No `add_automation` / free-text standing intents (the model-action
kill-switch is intentional and untouchable) · **no model writes into Home
Agent memory — group G's nightly distillation writes observations to a
separate non-authoritative store, and promotion to memory stays governed**
· no recording to network-mounted storage (Frigate segment corruption; the
48 h buffer stays on the HA box's local disk) · no conversation-tee
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
  the example-config inversion (repo-only; live has no face-recognition
  block at all) and per-surface regression fixtures. The live `sub_label`
  path through `find_clips` is confirmed and still needs tool-layer
  enforcement.
- **D8** Frigate recording/retention — **DECIDED 2026-08-16 by the owner:
  ENABLE, 48-hour rolling retention, with nightly distillation into
  durable observations before footage is wiped.** This supersedes the
  earlier same-day recommendation to leave recording off. The design is
  **group G** below; it unblocks A1b, A2, A3 and E1's clip narrative.
  Earlier options are kept only as a record of what was weighed:
  **(a)** leave recording off and ship **A1a snapshot-first** only — zero
  privacy delta, zero disk delta, delivers the package query today;
  **(b)** enable recording on the two outdoor cameras (`driveway`,
  `back_door`) with a short retention (e.g. 7 days, events-only) — unblocks
  A1b/E1 for the cameras that motivate them, at a bounded disk cost against
  802 GB free; **(c)** enable everywhere — largest capability gain, largest
  privacy and disk change, and it puts continuous indoor video on disk.
  The owner chose a fourth option not on this list: **enable everywhere
  with a 48 h rolling window and distil to durable observations nightly**,
  which trades the disk cost for a capability none of (a)–(c) offered —
  the house remembers what it saw without keeping the video. See group G.
- **D11** `/trace` retention acknowledgment covering F0 prelabel payloads,
  G2 replay, and matrix image traffic.
- **D12** browser/mobile exposure of `describe_event` (the vision POST
  route is env-disabled by default; widening it is a deliberate decision).
