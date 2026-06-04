# HANDOFF — Home AI Agent initiative

This file hands off the in-progress design work to a future Claude Code session
(ideally a **local** session on the Windows machine that is on the home LAN). It
captures what was decided, why, and where to pick up. The full plan is in
[`PLAN.md`](./PLAN.md).

## Start here (for the next session)

1. Read [`docs/agent/PLAN.md`](./PLAN.md) end-to-end — it is the authoritative plan.
2. Read this file for the decision rationale and current status.
3. You are continuing the work on branch `claude/home-agent-architecture-PI5Gy`.
4. **If you are a LOCAL session on the home LAN:** you can do the live recon and
   deployment the dispatched session could not (see "Environment constraints").
   Start with the recon step (PLAN §7 / Increment 1 `recon.py`) before writing
   `rooms.py`, because the room/sensor/camera mapping is unverified.
5. **If you are still dispatched (cloud):** you can write all code + the recon
   helper + the sim harness and push, but you cannot touch the live LAN.

## What this initiative is (one paragraph)

Turn the existing **reactive** smart-home chat app into an **ambient agent**. Today
the house only acts when spoken to and remembers nothing. We are adding an
always-on "brain" (`cognition-sidecar`) on the Ubuntu AI box that continuously
observes cheap signals (Frigate/MQTT detections, HA presence/mmWave/motion),
maintains a structured **belief state** per room (with preserved uncertainty),
selectively spends expensive vision compute under a budget, learns from manual
corrections, and **feeds its belief into the chatbot so context already exists at
question time**. MVP ships **read-only** (no autonomous device control).

## Topology (3 machines)

- **Windows** — Tauri/React app (`app/`), pure UI, localStorage only. Viewer/controller.
- **Ubuntu AI Box** — `stack/` Docker: vLLM (Qwen3-VL-30B-A3B), metrics-sidecar
  (:8092, SSE "chat tee"), vision-sidecar (:8091, `/describe`), Parakeet STT,
  Kokoro TTS. **New: cognition-sidecar (:8093) + SQLite Atlas + future V-JEPA.**
- **LattePanda Sigma** — Home Assistant + Frigate + MQTT (live, reachable on LAN).

## Decisions made (with rationale)

1. **Agent runs as a new always-on service on the AI box** — not in the Tauri app
   (it sleeps) and not in HA. App stays a viewer.
2. **Close the belief→chat loop in the MVP** — inject a compact "current home
   context" block in the **metrics-sidecar tee** (it already proxies
   `/v1/chat/completions`), so the chatbot answers from pre-formed belief with
   zero HA config change. This is the headline goal; it was nearly demoted to
   "read-only display only" — restored after adversarial review.
3. **Lean in-memory first increment** — prove Observe→active-room→belief→app
   card→chat-injection with NO database, NO VLM, NO curiosity budget. Persistence
   and learning come in Increment 2. Keeps the first PR testable.
4. **Fix data quality from the first learning increment** — classify light changes
   as human / automation / agent via HA event `context` (`parent_id` traces
   automations), so HA automations don't poison the `manual_corrections` table;
   and capture short **clips**, not single stills (activities are temporal and
   V-JEPA needs clips).
5. **Build a replay/sim test harness** (`sim.py`) so the loop is testable in CI
   with no house attached. The user runs the live `recon.py` on the LAN.
6. **Datastore = SQLite + sqlite-vec** on the AI box (added Increment 2), clips on
   the filesystem under a persistent docker volume. Embeddings/vector recall were
   **cut from the MVP** (Qwen3-VL likely has no embeddings endpoint — don't ship
   non-functional recall).

## Adversarial review — fixes folded into the plan

- #1 Belief must actually reach chat (tee injection) — was display-only.
- #2 Automations would poison `manual_corrections` — added human/automation/agent
  classification via HA `context`.
- #3 Single-frame VLM can't see activities — capture multi-frame clips.
- #4 No agent→user channel — added `POST /feedback` + `pending_question` + `ask`
  belief events.
- #5 Curiosity deferral is racy — added a priority semaphore in the tee on top of
  the token bucket; still validate empirically.
- #6 Embeddings likely vaporware — cut vector recall from MVP.
- #7 Cross-machine clips — pull Frigate clips to the AI box at capture time, not
  dangling remote paths.
- #8 24/7 service needs reconnect/backoff for HA WS + MQTT.
- #9/#10 Recon needs LAN access this dispatched session lacks → user-run
  `recon.py` + sim harness for CI testing.
- #11 First increment was over-scoped → lean in-memory Increment 1.

## Status

- **Phase:** planning complete; **no implementation code written yet.**
- **Branch:** `claude/home-agent-architecture-PI5Gy`.
- **Not started:** the `stack/services/cognition/` service, app changes
  (`home-belief.jsx` + SSE consumer + vision-card wiring), tee injection,
  SQLite Atlas, sim/recon scripts.
- **Open item:** V-JEPA spec verification (PLAN §7) — a research sub-agent was
  verifying whether "V-JEPA 2.1" is an official release (likely it is **V-JEPA 2**,
  Meta/FAIR 2025) and the exact clip/label format. This affects only the
  Increment-3 dataset exporter, not the MVP. Re-verify before building that exporter.

## Environment constraints (why recon is user-run)

This planning work was done in a **dispatched (cloud) Claude Code session**: an
isolated container (`hostname vm`, root), fresh repo clone, **no SSH keys, no route
to `192.168.0.x`** (HA at `192.168.0.125:8123` was unreachable), restricted egress.
So the dispatched session cannot probe HA/Frigate or deploy. A **local session on
the Windows machine** is on the LAN and has SSH access to the AI box and LattePanda
— prefer that for live recon, pulling real Frigate events/clips, and deployment.

## Key existing code to reuse (don't reinvent)

- `stack/services/metrics-sidecar/main.py` — SSE bus primitives (`_subscribers`,
  `_broadcast_completion`, `/conversations/stream`, `/conversations/recent`, lines
  ~169-188, 336-371) AND the `/v1/chat/completions` proxy = the belief→chat
  injection point.
- `stack/services/vision/app.py` — `POST /describe` (Qwen3-VL), camera aliases
  ~43-53; grabs a single JPEG at ~108 (replace with multi-frame for clips).
- `app/src/home-app.jsx` — existing SSE consumer ~1345-1451 (clone for
  `:8093/belief/stream`), vision card mount ~1488, `metricsBase` derivation.
- `app/src/home-vision.jsx` — activity-label render ~272-281 (placeholders at
  ~30-36) to fill from belief; window-export convention ~289.
- `app/src/index.html` — script registration pattern (~31-32) for `home-belief.jsx`.
- `app/src/home-ha.jsx` — HA WS auth/subscribe protocol shape (~58-118, 336-357)
  to re-implement server-side in Python `ha_ws.py`.
