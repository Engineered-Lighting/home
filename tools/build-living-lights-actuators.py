#!/usr/bin/env python3
"""build-living-lights-actuators.py — generator for the per-zone actuator
pilots (Layer 2 of the Adaptive Lighting + Living Lights activation plan).

Emits one YAML package per zone, gated by the master toggles:
  - input_boolean.living_lights_enabled       (default OFF)
  - input_boolean.living_lights_shadow        (default ON - must be OFF)
  - input_boolean.living_lights_travel_mode   (must be OFF)
  - input_boolean.living_lights_zone_<slug>_enabled (default ON)

Inert until explicitly activated.

Layer 2 — the confidence ramp. Each pilot is `mode: restart` so the ramp is
interruptible:
  - present       -> a fast reaction to ramp_initial_pct, then a slow
                     continuous ramp to predicted_brightness_pct;
  - pass_through  -> a quick path light (raises only, never dims);
  - vacant        -> ease down to the idle baseline (turn_off when 0);
  - away          -> turn every target off;
  - presence_override -> apply the override JSON brightness;
  - default (night_safe / other) -> turn on at predicted_brightness_pct.
Colour temperature: Adaptive Lighting (Layer 1) still owns the global
curve, but each pilot's `light.turn_on` data block now ALSO writes
`color_temp_kelvin` sourced from the zone's classifier
`predicted_color_temp_kelvin` (with `switch.home_adaptive_lighting_home`
fallback). This pins the bulb to the house ct on every brightness change
so per-bulb stored-ct drift can't surface between AL's 90s ticks.
Gradient-safe: the zone classifier emits the SAME ct value the gradient
package reads, so the pilot and gradient don't fight on sofa lights.
Carve-out: `light.turn_off` calls (away branch) never carry ct.

Assumes Adaptive Lighting `take_over_control: false` for ordinary generated
pilots. If that flag is ever flipped to true for a canary profile, that canary
zone must omit this generator's `color_temp_kelvin` injection so Living Lights
continues to own brightness while Adaptive Lighting owns color.

The ambient L/R switches moved to their own package
(living_lights_ambient.yaml).

Output: ha-config/packages/living_lights_pilot_<slug>.yaml per zone.
"""

from __future__ import annotations

import argparse
from pathlib import Path

# ───────────────────────── ramp / transition constants ───────────────────
RAMP_FAST_S = 2.0           # fast-reaction ramp (floor -> ramp_initial)
RAMP_SLOW_S = 10.0          # slow confidence ramp (ramp_initial -> target)
VACANT_TRANSITION_S = 6.0   # gentle ease-down on vacancy
AWAY_TRANSITION_S = 2.0     # turn-off on away
PASS_TRANSITION_S = 0.3     # quick path light
DEFAULT_TRANSITION_S = 1.5  # night_safe / other
OVERRIDE_TRANSITION_S = 0.8 # presence_override
# Phase 4 anticipation — perceptual τ (1.5s vacant→anticipated ramp). The
# anticipated branch raises ONLY (never dims a light already brighter than
# the anticipated target).
ANTICIPATED_TRANSITION_S = 1.5

# ── House-wide ct convergence (plan: keen-doodling-parasol.md → CT) ────
# Final fallback if both AL's switch attribute AND the zone classifier are
# unavailable. Matches build-living-lights-yaml.py's classifier default
# (line 342) so the system is internally consistent at startup.
HOUSE_CT_FALLBACK = 2700


def _ct_data_line(camera: str, slug: str, *, inject: bool = True) -> str:
    """Return the YAML data line that pins this light to the house ct curve.

    Source-of-truth chain (Flavor A — zone-aware):
      1. Primary: this zone's classifier `predicted_color_temp_kelvin`.
         In non-movie mode this equals AL's curve target. In movie mode it
         matches the gradient's source so pilots and gradient agree.
      2. Fallback 1: `switch.home_adaptive_lighting_home.color_temp_kelvin`
         (AL's global target).
      3. Fallback 2: literal HOUSE_CT_FALLBACK (warm-safe).

    Caller MUST only inject this on `light.turn_on` — never on `turn_off`.
    """
    if not inject:
        return "# color_temp_kelvin omitted: Adaptive Lighting canary owns color"
    sensor = f"sensor.{camera}_{slug}_lighting_state"
    # NOTE: the closing `}}"` lives in a non-f-string so the `}}` is two
    # literal closing braces (Jinja's `}}` end-of-expression). In an
    # f-string, `}}` escapes to ONE `}`, which silently produces invalid
    # template syntax that HA still renders as a string. Keep this split.
    return (
        'color_temp_kelvin: "{{ '
        f"state_attr('{sensor}', 'predicted_color_temp_kelvin') "
        f"| int(state_attr('switch.home_adaptive_lighting_home', "
        f"'color_temp_kelvin') | int({HOUSE_CT_FALLBACK})) "
        '}}"'
    )

# Per-zone light targets. Sourced from Addendum 33 Audit #B.
# Each target is a (domain, entity_id, supports_color) tuple.
# For zones with no known light, value is None — skip generation.
LIGHT_TARGETS: dict = {
    "dining_left":  [("light", "light.dining_table_left", True)],
    "dining_right": [("light", "light.dining_table_right", True)],
    "front_door":   [("light", "light.front_right", True)],
    "sink":         [("light", "light.sink", True)],
    "island_left":  [("light", "light.island_left", True)],
    "island_right": [("light", "light.island_right", True)],
    "office": [
        ("light", "light.office", True),
        # NEON-PLUG-REMOVED 2026-05-19 (Addendum 34 Step 0): the
        # smart_plug_mini_mss110 drives a NEON LIGHT whose driver has a
        # limited power-cycle lifetime. Kept manually controlled — not in
        # the new layer's path.
    ],
    "weights": [
        ("light", "light.front_right", True),
        ("light", "light.rear_right", True),
    ],
    "front_left": [("light", "light.front_left", True)],

    # Sofa zone — the 4 dimmable lights over/around the sofa. The 2 ambient
    # smart switches moved to ha-config/packages/living_lights_ambient.yaml
    # (Layer 2d) — they are on/off only and follow movie/away/asleep, not
    # the confidence ramp.
    # rear_right is ALSO targeted by the weights zone — the co-controller
    # vacant guard prevents the two zones fighting.
    "sofa": [
        ("light", "light.front_left",  True),
        ("light", "light.front_right", True),
        ("light", "light.rear_left",   True),
        ("light", "light.rear_right",  True),
    ],
    "whole_living_room": None,
    "whole_dining_room": None,
    "whole_kitchen":     None,
    "workshop_zone":     None,
    "e28":               None,
}

ZONE_CAMERA: dict = {
    "dining_left":   "dining_room",
    "dining_right":  "dining_room",
    "sink":          "kitchen",
    "island_left":   "kitchen",
    "island_right":  "kitchen",
    "sofa":              "living_room",
    "front_left":        "living_room",
    "weights":           "living_room",
    "office":            "living_room",
    "front_door":        "living_room",
    "whole_living_room": "living_room",
    "workshop_zone":     "workshop",
    "e28":               "driveway",
    "whole_dining_room": "dining_room",
    "whole_kitchen":     "kitchen",
}


# @@TOKEN@@ placeholders are substituted by str.replace — Jinja {% %} / {{ }}
# stay literal, so there is no brace-escaping to get wrong.
_TEMPLATE = r"""# living_lights_pilot_@@SLUG@@.yaml — GENERATED by tools/build-living-lights-actuators.py
#
# Actuator pilot for zone "@@SLUG@@" (camera=@@CAMERA@@).
# Triggers on @@SENSOR_ID@@.
# Targets: @@TARGET_LIST@@
#
# Layer 2 — the confidence ramp. mode:restart so the ramp is interruptible.
#
# INERT until all master gates allow actuation:
#   input_boolean.living_lights_enabled = on
#   input_boolean.living_lights_shadow = off
#   input_boolean.living_lights_travel_mode = off
#   input_boolean.living_lights_zone_@@SLUG@@_enabled = on
#
# Rollback: delete this file + reload automations.

automation:
  - alias: "Living Lights — @@SLUG@@ actuator"
    id: living_lights_actuator_@@SLUG@@
    description: >
      Actuates @@TARGET_LIST@@ from @@SENSOR_ID@@ via the confidence ramp.
      Inert until all master gates allow actuation.
    mode: restart
    triggers:
      # occupancy state changes (vacant / present / pass_through / away ...)
      - trigger: state
        entity_id: @@SENSOR_ID@@
      # Direct Frigate occupancy wakeups. These make presence response robust
      # even if the derived classifier sensor's state trigger is delayed or
      # swallowed during a burst of template updates.
      - trigger: state
        entity_id: @@STABLE_ENTITY@@
      - trigger: state
        entity_id: @@RAW_OCCUPANCY_ENTITY@@
      # target-brightness changes — the movie / asleep / time-of-day
      # modifiers move predicted_brightness_pct without changing the state.
      - trigger: state
        entity_id: @@SENSOR_ID@@
        attribute: predicted_brightness_pct
      # asleep flips can change the effective target even when the classifier
      # remains present/vacant, so wake the pilot immediately.
      - trigger: state
        entity_id: input_boolean.living_lights_asleep
      # cutover — apply the current target the instant shadow mode goes off.
      - trigger: state
        entity_id: input_boolean.living_lights_shadow
        to: "off"
      # Travel mode leaving should re-evaluate the current target; travel
      # mode entering is blocked by the condition below and by the force-off
      # package.
      - trigger: state
        entity_id: input_boolean.living_lights_travel_mode
      # Periodic re-evaluation so cooldown-expiry isn't gated on a presence
      # event. The pilot is mode:restart and exits early when no work is to
      # be done; re-evaluation is cheap.
      - trigger: time_pattern
        minutes: "/5"
    conditions:
      - condition: state
        entity_id: input_boolean.living_lights_enabled
        state: "on"
      - condition: state
        entity_id: input_boolean.living_lights_shadow
        state: "off"
      - condition: state
        entity_id: input_boolean.living_lights_travel_mode
        state: "off"
      - condition: state
        entity_id: input_boolean.living_lights_zone_@@SLUG@@_enabled
        state: "on"
      # Trigger-template sensors can emit state_changed events where the
      # classifier state and target brightness did not actually change. Do
      # not re-issue actuator calls for that churn; the dedicated
      # predicted_brightness_pct attribute trigger and the real state-change
      # trigger still handle meaningful target changes.
      - condition: template
        value_template: >-
          {% set eid = trigger.entity_id | default('', true) %}
          {% set attr = trigger.attribute | default(none, true) %}
          {% if eid == '@@SENSOR_ID@@'
                and attr in [none, '', 'None']
                and trigger.from_state is not none
                and trigger.to_state is not none
                and trigger.from_state.state == trigger.to_state.state %}
            false
          {% else %}true{% endif %}
      # Manual-touch cooldown gate (R5-aligned). Reads input_datetime directly
      # to avoid the 5-s classifier-attribute lag.
      #   - cooldown applies ONLY to states {vacant, present, pass_through, asleep}.
      #     Explicit/override states (away, presence_override, manual_override,
      #     night_safe, anticipated) bypass, but asleep does NOT. A manual
      #     touch while the house thinks the user is asleep must still stick.
      #   - sentinel year < 2000 (e.g., 1970-01-01) means the vacancy-expire
      #     or voice-override-reset automation cleared the manual.
      #   - in-zone + non-expired manual → block (manual sticks while in zone).
      #   - out-of-zone → respect 15 min from MAX(manual_dt, zone-leave-time).
      - condition: template
        value_template: >-
          {% set state = states('@@SENSOR_ID@@') %}
          {% if state not in ['vacant', 'present', 'pass_through', 'asleep'] %}true
          {% else %}
            {% set lm = states('input_datetime.living_lights_zone_@@SLUG@@_last_manual_at') %}
            {% if not lm or lm in ['unknown', 'unavailable', 'none', ''] %}true
            {% else %}
              {# input_datetime renders as a naive string; as_local attaches the local tz so
                 the comparisons below against last_changed (UTC-aware) and now() (local-aware)
                 don't raise TypeError: can't compare offset-naive and offset-aware datetimes. #}
              {% set manual_dt = as_datetime(lm | string) | as_local %}
              {% if manual_dt.year < 2000 %}true
              {% else %}
                {% set stable_obj = states.@@STABLE_ENTITY@@ %}
                {% if stable_obj is none %}true
                {% elif stable_obj.state == 'on' %}false
                {% else %}
                  {% set leave_time = stable_obj.last_changed %}
                  {{ (now() - ([manual_dt, leave_time] | max)).total_seconds() > 900 }}
                {% endif %}
              {% endif %}
            {% endif %}
          {% endif %}
    actions:
      - if:
          - condition: template
            value_template: "{{ trigger.entity_id in ['@@STABLE_ENTITY@@', '@@RAW_OCCUPANCY_ENTITY@@'] }}"
        then:
          - delay: "00:00:00.25"
      - variables:
          # Read the classifier state directly — not trigger.to_state — so the
          # pilot is correct whichever trigger fired (state / attribute / shadow).
          new_state: "{{ states('@@SENSOR_ID@@') }}"
          predicted_bri: "{{ state_attr('@@SENSOR_ID@@', 'predicted_brightness_pct') | int(0) }}"
          ramp_initial: "{{ state_attr('@@SENSOR_ID@@', 'ramp_initial_pct') | int(0) }}"
          safe_initial: "{{ [ramp_initial, predicted_bri] | min }}"
      - if:
          - condition: template
            value_template: "{{ new_state not in ['manual_override', 'unknown', 'unavailable'] }}"
        then:
          - choose:
              # vacant — ease down to the idle baseline (per-entity, with
              # co-controller guards; turn_off when the floor is 0).
              - conditions:
                  - condition: template
                    value_template: "{{ new_state == 'vacant' }}"
                sequence:
@@VACANT_ACTIONS@@
              # away — turn every target off.
              - conditions:
                  - condition: template
                    value_template: "{{ new_state == 'away' }}"
                sequence:
@@AWAY_ACTIONS@@
              # presence_override — apply the override JSON brightness.
              - conditions:
                  - condition: template
                    value_template: "{{ new_state == 'presence_override' }}"
                sequence:
@@OVERRIDE_ACTIONS@@
              # present — the confidence ramp (fast reaction, then slow ramp).
              - conditions:
                  - condition: template
                    value_template: "{{ new_state == 'present' }}"
                sequence:
@@PRESENT_ACTIONS@@
              # pass_through — a quick path light (raises only).
              - conditions:
                  - condition: template
                    value_template: "{{ new_state == 'pass_through' }}"
                sequence:
@@PASS_ACTIONS@@
              # anticipated — pre-warm for a person walking TOWARD this room
              # (Phase 4). Raises only (never dims a light already brighter)
              # over a 1.5 s perceptual ramp. The classifier-side kill switch
              # input_boolean.living_lights_anticipated_enabled prevents this
              # branch from ever being selected when OFF (the state stays at
              # `vacant`), so no separate gate is needed here.
              - conditions:
                  - condition: template
                    value_template: "{{ new_state == 'anticipated' }}"
                sequence:
@@ANTICIPATED_ACTIONS@@
            # default — night_safe and any other non-vacant state.
            default:
@@DEFAULT_ACTIONS@@
"""


def _build_co_controllers_map() -> dict:
    """Inverted map: light_entity_id -> list of (zone_slug, camera) of zones
    that target this light. Drives the per-light vacant guard: a zone's
    vacant action skips a light where any co-controlling zone is non-vacant."""
    inverted = {}
    for zone_slug, targets in LIGHT_TARGETS.items():
        if not targets:
            continue
        camera = ZONE_CAMERA[zone_slug]
        for tup in targets:
            domain, eid = tup[0], tup[1]
            if domain != "light":
                continue
            inverted.setdefault(eid, []).append((zone_slug, camera))
    return inverted


CO_CONTROLLERS = _build_co_controllers_map()


# Room/group entities that external controls commonly target (HomeKit/Siri,
# Lutron keypads, or broad Home Assistant service calls). These entities are
# not actuator targets themselves, but their state changes still express user
# intent and must arm a manual hold before the per-zone pilots can revert them.
AGGREGATE_LIGHT_CONTROLLERS = {
    "light.kitchen": [
        ("sink", "kitchen"),
        ("island_left", "kitchen"),
        ("island_right", "kitchen"),
    ],
    "light.kitchen_lights": [
        ("sink", "kitchen"),
        ("island_left", "kitchen"),
        ("island_right", "kitchen"),
    ],
    "light.dining_room": [
        ("dining_left", "dining_room"),
        ("dining_right", "dining_room"),
    ],
    "light.dining_room_lights": [
        ("dining_left", "dining_room"),
        ("dining_right", "dining_room"),
    ],
    "light.living_room_lights": [
        ("sofa", "living_room"),
        ("office", "living_room"),
    ],
}


def _per_entity_action_block(action, entity_ids, data_lines, indent) -> str:
    """A sequence of N per-entity action blocks (NOT one batched call with N
    entity_ids) — defence vs the Hue Bridge silent-drop bug."""
    pad = " " * indent
    pad_inner = " " * (indent + 2)
    pad_data = " " * (indent + 4)
    blocks = []
    for eid in entity_ids:
        lines = [
            f"{pad}- action: {action}",
            f"{pad_inner}target:",
            f"{pad_data}entity_id: {eid}",
        ]
        if data_lines:
            lines.append(f"{pad_inner}data:")
            for dl in data_lines:
                lines.append(f"{pad_data}{dl}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def _manual_cooldown_gate_template(sensor_id, slug, stable_entity, indent) -> str:
    """Jinja truth test used by actuators to decide whether automatic control
    may run while a manual touch is still hot."""
    ind = " " * indent
    stable_expr = "states." + stable_entity
    lines = [
        ind + "{% set state = states('" + sensor_id + "') %}",
        ind + "{% if state not in ['vacant', 'present', 'pass_through', 'asleep'] %}true",
        ind + "{% else %}",
        ind + "  {% set lm = states('input_datetime.living_lights_zone_" + slug + "_last_manual_at') %}",
        ind + "  {% if not lm or lm in ['unknown', 'unavailable', 'none', ''] %}true",
        ind + "  {% else %}",
        ind + "    {% set manual_dt = as_datetime(lm | string) | as_local %}",
        ind + "    {% if manual_dt.year < 2000 %}true",
        ind + "    {% else %}",
        ind + "      {% set stable_obj = " + stable_expr + " %}",
        ind + "      {% if stable_obj is none %}true",
        ind + "      {% elif stable_obj.state == 'on' %}false",
        ind + "      {% else %}",
        ind + "        {% set leave_time = stable_obj.last_changed %}",
        ind + "        {{ (now() - ([manual_dt, leave_time] | max)).total_seconds() > 900 }}",
        ind + "      {% endif %}",
        ind + "    {% endif %}",
        ind + "  {% endif %}",
        ind + "{% endif %}",
    ]
    return "\n".join(lines)


def _floor_or_off(eid, ct_line, indent) -> str:
    """`if predicted_bri > 0` -> turn_on at the floor, else turn_off. The
    `- if:` dash sits at column `indent`. `ct_line` is injected on the
    turn_on branch only (turn_off rejects color_temp_kelvin)."""
    i = " " * indent
    i2 = " " * (indent + 2)
    i4 = " " * (indent + 4)
    i6 = " " * (indent + 6)
    i8 = " " * (indent + 8)
    return "\n".join([
        i + "- if:",
        i4 + "- condition: template",
        i6 + 'value_template: "{{ predicted_bri > 0 }}"',
        i2 + "then:",
        i4 + "- action: light.turn_on",
        i6 + "target:",
        i8 + "entity_id: " + eid,
        i6 + "data:",
        i8 + 'brightness_pct: "{{ [predicted_bri, 1] | max }}"',
        i8 + ct_line,
        i8 + "transition: " + str(VACANT_TRANSITION_S),
        i2 + "else:",
        i4 + "- action: light.turn_off",
        i6 + "target:",
        i8 + "entity_id: " + eid,
        i6 + "data:",
        i8 + "transition: " + str(VACANT_TRANSITION_S),
    ])


def _vacant_block(light_targets, this_zone, co_map, ct_line, indent) -> str:
    """The vacant branch — per-entity `floor-or-off`, with co-controller
    guards on lights shared by another zone (skip if that zone is busy).
    `ct_line` flows through to the floor-or-off turn_on branch."""
    blocks = []
    for tup in light_targets:
        eid = tup[1]
        others = [(z, c) for (z, c) in co_map.get(eid, []) if z != this_zone]
        if not others:
            blocks.append(_floor_or_off(eid, ct_line, indent))
        else:
            i = " " * indent
            i2 = " " * (indent + 2)
            i4 = " " * (indent + 4)
            i6 = " " * (indent + 6)
            i8 = " " * (indent + 8)
            lines = [
                i + "- if:",
                i4 + "- condition: template",
                i6 + "value_template: >-",
                i8 + "{% set ns = namespace(any_active=false) %}",
            ]
            for (z, c) in others:
                s = "sensor." + c + "_" + z + "_lighting_state"
                lines.append(
                    i8 + "{% if states('" + s + "') != 'vacant' %}"
                    "{% set ns.any_active = true %}{% endif %}")
            lines.append(i8 + "{{ not ns.any_active }}")
            lines.append(i2 + "then:")
            lines.append(_floor_or_off(eid, ct_line, indent + 4))
            blocks.append("\n".join(lines))
    return "\n".join(blocks)


def _present_block(light_entity_ids, ct_line, indent, sensor_id, slug, stable_entity) -> str:
    """The confidence ramp: a conditional fast call (skipped when the zone
    is already bright — idempotent re-trigger), a delay, then the slow ramp
    to the target. `mode: restart` makes the whole thing interruptible.
    Both the fast and slow ramps carry `ct_line` so each call re-pins the
    house ct."""
    i = " " * indent
    i2 = " " * (indent + 2)
    i4 = " " * (indent + 4)
    i6 = " " * (indent + 6)
    bris = ", ".join(
        "(state_attr('" + e + "', 'brightness') | float(0)) / 2.55"
        for e in light_entity_ids)
    lines = [
        i + "- variables:",
        i4 + 'zone_max_bri: "{{ [' + bris + '] | max }}"',
        i + "- if:",
        i4 + "- condition: template",
        i6 + 'value_template: "{{ zone_max_bri < safe_initial }}"',
        i2 + "then:",
        # call 1 — fast reaction to the initial level (inside then:, dash @ +4)
        _per_entity_action_block(
            "light.turn_on", light_entity_ids,
            ['brightness_pct: "{{ [safe_initial, 1] | max }}"',
             ct_line,
             "transition: " + str(RAMP_FAST_S)],
            indent + 4),
        i4 + "- delay:",
        i6 + "  seconds: " + str(RAMP_FAST_S),
    ]
    # A physical/Siri/Lutron change can arrive during the fast-ramp delay.
    # Re-run the cooldown gate before the slow command so that new manual hold
    # cancels the pending automatic correction instead of dimming the user back
    # down two seconds later.
    lines += [
        i + "- condition: template",
        i2 + "value_template: >-",
        _manual_cooldown_gate_template(sensor_id, slug, stable_entity, indent + 4),
    ]
    # call 2 — slow confidence ramp to the target (branch top level, dash @ i)
    lines.append(_per_entity_action_block(
        "light.turn_on", light_entity_ids,
        ['brightness_pct: "{{ [predicted_bri, 1] | max }}"',
         ct_line,
         "transition: " + str(RAMP_SLOW_S)],
        indent))
    return "\n".join(lines)


def _pass_block(light_entity_ids, ct_line, indent) -> str:
    """pass_through — a quick path light. Per-entity, raises only (never dims
    a light already brighter). `ct_line` injected so each per-entity call
    also re-pins the house ct."""
    i = " " * indent
    i2 = " " * (indent + 2)
    i4 = " " * (indent + 4)
    blocks = []
    for e in light_entity_ids:
        blocks.append("\n".join([
            i + "- action: light.turn_on",
            i2 + "target:",
            i4 + "entity_id: " + e,
            i2 + "data:",
            i4 + 'brightness_pct: "{{ [predicted_bri, '
                 "(state_attr('" + e + "', 'brightness') | float(0)) / 2.55]"
                 ' | max | round(0) | int }}"',
            i4 + ct_line,
            i4 + "transition: " + str(PASS_TRANSITION_S),
        ]))
    return "\n".join(blocks)


def _anticipated_block(light_entity_ids, ct_line, indent) -> str:
    """anticipated — pre-warm a vacant room because someone is walking toward
    it (Phase 4). Same raise-only semantics as pass_through: max(predicted,
    current) so we never DIM a light that's already brighter (e.g. manually
    set just before walking back in). Slower 1.5 s perceptual ramp instead of
    pass_through's 0.3 s flash. The killswitch
    input_boolean.living_lights_anticipated_enabled lives in the classifier
    upstream — when OFF the state never reaches `anticipated`, so this branch
    is unreachable. `ct_line` re-pins the house ct on each pre-warm call."""
    i = " " * indent
    i2 = " " * (indent + 2)
    i4 = " " * (indent + 4)
    blocks = []
    for e in light_entity_ids:
        blocks.append("\n".join([
            i + "- action: light.turn_on",
            i2 + "target:",
            i4 + "entity_id: " + e,
            i2 + "data:",
            i4 + 'brightness_pct: "{{ [predicted_bri, '
                 "(state_attr('" + e + "', 'brightness') | float(0)) / 2.55]"
                 ' | max | round(0) | int }}"',
            i4 + ct_line,
            i4 + "transition: " + str(ANTICIPATED_TRANSITION_S),
        ]))
    return "\n".join(blocks)


def _override_block(slug, light_entity_ids, ct_line, indent) -> str:
    """presence_override — read the override JSON, apply its brightness.
    `ct_line` injects the house ct so an explicit-override brightness still
    converges to the house curve (the override JSON could be extended later
    with its own `color_temp_kelvin` field; for now, override = brightness
    only, ct = house)."""
    i = " " * indent
    i4 = " " * (indent + 4)
    vars_block = "\n".join([
        i + "- variables:",
        i4 + "ov_text: \"{{ states('input_text.living_lights_override_text_"
             + slug + "') }}\"",
        i4 + 'ov_obj: "{{ ov_text | from_json if ov_text else {} }}"',
        i4 + "ov_bri: \"{{ ov_obj.get('brightness_pct', 50) | int(50) }}\"",
    ])
    turn_on = _per_entity_action_block(
        "light.turn_on", light_entity_ids,
        ['brightness_pct: "{{ [ov_bri, 1] | max }}"',
         ct_line,
         "transition: " + str(OVERRIDE_TRANSITION_S)],
        indent)
    return vars_block + "\n" + turn_on


def emit_actuator(slug: str, targets: list, *, omit_ct_zones: set[str] | None = None) -> str:
    camera = ZONE_CAMERA[slug]
    sensor_id = f"sensor.{camera}_{slug}_lighting_state"
    stable_entity = f"binary_sensor.{camera}_{slug}_person_occupancy_stable"
    raw_occupancy_entity = f"binary_sensor.{slug}_person_occupancy"
    # House-wide ct convergence data line. Pinned to THIS zone's classifier
    # so the pilot and gradient agree on sofa lights during movie mode
    # (gradient reads the same predicted_color_temp_kelvin). One template,
    # used on every turn_on emission below — NEVER on the away turn_off.
    omit_ct_zones = omit_ct_zones or set()
    ct_line = _ct_data_line(camera, slug, inject=slug not in omit_ct_zones)

    light_targets = [t for t in targets if t[0] == "light"]
    light_entity_ids = [t[1] for t in light_targets]

    vacant_actions = _vacant_block(light_targets, slug, CO_CONTROLLERS, ct_line, 18)
    away_actions = _per_entity_action_block(
        "light.turn_off", light_entity_ids,
        ["transition: " + str(AWAY_TRANSITION_S)], 18)
    override_actions = _override_block(slug, light_entity_ids, ct_line, 18)
    present_actions = _present_block(light_entity_ids, ct_line, 18, sensor_id, slug, stable_entity)
    pass_actions = _pass_block(light_entity_ids, ct_line, 18)
    anticipated_actions = _anticipated_block(light_entity_ids, ct_line, 18)
    default_actions = _per_entity_action_block(
        "light.turn_on", light_entity_ids,
        ['brightness_pct: "{{ [predicted_bri, 1] | max }}"',
         ct_line,
         "transition: " + str(DEFAULT_TRANSITION_S)], 14)

    return (_TEMPLATE
            .replace("@@SENSOR_ID@@", sensor_id)
            .replace("@@CAMERA@@", camera)
            .replace("@@STABLE_ENTITY@@", stable_entity)
            .replace("@@RAW_OCCUPANCY_ENTITY@@", raw_occupancy_entity)
            .replace("@@TARGET_LIST@@", str(light_entity_ids))
            .replace("@@VACANT_ACTIONS@@", vacant_actions)
            .replace("@@AWAY_ACTIONS@@", away_actions)
            .replace("@@OVERRIDE_ACTIONS@@", override_actions)
            .replace("@@PRESENT_ACTIONS@@", present_actions)
            .replace("@@PASS_ACTIONS@@", pass_actions)
            .replace("@@ANTICIPATED_ACTIONS@@", anticipated_actions)
            .replace("@@DEFAULT_ACTIONS@@", default_actions)
            .replace("@@SLUG@@", slug))


# ────────────────────────── manual-touch detection ──────────────────────
# R5 / converged-plan First PR scope. The detection automation watches every
# light's brightness (with on/off coverage too), compares actual to all owning
# zones' predicted_brightness_pct AND ramp-window AND sofa-gradient (for sofa
# lights). Mismatches fire `living_lights_override_detected` HA bus event
# and (gated by living_lights_learning_enabled) append to the preference JSONL.

# Lights targeted by the sofa zone get an additional gradient-match branch so
# the gradient layer's divergent per-light brightness doesn't false-positive
# as a manual touch. Key in sensor.living_room_sofa_gradient attributes.
SOFA_GRADIENT_KEYS = {
    "light.front_left":  "front_left_pct",
    "light.front_right": "front_right_pct",
    "light.rear_left":   "rear_left_pct",
    "light.rear_right":  "rear_right_pct",
}

# Match tolerance (percentage points). Real manual touches are typically 30-80
# pp off predicted; in-flight ramp truncations are 10-15 pp off.
MATCH_TOLERANCE_PCT = 10
RAMP_WINDOW_PAD_PCT = 5    # extra pad on each side of the ramp envelope


def _emit_match_ok_template(light_entity, owning_zones, has_gradient, indent=12):
    """Self-contained Jinja returning ns.ok (boolean). Self-contained = no
    cross-variable references; can be evaluated independently."""
    ind = " " * indent
    lines = [
        ind + "{% set ns = namespace(ok=false) %}",
        ind + "{% set ap = ((state_attr('" + light_entity + "', 'brightness') | float(0)) / 2.55) | round(0) | int if is_state('" + light_entity + "', 'on') else 0 %}",
    ]
    for (slug, camera) in owning_zones:
        sensor = f"sensor.{camera}_{slug}_lighting_state"
        lines += [
            ind + "{% if not ns.ok %}",
            ind + "  {% set p = state_attr('" + sensor + "', 'predicted_brightness_pct') | float(-1) %}",
            ind + "  {% set ri = state_attr('" + sensor + "', 'ramp_initial_pct') | float(-1) %}",
            ind + "  {% set st = states('" + sensor + "') %}",
            ind + "  {% if p < 0 %}{% set ns.ok = true %}",
            ind + f"  {{% elif (ap - p) | abs <= {MATCH_TOLERANCE_PCT} %}}{{% set ns.ok = true %}}",
            ind + f"  {{% elif st == 'present' and ri >= 0 and ap >= ([ri, p] | min - {RAMP_WINDOW_PAD_PCT}) and ap <= ([ri, p] | max + {RAMP_WINDOW_PAD_PCT}) %}}{{% set ns.ok = true %}}",
            ind + "  {% endif %}",
            ind + "{% endif %}",
        ]
    if has_gradient:
        gradient_key = SOFA_GRADIENT_KEYS[light_entity]
        lines += [
            ind + "{% if not ns.ok %}",
            ind + "  {% set g = state_attr('sensor.living_room_sofa_gradient', '" + gradient_key + "') | float(-1) %}",
            ind + f"  {{% if g >= 0 and (ap - g) | abs <= {MATCH_TOLERANCE_PCT} %}}{{% set ns.ok = true %}}{{% endif %}}",
            ind + "{% endif %}",
        ]
    lines.append(ind + "{{ ns.ok }}")
    return "\n".join(lines)


def _emit_match_reason_template(light_entity, owning_zones, has_gradient, indent=12):
    """Self-contained Jinja returning ns.reason (string). Duplicates the match
    logic from _emit_match_ok_template because HA variables: render to scalars,
    so we cannot share a dict object between two consumers — each variable
    template must be independently complete."""
    ind = " " * indent
    lines = [
        ind + "{% set ns = namespace(ok=false, reason='none') %}",
        ind + "{% set ap = ((state_attr('" + light_entity + "', 'brightness') | float(0)) / 2.55) | round(0) | int if is_state('" + light_entity + "', 'on') else 0 %}",
    ]
    for (slug, camera) in owning_zones:
        sensor = f"sensor.{camera}_{slug}_lighting_state"
        lines += [
            ind + "{% if not ns.ok %}",
            ind + "  {% set p = state_attr('" + sensor + "', 'predicted_brightness_pct') | float(-1) %}",
            ind + "  {% set ri = state_attr('" + sensor + "', 'ramp_initial_pct') | float(-1) %}",
            ind + "  {% set st = states('" + sensor + "') %}",
            ind + "  {% if p < 0 %}{% set ns.ok = true %}{% set ns.reason = 'classifier_unavailable_" + slug + "' %}",
            ind + f"  {{% elif (ap - p) | abs <= {MATCH_TOLERANCE_PCT} %}}{{% set ns.ok = true %}}{{% set ns.reason = 'matches_predicted_{slug}' %}}",
            ind + f"  {{% elif st == 'present' and ri >= 0 and ap >= ([ri, p] | min - {RAMP_WINDOW_PAD_PCT}) and ap <= ([ri, p] | max + {RAMP_WINDOW_PAD_PCT}) %}}{{% set ns.ok = true %}}{{% set ns.reason = 'in_ramp_window_{slug}' %}}",
            ind + "  {% endif %}",
            ind + "{% endif %}",
        ]
    if has_gradient:
        gradient_key = SOFA_GRADIENT_KEYS[light_entity]
        lines += [
            ind + "{% if not ns.ok %}",
            ind + "  {% set g = state_attr('sensor.living_room_sofa_gradient', '" + gradient_key + "') | float(-1) %}",
            ind + f"  {{% if g >= 0 and (ap - g) | abs <= {MATCH_TOLERANCE_PCT} %}}{{% set ns.ok = true %}}{{% set ns.reason = 'matches_sofa_gradient' %}}{{% endif %}}",
            ind + "{% endif %}",
        ]
    lines.append(ind + "{{ ns.reason }}")
    return "\n".join(lines)


def _emit_per_zone_scalar_vars(owning_zones, indent=10):
    """Emit per-zone scalar variables in a `variables:` block. Each owning
    zone gets pred_<slug>_state, pred_<slug>_p, pred_<slug>_ri."""
    ind = " " * indent
    lines = []
    for (slug, camera) in owning_zones:
        sensor = f"sensor.{camera}_{slug}_lighting_state"
        lines += [
            ind + f"pred_{slug}_state: \"{{{{ states('{sensor}') }}}}\"",
            ind + f"pred_{slug}_p: \"{{{{ state_attr('{sensor}', 'predicted_brightness_pct') | int(0) }}}}\"",
            ind + f"pred_{slug}_ri: \"{{{{ state_attr('{sensor}', 'ramp_initial_pct') | int(0) }}}}\"",
            ind + f"pred_{slug}_ct: \"{{{{ state_attr('{sensor}', 'predicted_color_temp_kelvin') | int(0) }}}}\"",
        ]
    return "\n".join(lines)


def _emit_event_predictions_by_zone(owning_zones, indent=14):
    """Build event_data.predictions_by_zone as an inline YAML mapping. HA
    serializes this as a proper mapping (not a templated string) since each
    leaf value is a discrete scalar template."""
    ind = " " * indent
    ind2 = " " * (indent + 2)
    lines = []
    for (slug, _camera) in owning_zones:
        lines += [
            ind + f"{slug}:",
            ind2 + f"state: \"{{{{ pred_{slug}_state }}}}\"",
            ind2 + f"predicted_pct: \"{{{{ pred_{slug}_p | int(0) }}}}\"",
            ind2 + f"ramp_initial_pct: \"{{{{ pred_{slug}_ri | int(0) }}}}\"",
        ]
    return "\n".join(lines)


def _emit_last_manual_targets(owning_zones, indent=14):
    """YAML list of input_datetime entity_ids to update for every owning zone."""
    ind = " " * indent
    lines = []
    for (slug, _camera) in owning_zones:
        lines.append(ind + f"- input_datetime.living_lights_zone_{slug}_last_manual_at")
    return "\n".join(lines)


def _emit_last_override_ctx_targets(owning_zones, indent=14):
    """M4: YAML list of input_text entity_ids that store the last
    override_event's context_id per owning zone. capture-on-vacant reads
    these to populate related_override_context_id on pending_preference."""
    ind = " " * indent
    lines = []
    for (slug, _camera) in owning_zones:
        lines.append(ind + f"- input_text.living_lights_zone_{slug}_last_override_context_id")
    return "\n".join(lines)


def _emit_last_command_targets(owning_zones, indent=14):
    """YAML list of input_text entity_ids that carry the most recent
    explicit lighting command id for every owning zone."""
    ind = " " * indent
    lines = []
    for (slug, _camera) in owning_zones:
        lines.append(ind + f"- input_text.living_lights_zone_{slug}_last_command_id")
    return "\n".join(lines)


def _emit_light_transition_dict_lines(indent=20):
    """Jinja dict fragment for a raw HA light transition.

    Stored on override_event so UI/debug surfaces can distinguish the actual
    bulb transition from the system target.
    """
    ind = " " * indent
    return "\n".join([
        ind + "'light_transition': {",
        ind + "  'trigger_attribute': trigger.attribute | default(none, true),",
        ind + "  'from': {",
        ind + "    'state': trigger.from_state.state | default(none, true),",
        ind + "    'brightness': trigger.from_state.attributes.brightness | default(none, true),",
        ind + "    'color_temp_kelvin': trigger.from_state.attributes.color_temp_kelvin | default(none, true),",
        ind + "    'color_temp': trigger.from_state.attributes.color_temp | default(none, true),",
        ind + "    'color_mode': trigger.from_state.attributes.color_mode | default(none, true),",
        ind + "    'effect': trigger.from_state.attributes.effect | default(none, true)",
        ind + "  },",
        ind + "  'to': {",
        ind + "    'state': trigger.to_state.state | default(none, true),",
        ind + "    'brightness': trigger.to_state.attributes.brightness | default(none, true),",
        ind + "    'color_temp_kelvin': trigger.to_state.attributes.color_temp_kelvin | default(none, true),",
        ind + "    'color_temp': trigger.to_state.attributes.color_temp | default(none, true),",
        ind + "    'color_mode': trigger.to_state.attributes.color_mode | default(none, true),",
        ind + "    'effect': trigger.to_state.attributes.effect | default(none, true)",
        ind + "  },",
        ind + "  'ha_context': {",
        ind + "    'id': trigger.to_state.context.id | default(none, true),",
        ind + "    'parent_id': trigger.to_state.context.parent_id | default(none, true),",
        ind + "    'user_id': trigger.to_state.context.user_id | default(none, true)",
        ind + "  }",
        ind + "},",
    ])


def _emit_jsonl_payload_block(light_entity, camera, owning_zones, indent=18):
    """Build the JSON payload that gets base64-encoded and appended to the
    preferences JSONL. Single tojson-piped Jinja expression so the whole
    payload is unambiguously a JSON string at the end. Predictions_by_zone
    is built inline using the per-zone scalar variables."""
    ind = " " * indent
    primary_slug = owning_zones[0][0]
    owning_list = "[" + ", ".join(f"'{s}'" for (s, _c) in owning_zones) + "]"
    preds_dict_lines = []
    for (slug, _camera) in owning_zones:
        preds_dict_lines.append(
            ind + f"    '{slug}': {{'state': pred_{slug}_state, "
                  f"'predicted_pct': pred_{slug}_p | int(0), "
                  f"'ramp_initial_pct': pred_{slug}_ri | int(0)}},"
        )
    lines = [
        ind + "{{ {",
        ind + "  'kind': 'override_event',",
        ind + "  'ts': now().isoformat(),",
        ind + "  'context_id': ctx_id,",
        ind + f"  'zone': '{primary_slug}',",
        ind + f"  'light_entity': '{light_entity}',",
        ind + f"  'camera': '{camera}',",
        ind + f"  'owning_zones': {owning_list},",
        ind + "  'observed': {'brightness_pct': actual_pct | int(0), 'light_state': light_state},",
        ind + "  'predicted': {",
        ind + f"    'brightness_pct': pred_{primary_slug}_p | int(0),",
        ind + f"    'color_temp_kelvin': pred_{primary_slug}_ct | int(0)",
        ind + "  },",
        ind + f"  'delta_pct': (actual_pct | int(0)) - (pred_{primary_slug}_p | int(0)),",
        ind + "  'predictions_by_zone': {",
        *preds_dict_lines,
        ind + "  },",
        _emit_light_transition_dict_lines(indent + 2),
        ind + "  'context': {",
        ind + f"    'state': pred_{primary_slug}_state,",
        ind + "    'profile': profile_state,",
        ind + "    'tv_playing': tv_playing,",
        ind + "    'gaming_active': gaming_active,",
        ind + "    'working_hours_active': working_hours_active,",
        ind + "    'asleep': asleep_state,",
        ind + "    'night_safe': night_safe_state,",
        ind + "    'user_at_home': user_at_home_state,",
        ind + "    'anticipated_room': none,",
        ind + "    'any_occupied': any_occupied_state,",
        ind + "    'shadow_mode': shadow_mode,",
        ind + "    'source_user_id': trigger.to_state.context.user_id | default(none, true)",
        ind + "  },",
        ind + "  'debug_match_reason': match_reason",
        ind + "} | tojson }}",
    ]
    return "\n".join(lines)


def _emit_activity_jsonl_payload_block(light_entity, camera, owning_zones, indent=18):
    """Build a raw lighting activity ledger event for every material tracked
    light transition. This is metadata-only evidence; learning happens later
    after Intelligence collapses raw changes into episodes."""
    ind = " " * indent
    primary_slug = owning_zones[0][0]
    owning_list = "[" + ", ".join(f"'{s}'" for (s, _c) in owning_zones) + "]"
    preds_dict_lines = []
    for (slug, _camera) in owning_zones:
        preds_dict_lines.append(
            ind + f"    '{slug}': {{'state': pred_{slug}_state, "
                  f"'predicted_pct': pred_{slug}_p | int(0), "
                  f"'ramp_initial_pct': pred_{slug}_ri | int(0), "
                  f"'predicted_color_temp_kelvin': pred_{slug}_ct | int(0)}},"
        )
    lines = [
        ind + "{{ {",
        ind + "  'schema_version': 1,",
        ind + "  'kind': 'lighting_change_event',",
        ind + "  'raw_event_kind': 'lighting_change_event',",
        ind + "  'event_id': event_id,",
        ind + "  'ts': now().isoformat(),",
        ind + f"  'zone': '{primary_slug}',",
        ind + f"  'entity_id': '{light_entity}',",
        ind + f"  'light_entity': '{light_entity}',",
        ind + f"  'camera': '{camera}',",
        ind + f"  'owning_zones': {owning_list},",
        ind + "  'trigger_attribute': trigger.attribute | default(none, true),",
        ind + "  'from': {",
        ind + "    'state': from_state_s,",
        ind + "    'brightness': from_bri_raw if from_bri_raw not in ['', 'unknown', 'unavailable', 'none', 'None'] else none,",
        ind + "    'brightness_pct': from_bri_pct | int(0) if from_bri_pct not in ['', 'unknown', 'unavailable', 'none', 'None'] else none,",
        ind + "    'color_temp_kelvin': from_ct_kelvin | int(0) if from_ct_kelvin not in ['', 'unknown', 'unavailable', 'none', 'None'] else none,",
        ind + "    'color_temp': from_ct_mired | int(0) if from_ct_mired not in ['', 'unknown', 'unavailable', 'none', 'None'] else none,",
        ind + "    'color_mode': from_color_mode if from_color_mode not in ['', 'unknown', 'unavailable', 'none', 'None'] else none,",
        ind + "    'effect': from_effect if from_effect not in ['', 'unknown', 'unavailable', 'none', 'None'] else none",
        ind + "  },",
        ind + "  'to': {",
        ind + "    'state': to_state_s,",
        ind + "    'brightness': to_bri_raw if to_bri_raw not in ['', 'unknown', 'unavailable', 'none', 'None'] else none,",
        ind + "    'brightness_pct': to_bri_pct | int(0) if to_bri_pct not in ['', 'unknown', 'unavailable', 'none', 'None'] else none,",
        ind + "    'color_temp_kelvin': to_ct_kelvin | int(0) if to_ct_kelvin not in ['', 'unknown', 'unavailable', 'none', 'None'] else none,",
        ind + "    'color_temp': to_ct_mired | int(0) if to_ct_mired not in ['', 'unknown', 'unavailable', 'none', 'None'] else none,",
        ind + "    'color_mode': to_color_mode if to_color_mode not in ['', 'unknown', 'unavailable', 'none', 'None'] else none,",
        ind + "    'effect': to_effect if to_effect not in ['', 'unknown', 'unavailable', 'none', 'None'] else none",
        ind + "  },",
        ind + "  'predicted': {",
        ind + f"    'brightness_pct': pred_{primary_slug}_p | int(0),",
        ind + f"    'color_temp_kelvin': pred_{primary_slug}_ct | int(0)",
        ind + "  },",
        ind + "  'predictions_by_zone': {",
        *preds_dict_lines,
        ind + "  },",
        ind + "  'context': {",
        ind + f"    'state': pred_{primary_slug}_state,",
        ind + "    'profile': profile_state,",
        ind + "    'tv_playing': tv_playing,",
        ind + "    'gaming_active': gaming_active,",
        ind + "    'working_hours_active': working_hours_active,",
        ind + "    'sonos_playing': sonos_playing,",
        ind + "    'asleep': asleep_state,",
        ind + "    'night_safe': night_safe_state,",
        ind + "    'user_at_home': user_at_home_state,",
        ind + "    'any_occupied': any_occupied_state,",
        ind + "    'shadow_mode': shadow_mode",
        ind + "  },",
        ind + "  'ha_context': {",
        ind + "    'id': trigger.to_state.context.id | default(none, true),",
        ind + "    'parent_id': trigger.to_state.context.parent_id | default(none, true),",
        ind + "    'user_id': trigger.to_state.context.user_id | default(none, true)",
        ind + "  },",
        ind + "  'source_hint': source_hint,",
        ind + "  'source_confidence': source_confidence,",
        ind + "  'source_hints': {",
        ind + "    'has_user_id': trigger.to_state.context.user_id | default(none, true) not in [none, '', 'None'],",
        ind + "    'has_parent_id': trigger.to_state.context.parent_id | default(none, true) not in [none, '', 'None'],",
        ind + "    'trigger_attribute': trigger.attribute | default(none, true),",
        ind + "    'command_id_age_s': command_id_age_s | int(999999)",
        ind + "  },",
        ind + "  'command_id': (linked_command_id | string | trim) if (linked_command_id | string | trim) not in ['', 'unknown', 'unavailable', 'none', 'None'] else none",
        ind + "} | tojson }}",
    ]
    return "\n".join(lines)


def _emit_activity_automation(light_entity, owning_zones, camera) -> str:
    """One per-light universal activity-capture automation.

    Captures all material state/brightness/temperature/mode transitions for
    living-lights controlled leaf lights. It does not update manual cooldowns
    or preference labels; it only appends raw metadata evidence.
    """
    primary_slug = owning_zones[0][0]
    light_slug = light_entity.replace("light.", "")
    per_zone_scalars = _emit_per_zone_scalar_vars(owning_zones, indent=10)
    jsonl_block = _emit_activity_jsonl_payload_block(light_entity, camera, owning_zones, indent=20)
    return f"""  - alias: "Living Lights â€” lighting activity ledger ({light_entity})"
    id: living_lights_activity_{light_slug}
    mode: queued
    max: 40
    triggers:
      - trigger: state
        entity_id: {light_entity}
      - trigger: state
        entity_id: {light_entity}
        attribute: brightness
      - trigger: state
        entity_id: {light_entity}
        attribute: color_temp_kelvin
      - trigger: state
        entity_id: {light_entity}
        attribute: color_temp
      - trigger: state
        entity_id: {light_entity}
        attribute: color_mode
      - trigger: state
        entity_id: {light_entity}
        attribute: effect
    actions:
      - variables:
          from_state_s: "{{{{ trigger.from_state.state | default(none, true) }}}}"
          to_state_s: "{{{{ trigger.to_state.state | default(none, true) }}}}"
          from_bri_raw: "{{{{ trigger.from_state.attributes.brightness | default(none, true) }}}}"
          to_bri_raw: "{{{{ trigger.to_state.attributes.brightness | default(none, true) }}}}"
          from_bri_pct: >-
            {{% if from_state_s == 'off' %}}0
            {{% elif from_bri_raw not in [none, '', 'unknown', 'unavailable', 'none', 'None'] %}}{{{{ ((from_bri_raw | float(0)) / 2.55) | round(0) | int }}}}
            {{% else %}}{{{{ none }}}}{{% endif %}}
          to_bri_pct: >-
            {{% if to_state_s == 'off' %}}0
            {{% elif to_bri_raw not in [none, '', 'unknown', 'unavailable', 'none', 'None'] %}}{{{{ ((to_bri_raw | float(0)) / 2.55) | round(0) | int }}}}
            {{% else %}}{{{{ none }}}}{{% endif %}}
          from_ct_mired: "{{{{ trigger.from_state.attributes.color_temp | default(none, true) }}}}"
          to_ct_mired: "{{{{ trigger.to_state.attributes.color_temp | default(none, true) }}}}"
          from_ct_kelvin: >-
            {{% set k = trigger.from_state.attributes.color_temp_kelvin | default(none, true) %}}
            {{% set m = trigger.from_state.attributes.color_temp | default(none, true) %}}
            {{% if k not in [none, '', 'unknown', 'unavailable', 'none', 'None'] %}}{{{{ k | int(0) }}}}
            {{% elif m not in [none, '', 'unknown', 'unavailable', 'none', 'None'] and (m | float(0)) > 0 %}}{{{{ (1000000 / (m | float(1))) | round(0) | int }}}}
            {{% else %}}{{{{ none }}}}{{% endif %}}
          to_ct_kelvin: >-
            {{% set k = trigger.to_state.attributes.color_temp_kelvin | default(none, true) %}}
            {{% set m = trigger.to_state.attributes.color_temp | default(none, true) %}}
            {{% if k not in [none, '', 'unknown', 'unavailable', 'none', 'None'] %}}{{{{ k | int(0) }}}}
            {{% elif m not in [none, '', 'unknown', 'unavailable', 'none', 'None'] and (m | float(0)) > 0 %}}{{{{ (1000000 / (m | float(1))) | round(0) | int }}}}
            {{% else %}}{{{{ none }}}}{{% endif %}}
          from_color_mode: "{{{{ trigger.from_state.attributes.color_mode | default(none, true) }}}}"
          to_color_mode: "{{{{ trigger.to_state.attributes.color_mode | default(none, true) }}}}"
          from_effect: "{{{{ trigger.from_state.attributes.effect | default(none, true) }}}}"
          to_effect: "{{{{ trigger.to_state.attributes.effect | default(none, true) }}}}"
          material_changed: >-
            {{{{ from_state_s != to_state_s
                or (from_bri_raw | string) != (to_bri_raw | string)
                or (from_ct_kelvin | string) != (to_ct_kelvin | string)
                or (from_ct_mired | string) != (to_ct_mired | string)
                or (from_color_mode | string) != (to_color_mode | string)
                or (from_effect | string) != (to_effect | string) }}}}
          unknown_only_transition: >-
            {{{{ from_state_s in ['unknown', 'unavailable', 'none', 'None', '']
                and to_state_s in ['unknown', 'unavailable', 'none', 'None', ''] }}}}
          event_id: "lightchg-{{{{ now().timestamp() | int }}}}-{{{{ now().microsecond }}}}"
          command_id_raw: "{{{{ states('input_text.living_lights_zone_{primary_slug}_last_command_id') }}}}"
          command_id_age_s: >-
            {{% set parts = (command_id_raw | string).split('-') %}}
            {{% if parts | length >= 2 and parts[0] == 'cmd' %}}
              {{{{ (now().timestamp() | int) - (parts[1] | int(0)) }}}}
            {{% else %}}999999{{% endif %}}
          linked_command_id: >-
            {{{{ command_id_raw if command_id_raw not in ['unknown', 'unavailable', 'none', 'None', ''] and (command_id_age_s | int(999999)) <= 120 else none }}}}
          source_hint: >-
            {{% set cid = linked_command_id | string | trim %}}
            {{% if cid not in ['unknown', 'unavailable', 'none', 'None', ''] %}}home_app_ai
            {{% elif trigger.to_state.context.parent_id | default(none, true) not in [none, '', 'None'] %}}automation_or_script
            {{% elif trigger.to_state.context.user_id | default(none, true) not in [none, '', 'None'] %}}home_app_or_ha_user
            {{% else %}}physical_or_bridge_or_unknown{{% endif %}}
          source_confidence: >-
            {{% set cid = linked_command_id | string | trim %}}
            {{% if cid not in ['unknown', 'unavailable', 'none', 'None', ''] %}}high
            {{% elif trigger.to_state.context.parent_id | default(none, true) not in [none, '', 'None'] %}}medium
            {{% elif trigger.to_state.context.user_id | default(none, true) not in [none, '', 'None'] %}}low
            {{% else %}}low{{% endif %}}
{per_zone_scalars}
          shadow_mode: "{{{{ is_state('input_boolean.living_lights_shadow', 'on') }}}}"
          profile_state: "{{{{ states('sensor.living_lights_profile') }}}}"
          tv_playing: "{{{{ states('media_player.lg_tv') in ['on', 'playing', 'paused', 'buffering'] }}}}"
          sonos_playing: "{{{{ states('media_player.sonos') in ['on', 'playing', 'paused', 'buffering'] or states('media_player.living_room') in ['on', 'playing', 'paused', 'buffering'] }}}}"
          gaming_active: "{{{{ is_state('input_boolean.living_lights_gaming_enabled', 'on') and state_attr('sensor.steam_steam_76561198136331341', 'game') not in [none, '', 'unavailable', 'unknown'] }}}}"
          working_hours_active: "{{{{ is_state('binary_sensor.living_lights_working_hours_active', 'on') }}}}"
          asleep_state: "{{{{ is_state('input_boolean.living_lights_asleep', 'on') }}}}"
          night_safe_state: "{{{{ is_state('binary_sensor.living_lights_is_night_safe', 'on') }}}}"
          user_at_home_state: "{{{{ is_state('input_boolean.user_at_home', 'on') }}}}"
          any_occupied_state: "{{{{ is_state('binary_sensor.living_lights_any_occupied', 'on') }}}}"
      - if:
          - condition: template
            value_template: "{{{{ material_changed == true or material_changed == 'true' }}}}"
          - condition: template
            value_template: "{{{{ unknown_only_transition != true and unknown_only_transition != 'true' }}}}"
        then:
          - variables:
              json_payload: >-
{jsonl_block}
          - action: shell_command.living_lights_append_activity_log
            data:
              payload: "{{{{ json_payload | base64_encode }}}}"
"""


def _emit_detection_automation(light_entity, owning_zones, camera) -> str:
    """One per-light manual-touch detection automation. Fires
    `living_lights_override_detected` HA event (always when enabled) and
    appends to JSONL (gated on learning_enabled).

    P1-5 / R6 refactor: scalar variables only (`match_ok`, `match_reason`,
    `pred_<slug>_*`). `predictions_by_zone` is built INLINE in event_data as
    a proper YAML mapping, and in the JSONL payload via a single tojson-piped
    Jinja expression. No nested-object variables flow through HA's variables
    block (where they would be stringified)."""
    primary_slug = owning_zones[0][0]
    light_slug = light_entity.replace("light.", "")
    has_gradient = light_entity in SOFA_GRADIENT_KEYS and any(
        s == "sofa" for (s, _c) in owning_zones
    )
    owning_list = "[" + ", ".join(f'"{s}"' for (s, _c) in owning_zones) + "]"
    match_ok_block = _emit_match_ok_template(light_entity, owning_zones, has_gradient, indent=12)
    match_reason_block = _emit_match_reason_template(light_entity, owning_zones, has_gradient, indent=12)
    per_zone_scalars = _emit_per_zone_scalar_vars(owning_zones, indent=10)
    event_preds = _emit_event_predictions_by_zone(owning_zones, indent=16)
    last_manual_targets = _emit_last_manual_targets(owning_zones, indent=14)
    last_override_ctx_targets = _emit_last_override_ctx_targets(owning_zones, indent=14)
    jsonl_block = _emit_jsonl_payload_block(light_entity, camera, owning_zones, indent=20)
    return f"""  - alias: "Living Lights — manual touch detection ({light_entity})"
    id: living_lights_manual_detect_{light_slug}
    # M19: queued (not single) so rapid slider scrubs / multi-step brightness
    # changes within the 3-s settle window don't drop the FINAL evidence with
    # "Already running" warnings. `max: 20` is the burst headroom — a normal
    # slider scrub fires 3-8 state-change events before settling; 20 covers
    # extreme bursts (Hue-app rapid dimming, RGB picker sweeps) with margin.
    # Each queued instance still respects the variables-block dedupe logic
    # (last_override_context_id flicker-count guards downstream JSONL
    # duplication), so a 20-event burst produces at most one logged
    # override_event per truly-distinct context.
    mode: queued
    max: 20
    triggers:
      - trigger: state
        entity_id: {light_entity}
        for: "00:00:03"
      - trigger: state
        entity_id: {light_entity}
        attribute: brightness
        for: "00:00:03"
      - trigger: state
        entity_id: {light_entity}
        attribute: color_temp_kelvin
        for: "00:00:03"
      - trigger: state
        entity_id: {light_entity}
        attribute: color_temp
        for: "00:00:03"
    conditions:
      - condition: state
        entity_id: input_boolean.living_lights_enabled
        state: "on"
    actions:
      - variables:
          actual_pct: >-
            {{% if is_state('{light_entity}', 'on') %}}
            {{{{ ((state_attr('{light_entity}', 'brightness') | float(0)) / 2.55) | round(0) | int }}}}
            {{% else %}}0{{% endif %}}
          light_state: "{{{{ states('{light_entity}') }}}}"
          match_ok: >-
{match_ok_block}
          match_reason: >-
{match_reason_block}
{per_zone_scalars}
          shadow_mode: "{{{{ is_state('input_boolean.living_lights_shadow', 'on') }}}}"
          profile_state: "{{{{ states('sensor.living_lights_profile') }}}}"
          tv_playing: "{{{{ states('media_player.lg_tv') in ['on', 'playing', 'paused', 'buffering'] }}}}"
          # M22a-aligned: gaming context dimension. Mirrors the classifier's
          # `gaming_active` truth so override_event.context records the
          # gaming state at the moment the user touched a light. Downstream
          # M22 + Codex session-classification can read this to learn that
          # gaming sessions are a distinct policy context.
          gaming_active: "{{{{ is_state('input_boolean.living_lights_gaming_enabled', 'on') and state_attr('sensor.steam_steam_76561198136331341', 'game') not in [none, '', 'unavailable', 'unknown'] }}}}"
          # Working-hours context dimension. Reads the composite binary_sensor
          # the observability package emits (toggle + woke_up + weekday + 08-18).
          # Capturing this in override_event.context lets downstream learning
          # treat workday-touches as a distinct policy context (R-WH-23 / M22a).
          # NOT added to dedupe_key — keeps the existing 7-tuple Codex contract
          # stable. Promoted to policy_context + session_key in a follow-up.
          working_hours_active: "{{{{ is_state('binary_sensor.living_lights_working_hours_active', 'on') }}}}"
          asleep_state: "{{{{ is_state('input_boolean.living_lights_asleep', 'on') }}}}"
          night_safe_state: "{{{{ is_state('binary_sensor.living_lights_is_night_safe', 'on') }}}}"
          user_at_home_state: "{{{{ is_state('input_boolean.user_at_home', 'on') }}}}"
          any_occupied_state: "{{{{ is_state('binary_sensor.living_lights_any_occupied', 'on') }}}}"
          ctx_id: "ovr-{{{{ now().timestamp() | int }}}}-{{{{ now().microsecond }}}}"
      - if:
          - condition: template
            value_template: "{{{{ match_ok != true and match_ok != 'true' }}}}"
        then:
          - action: input_datetime.set_datetime
            target:
              entity_id:
{last_manual_targets}
            data:
              datetime: "{{{{ now().strftime('%Y-%m-%d %H:%M:%S') }}}}"
          # M4: stamp ctx_id on every owning zone so capture-on-vacant can
          # populate `related_override_context_id` for the same zone later.
          - action: input_text.set_value
            target:
              entity_id:
{last_override_ctx_targets}
            data:
              value: "{{{{ ctx_id }}}}"
          - event: living_lights_override_detected
            event_data:
              context_id: "{{{{ ctx_id }}}}"
              ts: "{{{{ now().isoformat() }}}}"
              zone: "{primary_slug}"
              light_entity: "{light_entity}"
              camera: "{camera}"
              owning_zones: {owning_list}
              actual_pct: "{{{{ actual_pct | int(0) }}}}"
              predicted_pct: "{{{{ pred_{primary_slug}_p | int(0) }}}}"
              delta_pct: "{{{{ (actual_pct | int(0)) - (pred_{primary_slug}_p | int(0)) }}}}"
              light_state: "{{{{ light_state }}}}"
              predictions_by_zone:
{event_preds}
              state: "{{{{ pred_{primary_slug}_state }}}}"
              profile: "{{{{ profile_state }}}}"
              tv_playing: "{{{{ tv_playing }}}}"
              gaming_active: "{{{{ gaming_active }}}}"
              working_hours_active: "{{{{ working_hours_active }}}}"
              asleep: "{{{{ asleep_state }}}}"
              night_safe: "{{{{ night_safe_state }}}}"
              user_at_home: "{{{{ user_at_home_state }}}}"
              anticipated_room: null
              any_occupied: "{{{{ any_occupied_state }}}}"
              shadow_mode: "{{{{ shadow_mode }}}}"
              source_user_id: "{{{{ trigger.to_state.context.user_id | default(none, true) }}}}"
              debug_match_reason: "{{{{ match_reason }}}}"
              light_transition:
                trigger_attribute: "{{{{ trigger.attribute | default(none, true) }}}}"
                from:
                  state: "{{{{ trigger.from_state.state | default(none, true) }}}}"
                  brightness: "{{{{ trigger.from_state.attributes.brightness | default(none, true) }}}}"
                  color_temp_kelvin: "{{{{ trigger.from_state.attributes.color_temp_kelvin | default(none, true) }}}}"
                  color_temp: "{{{{ trigger.from_state.attributes.color_temp | default(none, true) }}}}"
                  color_mode: "{{{{ trigger.from_state.attributes.color_mode | default(none, true) }}}}"
                  effect: "{{{{ trigger.from_state.attributes.effect | default(none, true) }}}}"
                to:
                  state: "{{{{ trigger.to_state.state | default(none, true) }}}}"
                  brightness: "{{{{ trigger.to_state.attributes.brightness | default(none, true) }}}}"
                  color_temp_kelvin: "{{{{ trigger.to_state.attributes.color_temp_kelvin | default(none, true) }}}}"
                  color_temp: "{{{{ trigger.to_state.attributes.color_temp | default(none, true) }}}}"
                  color_mode: "{{{{ trigger.to_state.attributes.color_mode | default(none, true) }}}}"
                  effect: "{{{{ trigger.to_state.attributes.effect | default(none, true) }}}}"
                ha_context:
                  id: "{{{{ trigger.to_state.context.id | default(none, true) }}}}"
                  parent_id: "{{{{ trigger.to_state.context.parent_id | default(none, true) }}}}"
                  user_id: "{{{{ trigger.to_state.context.user_id | default(none, true) }}}}"
          - if:
              - condition: state
                entity_id: input_boolean.living_lights_learning_enabled
                state: "on"
            then:
              - variables:
                  json_payload: >-
{jsonl_block}
              - action: shell_command.living_lights_append_preference_log
                data:
                  payload: "{{{{ json_payload | base64_encode }}}}"
"""


def _emit_fast_manual_arm_automation(light_entity, owning_zones, camera) -> str:
    """Immediate per-light manual hold armer for external changes."""
    light_slug = light_entity.replace("light.", "")
    has_gradient = light_entity in SOFA_GRADIENT_KEYS and any(
        s == "sofa" for (s, _c) in owning_zones
    )
    owning_list = "[" + ", ".join(f'"{s}"' for (s, _c) in owning_zones) + "]"
    match_ok_block = _emit_match_ok_template(light_entity, owning_zones, has_gradient, indent=12)
    last_manual_targets = _emit_last_manual_targets(owning_zones, indent=14)
    last_override_ctx_targets = _emit_last_override_ctx_targets(owning_zones, indent=14)
    return f"""  - alias: "Living Lights - fast manual hold arm ({light_entity})"
    id: living_lights_fast_manual_arm_{light_slug}
    mode: queued
    max: 20
    triggers:
      - trigger: state
        entity_id: {light_entity}
      - trigger: state
        entity_id: {light_entity}
        attribute: brightness
      - trigger: state
        entity_id: {light_entity}
        attribute: color_temp_kelvin
      - trigger: state
        entity_id: {light_entity}
        attribute: color_temp
    conditions:
      - condition: state
        entity_id: input_boolean.living_lights_enabled
        state: "on"
    actions:
      - variables:
          ctx_id: "hold-{{{{ now().timestamp() | int }}}}-{{{{ now().microsecond }}}}"
          match_ok: >-
{match_ok_block}
      - if:
          - condition: template
            value_template: "{{{{ match_ok != true and match_ok != 'true' }}}}"
        then:
          - action: input_datetime.set_datetime
            target:
              entity_id:
{last_manual_targets}
            data:
              datetime: "{{{{ now().strftime('%Y-%m-%d %H:%M:%S') }}}}"
          - action: input_text.set_value
            target:
              entity_id:
{last_override_ctx_targets}
            data:
              value: "{{{{ ctx_id }}}}"
          - event: living_lights_manual_hold_armed
            event_data:
              context_id: "{{{{ ctx_id }}}}"
              ts: "{{{{ now().isoformat() }}}}"
              zone: "{owning_zones[0][0]}"
              light_entity: "{light_entity}"
              camera: "{camera}"
              owning_zones: {owning_list}
              trigger_attribute: "{{{{ trigger.attribute | default(none, true) }}}}"
              source_user_id: "{{{{ trigger.to_state.context.user_id | default(none, true) }}}}"
              source_parent_id: "{{{{ trigger.to_state.context.parent_id | default(none, true) }}}}"
              debug_match_ok: "{{{{ match_ok }}}}"
"""


def _emit_vacancy_expire_automation(slug, camera) -> str:
    """Per-zone automation: after 15 min of stable occupancy = off,
    write the 1970 sentinel to last_manual_at so the cooldown expires
    permanently for that touch (even across cross-day re-entries)."""
    stable_entity = f"binary_sensor.{camera}_{slug}_person_occupancy_stable"
    return f"""  - alias: "Living Lights — manual cooldown expire ({slug})"
    id: living_lights_manual_expire_{slug}
    mode: single
    triggers:
      - trigger: state
        entity_id: {stable_entity}
        from: "on"
        to: "off"
        for: "00:15:00"
    conditions:
      # Only write the sentinel if there's actually a non-sentinel manual to expire.
      - condition: template
        value_template: >-
          {{% set lm = states('input_datetime.living_lights_zone_{slug}_last_manual_at') %}}
          {{% if not lm or lm in ['unknown', 'unavailable', 'none', ''] %}}false
          {{% else %}}
            {{% set manual_dt = as_datetime(lm | string) %}}
            {{{{ manual_dt.year >= 2000 }}}}
          {{% endif %}}
    actions:
      - action: input_datetime.set_datetime
        target:
          entity_id: input_datetime.living_lights_zone_{slug}_last_manual_at
        data:
          datetime: "1970-01-01 00:00:00"
"""


def _emit_voice_override_reset_automation(slug) -> str:
    """Per-zone automation: when input_text override is set to a non-empty
    value (e.g., agent's set_presence_override tool), reset last_manual_at
    to sentinel so voice supersedes prior manual cooldown."""
    return f"""  - alias: "Living Lights — voice override resets manual ({slug})"
    id: living_lights_voice_resets_manual_{slug}
    mode: parallel
    max: 4
    triggers:
      - trigger: state
        entity_id: input_text.living_lights_override_text_{slug}
    conditions:
      - condition: template
        value_template: >-
          {{{{ trigger.to_state.state not in ['', 'unknown', 'unavailable']
               and trigger.to_state.state != trigger.from_state.state }}}}
    actions:
      - action: input_datetime.set_datetime
        target:
          entity_id: input_datetime.living_lights_zone_{slug}_last_manual_at
        data:
          datetime: "1970-01-01 00:00:00"
"""


def emit_manual_detection() -> str:
    """Assemble the full living_lights_manual_detection.yaml package:
    - shell_command for appending to preference JSONL
    - per-light + aggregate fast manual-hold arming automations
    - 10 per-light manual-touch detection automations
    - 10 per-zone vacancy-expire automations (sentinel write after 15 min vacant)
    - 10 per-zone voice-override sentinel-reset automations
    """
    header = """# living_lights_manual_detection.yaml — GENERATED by tools/build-living-lights-actuators.py
#
# Fast manual hold arming + settled manual-touch detection +
# cooldown-expire + voice-override-reset.
#
# Fast arming stamps last_manual_at immediately for external light changes
# whose actual state does not match the current Living Lights prediction.
# This is what makes Lutron/HomeKit/Siri/physical touches stick before an
# actuator can revert them, even when HA context metadata is parented.
#
# Settled detection still fires `living_lights_override_detected` HA bus event
# after 3s whenever a light's brightness diverges from any owning zone's
# prediction (with ramp-window and sofa-gradient absorption). Event fires
# ALWAYS when input_boolean.living_lights_enabled is on. JSONL preference
# capture is additionally gated by input_boolean.living_lights_learning_enabled.
#
# Vacancy-expire writes the 1970-01-01 sentinel to last_manual_at after the
# zone has been vacant for 15 continuous minutes — the cooldown then treats
# the manual as fully expired even across cross-day re-entries.
#
# Voice-override-reset writes the sentinel when input_text.living_lights_
# override_text_<slug> is set (e.g., by the agent's set_presence_override
# tool) so voice commands supersede prior manual cooldown.
#
# DO NOT EDIT BY HAND. Edit tools/build-living-lights-actuators.py and re-run.

shell_command:
  living_lights_append_preference_log: 'sh -c "echo {{ payload }} | base64 -d >> /config/lighting_preferences_pending.jsonl; echo >> /config/lighting_preferences_pending.jsonl"'
  living_lights_append_activity_log: 'sh -c "echo {{ payload }} | base64 -d >> /config/lighting_activity.jsonl; echo >> /config/lighting_activity.jsonl"'

input_text:
@@COMMAND_HELPERS@@

automation:
"""
    # Per-light and aggregate immediate manual hold arming. This runs before
    # the slower detector in the generated file so external controls win the
    # race. Aggregate entities only arm a hold; preference logging remains on
    # the leaf-light detectors where actual physical-light evidence is clean.
    arm_blocks = []
    for light_entity, owning_pairs in {
        **AGGREGATE_LIGHT_CONTROLLERS,
        **CO_CONTROLLERS,
    }.items():
        owning_zones = list(owning_pairs)
        primary_camera = owning_zones[0][1]
        arm_blocks.append(
            _emit_fast_manual_arm_automation(light_entity, owning_zones, primary_camera)
        )
    # Per-light detection
    detection_blocks = []
    for light_entity, owning_pairs in CO_CONTROLLERS.items():
        owning_zones = list(owning_pairs)
        primary_camera = owning_zones[0][1]
        detection_blocks.append(
            _emit_detection_automation(light_entity, owning_zones, primary_camera)
        )
    # Per-light universal activity ledger capture. This is intentionally
    # separate from override detection: it never updates last_manual_at and
    # does not create preference labels.
    activity_blocks = []
    for light_entity, owning_pairs in CO_CONTROLLERS.items():
        owning_zones = list(owning_pairs)
        primary_camera = owning_zones[0][1]
        activity_blocks.append(
            _emit_activity_automation(light_entity, owning_zones, primary_camera)
        )
    # Per-zone vacancy-expire (only zones that own at least one light)
    expire_blocks = []
    seen_zones = set()
    for owning_pairs in CO_CONTROLLERS.values():
        for (slug, camera) in owning_pairs:
            if slug in seen_zones:
                continue
            seen_zones.add(slug)
            expire_blocks.append(_emit_vacancy_expire_automation(slug, camera))
    # Per-zone voice-override-reset
    voice_reset_blocks = []
    for slug in sorted(seen_zones):
        voice_reset_blocks.append(_emit_voice_override_reset_automation(slug))

    command_helpers = []
    for slug in sorted(seen_zones):
        friendly = slug.replace("_", " ")
        command_helpers.append(
            f"""  living_lights_zone_{slug}_last_command_id:
    name: "Living Lights {friendly} last command id"
    max: 80"""
        )
    header = header.replace("@@COMMAND_HELPERS@@", "\n".join(command_helpers))

    return header + "\n".join(arm_blocks + detection_blocks + activity_blocks + expire_blocks + voice_reset_blocks)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="Write the YAML files (default: dry-run)")
    ap.add_argument("--zone", help="Build only this zone")
    ap.add_argument("--omit-ct-zone", action="append", default=[],
                    help=(
                        "Omit color_temp_kelvin injection for this zone. Use only "
                        "for an isolated Adaptive Lighting canary profile with "
                        "take_over_control_mode=pause_changed. Repeatable."
                    ))
    args = ap.parse_args()

    repo = Path(__file__).resolve().parent.parent
    pkg_dir = repo / "ha-config" / "packages"

    targets = LIGHT_TARGETS
    if args.zone:
        if args.zone not in LIGHT_TARGETS:
            print(f"Unknown zone: {args.zone}.")
            return 2
        targets = {args.zone: LIGHT_TARGETS[args.zone]}
    omit_ct_zones = set(args.omit_ct_zone or [])
    unknown_omit_zones = sorted(omit_ct_zones - set(LIGHT_TARGETS))
    if unknown_omit_zones:
        print(f"Unknown --omit-ct-zone: {', '.join(unknown_omit_zones)}.")
        return 2

    built = 0
    skipped = 0
    for slug, light_targets in targets.items():
        if light_targets is None:
            print(f"SKIP {slug} (no known light targets)")
            skipped += 1
            continue
        yaml = emit_actuator(slug, light_targets, omit_ct_zones=omit_ct_zones)
        outfile = pkg_dir / f"living_lights_pilot_{slug}.yaml"
        if args.apply:
            outfile.write_text(yaml, encoding="utf-8")
            print(f"WROTE {outfile.name} ({[t[1] for t in light_targets]})")
        else:
            print(f"DRY-RUN {outfile.name} ({len(yaml)} bytes)")
        built += 1

    # Emit the manual-detection aggregate file (only when --zone is not used,
    # since detection is per-light across the full LIGHT_TARGETS set).
    if not args.zone:
        manual_yaml = emit_manual_detection()
        manual_outfile = pkg_dir / "living_lights_manual_detection.yaml"
        if args.apply:
            manual_outfile.write_text(manual_yaml, encoding="utf-8")
            armer_count = len(CO_CONTROLLERS) + len(AGGREGATE_LIGHT_CONTROLLERS)
            print(f"WROTE {manual_outfile.name} "
                  f"({armer_count} fast armers, "
                  f"{len(CO_CONTROLLERS)} settled detectors, "
                  f"{len(manual_yaml)} bytes)")
        else:
            print(f"DRY-RUN {manual_outfile.name} ({len(manual_yaml)} bytes)")

    print(f"\n{built} actuators built, {skipped} skipped.")
    if not args.apply:
        print("Re-run with --apply to write files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
