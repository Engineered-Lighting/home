#!/usr/bin/env python3
"""build-living-lights-yaml.py — generator for the Living Lights
observability + Layer 2 confidence-ramp classifier package.

Source of truth: the ZONES dict below. Adding/removing a zone =
single-line edit. Re-run to regenerate
ha-config/packages/living_lights_observability.yaml.

Per Addendum 33 AR33-48: sensor names use {camera}_{zone}_ prefix
to prevent collisions if zones in different cameras share a name.
Per the live-tested rev 3 patterns (trigger-based templates fire on
homeassistant.start + state-change + time_pattern).

Layer 2 (the approved Adaptive Lighting + Living Lights activation plan):
  - the dwell tiers active/linger/task collapse into one `present` state;
    the confidence ramp (in the pilots) drives the brightness escalation;
  - new `away` state, keyed on input_boolean.user_at_home;
  - a flat vacant idle baseline (VACANT_DAY_PCT / VACANT_NIGHT_PCT);
  - an `asleep` modifier (input_boolean.living_lights_asleep) that zeroes
    the vacant baseline once the house has been quiet overnight;
  - movie is a brightness MODIFIER, not a state — vacant zones (and the
    sofa watch zone) dim while the TV plays;
  - night_safe is now keyed on input_boolean.homeai_sleep ONLY (the clock
    no longer masks occupancy — the overnight cap + asleep cover that);
  - a reserved V-JEPA 2 activity seam (ACTIVITY_PROFILES, default-off).
"""

from __future__ import annotations

import argparse
from pathlib import Path

# Zone definitions. Add/remove here.
ZONES: dict = {
    # Phase 0a (5 zones — kitchen + dining)
    "dining_left":   {"camera": "dining_room", "vacant_pct": 15},
    "dining_right":  {"camera": "dining_room", "vacant_pct": 15},
    "sink":          {"camera": "kitchen",     "vacant_pct": 26},
    "island_left":   {"camera": "kitchen",     "vacant_pct": 25},
    "island_right":  {"camera": "kitchen",     "vacant_pct": 15},

    # Phase 0c (10 more zones — living_room + workshop + driveway + whole-rooms)
    "sofa":              {"camera": "living_room", "vacant_pct": 20},
    "front_left":        {"camera": "living_room", "vacant_pct": 20},
    "weights":           {"camera": "living_room", "vacant_pct": 18},
    "office":            {"camera": "living_room", "vacant_pct": 25},
    "front_door":        {"camera": "living_room", "vacant_pct": 22},
    "whole_living_room": {"camera": "living_room", "vacant_pct": 18},
    "workshop_zone":     {"camera": "workshop",    "vacant_pct": 18},
    "e28":               {"camera": "driveway",    "vacant_pct": 8},  # outdoor
    "whole_dining_room": {"camera": "dining_room", "vacant_pct": 15},
    "whole_kitchen":     {"camera": "kitchen",     "vacant_pct": 20},
}

# ───────────────────────── Layer 2 tuning constants ───────────────────────
# Vacant idle baseline — flat, by time-of-day bucket (not ToD-scaled).
VACANT_DAY_PCT = 50        # morning / midday / afternoon / evening
VACANT_NIGHT_PCT = 20      # late_evening / overnight
# Anticipated pre-warm (Phase 4). Conservative default: equals VACANT_DAY_PCT,
# so anticipation is a NO-OP against the daytime vacant baseline (= 50%) and
# only takes effect when the floor drops below 50% (evening / overnight).
# The brightness template floors at the vacant baseline — anticipation NEVER
# DIMS lights, only raises. Tune up (e.g. 70) if pre-warm should be visible
# during the day. Master kill switch: input_boolean.living_lights_anticipated_enabled.
ANTICIPATED_PCT = 50
# Hangover for the raw anticipated sensor — addon's MIN_HOLD_S is 2.5s but
# slow / lingering motion still produces sub-10s ON/OFF/ON flap on the same
# track. Treating the anticipated state as "sticky on" for ANTICIPATED_DECAY_S
# after the raw MQTT topic goes off gives the lights a clean stretch to settle
# back to the vacant floor instead of bouncing between anticipated and vacant.
ANTICIPATED_DECAY_S = 12
# Movie modifier.
MOVIE_DIM_PCT = 8
# Zones where presence is treated as "person is here to watch / be quiet" —
# during TV, the brightness template short-circuits to MOVIE_DIM_PCT regardless
# of the classifier state (vacant / pass_through / present / anticipated).
# Solves the "front_left briefly flips to present, jumps to 80%, then sticks
# because the co-controller guard prevents dim-back" failure: with all LR
# zones as watch zones, even a momentary present-pulse during a movie stays
# at the movie-dim baseline. Computed from ZONES so it stays in sync if
# zones are added/removed: every zone on the living_room camera is a watch
# zone (the TV is in the living room).
MOVIE_WATCH_ZONES = {
    slug for slug, meta in ZONES.items() if meta["camera"] == "living_room"
}
# Entity whose state means "a movie is on" -> movie modifier active.
# Was media_player.living_room_2 (the apple_tv integration), but that entity
# is unreliable: it drops to `unknown` whenever the Apple TV connection
# lapses, silently disabling movie mode. The LG TV reports a dependable
# on/off and is the actual living-room screen, so movie mode follows it.
MOVIE_MEDIA_PLAYER = "media_player.lg_tv"

# Gaming modifier — Steam-driven ambience. When the HA `steam_online`
# integration's `game` attribute is populated AND the master toggle is on,
# `gaming_active` flips true and the classifier rewrites the floor + present
# targets for office and living-room watch zones. Gated by
# input_boolean.living_lights_gaming_enabled (default off; user opts in).
# Office goes fully off; LR watch zones go to GAMING_DIM_PCT (= 3).
# Asleep, away, manual_override, and presence_override still win over gaming.
GAMING_DIM_PCT = 3
GAMING_OFF_ZONES = {"office"}
GAMING_DIM_ZONES = {"sofa", "front_left", "weights", "front_door"}
# Entity id is the format HA's `steam_online` integration creates:
# `sensor.steam_<unique_id>` where unique_id is itself `steam_<steamid64>`
# (yielding the double "steam" prefix in the entity id). Confirmed via
# core.entity_registry post-integration setup on 2026-05-27.
GAMING_SENSOR = "sensor.steam_steam_76561198136331341"

# Working-hours modifier — weekday 08:00-18:00 brightness boost. When the
# user is genuinely awake (`woke_up_today` latched) AND it's a weekday
# inside the working window AND the master toggle is on, `working_hours_active`
# flips true and the classifier raises the floor + present target everywhere.
# Goal: house stays bright + responsive while sitting at the desk — even
# when the office Frigate polygon misses a still desk-sitter (vacant floor
# rises to 80, present rises to 95).
#
# Thresholds are user-tunable via input_number (live, no regen). Asleep,
# away, manual_override, presence_override, and gaming still win over
# working_hours (gaming is a strong opt-in signal — see R-WH-6).
WORKING_HOURS_FLOOR_PCT_INITIAL = 80    # input_number initial; vacant during working hours
WORKING_HOURS_PRESENT_PCT_INITIAL = 95  # input_number initial; present during working hours
# Confidence ramp — occupancy targets, ToD-scaled + clamped by the cap.
RAMP_TARGET_PCT = 80       # `present` ramp destination
RAMP_INITIAL_PCT = 50      # fast-reaction level
PASS_PCT = 55              # `pass_through` quick path light
# Asleep modifier.
ASLEEP_IDLE_MINUTES = 15   # house quiet this long, overnight -> asleep
ASLEEP_WAKE_MINUTES = 10   # sustained occupancy this long -> awake
ASLEEP_CAP_PCT = 30        # occupied-zone ceiling while asleep: navigable,
                           # not a 3 a.m. blast (vacant zones still go dark)

# V-JEPA 2 activity layer — reserved seam (the plan's forward-compatible
# section). Each activity maps to a ramp target (pct, pre-ToD-scale) that the
# `present` branch climbs to instead of the default RAMP_TARGET_PCT. It is a
# safe no-op today: gated by input_boolean.living_lights_actuate_from_belief_changes
# (default-off) AND a per-zone sensor.<cam>_<slug>_activity that does not exist
# yet, so every zone resolves to the default target. When V-JEPA 2 ships,
# a belief-engine automation maintains the activity sensor and the toggle flips.
ACTIVITY_PROFILES: dict = {
    "cooking":     100,
    "working_out": 95,
    "working":     85,
    "reading":     80,
    "eating":      70,
    "watching_tv": 8,
    "napping":     3,
    # "idle" / "unknown" / absent -> default RAMP_TARGET_PCT
}

HEADER = '''# living_lights_observability.yaml — GENERATED by tools/build-living-lights-yaml.py
#
# DO NOT EDIT BY HAND. Edit the ZONES dict / tuning constants in
# build-living-lights-yaml.py and re-run to regenerate.
#
# Observability + the Layer 2 confidence-ramp classifier. Per-zone template
# sensors compute the lighting state (vacant / pass_through / present / away /
# night_safe / presence_override / manual_override), the confidence-ramp
# brightness target (predicted_brightness_pct) + ramp_initial_pct, the
# time-of-day curve, the auto `asleep` helper, and a house-wide any-occupied
# binary_sensor.
#
# Per AR33-48 sensor names use {camera}_{zone}_ prefix.
# Per rev 3 (trigger-based templates that fire on HA-start + state-change +
# time_pattern).
#
# Rollback: remove this file + `ha core restart`. All sensors disappear.
'''


def _slug_to_entity_id(slug: str) -> str:
    """Map a zone slug to its Frigate occupancy entity_id.

    Frigate's HA integration uses bare-slug entity_ids (no camera
    prefix), so all our zones map directly: 'sofa' →
    'binary_sensor.sofa_person_occupancy'.
    """
    return f"binary_sensor.{slug}_person_occupancy"


def emit_input_boolean() -> str:
    lines = ["input_boolean:",
             '  living_lights_enabled:',
             '    name: "Living Lights — master enable"',
             "    icon: mdi:lightbulb-auto",
             '  living_lights_shadow:',
             '    name: "Living Lights — shadow mode (log only, no actuation)"',
             "    icon: mdi:eye-outline",
             # No `initial:` — post-cutover the system is live; shadow must
             # restore its last state (default off) so a restart never
             # silently re-shadows Living Lights with legacy already disabled.
             '  living_lights_asleep:',
             '    name: "Living Lights — asleep (auto, presence-inferred)"',
             "    icon: mdi:sleep",
             '  living_lights_evidence_engine_enabled:',
             '    name: "Living Lights — belief engine (V-JEPA2-aware)"',
             "    icon: mdi:brain",
             '  living_lights_actuate_from_belief_changes:',
             '    name: "Living Lights — actuate from belief changes"',
             "    icon: mdi:lightbulb-on-outline",
             '  living_lights_gaming_enabled:',
             '    name: "Living Lights — gaming mode (Steam-driven)"',
             "    icon: mdi:gamepad-variant",
             "    initial: false",
             # Working-hours modifier — master toggle (default ON so the
             # behavior is live for the user's typical work week without
             # extra setup; flip OFF for vacation days / holidays).
             '  living_lights_working_hours_enabled:',
             '    name: "Living Lights — working hours bright mode"',
             "    icon: mdi:briefcase-clock",
             "    initial: true",
             # Wake-up latch. Set by the morning-latch automation when
             # sustained presence is detected during 05:00-12:00; reset at
             # 04:00 nightly + on sustained asleep. Default OFF so a fresh
             # deploy / midnight crossover does NOT auto-engage working
             # hours until the user actually wakes up.
             '  living_lights_woke_up_today:',
             '    name: "Living Lights — woke up today (latch)"',
             "    icon: mdi:weather-sunny-alert",
             "    initial: false",
             '  living_lights_learning_enabled:',
             '    name: "Living Lights — learning loop (preference capture)"',
             "    icon: mdi:school",
             '  living_lights_articulate_overrides:',
             '    name: "Living Lights — articulate manual overrides via LLM"',
             "    icon: mdi:message-text",
             "    initial: false"]
    # per-zone enable + manual override
    for slug in ZONES:
        lines.append(f"  living_lights_zone_{slug}_enabled:")
        lines.append(f'    name: "Living Lights — {slug} enabled"')
        lines.append("    icon: mdi:lightbulb")
        lines.append("    initial: true")
    for slug in ZONES:
        lines.append(f"  living_lights_override_{slug}:")
        lines.append(f'    name: "Living Lights — {slug} manual override"')
        lines.append("    icon: mdi:lightbulb-off")
    return "\n".join(lines) + "\n"


def emit_input_text() -> str:
    lines = ["input_text:"]
    # Scope helper for the house-wide bias knobs (warmth_bias_k,
    # brightness_bias_pp). Default 'all' = apply globally. Set to a
    # zone slug (e.g., 'office') to scope the bias to that one zone.
    # Classifier templates read this and gate the additive bias on
    # `bias_scope in ['all', <slug>]`.
    lines.append("  living_lights_bias_zone_scope:")
    lines.append('    name: "Living Lights — bias zone scope"')
    lines.append("    max: 32")
    lines.append("    initial: all")
    lines.append("    icon: mdi:target")
    for slug in ZONES:
        lines.append(f"  living_lights_override_text_{slug}:")
        lines.append(f'    name: "{slug} presence override payload"')
        lines.append("    max: 255")
        lines.append("    icon: mdi:code-json")
    # M4: per-zone context_id of the most recent override_event so the
    # capture-on-vacant automation can stamp pending_preference records with
    # `related_override_context_id`. Updated by the manual_detection
    # automation alongside `last_manual_at`. Declared for ALL zones for
    # symmetry with `_last_manual_at` even though only light-controlled
    # zones actually fire override events.
    for slug in ZONES:
        lines.append(f"  living_lights_zone_{slug}_last_override_context_id:")
        lines.append(f'    name: "{slug} — last override context_id"')
        lines.append("    max: 64")
        lines.append("    icon: mdi:identifier")
    return "\n".join(lines) + "\n"


def emit_input_number() -> str:
    """User-tunable thresholds, settable live from the HA UI without
    regenerating YAML. Templates read these via
    `states('input_number.X') | int(<original_constant>)` so a missing
    helper degrades gracefully to the original baked value.

    `step: 5` (pct) / `50` (K) / `0.05` (multiplier) — fine enough to
    matter, coarse enough to avoid fiddling.

    Groups:
    1. Working hours (shipped commit 584d53b) — floor + present pcts.
    2. House-wide bias (this commit) — warmth_k + brightness_pp.
       These are ADDITIVE on top of the cascade output, clamped to
       AL min/max and per-bulb cap. Scope is gated by
       `input_text.living_lights_bias_zone_scope` ('all' | <slug>).
    3. Cascade defaults (this commit) — promotes 7 baked brightness
       constants (VACANT_DAY, VACANT_NIGHT, RAMP_TARGET, RAMP_INITIAL,
       ASLEEP_CAP, MOVIE_DIM, GAMING_DIM) so the user can tune them
       live without a regen.
    4. Time-of-day (this commit) — 6 color-temperature bucket anchors
       and 2 brightness-factor multipliers for morning/midday.
    """
    lines = [
        "input_number:",
        # ── Group 1: Working hours ───────────────────────────────
        "  living_lights_working_hours_floor_pct:",
        '    name: "Living Lights — working-hours vacant floor (%)"',
        "    min: 0",
        "    max: 100",
        "    step: 5",
        f"    initial: {WORKING_HOURS_FLOOR_PCT_INITIAL}",
        "    unit_of_measurement: '%'",
        "    icon: mdi:briefcase-arrow-up-down",
        "    mode: slider",
        "  living_lights_working_hours_present_pct:",
        '    name: "Living Lights — working-hours present target (%)"',
        "    min: 0",
        "    max: 100",
        "    step: 5",
        f"    initial: {WORKING_HOURS_PRESENT_PCT_INITIAL}",
        "    unit_of_measurement: '%'",
        "    icon: mdi:briefcase-eye",
        "    mode: slider",
        # ── Group 2: House bias (additive, clamped) ──────────────
        # Warmth bias — Kelvin offset applied to predicted_color_temp_kelvin.
        # Clamped at AL min (2000) / max (4000). Negative = warmer (lower K).
        "  living_lights_warmth_bias_k:",
        '    name: "Living Lights — warmth bias (K, additive)"',
        "    min: -500",
        "    max: 500",
        "    step: 50",
        "    initial: 0",
        "    unit_of_measurement: 'K'",
        "    icon: mdi:thermometer-low",
        "    mode: slider",
        # Brightness bias — percentage-point offset applied to
        # predicted_brightness_pct. Clamped at 0 / cap (asleep-gated).
        "  living_lights_brightness_bias_pp:",
        '    name: "Living Lights — brightness bias (pp, additive)"',
        "    min: -30",
        "    max: 30",
        "    step: 5",
        "    initial: 0",
        "    unit_of_measurement: 'pp'",
        "    icon: mdi:brightness-percent",
        "    mode: slider",
        # ── Group 3: Cascade defaults (promoted constants) ───────
        "  living_lights_vacant_day_pct:",
        '    name: "Living Lights — vacant baseline (day) (%)"',
        "    min: 0", "    max: 100", "    step: 5",
        f"    initial: {VACANT_DAY_PCT}",
        "    unit_of_measurement: '%'",
        "    icon: mdi:weather-sunny",
        "    mode: slider",
        "  living_lights_vacant_night_pct:",
        '    name: "Living Lights — vacant baseline (night) (%)"',
        "    min: 0", "    max: 100", "    step: 5",
        f"    initial: {VACANT_NIGHT_PCT}",
        "    unit_of_measurement: '%'",
        "    icon: mdi:weather-night",
        "    mode: slider",
        "  living_lights_ramp_target_pct:",
        '    name: "Living Lights — present ramp target (%)"',
        "    min: 0", "    max: 100", "    step: 5",
        f"    initial: {RAMP_TARGET_PCT}",
        "    unit_of_measurement: '%'",
        "    icon: mdi:trending-up",
        "    mode: slider",
        "  living_lights_ramp_initial_pct:",
        '    name: "Living Lights — present ramp initial (%)"',
        "    min: 0", "    max: 100", "    step: 5",
        f"    initial: {RAMP_INITIAL_PCT}",
        "    unit_of_measurement: '%'",
        "    icon: mdi:speedometer-slow",
        "    mode: slider",
        "  living_lights_asleep_cap_pct:",
        '    name: "Living Lights — asleep occupied ceiling (%)"',
        "    min: 0", "    max: 100", "    step: 5",
        f"    initial: {ASLEEP_CAP_PCT}",
        "    unit_of_measurement: '%'",
        "    icon: mdi:sleep",
        "    mode: slider",
        "  living_lights_movie_dim_pct:",
        '    name: "Living Lights — TV-on dim target (%)"',
        "    min: 0", "    max: 100", "    step: 5",
        f"    initial: {MOVIE_DIM_PCT}",
        "    unit_of_measurement: '%'",
        "    icon: mdi:television",
        "    mode: slider",
        "  living_lights_gaming_dim_pct:",
        '    name: "Living Lights — gaming LR dim target (%)"',
        "    min: 0", "    max: 100", "    step: 1",
        f"    initial: {GAMING_DIM_PCT}",
        "    unit_of_measurement: '%'",
        "    icon: mdi:gamepad-variant",
        "    mode: slider",
        # ── Group 4: Time-of-day (CT buckets + factor multipliers) ──
        # Color temperature anchors per bucket. Drag a slider to make
        # that bucket warmer (lower K) or cooler (higher K).
        "  living_lights_ct_overnight_k:",
        '    name: "Living Lights — color temp (overnight)"',
        "    min: 1800", "    max: 4500", "    step: 50",
        "    initial: 2000",
        "    unit_of_measurement: 'K'",
        "    icon: mdi:weather-night",
        "    mode: slider",
        "  living_lights_ct_morning_k:",
        '    name: "Living Lights — color temp (morning)"',
        "    min: 1800", "    max: 4500", "    step: 50",
        "    initial: 2200",
        "    unit_of_measurement: 'K'",
        "    icon: mdi:weather-sunset-up",
        "    mode: slider",
        "  living_lights_ct_midday_k:",
        '    name: "Living Lights — color temp (midday)"',
        "    min: 1800", "    max: 4500", "    step: 50",
        "    initial: 2700",
        "    unit_of_measurement: 'K'",
        "    icon: mdi:weather-sunny",
        "    mode: slider",
        "  living_lights_ct_afternoon_k:",
        '    name: "Living Lights — color temp (afternoon)"',
        "    min: 1800", "    max: 4500", "    step: 50",
        "    initial: 3000",
        "    unit_of_measurement: 'K'",
        "    icon: mdi:weather-partly-cloudy",
        "    mode: slider",
        "  living_lights_ct_evening_k:",
        '    name: "Living Lights — color temp (evening)"',
        "    min: 1800", "    max: 4500", "    step: 50",
        "    initial: 2500",
        "    unit_of_measurement: 'K'",
        "    icon: mdi:weather-sunset-down",
        "    mode: slider",
        "  living_lights_ct_late_evening_k:",
        '    name: "Living Lights — color temp (late evening)"',
        "    min: 1800", "    max: 4500", "    step: 50",
        "    initial: 2200",
        "    unit_of_measurement: 'K'",
        "    icon: mdi:weather-night-partly-cloudy",
        "    mode: slider",
        # Brightness-factor multipliers. 1.0 = use the default ToD curve;
        # 1.2 = morning targets 20% brighter; 0.8 = morning 20% dimmer.
        # Range [0.5, 1.5] caps catastrophic mis-drags.
        "  living_lights_tod_factor_morning_mult:",
        '    name: "Living Lights — morning brightness multiplier"',
        "    min: 0.5", "    max: 1.5", "    step: 0.05",
        "    initial: 1.0",
        "    icon: mdi:weather-sunset-up",
        "    mode: slider",
        "  living_lights_tod_factor_midday_mult:",
        '    name: "Living Lights — midday brightness multiplier"',
        "    min: 0.5", "    max: 1.5", "    step: 0.05",
        "    initial: 1.0",
        "    icon: mdi:weather-sunny",
        "    mode: slider",
    ]
    return "\n".join(lines) + "\n"


def emit_input_datetime() -> str:
    lines = ["input_datetime:"]
    for slug in ZONES:
        lines.append(f"  living_lights_zone_{slug}_last_manual_at:")
        lines.append(f'    name: "{slug} — last manual touch"')
        lines.append("    has_date: true")
        lines.append("    has_time: true")
        lines.append("    icon: mdi:hand-back-right")
    # Per-zone rate-limit timestamp for LLM articulation (R5 P1-4).
    # One entry per zone with lights — kitchen/dining/LR zones plus the
    # whole_* aggregates kept as ZONES iterator items. We declare for ALL
    # zones (even unlit ones) for symmetry; the articulation automation
    # only ever stamps the ones that fired.
    for slug in ZONES:
        lines.append(f"  living_lights_zone_{slug}_last_articulated_at:")
        lines.append(f'    name: "{slug} — last LLM articulation"')
        lines.append("    has_date: true")
        lines.append("    has_time: true")
        lines.append("    icon: mdi:message-text-clock")
    return "\n".join(lines) + "\n"


def _present_target(is_watch: bool,
                    is_gaming_off: bool,
                    is_gaming_dim: bool) -> str:
    """Single-line Jinja for a `present` zone's brightness target — the
    confidence ramp's destination. Gaming, then working-hours, then
    movie, then V-JEPA 2 activity, then fall back to the default
    RAMP_TARGET_PCT.

    Gaming (R-G1) is the highest-priority MODIFIER in this cascade — when
    `gaming_active` is true and the zone is in GAMING_OFF_ZONES, the
    present target is 0 (office goes off even with the user at the desk).
    When in GAMING_DIM_ZONES, the present target is GAMING_DIM_PCT (LR
    stays dim even when the user walks past). Working-hours next: when
    `working_hours_active` is true (weekday 08-18 + woke-up latched + master
    on), the present target rises to `working_hours_present_pct` (default 95)
    house-wide so the desk + surrounds stay bright + responsive. Cap-clamped
    so per-bulb max calibration is respected (R-WH-4). Movie mode falls in
    next: a watch zone dims to floor while the TV plays. Then the V-JEPA 2
    activity seam (belief-gated). Default fallback: RAMP_TARGET_PCT,
    cap-clamped, floored at `floor`.

    `floor`, `cap`, `tv_playing`, `gaming_active`, `working_hours_active`,
    `working_hours_present_pct`, `belief`, `activity` are all in scope where
    this splices in. Activity targets are prescriptive — they are cap-clamped
    only, free to go below the floor (e.g. napping). Gaming targets are also
    free to go below the floor (e.g. office=0, LR=3 even during the day
    when floor>3). Working-hours intentionally dominates TV: a user watching
    background news during work still wants the bright workday surface."""
    branches = []
    opened = False
    if is_gaming_off:
        branches.append("{% if gaming_active %}0")
        opened = True
    elif is_gaming_dim:
        # gaming_dim_pct read from input_number with literal fallback (set
        # in the classifier template's variables block).
        branches.append("{% if gaming_active %}{{ gaming_dim_pct }}")
        opened = True
    # Working-hours is uniform across every zone — same target, same
    # input_number, same cap-clamp. Slots BETWEEN gaming and tv_playing
    # so a Steam-launched game still wins (gaming is a strong opt-in
    # user signal; the user can flip working_hours_enabled OFF for
    # gaming-on-workdays edge cases per R-WH-6).
    kw = "{% elif" if opened else "{% if"
    branches.append(kw + " working_hours_active %}"
                    + "{{ [working_hours_present_pct, cap] | min }}")
    opened = True
    if is_watch:
        branches.append("{% elif tv_playing %}{{ movie_dim_pct }}")
    for act, pct in ACTIVITY_PROFILES.items():
        branches.append("{% elif belief and activity == '" + act + "' %}"
                        + "{{ [" + str(pct) + ", cap] | min }}")
    # ramp_target_pct read from input_number with literal fallback in
    # the variables block; cap-clamped + floor-anchored.
    branches.append("{% else %}{{ [floor, [ramp_target_pct, cap] | min] | max }}{% endif %}")
    return "".join(branches)


# Per-zone classifier sensor. @@TOKEN@@ placeholders are substituted by
# str.replace (the gradient-generator pattern) — Jinja {% %} / {{ }} are
# literal here, so there is no brace-escaping to get wrong.
_CLASSIFIER_SENSOR = r"""      - name: "@@CAM_TITLE@@ @@SLUG_TITLE@@ Lighting State"
        unique_id: @@CAM@@_@@SLUG@@_lighting_state
        icon: mdi:state-machine
        state: >
          {% set manual = is_state('input_boolean.living_lights_override_@@SLUG@@', 'on') %}
          {% set away = is_state('input_boolean.user_at_home', 'off') %}
          {% set override_raw = states('input_text.living_lights_override_text_@@SLUG@@') %}
          {% set has_override = override_raw not in ['', 'unknown', 'unavailable', None] %}
          {% set night = is_state('binary_sensor.living_lights_is_night_safe', 'on') %}
          {% set stable_obj = states.@@STABLE_ENTITY@@ %}
          {% set stable = states('@@STABLE_ENTITY@@') %}
          {% set raw_obj = states.@@OCC_ENTITY@@ %}
          {% set raw_state = states('@@OCC_ENTITY@@') %}
          {% set t_end = raw_obj.last_changed if (raw_obj is not none and raw_state == 'off') else now() %}
          {% set dwell = ((t_end - stable_obj.last_changed).total_seconds() * 1000) | round(0) if (stable_obj is not none and stable == 'on') else 0 %}
          {% set speed = states('@@AVG_SPEED@@') | float(0) %}
          {# Phase 4 anticipation: only consulted when stable == 'off'.
             Master kill switch input_boolean.living_lights_anticipated_enabled
             gates the whole feature — OFF disables anticipation everywhere
             without touching the rest of the state machine. #}
          {% set anti_kill = is_state('input_boolean.living_lights_anticipated_enabled', 'on') %}
          {% set anti_tv = states('@@MOVIE_MP@@') not in ['off', 'unavailable', 'unknown'] %}
          {% set anti_obj = states.binary_sensor.anticipated_@@CAM@@ %}
          {% set anti_on = (not anti_tv) and anti_obj is not none and (anti_obj.state == 'on' or (anti_obj.last_changed and (now() - anti_obj.last_changed).total_seconds() < @@ANTI_DECAY@@)) %}
          {% if manual %}manual_override
          {% elif away %}away
          {% elif has_override %}presence_override
          {% elif night %}night_safe
          {% elif stable == 'off' and anti_kill and anti_on %}anticipated
          {% elif stable == 'off' %}vacant
          {% elif dwell < 2000 and speed >= 1.0 %}pass_through
          {% else %}present
          {% endif %}
        attributes:
          predicted_brightness_pct: >
            {% set manual = is_state('input_boolean.living_lights_override_@@SLUG@@', 'on') %}
            {% set away = is_state('input_boolean.user_at_home', 'off') %}
            {% set override_raw = states('input_text.living_lights_override_text_@@SLUG@@') %}
            {% set has_override = override_raw not in ['', 'unknown', 'unavailable', None] %}
            {% set night = is_state('binary_sensor.living_lights_is_night_safe', 'on') %}
            {% set asleep = is_state('input_boolean.living_lights_asleep', 'on') %}
            {% set tv_playing = states('@@MOVIE_MP@@') not in ['off', 'unavailable', 'unknown'] %}
            {# Gaming modifier — populated only when (a) master toggle is on
               AND (b) Steam's `game` attribute reports a running game. Defensive
               guards cover the three "no value" cases HA might surface. #}
            {% set gaming_active = is_state('input_boolean.living_lights_gaming_enabled', 'on')
                                   and state_attr('@@GAMING_SENSOR@@', 'game')
                                       not in [none, '', 'unavailable', 'unknown'] %}
            {# Working-hours modifier — read live from the working_hours_active
               composite binary_sensor (toggle + woke_up + weekday + 08-18). The
               input_number reads carry literal fallbacks matching the initial
               values so a missing helper degrades gracefully. #}
            {% set working_hours_active = is_state('binary_sensor.living_lights_working_hours_active', 'on') %}
            {% set working_hours_floor_pct = states('input_number.living_lights_working_hours_floor_pct') | int(@@WORKING_HOURS_FLOOR_INIT@@) %}
            {% set working_hours_present_pct = states('input_number.living_lights_working_hours_present_pct') | int(@@WORKING_HOURS_PRESENT_INIT@@) %}
            {# Promoted-constant reads — every literal that used to be baked
               into the template is now an input_number with a fallback to
               the original generator constant. Drag a slider, value
               propagates within ~5 s. If the helper goes missing the
               classifier degrades gracefully to the original value. #}
            {% set vacant_day_pct = states('input_number.living_lights_vacant_day_pct') | int(@@VACANT_DAY@@) %}
            {% set vacant_night_pct = states('input_number.living_lights_vacant_night_pct') | int(@@VACANT_NIGHT@@) %}
            {% set ramp_target_pct = states('input_number.living_lights_ramp_target_pct') | int(@@RAMP_TARGET@@) %}
            {% set asleep_cap_pct = states('input_number.living_lights_asleep_cap_pct') | int(@@ASLEEP_CAP@@) %}
            {% set movie_dim_pct = states('input_number.living_lights_movie_dim_pct') | int(@@MOVIE_DIM@@) %}
            {% set gaming_dim_pct = states('input_number.living_lights_gaming_dim_pct') | int(@@GAMING_DIM@@) %}
            {# House-wide bias knobs (additive, applied AFTER the cascade,
               clamped at cap and at 0). Scope gated by input_text:
               'all' = apply globally, slug name = only that zone. #}
            {% set brightness_bias_pp = states('input_number.living_lights_brightness_bias_pp') | int(0) %}
            {% set bias_scope = states('input_text.living_lights_bias_zone_scope') %}
            {% set bias_active = bias_scope in ['all', '@@SLUG@@'] %}
            {% set belief = is_state('input_boolean.living_lights_actuate_from_belief_changes', 'on') %}
            {% set activity = states('@@ACTIVITY_ENTITY@@') %}
            {% set stable_obj = states.@@STABLE_ENTITY@@ %}
            {% set stable = states('@@STABLE_ENTITY@@') %}
            {% set raw_obj = states.@@OCC_ENTITY@@ %}
            {% set raw_state = states('@@OCC_ENTITY@@') %}
            {% set t_end = raw_obj.last_changed if (raw_obj is not none and raw_state == 'off') else now() %}
            {% set dwell = ((t_end - stable_obj.last_changed).total_seconds() * 1000) | round(0) if (stable_obj is not none and stable == 'on') else 0 %}
            {% set speed = states('@@AVG_SPEED@@') | float(0) %}
            {% set tf = state_attr('sensor.living_lights_profile', 'tod_factor') | float(0.5) %}
            {# Cap is asleep-gated: a navigable ceiling only while actually asleep
               (you got up briefly); otherwise no ToD cap on occupied — an
               occupied room is always usable. Overnight gentleness is the
               asleep state's job, not a blanket clock cap. #}
            {% set cap = asleep_cap_pct if asleep else 100 %}
            {% set profile = states('sensor.living_lights_profile') %}
            {% set floor = 0 if asleep else (@@GAMING_FLOOR@@working_hours_floor_pct if working_hours_active else movie_dim_pct if tv_playing else (working_hours_floor_pct if working_hours_active else vacant_day_pct if profile in ['morning', 'midday', 'afternoon', 'evening'] else vacant_night_pct)) %}
            {# Phase 4 anticipation — same gating as the state branch. Brightness
               is floored at the vacant baseline so anticipation NEVER dims a
               vacant zone (the actuator's raise-only branch adds a second
               guard against dimming a manually-set light). #}
            {% set anti_kill = is_state('input_boolean.living_lights_anticipated_enabled', 'on') %}
            {% set anti_tv = states('@@MOVIE_MP@@') not in ['off', 'unavailable', 'unknown'] %}
            {% set anti_obj = states.binary_sensor.anticipated_@@CAM@@ %}
            {% set anti_on = (not anti_tv) and anti_obj is not none and (anti_obj.state == 'on' or (anti_obj.last_changed and (now() - anti_obj.last_changed).total_seconds() < @@ANTI_DECAY@@)) %}
            {# Capture the cascade output to a string via {% set %}...{% endset %},
               then add the additive brightness bias (scope-gated), then
               clamp at [0, cap]. The | trim | int chain parses the captured
               render back to an integer so arithmetic works. #}
            {% set raw_bri %}
            {% if manual or away or has_override %}0
            {% elif night %}8
            @@WATCH_TV_BRANCH@@{% elif stable == 'off' and anti_kill and anti_on %}{{ [floor, [@@ANTICIPATED_PCT@@, cap] | min] | max }}
            {% elif stable == 'off' %}{{ floor }}
            {% elif dwell < 2000 and speed >= 1.0 %}{{ [floor, [@@PASS_PCT@@, cap] | min] | max }}
            {% else %}@@PRESENT_TARGET@@{% endif %}
            {% endset %}
            {% set final_bri = (raw_bri | trim | int(0)) + (brightness_bias_pp if bias_active else 0) %}
            {{ [[0, final_bri] | max, cap] | min }}
          ramp_initial_pct: >
            {% set ramp_initial_pct = states('input_number.living_lights_ramp_initial_pct') | int(@@RAMP_INITIAL@@) %}
            {% set tf = state_attr('sensor.living_lights_profile', 'tod_factor') | float(0.5) %}
            {% set cap = state_attr('sensor.living_lights_profile', 'max_brightness_pct') | int(100) %}
            {{ [(ramp_initial_pct * tf) | round(0) | int, cap] | min }}
          predicted_color_temp_kelvin: >
            {% set night = is_state('binary_sensor.living_lights_is_night_safe', 'on') %}
            {% set warm = state_attr('sensor.living_lights_profile', 'tod_color_warm') | int(2700) %}
            {# Warmth bias — additive on the cascade output, scope-gated,
               clamped to AL min (2000) / max (4000) to keep Hue happy. #}
            {% set warmth_bias_k = states('input_number.living_lights_warmth_bias_k') | int(0) %}
            {% set bias_scope = states('input_text.living_lights_bias_zone_scope') %}
            {% set bias_active = bias_scope in ['all', '@@SLUG@@'] %}
            {% set raw_ct = 2000 if night else warm %}
            {% set final_ct = raw_ct + (warmth_bias_k if bias_active else 0) %}
            {{ [[2000, final_ct] | max, 4000] | min }}
          dwell_ms: >
            {% set stable = states.@@STABLE_ENTITY@@ %}
            {% if stable is not none and stable.state == 'on' %}
              {{ ((now() - stable.last_changed).total_seconds() * 1000) | round(0) }}
            {% else %}0{% endif %}
          speed_mps: "{{ states('@@AVG_SPEED@@') | float(0) }}"
          last_manual_at: >
            {{ states('input_datetime.living_lights_zone_@@SLUG@@_last_manual_at') }}"""


def emit_template() -> str:
    # Time-of-day profile + night_safe + per-zone dwell + per-zone classifier
    parts = ["template:"]

    # Profile sensor (time-driven + state-triggered on the per-bucket
    # CT input_numbers + ToD-factor multipliers so the Home app /lights
    # slider drags propagate within ~1 s).
    parts.append("""  - trigger:
      - platform: homeassistant
        event: start
      - platform: time_pattern
        minutes: "/1"
      - platform: state
        entity_id:
          - input_number.living_lights_ct_overnight_k
          - input_number.living_lights_ct_morning_k
          - input_number.living_lights_ct_midday_k
          - input_number.living_lights_ct_afternoon_k
          - input_number.living_lights_ct_evening_k
          - input_number.living_lights_ct_late_evening_k
          - input_number.living_lights_tod_factor_morning_mult
          - input_number.living_lights_tod_factor_midday_mult
    sensor:
      - name: "Living Lights Profile"
        unique_id: living_lights_profile
        icon: mdi:weather-sunset
        state: >
          {% set h = now().hour + now().minute/60 %}
          {% if h < 6 %}overnight
          {% elif h < 9 %}morning
          {% elif h < 13 %}midday
          {% elif h < 17 %}afternoon
          {% elif h < 20 %}evening
          {% elif h < 22.5 %}late_evening
          {% else %}overnight
          {% endif %}
        attributes:
          tod_factor: >
            {% set h = now().hour + now().minute/60 %}
            {# Morning + midday multipliers (default 1.0 = use the
               built-in curve). >1.0 brightens that bucket;
               <1.0 dims. Other buckets keep formulas literal in v1. #}
            {% set morning_mult = states('input_number.living_lights_tod_factor_morning_mult') | float(1.0) %}
            {% set midday_mult = states('input_number.living_lights_tod_factor_midday_mult') | float(1.0) %}
            {% if h < 6 %}0.10
            {% elif h < 9 %}{{ ((0.30 + (h-6)/3 * 0.50) * morning_mult) | round(2) }}
            {% elif h < 13 %}{{ ((0.80 + (h-9)/4 * 0.15) * midday_mult) | round(2) }}
            {% elif h < 17 %}0.95
            {% elif h < 20 %}{{ (0.85 - (h-17)/3 * 0.35) | round(2) }}
            {% elif h < 22.5 %}{{ (0.50 - (h-20)/2.5 * 0.30) | round(2) }}
            {% else %}0.10
            {% endif %}
          tod_color_warm: >
            {% set h = now().hour + now().minute/60 %}
            {# Per-bucket CT anchors — promoted from baked literals so the
               user can drag a single bucket warmer/cooler without affecting
               the others. Fallbacks match the original baked defaults. #}
            {% set ct_overnight = states('input_number.living_lights_ct_overnight_k') | int(2000) %}
            {% set ct_morning = states('input_number.living_lights_ct_morning_k') | int(2200) %}
            {% set ct_midday = states('input_number.living_lights_ct_midday_k') | int(2700) %}
            {% set ct_afternoon = states('input_number.living_lights_ct_afternoon_k') | int(3000) %}
            {% set ct_evening = states('input_number.living_lights_ct_evening_k') | int(2500) %}
            {% set ct_late_evening = states('input_number.living_lights_ct_late_evening_k') | int(2200) %}
            {% if h < 6 %}{{ ct_overnight }}
            {% elif h < 9 %}{{ ct_morning }}
            {% elif h < 13 %}{{ ct_midday }}
            {% elif h < 17 %}{{ ct_afternoon }}
            {% elif h < 20 %}{{ ct_evening }}
            {% elif h < 22.5 %}{{ ct_late_evening }}
            {% else %}{{ ct_overnight }}
            {% endif %}
          tod_color_cool: >
            {% set h = now().hour + now().minute/60 %}
            {% if h < 6 %}2200
            {% elif h < 9 %}2700
            {% elif h < 13 %}4000
            {% elif h < 17 %}4500
            {% elif h < 20 %}3000
            {% elif h < 22.5 %}2500
            {% else %}2200
            {% endif %}
          max_brightness_pct: >
            {% set h = now().hour + now().minute/60 %}
            {% if (h < 6) or (h >= 22.5) %}8
            {% elif h >= 20 %}50
            {% else %}100
            {% endif %}
          transition_s: >
            {% set h = now().hour + now().minute/60 %}
            {% if (h < 6) or (h >= 22.5) %}4.0
            {% elif h >= 20 %}2.5
            {% else %}1.5
            {% endif %}""")

    # Night-safe binary_sensor — Layer 2: keyed on input_boolean.homeai_sleep
    # ONLY. The overnight clock no longer masks occupancy (it used to force
    # every zone to night_safe overnight, which would defeat the confidence
    # ramp + the asleep auto-detection). Overnight gentleness now comes from
    # the profile's max_brightness_pct cap + the asleep modifier.
    parts.append("""  - trigger:
      - platform: homeassistant
        event: start
      - platform: state
        entity_id:
          - input_boolean.homeai_sleep
    binary_sensor:
      - name: "Living Lights is night-safe"
        unique_id: living_lights_is_night_safe
        icon: mdi:weather-night
        state: >
          {{ is_state('input_boolean.homeai_sleep', 'on') }}""")

    # Working-hours binary_sensor — composite of: master toggle on AND
    # woke-up latch on AND weekday AND time within 08:00-18:00. The
    # time_pattern minutes "/1" trigger ensures the 08:00 + 18:00
    # boundary crossings are picked up within 60 s (R-WH-13). State
    # changes on the two booleans fire instantly via state triggers.
    parts.append("""  - trigger:
      - platform: homeassistant
        event: start
      - platform: state
        entity_id:
          - input_boolean.living_lights_working_hours_enabled
          - input_boolean.living_lights_woke_up_today
      - platform: time_pattern
        minutes: "/1"
    binary_sensor:
      - name: "Living Lights working hours active"
        unique_id: living_lights_working_hours_active
        icon: mdi:briefcase-clock
        state: >
          {% set h = now().hour + now().minute / 60 %}
          {% set is_weekday = now().weekday() < 5 %}
          {% set in_window = 8 <= h < 18 %}
          {{ is_state('input_boolean.living_lights_working_hours_enabled', 'on')
             and is_state('input_boolean.living_lights_woke_up_today', 'on')
             and is_weekday
             and in_window }}""")

    # Per-zone dwell sensors (one trigger block per camera batch)
    occupancy_entity_ids = [_slug_to_entity_id(s) for s in ZONES]
    dwell_block_triggers = """  - trigger:
      - platform: homeassistant
        event: start
      - platform: state
        entity_id:
""" + "".join(f"          - {e}\n" for e in occupancy_entity_ids) + """      - platform: time_pattern
        seconds: "/15"
    sensor:"""

    dwell_sensors_text = []
    for slug, meta in ZONES.items():
        cam = meta["camera"]
        occ_entity = _slug_to_entity_id(slug)
        stable_entity = f"binary_sensor.{cam}_{slug}_person_occupancy_stable"
        # Dwell now reads the STABLE occupancy sensor's last_changed instead
        # of the raw Frigate sensor. This absorbs Frigate's rapid off-on
        # flapping (observed: 14 transitions/5s at the sink) so dwell
        # reflects the true continuous-presence start, not the latest
        # Frigate hiccup.
        dwell_sensors_text.append(f"""      - name: "{cam.replace('_',' ').title()} {slug.replace('_',' ').title()} Dwell"
        unique_id: {cam}_{slug}_dwell_ms
        icon: mdi:timer-sand
        unit_of_measurement: "ms"
        state: >
          {{% set stable = states.{stable_entity} %}}
          {{% set raw = states.{occ_entity} %}}
          {{% if stable is not none and stable.state == 'on' %}}
            {{% if raw is not none and raw.state == 'off' %}}
              {{{{ ((raw.last_changed - stable.last_changed).total_seconds() * 1000) | round(0) }}}}
            {{% else %}}
              {{{{ ((now() - stable.last_changed).total_seconds() * 1000) | round(0) }}}}
            {{% endif %}}
          {{% else %}}0{{% endif %}}""")

    parts.append(dwell_block_triggers + "\n" + "\n\n".join(dwell_sensors_text))

    # ─────────────────────────────────────────────────────────────────
    # STABLE OCCUPANCY binary_sensors per zone — absorbs Frigate flapping.
    # ON when raw occupancy is ON OR was ON within the last 20 seconds.
    # The classifier uses this for dwell instead of the raw sensor so
    # rapid Frigate off-on cycles don't reset dwell.
    # ─────────────────────────────────────────────────────────────────
    stable_trigger_entities = []
    for slug in ZONES:
        stable_trigger_entities.append(_slug_to_entity_id(slug))
    stable_block_triggers = """  - trigger:
      - platform: homeassistant
        event: start
      - platform: state
        entity_id:
""" + "".join(f"          - {e}\n" for e in stable_trigger_entities) + """      - platform: time_pattern
        seconds: "/3"
    binary_sensor:"""
    stable_sensors_text = []
    for slug, meta in ZONES.items():
        cam = meta["camera"]
        occ_entity = _slug_to_entity_id(slug)
        cam_title = cam.replace('_', ' ').title()
        slug_title = slug.replace('_', ' ').title()
        stable_sensors_text.append(f"""      - name: "{cam_title} {slug_title} Person Occupancy Stable"
        unique_id: {cam}_{slug}_person_occupancy_stable
        device_class: occupancy
        icon: mdi:account-check
        state: >
          {{% set occ = states.{occ_entity} %}}
          {{% if occ is none %}}off
          {{% elif occ.state == 'on' %}}on
          {{% elif (now() - occ.last_changed).total_seconds() < 20 %}}on
          {{% else %}}off{{% endif %}}""")
    parts.append(stable_block_triggers + "\n" + "\n\n".join(stable_sensors_text))

    # Per-zone classifier sensors (one shared trigger block).
    classifier_trigger_entities = []
    for slug in ZONES:
        classifier_trigger_entities.append(_slug_to_entity_id(slug))
    # Also trigger on stable-occupancy changes so the classifier reacts to
    # the de-flapped signal rather than only to raw Frigate state changes.
    for slug, meta in ZONES.items():
        classifier_trigger_entities.append(
            f"binary_sensor.{meta['camera']}_{slug}_person_occupancy_stable")
    for slug in ZONES:
        classifier_trigger_entities.append(
            f"input_boolean.living_lights_override_{slug}")
    for slug in ZONES:
        classifier_trigger_entities.append(
            f"input_text.living_lights_override_text_{slug}")
    classifier_trigger_entities.append("binary_sensor.living_lights_is_night_safe")
    # TV state -> classifier re-eval so the movie modifier applies.
    classifier_trigger_entities.append(MOVIE_MEDIA_PLAYER)
    # Layer 2 — away / asleep / V-JEPA 2 belief toggle re-eval the classifier.
    classifier_trigger_entities.append("input_boolean.user_at_home")
    classifier_trigger_entities.append("input_boolean.living_lights_asleep")
    classifier_trigger_entities.append(
        "input_boolean.living_lights_actuate_from_belief_changes")
    # Phase 4 anticipation: classifier re-evals when killswitch toggles OR
    # any anticipated_<room> sensor flips. One sensor per camera/room — the
    # anticipator publishes binary_sensor.anticipated_<camera_name>.
    classifier_trigger_entities.append(
        "input_boolean.living_lights_anticipated_enabled")
    for room in sorted(set(meta["camera"] for meta in ZONES.values())):
        classifier_trigger_entities.append(f"binary_sensor.anticipated_{room}")
    # User-tunable input_numbers (live-tunable knobs) — trigger the
    # classifier so a slider drag in the Home app /lights drawer
    # propagates within ~1 s rather than waiting for the 5-s time_pattern.
    for inum in [
        "living_lights_working_hours_floor_pct",
        "living_lights_working_hours_present_pct",
        "living_lights_warmth_bias_k",
        "living_lights_brightness_bias_pp",
        "living_lights_vacant_day_pct",
        "living_lights_vacant_night_pct",
        "living_lights_ramp_target_pct",
        "living_lights_ramp_initial_pct",
        "living_lights_asleep_cap_pct",
        "living_lights_movie_dim_pct",
        "living_lights_gaming_dim_pct",
    ]:
        classifier_trigger_entities.append(f"input_number.{inum}")
    # Bias scope helper — flipping 'all' <-> '<slug>' should also
    # immediately re-evaluate all classifiers (bias may apply or not).
    classifier_trigger_entities.append("input_text.living_lights_bias_zone_scope")
    # Working-hours composite binary_sensor — needed so a slider drag
    # AND a state change of the underlying input_booleans both flow.
    classifier_trigger_entities.append("binary_sensor.living_lights_working_hours_active")
    # Living Lights Profile — classifiers read tod_factor / tod_color_warm
    # from this sensor's attributes. Adding it as a state-trigger means
    # CT-bucket + tod_factor multiplier slider drags propagate to every
    # classifier within ~1 s (instead of waiting for the classifier's
    # own /5-s time_pattern).
    classifier_trigger_entities.append("sensor.living_lights_profile")

    classifier_block_triggers = """  - trigger:
      - platform: homeassistant
        event: start
      - platform: state
        entity_id:
""" + "".join(f"          - {e}\n" for e in classifier_trigger_entities) + """      - platform: time_pattern
        seconds: "/5"
    sensor:"""

    classifier_sensors_text = []
    for slug, meta in ZONES.items():
        cam = meta["camera"]
        occ_entity = _slug_to_entity_id(slug)
        stable_entity = f"binary_sensor.{cam}_{slug}_person_occupancy_stable"
        avg_speed_id = f"sensor.{cam}_{slug}_avg_speed"
        activity_entity = f"sensor.{cam}_{slug}_activity"
        cam_title = cam.replace('_', ' ').title()
        slug_title = slug.replace('_', ' ').title()
        present_target = _present_target(
            slug in MOVIE_WATCH_ZONES,
            slug in GAMING_OFF_ZONES,
            slug in GAMING_DIM_ZONES,
        )
        # Per-zone gaming-floor prefix injected into the floor cascade. Reads
        # `0 if gaming_active else` (office) or `gaming_dim_pct if gaming_active else`
        # (LR watch zones); empty for all other zones (no gaming effect on
        # kitchen / dining / driveway / workshop, etc.). The `gaming_dim_pct`
        # variable is set in the template's variables block; live-tunable
        # via input_number.living_lights_gaming_dim_pct.
        gaming_floor = (
            "0 if gaming_active else " if slug in GAMING_OFF_ZONES
            else "gaming_dim_pct if gaming_active else " if slug in GAMING_DIM_ZONES
            else ""
        )
        sensor_yaml = (_CLASSIFIER_SENSOR
                       .replace("@@CAM_TITLE@@", cam_title)
                       .replace("@@SLUG_TITLE@@", slug_title)
                       .replace("@@CAM@@", cam)
                       .replace("@@SLUG@@", slug)
                       .replace("@@STABLE_ENTITY@@", stable_entity)
                       .replace("@@OCC_ENTITY@@", occ_entity)
                       .replace("@@AVG_SPEED@@", avg_speed_id)
                       .replace("@@ACTIVITY_ENTITY@@", activity_entity)
                       .replace("@@MOVIE_DIM@@", str(MOVIE_DIM_PCT))
                       .replace("@@MOVIE_MP@@", MOVIE_MEDIA_PLAYER)
                       .replace("@@GAMING_SENSOR@@", GAMING_SENSOR)
                       .replace("@@GAMING_FLOOR@@", gaming_floor)
                       .replace("@@GAMING_DIM@@", str(GAMING_DIM_PCT))
                       .replace("@@RAMP_TARGET@@", str(RAMP_TARGET_PCT))
                       .replace("@@WORKING_HOURS_FLOOR_INIT@@",
                                str(WORKING_HOURS_FLOOR_PCT_INITIAL))
                       .replace("@@WORKING_HOURS_PRESENT_INIT@@",
                                str(WORKING_HOURS_PRESENT_PCT_INITIAL))
                       .replace("@@VACANT_DAY@@", str(VACANT_DAY_PCT))
                       .replace("@@VACANT_NIGHT@@", str(VACANT_NIGHT_PCT))
                       .replace("@@ANTICIPATED_PCT@@", str(ANTICIPATED_PCT))
                       .replace("@@ANTI_DECAY@@", str(ANTICIPATED_DECAY_S))
                       .replace(
                           "@@WATCH_TV_BRANCH@@",
                           "{% elif tv_playing %}{{ floor }}\n            "
                           if slug in MOVIE_WATCH_ZONES else "")
                       .replace("@@PASS_PCT@@", str(PASS_PCT))
                       .replace("@@RAMP_INITIAL@@", str(RAMP_INITIAL_PCT))
                       .replace("@@ASLEEP_CAP@@", str(ASLEEP_CAP_PCT))
                       .replace("@@PRESENT_TARGET@@", present_target))
        classifier_sensors_text.append(sensor_yaml)

    parts.append(classifier_block_triggers + "\n"
                 + "\n\n".join(classifier_sensors_text))

    # House-wide any-occupied binary_sensor — drives the asleep auto-detect.
    # ON if any zone classifier is `present` or `pass_through`.
    classifier_sensor_ids = [f"sensor.{meta['camera']}_{slug}_lighting_state"
                             for slug, meta in ZONES.items()]
    any_occ_checks = "".join(
        "          {% if states('" + e + "') in ['present', 'pass_through'] "
        "%}{% set ns.occ = true %}{% endif %}\n"
        for e in classifier_sensor_ids)
    any_occ_block = ("""  - trigger:
      - platform: homeassistant
        event: start
      - platform: state
        entity_id:
""" + "".join("          - " + e + "\n" for e in classifier_sensor_ids)
        + """      - platform: time_pattern
        minutes: "/2"
    binary_sensor:
      - name: "Living Lights Any Occupied"
        unique_id: living_lights_any_occupied
        device_class: occupancy
        icon: mdi:account-group
        state: >
          {% set ns = namespace(occ=false) %}
"""
        + any_occ_checks
        + "          {{ ns.occ }}")
    parts.append(any_occ_block)

    return "\n\n".join(parts) + "\n"


def emit_mqtt() -> str:
    lines = ["mqtt:", "  sensor:"]
    for slug, meta in ZONES.items():
        cam = meta["camera"]
        cam_title = cam.replace('_', ' ').title()
        slug_title = slug.replace('_', ' ').title()
        unique_id = f"{cam}_{slug}_avg_speed"
        prior_sensor = f"sensor.{cam}_{slug}_avg_speed"
        lines.append(f'''    - name: "{cam_title} {slug_title} Avg Speed"
      unique_id: {unique_id}
      state_topic: "frigate/events"
      unit_of_measurement: "m/s"
      icon: mdi:speedometer
      value_template: >
        {{% set j = value_json %}}
        {{% set after = j.after if j is mapping and 'after' in j else {{}} %}}
        {{% set zones = after.current_zones if after is mapping and 'current_zones' in after else [] %}}
        {{% set cam = after.camera if after is mapping else '' %}}
        {{% set lbl = after.label if after is mapping else '' %}}
        {{% if cam == '{cam}' and lbl == 'person' and '{slug}' in zones %}}
          {{{{ (after.average_estimated_speed | float(0)) | round(2) }}}}
        {{% else %}}
          {{{{ states('{prior_sensor}') | float(0) }}}}
        {{% endif %}}''')
    return "\n".join(lines) + "\n"


def emit_automations() -> str:
    """The package's single `automation:` block: the asleep ON/OFF
    automations.

    MD.1 — the prior "refresh classifier + dwell" belt-and-suspenders
    automation (5-min time_pattern + homeassistant.update_entity on every
    classifier + dwell sensor) was removed. Calling update_entity on a
    trigger-based template sensor invokes the coordinator's
    `_async_update_data` which raises NotImplementedError — generating
    recurring "Update for sensor.<...>_dwell fails" log noise without
    any functional benefit. The classifier sensors already have their
    own state-triggers (occupancy, anticipated, etc.) + a 5-second
    time_pattern; dwell sensors have a 15-second time_pattern. Pulling
    a 5-minute refresh on top added zero refresh value and produced
    only noise. The startup-refresh case is covered by HA's normal
    startup state-load (trigger templates initialize on `homeassistant`
    event during boot).
    """
    lines = ["automation:"]
    # ── Asleep ON: house quiet ASLEEP_IDLE_MINUTES, overnight, user home ──
    lines += [
        '  - alias: "Living Lights — asleep ON (house quiet overnight)"',
        "    id: living_lights_asleep_on",
        "    mode: single",
        "    triggers:",
        "      - trigger: state",
        "        entity_id: binary_sensor.living_lights_any_occupied",
        '        to: "off"',
        "        for:",
        f"          minutes: {ASLEEP_IDLE_MINUTES}",
        "    conditions:",
        "      - condition: state",
        "        entity_id: input_boolean.living_lights_enabled",
        '        state: "on"',
        "      - condition: state",
        "        entity_id: sensor.living_lights_profile",
        "        state: overnight",
        "      - condition: state",
        "        entity_id: input_boolean.user_at_home",
        '        state: "on"',
        "      - condition: state",
        "        entity_id: input_boolean.living_lights_asleep",
        '        state: "off"',
        "    actions:",
        "      - action: input_boolean.turn_on",
        "        target:",
        "          entity_id: input_boolean.living_lights_asleep",
        # ── Asleep OFF: sustained activity (genuinely up) or midday backstop ──
        '  - alias: "Living Lights — asleep OFF (genuinely up / midday backstop)"',
        "    id: living_lights_asleep_off",
        "    mode: single",
        "    triggers:",
        "      - trigger: state",
        "        entity_id: binary_sensor.living_lights_any_occupied",
        '        to: "on"',
        "        for:",
        f"          minutes: {ASLEEP_WAKE_MINUTES}",
        "      - trigger: state",
        "        entity_id: sensor.living_lights_profile",
        "        to: midday",
        "      - trigger: state",
        "        entity_id: input_boolean.user_at_home",
        '        to: "on"',
        "    conditions:",
        "      - condition: state",
        "        entity_id: input_boolean.living_lights_asleep",
        '        state: "on"',
        "    actions:",
        "      - action: input_boolean.turn_off",
        "        target:",
        "          entity_id: input_boolean.living_lights_asleep",
        # ── LLM articulation of manual overrides (opt-in, default OFF) ──
        # Listens for living_lights_override_detected. When the user has
        # opted in (input_boolean.living_lights_articulate_overrides = on),
        # call conversation.process with a tight one-sentence-only prompt
        # and emit a living_lights_articulation event for the Home app to
        # render. 30-s per-zone rate-limit via input_datetime.
        '  - alias: "Living Lights — articulate manual override via LLM"',
        "    id: living_lights_articulate_override",
        "    mode: queued",
        "    max: 10",
        "    triggers:",
        "      - trigger: event",
        "        event_type: living_lights_override_detected",
        "    conditions:",
        "      - condition: state",
        "        entity_id: input_boolean.living_lights_articulate_overrides",
        '        state: "on"',
        # Per-zone rate-limit (30 s).
        "      - condition: template",
        "        value_template: >-",
        "          {% set zone = trigger.event.data.zone %}",
        "          {% set last = states('input_datetime.living_lights_zone_' ~ zone ~ '_last_articulated_at') %}",
        "          {% if last and last not in ['unknown', 'unavailable', 'none', ''] %}",
        "            {{ (now() - as_datetime(last | string)).total_seconds() > 30 }}",
        "          {% else %}true{% endif %}",
        "    actions:",
        # Stamp the rate-limit BEFORE the slow LLM call so a backlog of
        # events doesn't all sneak past the gate while one is in flight.
        "      - action: input_datetime.set_datetime",
        "        target:",
        '          entity_id: "input_datetime.living_lights_zone_{{ trigger.event.data.zone }}_last_articulated_at"',
        "        data:",
        "          datetime: \"{{ now().strftime('%Y-%m-%d %H:%M:%S') }}\"",
        # Call the agent with a strict positive-instruction prompt. Capture
        # the response via response_variable, then fire a feed event that
        # the Home app subscribes to.
        "      - action: conversation.process",
        "        data:",
        "          text: >-",
        "            The user manually set the {{ trigger.event.data.zone | replace('_', ' ') }}",
        "            light to {{ trigger.event.data.actual_pct }}%. The system was targeting",
        "            {{ trigger.event.data.predicted_pct }}%. Context: profile is",
        "            {{ trigger.event.data.profile }}, asleep is {{ trigger.event.data.asleep }},",
        "            TV is {{ trigger.event.data.tv_playing }}.",
        "            Write exactly one short acknowledgement and stop after the acknowledgement.",
        "            Use friendly names (e.g. \"the office light\"), not entity IDs.",
        "            Example good: \"Okay, office light is at 30%.\"",
        "            Example bad: \"Want me to remember this for midday?\"",
        "        response_variable: agent_response",
        # Fire a feed event with the LLM's response. Defensively cap at
        # 200 chars in case the model ignores the one-sentence rule. The
        # default chain handles HA response-shape variation across versions.
        "      - event: living_lights_articulation",
        "        event_data:",
        '          zone: "{{ trigger.event.data.zone }}"',
        '          context_id: "{{ trigger.event.data.context_id }}"',
        "          text: >-",
        "            {{ (agent_response.response.speech.plain.speech",
        "               | default(agent_response.speech.plain.speech, true)",
        "               | default('Acknowledged.', true))[:200] }}",
        # ── Working-hours: morning latch (presence-driven, narrow window) ──
        # Trigger: sustained presence anywhere for 2 min during 05:00-12:00.
        # The 2-min debounce filters out phantom 5 AM Frigate flickers
        # (R-WH-14); the 05:00 lower bound prevents 03:00 bathroom trips
        # from latching. master toggle + woke_up_today gate is checked
        # in conditions so a re-deploy with woke_up already on doesn't
        # double-fire.
        '  - alias: "Living Lights — working hours morning latch"',
        "    id: living_lights_working_hours_morning_latch",
        "    mode: single",
        "    triggers:",
        "      - trigger: state",
        "        entity_id: binary_sensor.living_lights_any_occupied",
        '        to: "on"',
        "        for:",
        '          minutes: 2',
        "    conditions:",
        "      - condition: state",
        "        entity_id: input_boolean.living_lights_working_hours_enabled",
        '        state: "on"',
        "      - condition: state",
        "        entity_id: input_boolean.living_lights_woke_up_today",
        '        state: "off"',
        "      - condition: template",
        "        value_template: \"{{ 5 <= now().hour < 12 }}\"",
        "    actions:",
        "      - action: input_boolean.turn_on",
        "        target:",
        "          entity_id: input_boolean.living_lights_woke_up_today",
        # ── Working-hours: HA-restart catch-up ──
        # If HA restarts mid-workday with the user present + not asleep,
        # immediately arm the latch instead of waiting for the next presence
        # state transition (which may not come — the user may already be
        # at the desk for hours). R-WH-2 fold.
        '  - alias: "Living Lights — working hours start catch-up"',
        "    id: living_lights_working_hours_start_catchup",
        "    mode: single",
        "    triggers:",
        "      - trigger: homeassistant",
        "        event: start",
        "    conditions:",
        "      - condition: state",
        "        entity_id: input_boolean.living_lights_working_hours_enabled",
        '        state: "on"',
        "      - condition: state",
        "        entity_id: input_boolean.living_lights_woke_up_today",
        '        state: "off"',
        "      - condition: template",
        "        value_template: \"{{ 8 <= now().hour < 18 }}\"",
        "      - condition: state",
        "        entity_id: binary_sensor.living_lights_any_occupied",
        '        state: "on"',
        "      - condition: state",
        "        entity_id: input_boolean.living_lights_asleep",
        '        state: "off"',
        "    actions:",
        "      - action: input_boolean.turn_on",
        "        target:",
        "          entity_id: input_boolean.living_lights_woke_up_today",
        # ── Working-hours: morning resume (cron arm-if-still-here) ──
        # Handles the "user is in zone when the 04:00 reset fires" case.
        # No state transition has happened, so the morning_latch can't
        # fire. The cron tick at 05:00 + 08:00 re-arms if conditions hold.
        # R-WH-1 fold.
        '  - alias: "Living Lights — working hours morning resume"',
        "    id: living_lights_working_hours_morning_resume",
        "    mode: single",
        "    triggers:",
        "      - trigger: time",
        '        at: "05:00:00"',
        "      - trigger: time",
        '        at: "08:00:00"',
        "    conditions:",
        "      - condition: state",
        "        entity_id: input_boolean.living_lights_working_hours_enabled",
        '        state: "on"',
        "      - condition: state",
        "        entity_id: input_boolean.living_lights_woke_up_today",
        '        state: "off"',
        "      - condition: state",
        "        entity_id: binary_sensor.living_lights_any_occupied",
        '        state: "on"',
        "      - condition: state",
        "        entity_id: input_boolean.living_lights_asleep",
        '        state: "off"',
        "    actions:",
        "      - action: input_boolean.turn_on",
        "        target:",
        "          entity_id: input_boolean.living_lights_woke_up_today",
        # ── Working-hours: daily 04:00 reset ──
        # Resets the latch every day at 04:00. Moved from 00:00 (R-WH-1
        # fold) so users genuinely working past midnight aren't kicked
        # out of working-hours mid-session. Anyone working at 4 AM is
        # exceptional and can manually flip the latch back on.
        '  - alias: "Living Lights — working hours reset at 04:00"',
        "    id: living_lights_working_hours_reset_at_0400",
        "    mode: single",
        "    triggers:",
        "      - trigger: time",
        '        at: "04:00:00"',
        "    actions:",
        "      - action: input_boolean.turn_off",
        "        target:",
        "          entity_id: input_boolean.living_lights_woke_up_today",
        # ── Working-hours: asleep-triggered reset ──
        # If the user takes a sustained nap or goes to bed mid-day
        # (illness, jet lag, weekend Saturday-but-also-actually-asleep),
        # 10 min of asleep clears the latch so working_hours doesn't
        # blast the room when they get up briefly.
        '  - alias: "Living Lights — working hours reset on asleep"',
        "    id: living_lights_working_hours_reset_on_asleep",
        "    mode: single",
        "    triggers:",
        "      - trigger: state",
        "        entity_id: input_boolean.living_lights_asleep",
        '        to: "on"',
        "        for:",
        '          minutes: 10',
        "    actions:",
        "      - action: input_boolean.turn_off",
        "        target:",
        "          entity_id: input_boolean.living_lights_woke_up_today",
        # ── House-wide bias-changed event (preference evidence) ──
        # Fires whenever the user drags warmth_bias_k or brightness_bias_pp.
        # The override-evidence aggregator (intelligence service) can
        # consume these events to propose "user shifted morning warmth
        # +200K seven times → consider raising the morning ToD bucket
        # from 2200 → 2400 K". V1 just fires the event; aggregator-side
        # consumption is deferred. Captures the active modifier stack
        # at the moment of the drag so the proposal has context.
        '  - alias: "Living Lights — bias-changed event (preference evidence)"',
        "    id: living_lights_bias_changed_event",
        "    mode: queued",
        "    max: 10",
        "    triggers:",
        "      - trigger: state",
        "        entity_id:",
        "          - input_number.living_lights_warmth_bias_k",
        "          - input_number.living_lights_brightness_bias_pp",
        "    conditions:",
        # Skip the initial state-restoration on HA startup so we don't
        # flood the bus with synthetic 'change' events at boot.
        "      - condition: template",
        "        value_template: >-",
        "          {{ trigger.from_state is not none",
        "             and trigger.from_state.state not in ['unknown', 'unavailable']",
        "             and trigger.from_state.state != trigger.to_state.state }}",
        "    actions:",
        "      - event: living_lights_bias_changed",
        "        event_data:",
        "          ts: \"{{ now().isoformat() }}\"",
        "          kind: >-",
        "            {% if 'warmth_bias_k' in trigger.entity_id %}warmth_bias",
        "            {% else %}brightness_bias{% endif %}",
        "          entity_id: \"{{ trigger.entity_id }}\"",
        "          zone_scope: \"{{ states('input_text.living_lights_bias_zone_scope') }}\"",
        "          prior_value: \"{{ trigger.from_state.state | float(0) }}\"",
        "          new_value: \"{{ trigger.to_state.state | float(0) }}\"",
        "          delta: \"{{ (trigger.to_state.state | float(0)) - (trigger.from_state.state | float(0)) }}\"",
        "          profile: \"{{ states('sensor.living_lights_profile') }}\"",
        "          tv_playing: \"{{ states('media_player.lg_tv') in ['on', 'playing', 'paused', 'buffering'] }}\"",
        "          gaming_active: \"{{ is_state('input_boolean.living_lights_gaming_enabled', 'on') and state_attr('sensor.steam_steam_76561198136331341', 'game') not in [none, '', 'unavailable', 'unknown'] }}\"",
        "          working_hours_active: \"{{ is_state('binary_sensor.living_lights_working_hours_active', 'on') }}\"",
        "          asleep: \"{{ is_state('input_boolean.living_lights_asleep', 'on') }}\"",
        "          night_safe: \"{{ is_state('binary_sensor.living_lights_is_night_safe', 'on') }}\"",
        "          user_at_home: \"{{ is_state('input_boolean.user_at_home', 'on') }}\"",
        "          source_user_id: \"{{ trigger.to_state.context.user_id | default(none, true) }}\"",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", default="ha-config/packages/living_lights_observability.yaml",
                    help="Output file path (relative to repo root)")
    ap.add_argument("--zones", action="store_true",
                    help="List zone slugs and exit")
    args = ap.parse_args()

    if args.zones:
        print(f"{len(ZONES)} zones:")
        for slug, meta in ZONES.items():
            print(f"  {slug} (camera={meta['camera']}, vacant_pct={meta['vacant_pct']})")
        return 0

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    parts = [HEADER,
             emit_input_boolean(),
             emit_input_text(),
             emit_input_number(),
             emit_input_datetime(),
             emit_template(),
             emit_mqtt(),
             emit_automations()]
    text = "\n".join(parts)
    out_path.write_text(text, encoding="utf-8")
    print(f"wrote {out_path}: {len(text)} chars, {text.count(chr(10))} lines, {len(ZONES)} zones")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
