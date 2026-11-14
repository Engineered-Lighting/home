"""The den's bulb plan: what den_lighting would be asked to do with the room's bulbs (contract 4's `bulbs`).

The coordinator decides the bulbs the way it decides the spots, from the same situation, and publishes the plan at once,
whether or not anything is sent. den_lighting stays the only writer of the room lights: a plan only ever reaches them
as den_lighting's `coordinator_plan` (1.2: epoch, publisher, a generation that rises on every send), and a light the
plan leaves out follows den_lighting's own rules. Nothing here writes `light.*`, and nothing here does I/O.

- **The reading scene.** One person doing a close-up task: each bulb that's on goes to READING's level and white over
  READING's time, never brighter than it already was, never warmer than it can go. When the task ends the plan is
  released and den_lighting brings its own scene back.
- **Gaming:** den_lighting's own gaming scene, plus what was learned. **Sitting, present:** only what was learned.
  **Vacant, unknown, Off, Stop:** release. **Several people:** no reading scene, since the room isn't one reader's.
- **Learned scenes win** over the defaults, light by light.

The numbers are the lighting-direction chat's ambient scene (house 0.75 x task 0.4, dim to 2700 K): the ambient record
is the one source for the room's other light. While that record doesn't carry its goal yet, READING holds the same
numbers. Standard library only.
"""

import math

SCHEMA = "den-bulb-plan-1"
LEASE_S = 60            # den_lighting's valid_for_s: a quiet coordinator hands the bulbs back within a minute
RENEW_S = 10.0          # a live plan is renewed this often, with a new generation (1.2 never extends an exact retry)
READING = {"level": 0.30, "kelvin": 2700, "transition_s": 4.0}
RESTORE_S = 2.0         # den_lighting's own transition when a released light goes back to its scene
GAMING_BRIGHTNESS = 38  # den_lighting's own gaming scene (policy.desired)
ALL = "all"             # without den_lighting's list, a scene for every bulb that's on
SITUATION_MAX = 32      # den_lighting's limit on a plan's situation text
TRANSITION_S = (0.0, 10.0)
WARMEST_K = 2000        # den_lighting's DEFAULT_WARMEST


def situation(core, context, attrs, wall, utc_offset_h=0.0):
    """(key, learnable, source). With den_lighting's status (`attrs`), its occupancy and gaming signal decide, as the
    overlay always has. Without it, den-live's own view: someone tracked is occupied, nobody is unknown (a person can
    be out of view), and no game is assumed. Such a situation is never learned from."""
    if attrs is not None:
        occupied = {"on": True, "off": False}.get(attrs.get("occupancy"))
        playing = {"playing": True, "idle": False}.get(attrs.get("gaming_signal"))
        source = "home_assistant"
    else:
        occupied = True if context["people"] > 0 else None
        playing, source = False, "den_live"
    kind = core.situation.activity(occupied, playing, int(bool(context["task"])), int(bool(context["seated"])),
                                   context["activity_known"])
    key = core.situation.key(kind, core.situation.part_of_day(wall, utc_offset_h))
    return key, source == "home_assistant" and kind in core.situation.LEARNABLE, source


def release(key, reason):
    return {"situation": key, "scenes": {}, "release": True, "transition_s": RESTORE_S, "reason": reason}


def goal(ambient):
    """(level, kelvin, seconds): the ambient record's goal when it carries one, else READING."""
    if isinstance(ambient, dict):
        level, kelvin, ramp = (ambient.get(key) for key in ("goal_level", "goal_cct_k", "ramp_s"))
        if all(type(x) in (int, float) for x in (level, kelvin, ramp)) and 0 <= level <= 1 and ramp >= 0:
            return float(level), int(round(kelvin)), float(ramp)
    return READING["level"], READING["kelvin"], READING["transition_s"]


def reading_scene(lights, warmest, house, ambient=None):
    """{light: scene}: every bulb that's on (or that we can't see), dimmed to the goal and never brighter than now."""
    level, kelvin, _ = goal(ambient)
    target = max(1, math.floor(255 * level + 0.5))   # contract 5's rounding: 30 % is 77
    scenes = {}
    for light in lights or (ALL,):
        now = (house or {}).get(light)
        if now is not None and now.get("on") is False:
            continue   # a bulb that's off stays off
        brightness = now.get("brightness") if now is not None else None
        level_now = target if type(brightness) is not int else max(1, min(target, brightness))
        floor = (warmest or {}).get(light, WARMEST_K) if light != ALL else WARMEST_K
        scenes[light] = {"on": True, "brightness": level_now, "kelvin": max(kelvin, floor)}
    return scenes


def desired(key, *, people, ambient=None, learned=None, lights=(), warmest=None, house=None):
    """The plan for this situation: {situation, scenes, release, transition_s, reason}. `learned` is
    {light: {on, brightness, kelvin}} for this key (Learner.scene's rows); `house` is {light: {on, brightness, kelvin}}
    as each bulb stood before any plan of ours, or None when unknown."""
    kind = key.split("/", 1)[0]
    if kind in ("unknown", "vacant"):
        return release(key, kind)
    learned = {light: scene for light, scene in (learned or {}).items() if scene and (not lights or light in lights)}
    scenes, transition, reason = dict(learned), 2.0, "learned"
    if kind == "gaming":
        for light in lights:
            base = {"on": True, "brightness": GAMING_BRIGHTNESS, "kelvin": (warmest or {}).get(light, WARMEST_K)}
            scenes[light] = {**base, **{k: v for k, v in learned.get(light, {}).items() if v is not None}}
        reason = "gaming"
    elif kind == "task":
        if people > 1:
            return release(key, "several_people")
        _, _, transition = goal(ambient)
        scenes = {**reading_scene(lights, warmest, house, ambient), **learned}
        reason = "reading"
    if not scenes:
        return release(key, "den_lighting_rules")
    return {"situation": key, "scenes": scenes, "release": False,
            "transition_s": min(TRANSITION_S[1], max(TRANSITION_S[0], float(transition))), "reason": reason}


def payload(plan, *, epoch, publisher, generation, valid_for_s=LEASE_S):
    """den_lighting 1.2's coordinator_plan data for this plan. Built here only, for every send and every renewal."""
    if not plan["release"] and ALL in plan["scenes"]:
        raise ValueError("a plan for every bulb can't be sent: den_lighting's lights aren't known")
    data = {"epoch": epoch, "publisher": publisher, "plan_id": "plan-%d" % generation, "generation": generation,
            "valid_for_s": valid_for_s, "transition_s": plan["transition_s"],
            "situation": plan["situation"][:SITUATION_MAX], "scenes": {} if plan["release"] else plan["scenes"]}
    if plan["release"]:
        data["release"] = True
    return data


def record(plan, *, mode, since_mono, source, sent_generation=None, problem=None):
    """The published record (contract 4's `bulbs`): what the bulbs would do, and whether it was sent."""
    return {"schema": SCHEMA, "mode": mode, "situation": plan["situation"], "scenes": plan["scenes"],
            "release": plan["release"], "transition_s": plan["transition_s"], "reason": plan["reason"],
            "since_mono": since_mono, "source": source, "sent_generation": sent_generation, "problem": problem}
