# Experiment — full-duplex speech-to-speech (PersonaPlex)

Status: **Phase 1.5a + 1.5b + 1.5c deployed; 6/6 PASS on the harness suite.**
Bridge architecture moved from "open new upstream WS per client" to
"single persistent upstream session, multiplex clients onto it" (1.5a,
Thinking-Machines-inspired) plus intent-fire-on-match (1.5b) plus a
Parakeet side-channel with per-client utterance boundaries (1.5c).
S-01 hits tier2 (real `light.sink` state change in HA, 7.9 s); S-02..S-04
hit tier4 (intent dispatched); S-13/S-14 correctly don't dispatch.

## Phase 1.5 changes (since the original Phase 1 docs below)

- **1.5a — PersistentUpstream**: bridge boot opens ONE upstream WS to
  `moshi.server` and keeps it open. Clients attach/detach onto the
  same session; the 6.7 s `moshi.server` handshake is paid once at
  bridge boot, not per mic press. Side benefit: the "session
  degradation across consecutive sessions" failure mode that drove
  the every-10-attempts moshi-restart workaround stops applying —
  there is only one session for the lifetime of the bridge.
- **1.5b — intent-fire-on-match**: classifier runs on every text-buffer
  update with a `_looks_like_complete_object()` guard (verb + device
  noun present). Fires the HA dispatch the instant the regex matches,
  not on the 800 ms idle timer or the sentence terminator. Dedupe
  protects against the trailing-period double-fire.
- **1.5c — Parakeet side-channel**: bridge forks the user PCM stream
  into a parallel Wyoming TCP client to `wyoming-parakeet:10300`.
  Parakeet emits a Transcript event with reliable ASR-grade text;
  bridge classifies on that text via `_extract_user_intent()` (an
  imperative-form regex distinct from the assistant-announce-phrase
  regex). When the user says "turn off the kitchen lights," we no
  longer depend on PersonaPlex echoing the right announce-phrase to
  trigger dispatch — Parakeet's text fires it directly.

## Phase 1 details (kept for reference)

## What it is

NVIDIA PersonaPlex-7B-v1, a full-duplex S2S model based on Kyutai Moshi,
wired into the Home app and Home Assistant. The user speaks; PersonaPlex
replies (in PersonaPlex's voice) and announces the action it's taking;
a bridge sidecar extracts the intent from PersonaPlex's text channel
and dispatches via HA's `assist_pipeline/run` to fire real device
actions.

Side-by-side with the existing Voice PE / Parakeet / Qwen3-VL / Kokoro
pipeline. Everything new lives behind the `s2s` Docker Compose profile;
default `docker compose up -d` leaves it off.

## Architecture

```
   ┌────────────────────┐         ┌────────────────────┐
   │  Home app /s2s     │         │  Browser :8998     │
   │  (PCM via WS)      │         │  (NVIDIA web UI)   │
   └──────────┬─────────┘         └──────────┬─────────┘
              │                              │
              ▼                              │ direct
   ┌────────────────────────────┐            │
   │  personaplex-bridge :8094  │            │
   │   - Opus transcode         │            │
   │   - handshake-gating       │            │
   │   - text-channel tap       │            │
   │   - intent classifier      │            │
   │   - HA WS client           │            │
   │   - chat-tee POST          │            │
   │   - BRIDGE_TOKEN auth      │            │
   └─────────┬──────────────────┘            │
             │  Opus + kind=1                 │
             ▼                                │
   ┌────────────────────────────┐             │
   │   s2s-model :8998          │◄────────────┘
   │   (NVIDIA moshi.server +   │
   │    PersonaPlex-7B-v1)      │
   └─────────┬──────────────────┘
             │ in parallel: text-channel intent
             ▼
   ┌────────────────────────────┐
   │  HA assist_pipeline/run    │
   │  → Extended OpenAI Conv    │
   │  → Qwen3-VL                │
   │  → tool_call dispatch      │
   │  → device + action card    │
   └────────────────────────────┘
```

## How to enable

```bash
ssh hav-ubuntu
cd /opt/home-ai-voice
# bridge is the only thing always-up; the model is heavy
docker compose --profile s2s up -d personaplex-bridge s2s-model
```

Set `S2S_BACKEND=moshi` in `.env` (or it defaults to `echo` for wire
testing without the GPU model).

From the Home app:
```
/s2s http://192.168.0.100:8094
/s2s on
```

Press the mic, talk. The `s2s` badge appears next to the mic. PersonaPlex's
voice answers through the Home app's speakers.

## Phase 1 results — what works, what doesn't

Tested via the autonomous harness at `tools/s2s_harness/` (on the AI box).
6 canned scenarios, audio generated once via Kokoro TTS, played through
the bridge as if from a real mic.

| Scenario | Status | Tier | Notes |
|---|---|---|---|
| S-01 — turn off kitchen lights | PASS | tier4 | intent classifier matched; bridge dispatched. `light.kitchen_floodlight_timed` is unavailable in HA, so no actual light toggled |
| S-02 — dim living room 30% | FAIL | no_evidence | model responded with greeting; no intent announce |
| S-03 — lock front door | FAIL | no_evidence | 0 transcripts — model went silent (degraded after preceding sessions) |
| S-04 — thermostat 68 | FAIL | no_evidence | 0 transcripts — same |
| S-13 — weather chitchat (chitchat) | PASS | no_dispatch_as_expected | model didn't dispatch (correct) |
| S-14 — joke (chitchat) | PASS | no_dispatch_as_expected | same |

3/6 passing. The pattern: PersonaPlex behaves correctly on the FIRST
session after a fresh `moshi.server` restart; subsequent sessions get
progressively less responsive (more "Hello, this is Home" greetings,
fewer action announcements, eventually 0 transcripts).

## Known issues + workarounds

### Critical: model-response degradation across sessions

After 2-3 consecutive sessions, PersonaPlex stops emitting action
announcements and defaults to generic greetings ("Hello, this is Home.
How can I help you today?"). After 4+ sessions, it stops emitting any
transcript at all.

Mitigation: restart `hav-s2s-model` between scenarios. ~30 s warmup,
but produces a clean session every time. The autonomous harness should
do this automatically — open issue.

Root cause suspected: moshi.server's internal Mimi codec or LM state
isn't fully reset between WS sessions. The `streaming_forever(1)`
buffer or the LM's KV cache may retain residue. NVIDIA's upstream
server doesn't document a hard reset mechanism.

### `moshi.server` patches we apply (Dockerfile-baked)

Two one-line patches:
1. **`pcm is None` guard in opus_loop** — NVIDIA's stock server.py
   `if pcm.shape[-1] == 0:` crashes when sphn's `read_pcm()` returns
   None. We wrap as `if pcm is None or pcm.shape[-1] == 0:`.
2. (none others currently)

### Bridge handshake-gating + buffer-and-flush

PersonaPlex takes ~6 s to load voice + system prompts after WS connect.
During that window, the bridge must NOT send audio bytes — sphn's
decoder on the server side chokes on bytes arriving before its
`opus_loop` task starts. We buffer pre-handshake PCM and flush at
real-time pace (80 ms per 1280-sample chunk) once `kind=0` handshake
arrives.

Without this: `ValueError: sending on a closed channel` from sphn,
session dies before responding.

### PersonaPlex sometimes hallucinates the wrong entity

S-01's test reliably produces "turning off the kitchen lights" OR
"turning off the living room lights" (the model picks one). HA dispatches
the literal text it receives. If the model says "living room", that's
what HA actuates. Bridge's job ends at correct dispatch — the model's
entity choice is the model's decision.

### Inconsistent response cadence

Some sessions: model responds in ~2 s after audio finishes.
Other sessions: model responds within audio (interjecting before user
"finishes"). Other sessions: 0 response.

PersonaPlex is a continuously-running full-duplex model; it decides
when to speak. The system prompt requests action-announcement BEFORE
acknowledgement, but compliance is ~50%.

## VRAM budget on the 96 GB Blackwell

| Service | VRAM | Notes |
|---|---|---|
| vLLM (Qwen3-VL-30B BF16, gpu_mem_util=0.65) | 62 GB | Dropped from 0.75 to make room |
| PersonaPlex 7B BF16 | 17 GB | Idle/streaming |
| Parakeet STT | 3 GB | |
| Kokoro TTS | 3 GB | |
| ComfyUI + oracle-ml (occasional) | ~1.5 GB | |
| **Total** | **~87 GB** | **~10 GB headroom** |

For future V-JEPA-2 integration, FP8 quantization of Qwen3-VL frees
~30 GB. Migration deferred per user; documented in plan file.

## Files

**AI box (HomeAIVoice):**
- `services/personaplex-bridge/main.py` — bridge logic
- `services/personaplex-bridge/Dockerfile` — Python + sphn + numpy
- `services/s2s-model/Dockerfile` — NVIDIA PyTorch + moshi from
  github.com/NVIDIA/personaplex + None-guard patch
- `services/s2s-model/server.py` — placeholder (currently launches
  stock `moshi.server` from the patched package)
- `docker-compose.yml` — `s2s` profile services
- `tools/s2s_harness/` — autonomous test harness:
  - `test_s01.py` — single-scenario quick test
  - `run_scenario.py` — full suite runner with tiered pass/fail
  - `autoloop.sh` — self-fix iteration loop for debugging
  - `scenarios.yaml` — 6 scenarios defined
  - `fixtures/*.wav` — Kokoro-generated test audio

**Home app (this repo):**
- `app/src/home-s2s.jsx` — bridge WebSocket client
- `app/src/home-app.jsx` — `/s2s` slash command + mic toggle integration
- `app/src/index.html` — loads home-s2s.jsx

## Validating Phase 1.5

```bash
# Confirm the new bridge is up with all 1.5 features
ssh hav-ubuntu "docker logs --tail 20 hav-personaplex-bridge"
# Look for: PersistentUpstream: handshake done, ready for clients
# Look for: ParakeetTap: ready (Transcribe session open)
# Look for: HA WS authenticated

# Run the 6-scenario suite against Phase 1.5c
ssh hav-ubuntu "ha_token=\$(docker exec hav-personaplex-bridge env | grep '^HA_TOKEN=' | cut -d= -f2-) && \
  docker exec -e HA_TOKEN=\"\$ha_token\" -e TEST_TIMEOUT_S=60 hav-personaplex-bridge \
    python /app/run_scenario.py --suite"
# Expected: action scenarios (S-01..S-04) should reliably PASS via the
# Parakeet path even when PersonaPlex's text channel doesn't match —
# eliminates the alternating-fail pattern from Phase 1.
```

## Restart procedure

If the model gets stuck (no responses to test commands):
```bash
ssh hav-ubuntu "docker restart hav-s2s-model"
# wait ~30 s for warmup
ssh hav-ubuntu "docker logs --tail 5 hav-s2s-model"
# look for: ======== Running on https://0.0.0.0:8998 ========
```

If the bridge gets stuck (lock held, "another s2s session active"):
```bash
ssh hav-ubuntu "docker restart hav-personaplex-bridge"
```

## Phase 2-4 status

- **Phase 2 (dedupe + tool_call_id)** — partially implemented in
  bridge: `DedupeWindow` class exists, UUID tool_call_id is minted
  per dispatch. HA-side error correlation (`intent-end{success:false}`
  → typed error event) is NOT wired up yet.
- **Phase 3 (Wyoming shim for Voice PE)** — designed in plan, not
  implemented. Halts before HA voice-pipeline reconfig (user
  authorization required).
- **Phase 4 (kind=3 spoken confirmations)** — designed in plan, not
  implemented. ~90 LOC patch to `moshi.server` to expose the
  `lm_gen.step(text_token=...)` capability that NVIDIA already has
  at the model level but doesn't expose at the WS protocol level.

## Tear down

```bash
docker compose --profile s2s down personaplex-bridge s2s-model
```

The default pipeline (Voice PE + Parakeet + Qwen3-VL + Kokoro) is
unaffected.
