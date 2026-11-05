#!/usr/bin/env python3
"""Rung 8 Stage 1's probe safety rules. Pure: no I/O, no clock, no network.

probe.py feeds these functions what it reads from Home Assistant and the
camera, and does what they return. The rules are the plan's "Probe safety
with den_lighting" (docs/plans/rung-8-light-model/PLAN.md): the start checks,
which bulb states are the tool's, when to abort, and the restore order.
Everything here is about den_lighting 1.0.4; a different store version or
sensor attribute set is refused.
"""
import json
import math

LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
PAIRS = (LIGHTS[:2], LIGHTS[2:])  # front and rear: den_lighting's WARMEST suggests two bulb types
OCCUPANCY = "binary_sensor.den_oak_person_occupancy"
INCUMBENT = "automation.den_lights_follow_person_occupancy"
SWITCH_NAME = "Den lighting automatic"
SENSOR_NAME = "Den lighting control"
STORE_VERSION = 1
STATUS_KEYS = frozenset({"automatic", "gaming_enabled", "gaming_session", "gaming_signal", "occupancy",
                         "revision", "holds", "error", "gaming_entity", "occupancy_source",
                         "pending_fixtures", "provisional_fixtures"})
HA_KEYS = frozenset({"friendly_name", "icon"})
PROBE_CHANGE = "den-light-probe-1"  # how probes.jsonl names a non-human change
VACANCY_S = 120                     # den_lighting's VACANCY_SECONDS


class Refused(Exception):
    """A start check failed: nothing was paused or written."""


# ---- scenes ---------------------------------------------------------------------

def scene(on, brightness=None, kelvin=None):
    return {"on": bool(on), "brightness": brightness, "kelvin": kelvin}


def scene_of_state(state):
    """A light's state as a scene, or None when it is unavailable or unknown (not learned)."""
    if not isinstance(state, dict) or state.get("state") not in ("on", "off"):
        return None
    attributes = state.get("attributes") or {}
    b, k = attributes.get("brightness"), attributes.get("color_temp_kelvin")
    return scene(state["state"] == "on", int(b) if isinstance(b, (int, float)) else None,
                 int(k) if isinstance(k, (int, float)) else None)


def mired(kelvin):
    return 1e6 / kelvin


def matches(target, actual):
    """den_lighting's Scene.matches(): on equal; brightness within 1 and mired within 2 when on."""
    if actual is None or target["on"] != actual["on"]:
        return False
    if not target["on"]:
        return True
    if target["brightness"] is not None and (actual["brightness"] is None
                                             or abs(target["brightness"] - actual["brightness"]) > 1):
        return False
    if target["kelvin"] is not None and (actual["kelvin"] is None
                                         or abs(mired(target["kelvin"]) - mired(actual["kelvin"])) > 2):
        return False
    return True


def hold_ok(expected, target, brightness_tol=1, mired_tol=2):
    """The restored-hold check: `on` exact, brightness within 1, mired within 2 (off holds too)."""
    if not isinstance(target, dict) or target.get("on") is not expected["on"]:
        return False
    for key, tolerance, convert in (("brightness", brightness_tol, float), ("kelvin", mired_tol, mired)):
        want, got = expected[key], target.get(key)
        if want is None:
            continue
        if type(got) not in (int, float) or abs(convert(want) - convert(got)) > tolerance:
            return False
    return True


# ---- the start ------------------------------------------------------------------

def read_store(text):
    """The normal-scene snapshot from the owner's copy of .storage/den_lighting.state; else Refused."""
    try:
        outer = json.loads(text.lstrip("﻿"))
    except ValueError:
        raise Refused("the store copy is not JSON") from None
    data = outer.get("data") if isinstance(outer, dict) and "data" in outer else outer
    if not isinstance(data, dict) or data.get("version") != STORE_VERSION:
        raise Refused(f"the store copy is not den_lighting store version {STORE_VERSION}")
    if data.get("enabled") is not True:
        raise Refused("the store copy has automatic off")
    if data.get("gaming") is not False:
        raise Refused("the store copy has a game on")
    if data.get("holds") or data.get("snapshot"):
        raise Refused("the store copy has holds or a game snapshot; wait until they clear, then copy it again")
    normal = data.get("normal")
    if not isinstance(normal, dict) or set(normal) != set(LIGHTS):
        raise Refused("the store copy's normal scene does not name all four bulbs")
    snapshot = {}
    for e in LIGHTS:
        s = normal[e]
        if (not isinstance(s, dict) or type(s.get("on")) is not bool or type(s.get("brightness")) is not int
                or type(s.get("kelvin")) is not int or not 1 <= s["brightness"] <= 255
                or not 1000 <= s["kelvin"] <= 20000):
            raise Refused(f"{e}'s normal scene has a null or bad brightness or kelvin; set it once in the HA app")
        snapshot[e] = scene(s["on"], s["brightness"], s["kelvin"])
    return snapshot


def find_entities(states):
    """(switch id, sensor id): exactly one of each by friendly name."""
    def named(domain, name):
        found = [s["entity_id"] for s in states if s.get("entity_id", "").startswith(domain + ".")
                 and (s.get("attributes") or {}).get("friendly_name") == name]
        if len(found) != 1:
            raise Refused(f"found {len(found)} {domain} entities named '{name}', not exactly one")
        return found[0]
    return named("switch", SWITCH_NAME), named("sensor", SENSOR_NAME)


def status_problems(attributes):
    """The sensor's attributes (not its state word) as a clean, automatic, idle den_lighting."""
    if not isinstance(attributes, dict):
        return ["the den lighting sensor has no attributes"]
    keys = set(attributes) - HA_KEYS
    if keys != STATUS_KEYS:
        return ["the den lighting sensor's attributes are not den_lighting 1.0.4's "
                f"(missing {sorted(STATUS_KEYS - keys)}, extra {sorted(keys - STATUS_KEYS)})"]
    problems = []
    if attributes["automatic"] is not True:
        problems.append("den_lighting automatic is off")
    if attributes["error"] is not None:
        problems.append(f"den_lighting reports an error ({attributes['error']})")
    if attributes["gaming_session"] is not False:
        problems.append("a game session is on")
    if attributes["pending_fixtures"] or attributes["provisional_fixtures"]:
        problems.append("den_lighting has pending or provisional fixtures")
    if attributes["holds"]:
        problems.append("den_lighting has holds")
    return problems


def start_problems(by_id, switch_id, sensor_id, snapshot_problem=None):
    """Every reason not to start, from one read of HA's states (by entity id)."""
    problems = [snapshot_problem] if snapshot_problem else []
    sensor = by_id.get(sensor_id) or {}
    problems += status_problems(sensor.get("attributes"))
    if (by_id.get(switch_id) or {}).get("state") != "on":
        problems.append("the den lighting automatic switch is not on")
    if (by_id.get(INCUMBENT) or {}).get("state") != "off":
        problems.append("the incumbent den automation is not off")
    for e in LIGHTS:
        if scene_of_state(by_id.get(e)) is None:
            problems.append(f"{e} is not available")
    return problems


def vacant(by_id, sensor_id):
    """Both occupancy signals read off and all four bulbs are off."""
    attributes = (by_id.get(sensor_id) or {}).get("attributes") or {}
    return (attributes.get("occupancy") == "off" and (by_id.get(OCCUPANCY) or {}).get("state") == "off"
            and all((scene_of_state(by_id.get(e)) or {}).get("on") is False for e in LIGHTS))


def wait_problems(by_id, sensor_id):
    """Refusals during the wait for vacancy: holds appear or a game starts."""
    attributes = (by_id.get(sensor_id) or {}).get("attributes") or {}
    problems = []
    if attributes.get("holds"):
        problems.append("holds appeared while waiting (someone set a bulb)")
    if attributes.get("gaming_session") is not False:
        problems.append("a game started while waiting")
    return problems


# ---- whose change is it ----------------------------------------------------------

class Owner:
    """A bulb is the tool's when its state matches the tool's latest intent for it, or the intent
    before when the latest got no response. A write HA applied unheard is still the tool's."""

    def __init__(self, initial):
        self.intents = {e: [{"target": initial[e], "responded": True}] for e in LIGHTS}
        self.restoring = {e: [] for e in LIGHTS}  # restore writes --recover found in the log
        self.contexts = set()
        self.foreign = set()        # person-owned: never written again
        self.unavailable = set()

    def intend(self, e, target):
        self.intents[e].append({"target": target, "responded": False})

    def responded(self, e, context_id):
        self.intents[e][-1]["responded"] = True
        if context_id:
            self.contexts.add(context_id)

    def latest(self, e):
        return self.intents[e][-1]["target"]

    def is_own(self, e, actual):
        history = self.intents[e]
        if matches(history[-1]["target"], actual) or any(matches(t, actual) for t in self.restoring[e]):
            return True
        return not history[-1]["responded"] and len(history) > 1 and matches(history[-2]["target"], actual)

    def tools(self):
        """The bulbs still the tool's to restore."""
        return [e for e in LIGHTS if e not in self.foreign]


def classify(e, before, after, owner):
    """None (no change), 'own', 'foreign' or 'unavailable' for one bulb between two reads."""
    if after is None:
        return "unavailable"
    if after.get("state") not in ("on", "off"):
        return "unavailable"
    if before is not None and after.get("last_updated") == before.get("last_updated"):
        return None
    if before is not None and before.get("state") not in ("on", "off"):
        return "unavailable"  # coming back at whatever level: not a person's change
    found = scene_of_state(after)
    if not owner.is_own(e, found):
        return "foreign"
    context = (after.get("context") or {}).get("id")
    if before is None or context in owner.contexts:
        return "own"
    if found == scene_of_state(before):
        return "foreign"  # a moved last_updated with a foreign context: the value left and came back
    owner.contexts.add(context)  # a device's own report of the target, or the first report of a write
    return "own"                 # whose reply named no state: within tolerance of the intent, and the value moved


def watched_changed(before, after, switch_id, sensor_id):
    """Any change to the switch, the incumbent automation or gaming_session."""
    reasons = []
    if (before.get(switch_id) or {}).get("state") != (after.get(switch_id) or {}).get("state"):
        reasons.append("the den lighting automatic switch changed")
    if (before.get(INCUMBENT) or {}).get("state") != (after.get(INCUMBENT) or {}).get("state"):
        reasons.append("the incumbent den automation changed")
    old = ((before.get(sensor_id) or {}).get("attributes") or {}).get("gaming_session")
    new = ((after.get(sensor_id) or {}).get("attributes") or {}).get("gaming_session")
    if old != new:
        reasons.append("gaming_session changed")
    return reasons


def occupancy_reason(by_id, sensor_id):
    attributes = (by_id.get(sensor_id) or {}).get("attributes") or {}
    sensor, binary = attributes.get("occupancy"), (by_id.get(OCCUPANCY) or {}).get("state")
    if sensor != "off" or binary != "off":
        return f"occupancy is not off (sensor {sensor}, binary sensor {binary})"
    return None


def abort_reason(before, after, owner, switch_id, sensor_id):
    """The first abort reason in one HA read against the last, or None. Marks person-owned bulbs."""
    reason = occupancy_reason(after, sensor_id)
    for e in LIGHTS:
        kind = classify(e, before.get(e), after.get(e), owner)
        if kind == "foreign":
            owner.foreign.add(e)
            reason = reason or f"{e} changed and the tool did not send it"
        elif kind == "unavailable":
            owner.unavailable.add(e)
            reason = reason or f"{e} went unavailable or unknown"
    changed = watched_changed(before, after, switch_id, sensor_id)
    return reason or (changed[0] if changed else None)


# ---- the restore ---------------------------------------------------------------------

def restore_plan(snapshot, bulbs, *, arrival=False):
    """The restore writes, in order, for the bulbs still the tool's (den_lighting stays paused).

    A snapshot-on bulb gets turn_on at its level and kelvin. A snapshot-off bulb gets turn_on,
    then a wait for the on report, then turn_off, so den_lighting learns off with the level.
    With arrival (occupancy on straight from off, 120 s or more after vacancy began), a
    snapshot-off bulb gets only turn_on: den_lighting's arrival rule made its normal on."""
    actions, expected = [], {}
    for e in LIGHTS:
        if e not in bulbs:
            continue
        s = snapshot[e]
        actions.append({"do": "turn_on", "entity_id": e, "brightness": s["brightness"], "kelvin": s["kelvin"]})
        if s["on"] or arrival:
            expected[e] = scene(True, s["brightness"], s["kelvin"])
        else:
            actions += [{"do": "wait_on", "entity_id": e}, {"do": "turn_off", "entity_id": e}]
            expected[e] = dict(s)
    actions.append({"do": "check_holds", "expect": expected})
    return actions


def hold_failures(expected, holds, brightness_tol=1, mired_tol=2):
    """The bulbs whose hold, as the sensor reports it, does not equal the expected scene."""
    return [e for e, want in expected.items()
            if not hold_ok(want, ((holds or {}).get(e) or {}).get("target"), brightness_tol, mired_tol)]


def resume_plan(restored):
    """Switch on first (resume() checks bulbs only when automatic is on), then resume_automatic."""
    return [{"do": "switch_on"}, {"do": "resume", "entity_ids": list(restored)}]


def unrestored_line(e, last_probe):
    level = "" if not last_probe else f" ({last_probe})"
    return (f"{e} was not restored: its normal now carries the probe's level and temperature{level}. "
            "Set it once by hand in the HA app.")


# ---- --recover: did den_lighting's arrival rule run? ----------------------------------

def _stamp(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def arrival_branch(history, logbook_starts, window_start, vacancy_began):
    """'turn_on_only' when every occupancy signal shows a direct off-to-on change 120 s or more after
    vacancy began, with no unknown, unavailable or gap; else 'on_then_off'.

    history: {signal: [(time, value), ...]} in HA's order; a signal must have an entry at or
    before window_start. logbook_starts: the times of homeassistant_start inside the window."""
    if any(window_start <= t for t in logbook_starts):
        return "on_then_off"
    if not history or vacancy_began is None:
        return "on_then_off"
    for signal, entries in history.items():
        entries = [(t, v) for t, v in entries if _stamp(t) is not None]
        if not entries or entries[0][0] > window_start:
            return "on_then_off"  # no state at the window start: a gap
        values = [v for _, v in entries]
        if any(v not in ("on", "off") for v in values):
            return "on_then_off"  # unknown or unavailable (an HA restart reads unavailable)
        arrival = next((t for (t0, v0), (t, v) in zip(entries, entries[1:]) if v0 == "off" and v == "on"), None)
        if arrival is None or arrival - vacancy_began < VACANCY_S:
            return "on_then_off"
    return "turn_on_only"
