# Home AI Agent — Cognitive Architecture, Intelligence Atlas & V-JEPA Initiative

## Context

`engineered-lighting/home` today is a **reactive** smart-home chat client. The Tauri/React app
(`app/`) sends a user's text to Home Assistant's `assist_pipeline/run`; HA gathers context per-turn
and runs Qwen3-VL on the AI box; the app renders the result. There is **no ambient awareness, no
memory, no Intelligence Atlas, no dataset capture, and no activity recognition** — the vision card's
`activity` labels are hardcoded `"undetected"` placeholders. "Intelligence Atlas" exists only as a
goal, not as code.

The goal is to evolve (not rebuild) this into an **ambient intelligence layer**: a continuously
running agent that observes cheap signals, maintains a structured belief state, **feeds that belief
into the chatbot so context already exists at question time**, spends expensive vision compute
selectively, learns from manual corrections, and accumulates high-quality clips/labels for future
V-JEPA training.

This plan covers the architecture review + design + a staged, read-only-action MVP. Per the user's
explicit instruction, **no autonomous device control ships** (autonomy Level 0/1 only — the agent
observes, believes, suggests, and informs chat; it never flips a switch).

### Confirmed decisions
1. **Agent = a new always-on service on the Ubuntu AI box** (`cognition-sidecar`), not in the Tauri
   app (which sleeps) and not in HA. The Windows app is a viewer/controller.
2. **Close the belief→chat loop in the MVP**: the chatbot answers from a pre-formed "current home
   context" block, not by gathering context reactively per turn. *(Adversarial fix #1.)*
3. **Lean in-memory first increment**: prove Observe→active-room→belief→app card→chat-injection with
   **no database, no VLM, no curiosity budget**. Persistence/curiosity/learning follow.
4. **Fix data quality from the first learning increment** (not retrofitted): distinguish
   human/automation/agent light changes, and capture short **clips** (not single stills).
   *(Adversarial fixes #2, #3.)*
5. **Build a replay/sim test harness** so the loop is testable in CI with no house attached; the user
   runs the live recon. *(Adversarial fixes #9, #10.)*
6. **Frigate + MQTT are live**; **datastore = SQLite + sqlite-vec** on the AI box (added in
   Increment 2), clips on the filesystem.

---

## 1. Repository architecture review (current state)

| Machine | Runs today | Role going forward |
|---|---|---|
| **Windows** | Tauri 2 + React app (`app/`), pure UI, localStorage only | Chat/Agent/Atlas UI, viewer |
| **Ubuntu AI Box** | `stack/` Docker: vLLM (Qwen3-VL-30B-A3B), metrics-sidecar (:8092), vision-sidecar (:8091), Parakeet STT, Kokoro TTS | **+ cognition-sidecar (:8093)**, Atlas DB, future V-JEPA |
| **LattePanda Sigma** | Home Assistant + **Frigate + MQTT** (live) | Source of truth: home state, detections, clips |

Facts that shape the design:
- The app already consumes a **real-time SSE bus**: `metrics-sidecar:8092/conversations/stream` (the
  "chat tee"), consumed at `app/src/home-app.jsx:1345-1451`. This is the proven extension pattern.
- `metrics-sidecar/main.py` holds reusable SSE primitives (`_subscribers`, `_broadcast_completion`,
  `/conversations/stream`, `/conversations/recent`, lines 169-188, 336-371) **and proxies vLLM's
  `/v1/chat/completions`** (the tee). That proxy is the natural **belief→chat injection point**.
- `vision-sidecar` (`stack/services/vision/app.py`) exposes `POST /describe` → one Qwen3-VL frame
  description; it grabs a **single JPEG** (`app.py:108`), camera aliases at lines 43-53. Expensive signal.
- vLLM is single-GPU, `--max-num-seqs 4`, **shared** between chat and any agent vision calls, and is
  **not** host-exposed (only reachable inside `homeai-net`).
- HA WebSocket client (`home-ha.jsx`) is browser-only → the agent needs a **server-side** Python HA
  WS client with its own reconnect/backoff.
- No DB exists anywhere; app persistence is localStorage (`home-tauri.jsx`).

---

## 2. Cognitive architecture (the agent loop)

`cognition-sidecar` runs the continuous cycle: **Observe → Interpret → Hypothesize → Investigate →
Decide → (Learn)**. Three concurrent asyncio tasks (mirroring `metrics-sidecar/main.py:135-137`):
1. `observe_mqtt()` — `aiomqtt` on `frigate/events` → cheap, primary detections.
2. `observe_ha()` — HA WS `subscribe_events("state_changed")`, filtered to presence/mmWave/motion/
   occupancy + `light.*`. **Both observers need robust reconnect/backoff** (24/7 service; the AI box↔
   LattePanda link will blip — *adversarial fix #8*). The slow tick is the floor when feeds drop.
3. `cognition_tick()` — slow ~5s loop: Interpret→Hypothesize→Decide, recompute active-room, refresh
   belief; (Increment 2+) decide whether to spend a curiosity token on an Investigate.

Reactive events update a shared in-memory `WorldState` under an `asyncio.Lock` and can request an
immediate tick for high-salience events. Per-cycle decision: `wait | learn | ignore` (and, scaffolded
but inert, `act`); each justified in the belief `explanation`.

### Active-room selection (`rooms.py`)
Score each room per tick from cheap signals; argmax = focus room:
```
score(room) = 3.0*frigate_person(30s) + 2.0*mmwave + 1.5*motion(60s)*decay
            + 1.0*occupancy + 0.5*recent_light_change(120s) - staleness_penalty
```
Near-ties broken by longest-time-since-investigated so attention rotates. **Mapping (Frigate
zone→room, HA entity names, camera aliases) is unverified and must come from user-run recon
(§7).** Weights are config constants.

### Curiosity / compute budget (`curiosity.py`, Increment 2)
Global **token bucket** gates all `/describe` calls (refill 4/min, cap 6, per-room cooldown 90s).
Before spending, check chat activity via `metrics-sidecar:8092/metrics` and **defer** if chat is live.
**Adversarial fix #5:** a 2s-polled snapshot is racy, not a guarantee — so additionally route agent
VLM calls through a **shared single-slot semaphore in the metrics-sidecar tee** that interactive chat
acquires with priority. The token bucket limits volume; the semaphore prevents an agent describe from
landing mid-chat-turn. Treat this as best-effort-plus and validate empirically.

---

## 3. Belief state (`beliefs.py`)

Structured per-room belief preserving **uncertainty** (ranked hypotheses + probabilities + entropy),
serialized to JSON for the API and (Increment 2+) snapshotted to SQLite:
```json
{ "schema_version":1, "generated_at":0, "active_room":"living_room",
  "curiosity":{"tokens":3.2,"bucket_max":6,"deferred_for_chat":false},
  "rooms":{"living_room":{
    "room":"living_room","camera_entity":"camera.living_room",
    "occupants":{"count":1,"confidence":0.72,"source":["mmwave","frigate"]},
    "activity":{"hypotheses":[
        {"label":"watching_tv","p":0.55,"evidence":["tv_on","low_motion","vlm:person_on_sofa"]},
        {"label":"reading","p":0.25,"evidence":["low_motion","lamp_on"]},
        {"label":"idle","p":0.20,"evidence":["no_recent_motion"]}],
      "top":"watching_tv","confidence":0.55,"uncertainty":0.66},
    "lighting":{"any_on":true,"entities":["light.living_room_lamp"],"brightness_pct":40},
    "last_observation_at":0,"last_investigated_at":0,"investigated_by":"cheap_signals",
    "recommended_action":{"kind":"suggest","service":"light.turn_on",
      "target":"light.living_room_lamp","params":{"brightness_pct":60},
      "confidence":0.41,"autonomy_level_required":2,"would_execute":false},
    "explanation":"1 person likely watching TV (mmWave+Frigate, low motion). Read-only — not acting.",
    "pending_question":null }}}
```

**Exposure — separate SSE bus on `:8093`** (copy the metrics-sidecar pattern verbatim):
`GET /belief`, `GET /belief/{room}`, `GET /belief/recent?since=`, `GET /belief/stream` (SSE, `: ping`
keepalive, `backfill_n`). Plus the **feedback channel** (*adversarial fix #4*): `POST /feedback`
(`{room, belief_id, verdict, note}`) and a `pending_question` field + `ask`-type belief event so the
agent can surface "Was I right that you were reading?" and receive Yes/No/Mostly back. Add permissive
CORS like metrics-sidecar.

### Belief → chat injection (*the headline; decision #2*)
The chatbot still goes HA `assist_pipeline/run` → Extended OpenAI Conversation → `/v1/chat/completions`
**through the metrics-sidecar tee**. Implement injection **in the tee**: on each incoming chat
completion, fetch `cognition:8093/belief`, render a compact, clearly-delimited, **data-only** "Current
home context" system block, and prepend it before forwarding to vLLM. This delivers "context already
exists → answer immediately" with **zero HA config change** and works with in-memory belief (so it
ships in Increment 1).
- *Security (adversarial):* the block includes VLM-derived text that ultimately originates from camera
  view → treat as untrusted: hard-delimit it, label it as observations, never as instructions, and cap
  its length. Low risk in a home, but designed defensively.

---

## 4. Memory architecture & Intelligence Atlas (`store.py`, SQLite + sqlite-vec — Increment 2)

Single DB `/data/cognition.db` (WAL, **single serialized writer task** to avoid `database is locked`),
clips on `/data/clips/`. Tables: `observations` (~14d retention), `episodes`, `belief_snapshots`,
`preferences`, `policies` (inert), `manual_corrections` (highest value), `dataset_clips` (V-JEPA-
forward: `clip_path/fps/frame_count/dims/labels(multi-label+prob)/belief_snapshot/feedback`).
- **Embeddings honesty (*adversarial fix #6*):** Qwen3-VL likely exposes no `/v1/embeddings`. **Cut
  vector recall from the MVP** — ship the `dataset_clips`/episodic tables without semantic search;
  add a real embedding model later (its own GPU-tenancy decision). Don't advertise recall we can't do.

### Learning from corrections (`corrections.py`) — built correctly the first time (*decision #4*)
Every `light.*` `state_changed` is classified **human vs automation vs agent** via HA event `context`:
agent calls carry the agent's context; **automation/script changes have null `user_id` but a
`parent_id` tracing to an automation trigger** — these must be **excluded** (the naive "every change is
human" approach poisons the table — *adversarial fix #2*). Only genuine human changes become training
rows: snapshot belief, `light_before/after`, time bucket, occupancy, inferred activity+confidence,
`agent_predicted`, and (budget permitting) attach a **clip**. Feed a weak inferred `preferences` row.

### Clips, not stills (*decision #4 / adversarial fixes #3, #7*)
Investigate captures a **short multi-frame burst/clip**, since activities are temporal (one frame can't
tell reading from idle). **Cross-machine resolution:** Frigate clips live on the LattePanda; pull the
clip over HTTP from the Frigate API into the AI box `/data/clips/` at capture time (don't store a dangling
remote path that Frigate's retention later deletes). Store the local copy + time range + multi-label.

---

## 5. Autonomy framework (`policy.py`, scaffolding only — inert in MVP)
Levels 0 observe · 1 suggest · 2 act-with-confirm · 3 autonomous. Per-room/activity/device rows in
`policies`. Future gate: `enabled && level>=required && confidence>=threshold && !cooldown &&
!manual_override_cooldown` → `ha_ws.call_service()`. `manual_override_cooldown_s` (≥1800) enforces
"don't undo my choice." Entire action path behind a single `AUTONOMY_ENABLED=false` env gate (dead code).

---

## 6. Event/storage flow
```
Frigate ─MQTT frigate/events─┐
HA presence/mmWave/motion ─WS┤→ cognition-sidecar :8093 (Observe→WorldState→tick→belief)
HA light.* state_changed ──WS┘    ├ active-room + belief → (Inc.2) SQLite Atlas + clips/
                                  ├ (Inc.2) curiosity ─(gated/semaphore)→ vision /describe → Qwen3-VL
                                  ├ /belief, /belief/stream (SSE)   POST /feedback ← app
                                  └ /belief (GET) ← metrics-sidecar tee → inject into chat system prompt
Windows app ─EventSource :8093/belief/stream→ awareness card + vision labels (read-only)
            ─existing :8092/conversations/stream (unchanged)
```
Volume `cognition_data:/data` (DB+clips) survives recreation. Retention/LRU prune: `observations` ~14d;
keep `manual_corrections`/`dataset_clips` long-term; cap clip bytes. **Privacy: local-first — clips
never leave the LAN box; documented in README/ADR.**

---

## 7. V-JEPA research & compatibility (verification pending; affects only the late exporter)

> A research sub-agent is verifying the exact spec; this finalizes before the Increment-3 exporter.
> **Working assumptions to confirm:** "V-JEPA **2.1**" is likely **not an official release** — the
> shipped artifact is **V-JEPA 2** (Meta/FAIR 2025; self-supervised joint-embedding predictive video
> model; ViT encoder+predictor; a **V-JEPA 2-AC** action-conditioned variant). Downstream activity
> recognition uses a **frozen encoder + attentive probe** (not full fine-tune) → feasible on the
> reserved ~24GB. Inputs are short clips (~16-64 frames), fixed resolution, fps-downsampled;
> Kinetics/SSv2-style label manifests.

Compatibility audit: capturing **clips** (decision #4) + **multi-label-with-probability** (preserves
uncertainty) + room/time/occupancy/belief metadata satisfies V-JEPA's needs. Missing pieces, all
**additive** (no core-loop migration): clip length/fps normalization, a frame-sampling step matching
the encoder's tubelet/frame count, and a DB→manifest exporter.

**Recon must be user-run** (*adversarial fix #9*): this remote/CI environment has **no LAN access** to
HA/Frigate/AI box. Deliverable includes a `recon.py` script the user runs on their network to dump
`GET /api/states`, capture real `frigate/events` payloads + zone names, and inspect a manual-vs-
automation light change `context`. Its output configures `rooms.py`. I cannot validate mappings myself.

---

## 8. Implementation plan (staged)

New service mirrors the existing single-module FastAPI sidecars.
```
stack/services/cognition/
  Dockerfile  requirements.txt  config.py  main.py        # FastAPI + SSE bus + lifespan tasks
  loop.py  observe.py  rooms.py  beliefs.py  ha_ws.py
  curiosity.py  investigate.py  store.py  corrections.py  policy.py   # Increment 2+
  sim.py  recon.py                                          # test harness + user recon script
```
Deps: `fastapi, uvicorn, httpx, pydantic, aiomqtt, websockets` (Inc.1); `+ sqlite-vec` (Inc.2).
Compose: add `cognition-sidecar` on `homeai-net`, port 8093, `restart: unless-stopped`, env
(`HA_URL/HA_TOKEN`, `MQTT_HOST/PORT/USER/PASS`, `VISION_URL=http://vision-sidecar:8091`,
`VLLM_URL=http://metrics-sidecar:8092`), volume `cognition_data:/data`, `depends_on` vision+metrics
healthy, `/healthz`. **No GPU reservation** (it consumes GPU only indirectly via vision). Add
`cognition_data` volume + `.env.example` keys.

### Increment 1 — Lean MVP (in-memory, closes the chat loop) — *decisions #2, #3*
- Service skeleton + `/healthz` + SSE bus; `config.py`, `main.py`.
- `ha_ws.py` (auth + subscribe_events + REST snapshot + reconnect), `observe.py`, `aiomqtt` consumer
  (reconnect), `rooms.py`, `beliefs.py` — **cheap signals only, no VLM, no DB, no curiosity**.
- `GET /belief` + `/belief/stream`; **belief→chat injection in the metrics-sidecar tee**.
- `sim.py` **replay harness** (feed recorded MQTT/HA events) → loop testable with no house (*#5/#10*).
- `recon.py` for the user to run against their live network (*#9*).
- App: register `home-belief.jsx` in `index.html`; new `HomeBeliefCard` (window-export like
  `home-vision.jsx:289`); second SSE consumer in `home-app.jsx` (clone `:1345-1451`, point at
  `:8093/belief/stream`, derive `cognitionBase` like `metricsBase`); mount under `<HomeVisionCard/>`
  at `:1488`; thread belief into `home-vision.jsx` to fill `activity`/`activityConfidence` (render at
  `:272-281` unchanged — only the data source). **Read-only.**
- **Exit criteria:** walking between rooms changes `active_room` live in the card; the chatbot answers
  "what am I doing?" from injected belief without a reactive context fetch.

### Increment 2 — Persistence, curiosity, learning (data-quality-correct) — *decisions #4, #6*
- `store.py` SQLite (WAL, single writer) + tables (no vector search yet).
- `curiosity.py` token bucket + chat-aware deferral **+ tee semaphore** (*#5*).
- `investigate.py`: **multi-frame clip** capture (not stills), pulled from Frigate to `/data/clips`
  (*#3/#7*); VLM/LLM interpretation → hypotheses.
- `corrections.py`: human/automation/agent classification via `context` (*#2*) → `manual_corrections`
  + `preferences` + clip attach. Feedback channel (`POST /feedback`, `pending_question`, `ask` events).
- Retention/LRU prune job.

### Increment 3 — Policy scaffolding + V-JEPA dataset
- `policy.py` + `policies` (inert behind `AUTONOMY_ENABLED=false`); episodic memory; DB→V-JEPA
  manifest exporter + frame-sampling normalization (finalized against §7 research).

---

## 9. Risks & assumptions (post-review, highest first)
1. **GPU starvation of chat.** Mitigated by token bucket **+ priority semaphore in the tee**; still
   validate empirically that an agent describe never degrades an in-flight voice turn. The deferral
   alone is racy (*#5*).
2. **Frigate zone→room / HA entity naming unverified.** Whole loop quality rests on it; gated behind
   user-run `recon.py` (*#9*) — I can't check it from CI.
3. **Correction data quality.** Excluding HA-automation-driven changes via `context.parent_id` is
   essential and unproven against the live config (*#2*); validate in recon before trusting the table.
4. **Belief→chat injection = prompt surface + coupling.** Tee now depends on cognition being up
   (degrade gracefully: skip injection if `/belief` errors) and injects camera-derived text (delimit,
   data-only). 
5. **SQLite concurrency / clip growth / privacy.** Single writer + WAL; LRU prune; clips stay on-box.
6. **Embeddings cut from MVP** to avoid shipping non-functional recall (*#6*).

## 10. Success metrics
Chatbot answers contextual questions with **no reactive context fetch** (loop closed); `active_room`/
belief track reality as you move; fewer manual corrections over time; growing count of clean
(automation-filtered) `manual_corrections` + labeled `dataset_clips`; curiosity hit-rate; **zero
unwanted actions** (guaranteed by read-only). *(Note: metrics tied to autonomy are out of scope.)*

## 11. Verification
- `docker compose up`; `curl :8093/healthz` green; logs clean.
- **Sim harness:** replay recorded events → assert `active_room` transitions and belief hypotheses
  without live infra (CI-runnable).
- `curl :8093/belief` populated; `curl -N :8093/belief/stream` shows live deltas as you move.
- App: awareness card shows focus room + hypotheses; vision labels change from `undetected` to real
  activities; ask the chatbot "what am I doing?" → answered from injected belief.
- Increment 2: change a light manually → a `manual_corrections` row appears; trigger an HA automation
  → **no** row (classifier works); `POST /feedback` records a verdict. Start a chat turn while the
  agent is investigating → confirm no added chat latency (semaphore).
