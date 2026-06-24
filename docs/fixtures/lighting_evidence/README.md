# Lighting Evidence — event contract & fixtures

This directory pins the on-disk JSONL shapes that the Living Lights override-observation pipeline writes to `/config/lighting_preferences_pending.jsonl` (on HAOS), and the matching HA bus event shapes. Codex consumes these records to build durable preference signals.

Both record kinds share that JSONL file. They are discriminated by `kind`:

- `kind: "override_event"` — immediate moment-of-touch evidence, fired by per-light manual-detection automations when actual brightness diverges from any owning zone's prediction outside a tolerance window.
- `kind: "pending_preference"` — zone-exit snapshot, fired ~5 s after `binary_sensor.<slug>_person_occupancy` goes `on → off`, when the zone's deviation sensor is `on` AND `input_boolean.living_lights_learning_enabled` is `on`.

## Fixtures

The two example files mirror real records captured during the Milestone-1 + Milestone-2 live office E2E. `source_user_id` in the override_event is redacted (`"REDACTED-user-uuid"`); every other field is verbatim from disk.

- [`override_event.example.jsonl`](./override_event.example.jsonl) — one record, single-owner light (`light.office` owned only by `office`).
- [`pending_preference.example.jsonl`](./pending_preference.example.jsonl) — two records demonstrating both `capture_provenance.capture_trigger` variants:
  1. `vacancy_settle` — the natural path. `trigger_*` fields populated from the state trigger that fired the automation.
  2. `manual_automation_trigger` — the forced-fire path used by `automation.trigger` service or test harnesses. `trigger_*` fields are all `null`.

For shared lights (e.g. `light.front_left` owned by both `sofa` and `front_left`), `owning_zones` and `predictions_by_zone` both carry entries for every owning zone — same rule for both record kinds.

## `override_event` schema

Top-level keys (alphabetised, JSONL output uses sorted keys via `tojson`):

| key | type | semantics |
| --- | --- | --- |
| `camera` | string | Frigate camera the primary zone lives on |
| `context.anticipated_room` | string \| null | room the kinematic anticipator predicts the user is heading toward; null when no anticipation |
| `context.any_occupied` | bool | `binary_sensor.living_lights_any_occupied` at moment-of-touch |
| `context.asleep` | bool | `input_boolean.living_lights_asleep` |
| `context.night_safe` | bool | `binary_sensor.living_lights_is_night_safe` |
| `context.profile` | string | `sensor.living_lights_profile` value (morning / midday / afternoon / evening / late_evening / overnight) |
| `context.shadow_mode` | bool | `input_boolean.living_lights_shadow` — when `true`, the pilot was not actuating; the override is counterfactual and should be weighted lower |
| `context.source_user_id` | string \| null | HA user UUID for HA-UI / mobile-app / API-token changes; `null` for wall-switch / Hue-Bridge / non-HA-attributed sources |
| `context.state` | string | classifier state of the primary zone at touch time (`vacant` / `present` / `pass_through` / `presence_override` / `away` / `night_safe` / `anticipated`) |
| `context.tv_playing` | bool | tightened `media_player.lg_tv in ['on','playing','paused','buffering']` |
| `context.user_at_home` | bool | `input_boolean.user_at_home` |
| `context_id` | string | per-event correlation id `ovr-<unix>-<microsecond>` |
| `debug_match_reason` | string | always `"none"` on the firing path (the match logic said no-match → fire); other values indicate a match branch (see actuator generator) |
| `delta_pct` | int | signed; `actual − predicted` against the **primary** zone's predicted_pct |
| `kind` | string | `"override_event"` |
| `light_entity` | string | HA light entity that changed |
| `observed.brightness_pct` | int | rounded; `0` if the light is off |
| `observed.light_state` | string | `"on"` / `"off"` |
| `owning_zones` | string[] | every zone whose `LIGHT_TARGETS` contains `light_entity`; for shared lights this has length > 1 |
| `predicted.brightness_pct` | int | primary zone's predicted_brightness_pct |
| `predicted.color_temp_kelvin` | int | primary zone's predicted_color_temp_kelvin |
| `predictions_by_zone[slug]` | object | per-owning-zone snapshot at moment-of-touch: `{state, predicted_pct, ramp_initial_pct}` |
| `ts` | string | ISO-8601 with offset |
| `zone` | string | primary zone slug (first entry of `owning_zones`) |

Mirror HA bus event: `living_lights_override_detected` — same payload, no `kind` discriminator (event type IS the kind), no leading `context.` namespace on context fields (they appear at top-level of `event_data`).

## `pending_preference` schema (Milestone-2 enriched)

Top-level keys:

| key | type | semantics |
| --- | --- | --- |
| `capture_provenance.capture_trigger` | string | `"vacancy_settle"` (natural binary_sensor on→off settle) OR `"manual_automation_trigger"` (forced via `automation.trigger` service) |
| `capture_provenance.trigger_entity_id` | string \| null | the binary_sensor that fired the trigger; `null` for forced fires |
| `capture_provenance.trigger_from` | string \| null | `"on"` for natural settle; `null` for forced |
| `capture_provenance.trigger_to` | string \| null | `"off"` for natural settle; `null` for forced |
| `capture_provenance.trigger_for_s` | int \| null | `5` (the configured `for:` duration) for natural settle; `null` for forced |
| `captured_state.brightness_pct` | int | actual brightness at capture |
| `captured_state.color_temp_kelvin` | int | actual color-temp at capture |
| `context` | object | same shape as `override_event.context` plus `dwell_s` (seconds since person_occupancy last changed, includes the 5 s settle window) and `source: "capture_on_vacant"` |
| `kind` | string | `"pending_preference"` |
| `light_entity` | string | the primary light captured (`LIGHT_TARGETS[zone][0]`) |
| `occupancy.age_s` | int | seconds since `occupancy.last_changed`; near-zero values (< 10) flag the capture as weaker — captured during an occupancy flicker |
| `occupancy.entity_id` | string | `binary_sensor.<slug>_person_occupancy` |
| `occupancy.flicker_count_5min` | int | M4 rolling count of person-occupancy state changes in the previous 5 minutes; high values weaken the evidence |
| `occupancy.last_changed` | string \| null | ISO-8601 of last state change; null if entity missing |
| `occupancy.state` | string | current state at capture (`on` / `off` / `unavailable`) |
| `owning_zones` | string[] | every zone whose `LIGHT_TARGETS` contains `light_entity` |
| `prediction_consistency.primary_prediction_pct` | int | live read from `state_attr('sensor.<cam>_<slug>_lighting_state', 'predicted_brightness_pct')` at capture |
| `prediction_consistency.would_have_done_brightness_pct` | int | cached value from `binary_sensor.living_lights_<slug>_deviation.attributes.predicted_brightness_pct` (re-evaluated every 30 s) |
| `prediction_consistency.prediction_mismatch_pct` | int | `primary_prediction_pct − would_have_done_brightness_pct`; non-zero values flag that the classifier transitioned within the deviation sensor's 30 s re-eval window — the evidence is internally inconsistent and should be weighted lower |
| `predictions_by_zone[slug]` | object | per-owning-zone snapshot at capture: `{state, predicted_pct, ramp_initial_pct}` |
| `primary_zone` | string | the slug the capture automation owns; equals `zone` |
| `ts` | string | ISO-8601 with offset |
| `what_automation_would_have_done.brightness_pct` | int | from the deviation sensor's cached predicted_brightness_pct — kept for backward compat with pre-M2 records |
| `what_automation_would_have_done.color_temp_kelvin` | int | from the deviation sensor's cached predicted_color_temp_kelvin |
| `zone` | string | same as `primary_zone` |

Mirror HA bus event: `living_lights_preference_pending` — same shape with M2 blocks as flat children of `event_data`.

## Aggregation hints (V2+ Codex consumers)

The schema is designed so an aggregator can do the trustworthy thing without much logic:

- **Prefer `pending_preference` over `override_event`** for durable signals — it's the "what the user stayed with" snapshot.
- **Weight `vacancy_settle` higher than `manual_automation_trigger`.** The latter is test traffic, not user behavior.
- **Discount when `prediction_mismatch_pct` ≠ 0.** The deviation sensor and the classifier disagreed at capture time; the evidence is internally inconsistent.
- **Discount when `occupancy.age_s < 10`.** Capture happened during an occupancy flicker; the user may not have meaningfully "left" the zone.
- **Discount when `context.shadow_mode == true`.** Counterfactual — the pilot wasn't actuating, so the user's "preference" is hypothetical.
- **Join `override_event` + `pending_preference` on identical `context.*` keys** (profile, tv_playing, asleep, night_safe, user_at_home, any_occupied, state, shadow_mode). Both record kinds carry the same context dimensions so episode-grouping is straightforward.
- **For shared lights**, `predictions_by_zone` carries every owning zone's view. Aggregation can decide which zone "owns" the preference by comparing per-zone predicted_pct against captured_state.brightness_pct.

## Generator + REST endpoints

Source of truth (do not hand-edit the generated YAML):

- `tools/build-living-lights-actuators.py` → emits per-light manual-detection automations that fire `override_event` records into `living_lights_manual_detection.yaml`.
- `tools/build-living-lights-learning.py` → emits per-zone capture-on-vacant automations that fire `pending_preference` records into `living_lights_learning.yaml`.

Read paths used by the Home app's `/why-light` slash command and Codex:

- `GET /api/extended_openai_conversation/recent_overrides?zone=<slug>&hours=<H>` — in-memory 1-hour deque from `WorldStateAggregator`. Fast; covers only the most recent hour.
- `GET /api/extended_openai_conversation/lighting_decisions?zone=<slug>&tail=<N>&hours=<H>` — streaming-tail-with-filter over `/config/lighting_decisions.log` (shadow-mode pilot's transition log).
- `GET /api/extended_openai_conversation/recent_pending_preferences?zone=<slug>&tail=<N>&hours=<H>` — Milestone-2 endpoint; streaming-tail-with-filter over `/config/lighting_preferences_pending.jsonl`, filters to `kind: pending_preference` + exact zone match.

All three are read-only HA REST views registered in `ha-config/extended_openai_conversation/__init__.py`.
