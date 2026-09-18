"""Every timing constant and threshold of the two lighting stories, in one place.

Story T (television) and story S (asleep) are state machines whose behaviour
is fixed by the numbers below. The tests replay scripted evenings and nights
against these constants, so changing one here changes what the fixtures
accept; nothing else in the package hard-codes a duration or a probability.

Units: durations are seconds (``*_S``), probabilities are in [0, 1] (``P_*``),
clock hours are local. ``now`` is always a timezone-aware ``datetime`` handed
in by the caller, which keeps every machine deterministic under test.
"""
from __future__ import annotations

import datetime as dt

# --- clocks and freshness -------------------------------------------------

HEARTBEAT_S = 60
"""``sensor.lighting_publisher_heartbeat`` is republished this often."""

PUBLISHER_STALE_S = 180
"""What the generated ``binary_sensor.living_lights_publisher_fresh`` allows
before it turns off (informational; the generator owns the template)."""

OBSERVER_FRESH_S = 15
"""An observer ``/api/state`` older than this is stale: its presence no longer
corroborates Frigate and health drops."""

OBSERVER_POLL_S = 5
"""How often the observer is polled."""

MIRROR_FRESH_S = 300
"""``living_lights/mirror/heartbeat`` older than this means Home Assistant's
mirror is stale: health drops and the estimator's latch input is untrusted."""

MIN_FLIP_INTERVAL_S = 8
"""No published boolean (``tv_watching``) flips faster than this."""

# --- story T: the television machine ---------------------------------------

TV_ON_STATES = ("on", "playing", "paused")
"""``media_player.lg_tv`` states that mean the screen is on."""

TV_OFF_STATES = ("off", "standby")
"""States that mean the screen is affirmatively off. ``unavailable`` and
``unknown`` are neither: they enter the unavailable grace."""

TV_UNAVAILABLE_GRACE_S = 600
"""``lg_tv`` reads ``unavailable`` in blips while on; the machine keeps its
state this long before treating the screen as off."""

PROVISIONAL_GRACE_S = 600
"""Alias of the unavailable grace as the plan names it for the PROVISIONAL
state (same number, same rule)."""

AWAY_HOLD_S = 30 * 60
"""After the sofa (and the room) empties, ``tv_watching`` stays on this long;
an errand to the kitchen returns to PROVISIONAL/WATCHING, a longer absence
ends in UNATTENDED."""

P_WATCHING_ATTENTION_GE2 = 0.60
"""WATCHING from a belief needs ``P(attention >= 2)`` at or above this ..."""
WATCHING_COMMITS = 2
"""... on this many consecutive belief commits."""

P_UNATTENDED_ATTENTION_EQ0 = 0.80
"""UNATTENDED from a belief needs ``P(attention == 0)`` at or above this ..."""
UNATTENDED_OCCUPIED_S = 3 * 60
"""... sustained this long while the machine was WATCHING ..."""
UNATTENDED_PROVISIONAL_S = 5 * 60
"""... or this long while it was only PROVISIONAL. Never while the sofa's
stable occupancy or a living-room track is on."""

P_NAPPING_REST_EQ2 = 0.60
"""NAPPING needs ``P(rest == 2)`` at or above this ..."""
NAPPING_S = 5 * 60
"""... sustained this long, and only ever from a belief."""

# --- story S: the asleep estimator ------------------------------------------

NIGHT_WINDOW_START_H = 0
NIGHT_WINDOW_END_H = 8
"""``likely_asleep`` may be entered when the local hour is in
[00:00, 08:00) or the profile is ``overnight``."""

OVERNIGHT_PROFILE = "overnight"

IDLE_S = 15 * 60
"""Quiet minutes (no credible person anywhere) before ``likely_asleep``."""

BRIGHTEN_QUIET_S = 15 * 60
"""No explicit brighten command within this before ``likely_asleep``."""

P_TV_ATTENTION_EQ0 = 0.80
TV_ATTENTION_QUIET_S = 15 * 60
"""With beliefs, a TV that is on needs ``P(attention == 0) >= 0.80`` for this
long. Without beliefs the legacy rule applies: the TV state is ignored and
the evidence records ``no_answer_tv_rule = legacy``."""

WAKE_S = 10 * 60
"""Credible occupancy this long, at any hour, exits ``likely_asleep``. The
run counts up to the moment it ends, so a trip that lasts exactly this long
still clears the latch on the tick the cameras go quiet again."""

MORNING_WAKE_START_H = 4.5
MORNING_WAKE_END_H = 11.0
"""The morning window of the legacy ``woke up (house up 15 min in the
morning)`` automation, in local hours."""

MORNING_UP_S = 15 * 60
"""Credible occupancy sustained this long inside the morning window means the
household is up for the day: no new ``likely_asleep`` until the window
closes. A ten-minute night excursion never reaches it (that is why this is
the idle window and not ``WAKE_S``), while a guest who is on camera all
night and leaves at 07:00 has plainly not just gone to bed."""

REARM_S = 45 * 60
"""After a manual flip of the latch the estimator follows it this long."""
MANUAL_FLIP_HONORED_S = REARM_S
MANUAL_WRITERS = ("manual",)
"""``input_text.living_lights_asleep_writer`` values that mean a person
flipped the latch by hand (compared case-insensitively)."""

ARRIVAL_DOOR_WINDOW_S = 60
"""A credible arrival: ``user_at_home`` turning on and front-door occupancy
within this of each other, in either order."""

DEPARTURE_DOOR_WINDOW_S = 10 * 60
"""A credible departure: ``user_at_home`` off with front-door occupancy in
the preceding window, or ..."""
DEPARTURE_CONFIRM_S = 10 * 60
"""... ``user_at_home`` off for this long with no credible person since. A
20 s presence reconnect is neither."""

# --- activity sensors --------------------------------------------------------

P_COOKING_FOOD_PREP_GE2 = 0.60
P_EATING = 0.60
ACTIVITY_SUSTAIN_S = 30
"""``cooking`` needs ``P(food_prep >= 2)`` and ``eating`` needs ``P(eating)``
at or above their thresholds for this long, in an occupied kitchen or dining
zone. Everything else is ``idle`` in this milestone."""

ACTIVITY_VALUES = ("cooking", "eating", "napping", "reading", "watching_tv",
                   "working", "working_out", "idle")
"""The generator's vocabulary; only cooking, eating and idle are published."""

# --- shared helpers ---------------------------------------------------------


def seconds_between(earlier: dt.datetime | None, later: dt.datetime) -> float | None:
    """Seconds from ``earlier`` to ``later``; None when ``earlier`` is unknown."""
    if earlier is None:
        return None
    return (later - earlier).total_seconds()


def elapsed_at_least(earlier: dt.datetime | None, later: dt.datetime, seconds: float) -> bool:
    """True when ``earlier`` is known and at least ``seconds`` before ``later``."""
    gap = seconds_between(earlier, later)
    return gap is not None and gap >= seconds


def in_night_window(local_time: dt.datetime) -> bool:
    """True in [NIGHT_WINDOW_START_H, NIGHT_WINDOW_END_H) local."""
    return NIGHT_WINDOW_START_H <= local_time.hour < NIGHT_WINDOW_END_H


def in_morning_window(local_time: dt.datetime) -> bool:
    """True in [MORNING_WAKE_START_H, MORNING_WAKE_END_H) local, half hours
    included (the legacy template compares ``hour + minute / 60``)."""
    hours = local_time.hour + local_time.minute / 60
    return MORNING_WAKE_START_H <= hours < MORNING_WAKE_END_H


def iso(value: dt.datetime | None) -> str | None:
    """ISO 8601 with seconds, or None."""
    return None if value is None else value.isoformat(timespec="seconds")
