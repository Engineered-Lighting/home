#!/usr/bin/env python3
"""build-living-lights-learning.py — generator for
ha-config/packages/living_lights_learning.yaml.

Emits per-zone deviation sensors + capture-on-vacant `pending_preference`
automations for ALL light-controlled zones (the 10 zones in LIGHT_TARGETS).

Replaces the original hand-written single-zone (dining_left) scaffold —
this is now the single source of truth for the learning package.

DEFAULT-OFF. Master toggle: input_boolean.living_lights_learning_enabled
(declared in living_lights_observability.yaml). When OFF, deviation
sensors still update but no JSONL records are written and no event fires.

`pending_preference` records carry (M2 evidence drilldown + M4 hygiene):
  - kind, ts, zone, primary_zone, light_entity, owning_zones
  - captured_state {brightness_pct, color_temp_kelvin}
  - what_automation_would_have_done {brightness_pct, color_temp_kelvin}
  - predictions_by_zone {<slug>: {state, predicted_pct, ramp_initial_pct}, ...}

  - evidence_id, dedupe_key, dedupe_window_s, capture_suppressed_count
    (M4) Per-capture unique id + the dedupe key the suppression logic uses.
    `capture_suppressed_count` is the number of materially-identical attempts
    suppressed since the last actual emit — non-zero means the system saved
    Codex from N redundant records.

  - related_override_context_id
    (M4) ctx_id of the most recent override_event observed in this zone,
    or `null` if none. Lets aggregation link a captured preference back to
    the touch episode that produced it.

  - prediction_consistency {primary_prediction_pct,
                             would_have_done_brightness_pct,
                             prediction_mismatch_pct}
    The classifier's live predicted_brightness_pct vs the deviation-sensor's
    cached "what_automation_would_have_done" can diverge by tens of percent
    when the classifier transitioned within the deviation sensor's 30 s
    re-evaluation window. mismatch_pct surfaces that gap so an aggregator can
    discount preferences whose evidence is internally inconsistent.

  - capture_provenance {capture_trigger, trigger_entity_id, trigger_from,
                         trigger_to, trigger_for_s}
    Distinguishes natural vacancy-settle fires (capture_trigger:
    'vacancy_settle', trigger_* populated from binary_sensor on→off) from
    forced test fires via automation.trigger service (capture_trigger:
    'manual_automation_trigger', trigger_* nullable). Codex must weight
    natural captures higher.

  - occupancy {entity_id, state, last_changed, age_s, flicker_count_5min}
    Snapshot of the zone's person_occupancy sensor at capture time. age_s is
    seconds since last_changed; near-zero values indicate the capture
    happened during occupancy flicker and the evidence is weaker.
    flicker_count_5min is the rolling count of occupancy state changes in the
    previous 5 minutes, maintained by the M4 flicker tracker.

  - context {profile, source, dwell_s, state, tv_playing, asleep, night_safe,
             user_at_home, any_occupied, shadow_mode}

The enriched schema mirrors override_event's context dimensions so an
aggregator can join immediate-touch evidence and final-state evidence on
identical context keys. For shared lights (a light owned by multiple zones,
e.g. light.front_left ∈ {sofa, front_left}), predictions_by_zone carries an
entry per owning zone so V2 aggregation can disambiguate which zone "owns"
the demonstrated preference.

shadow_mode is preserved so the aggregation layer can weight counterfactual
evidence (shadow=true) differently from real-world overrides.

override_event shape is NOT modified by Milestone 2 — only pending_preference
gets the new provenance/consistency/occupancy blocks. See the actuator
generator (tools/build-living-lights-actuators.py) for override_event.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


# Single source of truth: pull LIGHT_TARGETS + ZONE_CAMERA from the actuator
# generator. File has hyphens so we use importlib.util.spec_from_file_location.
_ACTUATORS_PATH = Path(__file__).resolve().parent / "build-living-lights-actuators.py"
_spec = importlib.util.spec_from_file_location("build_living_lights_actuators", _ACTUATORS_PATH)
_actuators = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_actuators)
LIGHT_TARGETS = _actuators.LIGHT_TARGETS
ZONE_CAMERA = _actuators.ZONE_CAMERA
CO_CONTROLLERS = _actuators.CO_CONTROLLERS

# Inverse of CO_CONTROLLERS: light_entity_id -> [zone_slug, zone_slug, ...]
# Used by emit_capture_automations to emit per-owning-zone scalar variables +
# predictions_by_zone mappings, mirroring the actuators' override-event shape.
LIGHT_TO_ZONES: dict[str, list[str]] = {
    eid: [t[0] for t in entries] for eid, entries in CO_CONTROLLERS.items()
}


def _light_zones():
    """Return [(slug, camera, [light_entities]), ...] for zones with lights."""
    zones = []
    for slug, lights in LIGHT_TARGETS.items():
        if not lights:
            continue
        camera = ZONE_CAMERA[slug]
        light_entities = [t[1] for t in lights if t[0] == "light"]
        if not light_entities:
            continue
        zones.append((slug, camera, light_entities))
    return zones


def emit_input_numbers() -> str:
    return """input_number:
  living_lights_learning_bri_threshold_pct:
    name: "Learning — brightness deviation threshold (%)"
    initial: 15
    min: 5
    max: 50
    step: 5
    icon: mdi:lightbulb-multiple
  living_lights_learning_ct_threshold_kelvin:
    name: "Learning — color temperature deviation threshold (K)"
    initial: 500
    min: 100
    max: 2000
    step: 100
    icon: mdi:thermometer
  living_lights_learning_daily_cap:
    name: "Learning — max new automations per 24h"
    initial: 5
    min: 1
    max: 20
    step: 1
    icon: mdi:counter
"""


def emit_deviation_template(zones) -> str:
    """One trigger block + N per-zone binary_sensor entries.

    Triggers on every light's brightness + color_temp_kelvin attribute, HA
    startup, and every 30 s. Each per-zone sensor reads from its primary
    light entity (the first in LIGHT_TARGETS[slug]) and compares against
    the zone's classifier sensor predicted_brightness_pct +
    predicted_color_temp_kelvin.
    """
    # Collect all unique light entities for triggers (deduped, order-preserved)
    all_lights = []
    seen = set()
    for _slug, _camera, lights in zones:
        for lt in lights:
            if lt not in seen:
                seen.add(lt)
                all_lights.append(lt)

    triggers = [
        "  - trigger:",
        "      - platform: state",
        "        entity_id:",
    ]
    triggers += [f"          - {lt}" for lt in all_lights]
    triggers += [
        "        attribute: brightness",
        "      - platform: state",
        "        entity_id:",
    ]
    triggers += [f"          - {lt}" for lt in all_lights]
    triggers += [
        "        attribute: color_temp_kelvin",
        "      - platform: homeassistant",
        "        event: start",
        "      - platform: time_pattern",
        '        seconds: "/30"',
        "    binary_sensor:",
    ]

    sensors = []
    for slug, camera, lights in zones:
        primary_light = lights[0]
        sensor_id = f"sensor.{camera}_{slug}_lighting_state"
        sensors.append(f"""      - name: "Living Lights — {slug} deviation"
        unique_id: living_lights_deviation_{slug}
        icon: mdi:alert-circle-outline
        state: >
          {{% set light = states.{primary_light} %}}
          {{% set bri_thr_pct = states('input_number.living_lights_learning_bri_threshold_pct') | float(15) %}}
          {{% set ct_thr = states('input_number.living_lights_learning_ct_threshold_kelvin') | float(500) %}}
          {{% if light is none or light.state != 'on' %}}
            false
          {{% else %}}
            {{% set actual_bri = (light.attributes.brightness | int(0)) / 2.55 %}}
            {{% set actual_ct = light.attributes.color_temp_kelvin | int(2700) %}}
            {{% set predicted_bri = state_attr('{sensor_id}', 'predicted_brightness_pct') | int(0) %}}
            {{% set predicted_ct = state_attr('{sensor_id}', 'predicted_color_temp_kelvin') | int(2700) %}}
            {{% if predicted_bri == 0 %}}
              false
            {{% else %}}
              {{{{ (actual_bri - predicted_bri) | abs > bri_thr_pct or (actual_ct - predicted_ct) | abs > ct_thr }}}}
            {{% endif %}}
          {{% endif %}}
        attributes:
          zone: {slug}
          light_entity: "{primary_light}"
          actual_brightness_pct: >
            {{% set light = states.{primary_light} %}}
            {{% if light and light.state == 'on' %}}
              {{{{ ((light.attributes.brightness | int(0)) / 2.55) | round(0) }}}}
            {{% else %}}0{{% endif %}}
          actual_color_temp_kelvin: >
            {{% set light = states.{primary_light} %}}
            {{% if light and light.state == 'on' %}}
              {{{{ light.attributes.color_temp_kelvin | int(2700) }}}}
            {{% else %}}0{{% endif %}}
          predicted_brightness_pct: >
            {{{{ state_attr('{sensor_id}', 'predicted_brightness_pct') | int(0) }}}}
          predicted_color_temp_kelvin: >
            {{{{ state_attr('{sensor_id}', 'predicted_color_temp_kelvin') | int(2700) }}}}
""")

    return "\n".join(triggers) + "\n" + "".join(sensors)


def emit_shell_command() -> str:
    return """shell_command:
  living_lights_capture_preference: 'sh -c "echo {{ payload }} | base64 -d >> /config/lighting_preferences_pending.jsonl; echo >> /config/lighting_preferences_pending.jsonl"'
"""


def _emit_per_owning_zone_scalars(primary_light: str, capture_slug: str) -> tuple[list[str], list[str]]:
    """Return (pred_var_lines, owning_zones_list).

    For each zone that targets primary_light (via CO_CONTROLLERS), emit three
    indented variable lines at 10-space indent ready to drop into the
    variables: block:
        pred_<own_slug>_state: "{{ states('sensor.<cam>_<own_slug>_lighting_state') }}"
        pred_<own_slug>_p:     "{{ state_attr(..., 'predicted_brightness_pct') | int(0) }}"
        pred_<own_slug>_ri:    "{{ state_attr(..., 'ramp_initial_pct') | int(0) }}"

    Always includes the capture zone itself even if CO_CONTROLLERS lookup
    misses (defensive — light could be hand-edited out of map mid-fix).
    """
    raw = LIGHT_TO_ZONES.get(primary_light, [])
    owning = list(dict.fromkeys([capture_slug] + raw))  # capture zone first, dedup
    lines: list[str] = []
    for own_slug in owning:
        own_camera = ZONE_CAMERA[own_slug]
        sensor_id = f"sensor.{own_camera}_{own_slug}_lighting_state"
        lines.append(
            f'          pred_{own_slug}_state: "{{{{ states(\'{sensor_id}\') }}}}"'
        )
        lines.append(
            f'          pred_{own_slug}_p: "{{{{ state_attr(\'{sensor_id}\', \'predicted_brightness_pct\') | int(0) }}}}"'
        )
        lines.append(
            f'          pred_{own_slug}_ri: "{{{{ state_attr(\'{sensor_id}\', \'ramp_initial_pct\') | int(0) }}}}"'
        )
    return lines, owning


def _emit_predictions_inline(owning_zones: list[str]) -> str:
    """Emit a Jinja dict literal mapping owning_zone -> per-zone prediction
    dict, using the pred_<slug>_* scalar variables defined in the same
    variables: block. Used inline inside the json_payload tojson expression."""
    entries = []
    for own_slug in owning_zones:
        entries.append(
            f"'{own_slug}': {{'state': pred_{own_slug}_state, "
            f"'predicted_pct': pred_{own_slug}_p | int(0), "
            f"'ramp_initial_pct': pred_{own_slug}_ri | int(0)}}"
        )
    return "{" + ", ".join(entries) + "}"


def _emit_predictions_event_yaml(owning_zones: list[str], indent: int = 12) -> str:
    """Emit predictions_by_zone as inline YAML nested mapping for event_data.
    The mapping is rendered with HA Jinja substitutions per leaf so the bus
    event carries a real object (not a stringified dict).

    indent = column of the zone-slug keys (children of `predictions_by_zone:`
    which sits at indent-2).
    """
    pad = " " * indent
    pad_inner = " " * (indent + 2)
    lines = []
    for own_slug in owning_zones:
        lines.append(f"{pad}{own_slug}:")
        lines.append(f'{pad_inner}state: "{{{{ pred_{own_slug}_state }}}}"')
        lines.append(f'{pad_inner}predicted_pct: "{{{{ pred_{own_slug}_p | int(0) }}}}"')
        lines.append(f'{pad_inner}ramp_initial_pct: "{{{{ pred_{own_slug}_ri | int(0) }}}}"')
    return "\n".join(lines)


def emit_m4_helpers(zones) -> str:
    """M4: per-zone input_text helpers — pending_dedupe_state (JSON blob with
    last emit's k/at/bri/wbri/s) and occupancy_flicker_history (JSON array of
    recent unix timestamps, capped at the last 10 within a rolling 5 min)."""
    lines = ["input_text:"]
    for slug, _camera, _lights in zones:
        lines.append(f"  living_lights_zone_{slug}_pending_dedupe_state:")
        lines.append(f'    name: "{slug} — pending_preference dedupe state"')
        lines.append("    max: 255")
        lines.append("    initial: \"\"")
        lines.append("    icon: mdi:filter-cog")
        lines.append(f"  living_lights_zone_{slug}_occupancy_flicker_history:")
        lines.append(f'    name: "{slug} — occupancy flicker history (last 10 in 5 min)"')
        lines.append("    max: 255")
        lines.append("    initial: \"[]\"")
        lines.append("    icon: mdi:waveform")
    return "\n".join(lines) + "\n"


def emit_flicker_tracker_automations(zones) -> str:
    """M4: per-zone automation that maintains a rolling 5 min history of
    occupancy on/off transitions in input_text.*_occupancy_flicker_history.
    Capped at the last 10 entries (input_text has a 255-char ceiling — 10
    unix timestamps in a JSON array fits comfortably with margin).

    Triggers on every state change of binary_sensor.<slug>_person_occupancy.
    On fire: read history, prune entries older than now-300s, append the
    current timestamp, keep last 10, persist.

    Used by capture-on-vacant to populate occupancy.flicker_count_5min.

    Returns YAML automation entries WITHOUT the `automation:` header so
    main() can splice them under a single shared header alongside the
    capture-on-vacant automations."""
    blocks: list[str] = []
    for slug, _camera, _lights in zones:
        blocks.append(f"""  - alias: "Living Lights — track {slug} occupancy flicker (rolling 5min)"
    id: living_lights_track_flicker_{slug}
    description: >
      Maintains a rolling 5-minute history of binary_sensor.{slug}_person_occupancy
      transitions in input_text.living_lights_zone_{slug}_occupancy_flicker_history,
      capped at the last 10 entries. Read by capture-on-vacant to populate
      occupancy.flicker_count_5min on pending_preference records.
    mode: queued
    max: 20
    triggers:
      - trigger: state
        entity_id:
          - binary_sensor.{slug}_person_occupancy
    actions:
      - action: input_text.set_value
        target:
          entity_id: input_text.living_lights_zone_{slug}_occupancy_flicker_history
        data:
          value: >-
            {{% set ns = namespace(hist=[], pruned=[]) %}}
            {{% set raw = states('input_text.living_lights_zone_{slug}_occupancy_flicker_history') %}}
            {{% if raw and raw not in ['', 'unknown', 'unavailable', 'none', 'null'] %}}
              {{% set ns.hist = raw | from_json %}}
            {{% endif %}}
            {{% if ns.hist is not iterable or ns.hist is string %}}{{% set ns.hist = [] %}}{{% endif %}}
            {{% set now_ts = now().timestamp() | int %}}
            {{% set cutoff = now_ts - 300 %}}
            {{% for t in ns.hist %}}
              {{% if t is number and t >= cutoff %}}
                {{% set ns.pruned = ns.pruned + [t] %}}
              {{% endif %}}
            {{% endfor %}}
            {{{{ (ns.pruned + [now_ts])[-10:] | tojson }}}}
""")
    return "\n".join(blocks)


def emit_capture_automations(zones) -> str:
    """Per-zone capture-on-vacant automation. Triggers on
    binary_sensor.<slug>_person_occupancy off-for-5s, gated on
    learning_enabled + the zone's deviation sensor being on. Writes the
    enriched pending_preference JSONL record (with owning_zones,
    predictions_by_zone, and full context matching override_event) and
    fires the living_lights_preference_pending HA event with mirror payload.

    M2: context carries state, tv_playing, asleep, night_safe, user_at_home,
    any_occupied, dwell_s; predictions_by_zone carries every owning zone.

    M4: per-zone material-dedupe (10 min window). The automation always
    evaluates the variables block (computes is_duplicate) but the actions
    branch — emit + reset dedupe state, OR suppress (just increment the
    counter encoded in the dedupe state input_text). Mode is queued so two
    near-simultaneous triggers serialize (second sees first's emit and
    suppresses). Records carry evidence_id, dedupe_key, dedupe_window_s,
    capture_suppressed_count, related_override_context_id, and
    occupancy.flicker_count_5min populated from the flicker tracker.

    Returns YAML automation entries WITHOUT the `automation:` header so
    main() can splice them under a single shared header alongside the
    flicker-tracker automations.
    """
    blocks: list[str] = []
    for slug, camera, lights in zones:
        primary_light = lights[0]
        primary_sensor = f"sensor.{camera}_{slug}_lighting_state"
        pred_var_lines, owning_zones = _emit_per_owning_zone_scalars(primary_light, slug)
        predictions_inline = _emit_predictions_inline(owning_zones)
        # M4: event_data is now nested inside an `if/then:` branch, so its
        # children sit one indent level deeper than they did in M2. The
        # `predictions_by_zone:` key lives at column 14 (event_data children),
        # so the per-zone slug keys must be at column 16.
        predictions_event_yaml = _emit_predictions_event_yaml(owning_zones, indent=16)
        owning_list_literal = "[" + ", ".join(f"'{s}'" for s in owning_zones) + "]"
        pred_vars_block = "\n".join(pred_var_lines)

        blocks.append(f"""  - alias: "Living Lights — capture preference on vacant + 5s settle ({slug})"
    id: living_lights_capture_preference_{slug}
    description: >
      When the {slug} zone has a deviation flagged AND occupancy goes off
      + 5s settle, capture the actual state to pending JSONL + fire the
      living_lights_preference_pending bus event. M2 enriched: owning_zones,
      predictions_by_zone, full context, shadow_mode. M4 hygiene: material
      dedupe over 10 min window — duplicates suppressed (counter incremented)
      rather than re-written. Records carry evidence_id, dedupe_key,
      dedupe_window_s, capture_suppressed_count, related_override_context_id,
      and occupancy.flicker_count_5min.
    mode: queued
    max: 4
    triggers:
      - trigger: state
        entity_id:
          - binary_sensor.{slug}_person_occupancy
        from: "on"
        to: "off"
        for: "00:00:05"
    conditions:
      - condition: state
        entity_id: input_boolean.living_lights_learning_enabled
        state: "on"
      - condition: state
        entity_id: binary_sensor.living_lights_{slug}_deviation
        state: "on"
    actions:
      - variables:
          zone: "{slug}"
          primary_zone: "{slug}"
          light_entity: "{primary_light}"
          owning_zones: {owning_list_literal}
          dev: "binary_sensor.living_lights_{slug}_deviation"
          ts: "{{{{ now().isoformat() }}}}"
          actual_bri: "{{{{ state_attr('binary_sensor.living_lights_{slug}_deviation', 'actual_brightness_pct') | int(0) }}}}"
          actual_ct: "{{{{ state_attr('binary_sensor.living_lights_{slug}_deviation', 'actual_color_temp_kelvin') | int(0) }}}}"
          predicted_bri: "{{{{ state_attr('binary_sensor.living_lights_{slug}_deviation', 'predicted_brightness_pct') | int(0) }}}}"
          predicted_ct: "{{{{ state_attr('binary_sensor.living_lights_{slug}_deviation', 'predicted_color_temp_kelvin') | int(0) }}}}"
          tod_state: "{{{{ states('sensor.living_lights_profile') }}}}"
          shadow_mode: "{{{{ is_state('input_boolean.living_lights_shadow', 'on') }}}}"
          primary_state: "{{{{ states('{primary_sensor}') }}}}"
          tv_playing: "{{{{ states('media_player.lg_tv') in ['on', 'playing', 'paused', 'buffering'] }}}}"
          # Steam-driven gaming context (M22a-aligned). Captured into the
          # pending_preference context so downstream learning can later
          # distinguish "user preferred X while gaming" from "user preferred X
          # normally". NOT included in dedupe_key yet — would break the
          # existing Codex contract (test_contract.py line 93's 7-tuple key
          # `office|midday|present|F|F|F|T`). Downstream owner adds gaming to
          # policy_context + session_key when ready.
          gaming_active: "{{{{ is_state('input_boolean.living_lights_gaming_enabled', 'on') and state_attr('sensor.steam_steam_76561198136331341', 'game') not in [none, '', 'unavailable', 'unknown'] }}}}"
          # Working-hours context (R-WH-23 / M22a-aligned). Same rationale
          # as gaming_active: captured into pending_preference.context so
          # downstream can learn that workday-preferences are a distinct
          # policy context. NOT in dedupe_key — preserves the 7-tuple Codex
          # contract until policy_context + session_key are promoted.
          working_hours_active: "{{{{ is_state('binary_sensor.living_lights_working_hours_active', 'on') }}}}"
          asleep_state: "{{{{ is_state('input_boolean.living_lights_asleep', 'on') }}}}"
          night_safe_state: "{{{{ is_state('binary_sensor.living_lights_is_night_safe', 'on') }}}}"
          user_at_home_state: "{{{{ is_state('input_boolean.user_at_home', 'on') }}}}"
          any_occupied_state: "{{{{ is_state('binary_sensor.living_lights_any_occupied', 'on') }}}}"
          dwell_s: "{{{{ (now() - states.binary_sensor.{slug}_person_occupancy.last_changed).total_seconds() | int(0) if states.binary_sensor.{slug}_person_occupancy else 0 }}}}"
          # ── M2: occupancy-stability snapshot ─────────────────────────────
          occupancy_entity_id: "binary_sensor.{slug}_person_occupancy"
          occupancy_state: "{{{{ states('binary_sensor.{slug}_person_occupancy') }}}}"
          occupancy_last_changed: "{{{{ states.binary_sensor.{slug}_person_occupancy.last_changed.isoformat() if states.binary_sensor.{slug}_person_occupancy else none }}}}"
          occupancy_age_s: "{{{{ (now() - states.binary_sensor.{slug}_person_occupancy.last_changed).total_seconds() | int(0) if states.binary_sensor.{slug}_person_occupancy else 0 }}}}"
          # ── M4: rolling 5-min flicker count from the flicker-tracker history.
          # Reads the same input_text the tracker maintains and counts entries
          # within last 300 s. Returns int (≥ 0).
          occupancy_flicker_count_5min: >-
            {{% set ns = namespace(count=0) %}}
            {{% set raw = states('input_text.living_lights_zone_{slug}_occupancy_flicker_history') %}}
            {{% if raw and raw not in ['', 'unknown', 'unavailable', 'none', 'null'] %}}
              {{% set hist = raw | from_json %}}
              {{% if hist is iterable and hist is not string %}}
                {{% set now_ts = now().timestamp() | int %}}
                {{% set cutoff = now_ts - 300 %}}
                {{% for t in hist %}}
                  {{% if t is number and t >= cutoff %}}{{% set ns.count = ns.count + 1 %}}{{% endif %}}
                {{% endfor %}}
              {{% endif %}}
            {{% endif %}}
            {{{{ ns.count }}}}
          # ── M2: capture provenance — natural vs forced ──────────────────
          capture_trigger: "{{{{ 'vacancy_settle' if (trigger is not none and trigger.platform == 'state') else 'manual_automation_trigger' }}}}"
          trigger_entity_id: "{{{{ trigger.entity_id if (trigger is not none and trigger.platform == 'state') else none }}}}"
          trigger_from: "{{{{ trigger.from_state.state if (trigger is not none and trigger.platform == 'state' and trigger.from_state is not none) else none }}}}"
          trigger_to: "{{{{ trigger.to_state.state if (trigger is not none and trigger.platform == 'state' and trigger.to_state is not none) else none }}}}"
          trigger_for_s: "{{{{ trigger.for.total_seconds() | int(0) if (trigger is not none and trigger.platform == 'state' and trigger.for is not none) else none }}}}"
{pred_vars_block}
          # ── M2: prediction-consistency cross-check ──────────────────────
          primary_prediction_pct: "{{{{ pred_{slug}_p | int(0) }}}}"
          would_have_done_brightness_pct: "{{{{ predicted_bri | int(0) }}}}"
          prediction_mismatch_pct: "{{{{ (pred_{slug}_p | int(0)) - (predicted_bri | int(0)) }}}}"
          # ── M4: evidence_id + related override id ───────────────────────
          evidence_id: "pp-{{{{ now().timestamp() | int }}}}-{{{{ now().microsecond }}}}"
          related_override_context_id: >-
            {{% set v = states('input_text.living_lights_zone_{slug}_last_override_context_id') %}}
            {{% if v and v not in ['', 'unknown', 'unavailable', 'none', 'null'] %}}{{{{ v }}}}{{% else %}}none{{% endif %}}
          # ── M4: dedupe key + prior state read + is_duplicate flag ──────
          dedupe_window_s: 600
          dedupe_key: >-
            {slug}|{{{{ tod_state }}}}|{{{{ primary_state }}}}|{{{{ 'T' if tv_playing else 'F' }}}}|{{{{ 'T' if asleep_state else 'F' }}}}|{{{{ 'T' if night_safe_state else 'F' }}}}|{{{{ 'T' if user_at_home_state else 'F' }}}}
          dedupe_raw: "{{{{ states('input_text.living_lights_zone_{slug}_pending_dedupe_state') }}}}"
          dedupe_prior: >-
            {{% set raw = states('input_text.living_lights_zone_{slug}_pending_dedupe_state') %}}
            {{% if raw and raw not in ['', 'unknown', 'unavailable', 'none', 'null'] %}}
              {{{{ raw | from_json }}}}
            {{% else %}}
              {{{{ {{}} }}}}
            {{% endif %}}
          prior_key: "{{{{ (dedupe_prior.k if dedupe_prior is mapping and 'k' in dedupe_prior else '') }}}}"
          prior_at: "{{{{ (dedupe_prior.at if dedupe_prior is mapping and 'at' in dedupe_prior else '') }}}}"
          prior_bri: "{{{{ (dedupe_prior.bri if dedupe_prior is mapping and 'bri' in dedupe_prior else -1) | int(-1) }}}}"
          prior_wbri: "{{{{ (dedupe_prior.wbri if dedupe_prior is mapping and 'wbri' in dedupe_prior else -1) | int(-1) }}}}"
          prior_suppressed: "{{{{ (dedupe_prior.s if dedupe_prior is mapping and 's' in dedupe_prior else 0) | int(0) }}}}"
          is_duplicate: >-
            {{% if prior_key != dedupe_key %}}false
            {{% elif prior_at in ['', 'unknown', 'unavailable', 'none', 'null'] %}}false
            {{% else %}}
              {{% set prior_dt = as_datetime(prior_at) %}}
              {{% if prior_dt is none %}}false
              {{% else %}}
                {{% set age = (now() - prior_dt).total_seconds() | int(0) %}}
                {{% if age > dedupe_window_s %}}false
                {{% elif ((actual_bri | int(0)) - (prior_bri | int(0))) | abs > 3 %}}false
                {{% elif ((predicted_bri | int(0)) - (prior_wbri | int(0))) | abs > 3 %}}false
                {{% else %}}true{{% endif %}}
              {{% endif %}}
            {{% endif %}}
          # capture_suppressed_count emitted on the NEXT real emit; equals
          # the number of suppressed attempts since the last emit (which is
          # exactly prior_suppressed at the moment of this fire).
          capture_suppressed_count: "{{{{ prior_suppressed | int(0) }}}}"
          new_suppressed_count: "{{{{ (prior_suppressed | int(0)) + 1 }}}}"
          json_payload: >-
            {{{{ {{
              'kind': 'pending_preference',
              'ts': now().isoformat(),
              'evidence_id': evidence_id,
              'zone': '{slug}',
              'primary_zone': '{slug}',
              'light_entity': '{primary_light}',
              'owning_zones': owning_zones,
              'captured_state': {{'brightness_pct': actual_bri | int(0), 'color_temp_kelvin': actual_ct | int(0)}},
              'what_automation_would_have_done': {{'brightness_pct': predicted_bri | int(0), 'color_temp_kelvin': predicted_ct | int(0)}},
              'predictions_by_zone': {predictions_inline},
              'prediction_consistency': {{
                'primary_prediction_pct': primary_prediction_pct | int(0),
                'would_have_done_brightness_pct': would_have_done_brightness_pct | int(0),
                'prediction_mismatch_pct': prediction_mismatch_pct | int(0)
              }},
              'capture_provenance': {{
                'capture_trigger': capture_trigger,
                'trigger_entity_id': trigger_entity_id,
                'trigger_from': trigger_from,
                'trigger_to': trigger_to,
                'trigger_for_s': trigger_for_s
              }},
              'occupancy': {{
                'entity_id': occupancy_entity_id,
                'state': occupancy_state,
                'last_changed': occupancy_last_changed,
                'age_s': occupancy_age_s | int(0),
                'flicker_count_5min': occupancy_flicker_count_5min | int(0)
              }},
              'dedupe_key': dedupe_key,
              'dedupe_window_s': dedupe_window_s | int(0),
              'capture_suppressed_count': capture_suppressed_count | int(0),
              'related_override_context_id': (none if related_override_context_id == 'none' else related_override_context_id),
              'context': {{
                'profile': tod_state,
                'source': 'capture_on_vacant',
                'dwell_s': dwell_s | int(0),
                'state': primary_state,
                'tv_playing': tv_playing,
                'gaming_active': gaming_active,
                'working_hours_active': working_hours_active,
                'asleep': asleep_state,
                'night_safe': night_safe_state,
                'user_at_home': user_at_home_state,
                'any_occupied': any_occupied_state,
                'shadow_mode': shadow_mode
              }}
            }} | tojson }}}}
      # ── M4: branch on dedupe ──────────────────────────────────────────
      - if:
          - condition: template
            value_template: "{{{{ is_duplicate != true and is_duplicate != 'true' }}}}"
        then:
          # ── EMIT branch ─────────────────────────────────────────────
          - action: shell_command.living_lights_capture_preference
            data:
              payload: "{{{{ json_payload | base64_encode }}}}"
          - event: living_lights_preference_pending
            event_data:
              evidence_id: "{{{{ evidence_id }}}}"
              zone: "{slug}"
              primary_zone: "{slug}"
              light_entity: "{primary_light}"
              owning_zones: {owning_list_literal}
              captured_brightness_pct: "{{{{ actual_bri | int(0) }}}}"
              captured_color_temp_kelvin: "{{{{ actual_ct | int(0) }}}}"
              predicted_brightness_pct: "{{{{ predicted_bri | int(0) }}}}"
              predicted_color_temp_kelvin: "{{{{ predicted_ct | int(0) }}}}"
              predictions_by_zone:
{predictions_event_yaml}
              prediction_consistency:
                primary_prediction_pct: "{{{{ primary_prediction_pct | int(0) }}}}"
                would_have_done_brightness_pct: "{{{{ would_have_done_brightness_pct | int(0) }}}}"
                prediction_mismatch_pct: "{{{{ prediction_mismatch_pct | int(0) }}}}"
              capture_provenance:
                capture_trigger: "{{{{ capture_trigger }}}}"
                trigger_entity_id: "{{{{ trigger_entity_id }}}}"
                trigger_from: "{{{{ trigger_from }}}}"
                trigger_to: "{{{{ trigger_to }}}}"
                trigger_for_s: "{{{{ trigger_for_s }}}}"
              occupancy:
                entity_id: "{{{{ occupancy_entity_id }}}}"
                state: "{{{{ occupancy_state }}}}"
                last_changed: "{{{{ occupancy_last_changed }}}}"
                age_s: "{{{{ occupancy_age_s | int(0) }}}}"
                flicker_count_5min: "{{{{ occupancy_flicker_count_5min | int(0) }}}}"
              dedupe_key: "{{{{ dedupe_key }}}}"
              dedupe_window_s: "{{{{ dedupe_window_s | int(0) }}}}"
              capture_suppressed_count: "{{{{ capture_suppressed_count | int(0) }}}}"
              related_override_context_id: "{{{{ related_override_context_id }}}}"
              tod_state: "{{{{ tod_state }}}}"
              state: "{{{{ primary_state }}}}"
              tv_playing: "{{{{ tv_playing }}}}"
              gaming_active: "{{{{ gaming_active }}}}"
              working_hours_active: "{{{{ working_hours_active }}}}"
              asleep: "{{{{ asleep_state }}}}"
              night_safe: "{{{{ night_safe_state }}}}"
              user_at_home: "{{{{ user_at_home_state }}}}"
              any_occupied: "{{{{ any_occupied_state }}}}"
              dwell_s: "{{{{ dwell_s | int(0) }}}}"
              shadow_mode: "{{{{ shadow_mode }}}}"
          # Reset dedupe state to this emit's signature (anchors window;
          # suppressed counter resets to 0).
          - action: input_text.set_value
            target:
              entity_id: input_text.living_lights_zone_{slug}_pending_dedupe_state
            data:
              value: >-
                {{{{ {{'k': dedupe_key, 'at': now().isoformat(), 'bri': actual_bri | int(0), 'wbri': predicted_bri | int(0), 's': 0}} | tojson }}}}
        else:
          # ── SUPPRESS branch ─────────────────────────────────────────
          # Materially identical capture within dedupe_window_s. Do NOT
          # write JSONL, do NOT fire the event — just bump the counter
          # encoded in the dedupe state input_text. Keep prior_at/key/bri
          # the same so the window stays anchored to the last real emit.
          - action: input_text.set_value
            target:
              entity_id: input_text.living_lights_zone_{slug}_pending_dedupe_state
            data:
              value: >-
                {{{{ {{'k': prior_key, 'at': prior_at, 'bri': prior_bri | int(-1), 'wbri': prior_wbri | int(-1), 's': new_suppressed_count | int(0)}} | tojson }}}}
""")
    return "\n".join(blocks)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="Write the file (default: dry-run)")
    args = ap.parse_args()

    repo = Path(__file__).resolve().parent.parent
    outfile = repo / "ha-config" / "packages" / "living_lights_learning.yaml"

    zones = _light_zones()
    header = """# living_lights_learning.yaml — GENERATED by tools/build-living-lights-learning.py
#
# Per-zone deviation sensors + capture-on-vacant pending_preference
# automations. Covers all 10 light-controlled zones (was previously
# dining_left only).
#
# DEFAULT-OFF. Master toggle: input_boolean.living_lights_learning_enabled
# (declared in living_lights_observability.yaml). When OFF, deviation
# sensors still update but no JSONL records persist and no events fire.
#
# pending_preference records include shadow_mode + light_entity so the
# aggregation layer (deferred V2 work) can weight counterfactual evidence
# (shadow=true) differently from real-world overrides.
#
# DO NOT EDIT BY HAND. Edit tools/build-living-lights-learning.py + re-run.

"""

    body = (
        header
        + emit_input_numbers()
        + "\n"
        + emit_m4_helpers(zones)
        + "\n"
        + "template:\n"
        + emit_deviation_template(zones)
        + "\n"
        + emit_shell_command()
        + "\n"
        + "automation:\n"
        + emit_flicker_tracker_automations(zones)
        + emit_capture_automations(zones)
    )

    if args.apply:
        outfile.write_text(body, encoding="utf-8")
        print(f"WROTE {outfile.name} ({len(body)} bytes, {len(zones)} zones)")
    else:
        print(f"DRY-RUN {outfile.name} ({len(body)} bytes, {len(zones)} zones)")
        print(f"  Zones covered: {[z[0] for z in zones]}")
        print("  Re-run with --apply to write the file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
