# Architecture Decisions

This file is the short release-facing decision trail for large pivots whose
full history is scattered across experiment notes, compose comments, and
source-level rationale. It is not a replacement for those sources; it is an
index of the current decision, why it exists, how to roll it back, and where
the detailed evidence lives.

## Decision Index

| ID | Decision | Current status | Primary evidence |
|---|---|---|---|
| ADR-001 | Retire the Markov next-zone predictor as the primary anticipator and use kinematic trajectory projection. | Kinematic anticipator is primary; Markov predictor remains on disk as legacy/offline reference. | `addons/predictive-lighting/anticipate.py`, `addons/predictive-lighting/app.py`, `predictor/` |
| ADR-002 | Keep PersonaPlex/Moshi speech-to-speech out of the default stack. | S2S services are behind the `s2s` profile or commented experimental blocks; default voice path uses split STT/LLM/TTS services. | `docs/EXPERIMENTS-S2S.md`, `stack/docker-compose.yml` |
| ADR-003 | Use Chatterbox as primary TTS and Kokoro as fallback while preserving the `wyoming-kokoro` service identity for HA pairing stability. | Chatterbox is the default TTS engine; Kokoro stays available as fallback. | `stack/docker-compose.yml`, `docs/HOME_SYSTEM_OVERVIEW.md`, `docs/RUNBOOK.md` |
| ADR-004 | Use Qwen3-VL-30B-A3B-Instruct-FP8 for the local home agent instead of the smaller 4B swap. | 30B MoE FP8 is the default served model; 4B was reverted after natural command misses. | `stack/docker-compose.yml`, `docs/EXPERIMENTS-S2S.md` |

## ADR-001: Markov Predictor To Kinematic Anticipator

**Decision:** The predictive-lighting add-on uses the kinematic anticipator
as the primary pre-warm mechanism. The legacy Markov predictor remains in
`predictor/` for reference and offline comparison, but live predictions come
from `addons/predictive-lighting/anticipate.py`.

**Rationale:** The Markov approach tried to infer direction from aggregate
transition counts. The kinematic approach uses Frigate `path_data` directly:
derive recent velocity, ray-cast the current foot point against the camera
field, map camera edges or polygons to a destination room, and publish
per-room anticipated occupancy over MQTT.

**Current implementation:** `addons/predictive-lighting/app.py` keeps the
old zone-transition logger as cold-storage ground truth, but routes each
person event through `anticipate.Anticipator` for live predictions. Living
Lights consumes those anticipated room booleans for pre-warm behavior.

**Rollback path:** Disable anticipated pre-warm with the Living Lights
anticipated kill switch, or reintroduce the Markov predictor from `predictor/`
behind the add-on. Keep the transition logger running either way so future
evaluation has ground-truth room transitions.

**Evidence:** `addons/predictive-lighting/anticipate.py` documents the
replacement rationale and trajectory model; `addons/predictive-lighting/app.py`
documents the logger/anticipator split; `predictor/__init__.py` identifies the
legacy Markov predictor.

## ADR-002: PersonaPlex/Moshi Out Of The Default Stack

**Decision:** PersonaPlex/Moshi full-duplex speech-to-speech is retained as
experimental code, but it is not part of the default Home app release path.

**Rationale:** The Phase 1 PersonaPlex harness passed only 3 of 6 scenarios
and showed response degradation across consecutive sessions. Phase 2 then
moved to vanilla Moshi, later proving a Rust Moshi backend could run on
Blackwell, but integration remained deferred because the split pipeline was
already reliable enough for current use.

**Current implementation:** `stack/docker-compose.yml` keeps
`personaplex-bridge` and `s2s-model` behind the `s2s` profile, while the
previous Moshi listener block is commented out. Default operation uses
Voice PE/Parakeet for STT, Extended OpenAI Conversation plus vLLM for tool
reasoning, and Chatterbox/Kokoro for TTS.

**Rollback path:** Bring the `s2s` profile up intentionally, set the bridge
backend to the desired S2S backend, and run the S2S suite before treating it
as user-facing again.

**Evidence:** `docs/EXPERIMENTS-S2S.md` records Phase 1 through Phase 2.6
results, including PersonaPlex failure modes, Moshi deployment, and Rust
Moshi viability. `stack/docker-compose.yml` records the current default
profile boundary and commented Moshi listener block.

## ADR-003: Chatterbox Primary, Kokoro Fallback

**Decision:** Chatterbox is the primary TTS engine for the Home voice path,
with Kokoro retained as fallback. The `wyoming-kokoro` service name remains
for Home Assistant pairing stability even though it now fronts Chatterbox by
default.

**Rationale:** The compose comments record Chatterbox as the production
default after the smoke suite, with low synthesis latency and an automatic
fallback path to Kokoro on failure. Keeping `wyoming-kokoro` avoids forcing a
Home Assistant re-pair just because the upstream OpenAI-compatible TTS engine
changed.

**Current implementation:** `TTS_ENGINE` defaults to `chatterbox` and
`TTS_FALLBACK` defaults to `kokoro` for the bridge. The Wyoming bridge points
to `http://chatterbox-tts:8000/v1` by default and exposes `Gianna.wav` as the
voice, while Kokoro continues to run on port 8880.

**Rollback path:** Set the bridge TTS engine or Wyoming upstream back to
Kokoro, then recreate the relevant TTS bridge containers and re-select the
voice in the HA pipeline UI if needed.

**Evidence:** `stack/docker-compose.yml` documents Chatterbox, Kokoro,
`wyoming-kokoro`, `TTS_ENGINE`, and `TTS_FALLBACK`. `docs/HOME_SYSTEM_OVERVIEW.md`
and `docs/RUNBOOK.md` document the current roles.

## ADR-004: Qwen3-VL 30B MoE As The Local Agent Model

**Decision:** The local home agent defaults to
`Qwen/Qwen3-VL-30B-A3B-Instruct-FP8` served as `qwen3-vl-30b`, rather than
the smaller Qwen3-VL 4B experiment.

**Rationale:** The compose command comments record that the 4B dense variant
missed natural tool intents such as whole-home light commands. The 30B MoE
model restored command reliability while keeping latency acceptable because
only a small active parameter subset is used per token.

**Current implementation:** The vLLM service in `stack/docker-compose.yml`
uses the 30B MoE FP8 model, keeps the served model name stable as
`qwen3-vl-30b`, and keeps FP8 weights while avoiding the FP8 KV-cache flags
that produced garbled output.

**Rollback path:** Any future smaller-model attempt must run the workflow,
slash-command, and live/non-production planning scenarios before becoming the
default. Preserve the served-model-name contract or update every consumer that
depends on it.

**Evidence:** `stack/docker-compose.yml` records the swap-back rationale and
current vLLM command. `docs/EXPERIMENTS-S2S.md` records the Phase 2.1 4B swap
and the surrounding VRAM/latency context.
