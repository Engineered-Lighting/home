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
| ADR-005 | Allow bounded outbound calls to TypeSafe from the lighting belief publisher, behind a signed egress record, an environment flag and a Home Assistant kill switch. | Draft; pending owner review and the first signed record. | `stack/services/lighting-publisher/`, `docs/ARCHITECTURE_DECISIONS.md#adr-005` |

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

## ADR-005: Bounded Outbound Calls To TypeSafe From The Lighting Belief Publisher

**Status:** Draft for owner review (plan rev 5, milestone M3). Not in force
until the owner signs the first egress record.

**Decision:** The lighting belief publisher (`stack/services/lighting-publisher`,
package `lighting_beliefs`) may make bounded outbound HTTPS calls to
TypeSafe's System One endpoint to obtain calibrated probabilities for five
observational questions over a house-level packet. This is the only sanctioned
outbound path from the AI stack. It amends the sentence "No other outbound
traffic." in `docs/ARCHITECTURE.md` (the "Home desktop app" section, around
line 67), which was written for the desktop app's two connections and has been
read as a stack-wide rule. The proposed replacement wording is:

> No other outbound traffic from the desktop app. On the AI host, the only
> outbound path is the lighting belief publisher's bounded calls to TypeSafe,
> governed by ADR-005: a signed egress record, an environment flag, a Home
> Assistant kill switch, a leak guard, and a journal.

`docs/ARCHITECTURE.md` itself is edited only once the owner accepts this ADR.

**Rationale:** Jev supplies narrow semantic judgments (is someone attending to
the TV, is the house settling) with probabilities that code can threshold,
which the deterministic generator cannot derive from occupancy alone. The
model never generates text and never controls anything: the publisher reduces
answers to probabilities, the generator keeps every guard, and the stories are
proven on the offline simulator and a public-dataset ladder before household
prose is ever sent.

**Bounds:**

- The packet is `lighting-beliefs-state/v1` only: per-camera coverage, zone
  occupancy, anonymous tracks with position and posture, short fallible
  claims, one short account, person-adjacent object labels, media role and
  state, and a quiet age. No names, relationships, entity ids, modes, light
  levels, presence flags, clock or absolute timestamps; free text capped at
  240 characters; keys enforced by a recursive allow-list.
- `leak_guard.assert_clean()` runs on every packet and blocks on any hit
  (entity ids, `hav-*` containers, LAN addresses, JWT prefixes, model names,
  the OS username, RTSP and HTTP URLs, digit runs, emails, ISO timestamps,
  household names from a 0600 roster). There is no redaction helper.
- `EgressGate` allows a call only when the egress record exists with
  `enabled: true` and lists the scope, `TYPESAFE_EGRESS=1`, and the HA kill
  switch mirror reads `on` and is fresher than 300 s; at most six calls a
  minute, one in flight; every decision is journaled with its reason.
- Scopes are added only by re-signing the record: `public_eval` (ladder level
  1, public dataset text), then `household_shadow`, then `household_live`,
  each with dates and call counts in the record.
- The observer repository stays loopback-only; the publisher is the sole
  egress point and holds no Home Assistant token.

**Rollback path:** three independent rungs, any one of which stops egress:
set `enabled: false` in the record, turn the HA kill switch off (or let its
mirror go stale), or unset `TYPESAFE_EGRESS`. The generator's belief path is
itself behind `input_boolean.living_lights_actuate_from_belief_changes` and
falls back to the deterministic packages when the publisher's heartbeat goes
stale.

**Evidence:** `stack/services/lighting-publisher/README.md`,
`stack/services/lighting-publisher/egress-record.example.json`, the gate
matrix and leak-guard tests under `stack/services/lighting-publisher/tests/`,
`docs/RUNBOOK.md` ("External reasoning provider", the privacy test this guard
extends), and plan rev 5 milestone M3.
