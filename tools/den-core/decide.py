"""den-core decide v0: the K16 coordinator skeleton on hue-den-4, in shadow only.

What v0 is
- A pure policy module in rung 9's 'Coordinator v0' shape: event-driven, held channels
  pinned at read-back, one Decision per change of requested scene, and candidates that
  each carry a cost and a why.
- It reads PersonState (from need), occupancy, and the mode of whoever owns the four den
  Hue bulbs: today den_lighting, through the incumbent's mode events (hold set, game flag,
  normal scene, revision). It writes Decision records in the hue-den-4 dialect, with
  scenes as {on, brightness, kelvin}.
- Its only behaviour beyond den_lighting is a labelled placeholder reading look
  (config looks['reading@0']) on the free bulbs while reading is engaged.

What v0 is not
- No light model, no lux, no solver, no glare or body envelope, no step executor or
  readback, no preferences. It never opens or ends a hold; holds come from mode.holds.
- Nothing is sent. handle() only returns Decision records for the runner to log.

Cost
- The cost is in scene units: the number of targeted free bulbs whose candidate scene
  misses the rule's target, by den_lighting's Scene.matches rule. After rung 8 measures
  the bulbs, a light-model objective replaces this cost behind the same
  plan(state, now, config) signature, without changing the record or the interface.

Occupancy
- Unknown occupancy fails closed (no decision), as den_lighting does. Facts-only replays
  carry no occupancy, so decide stays silent on them unless occupancy is supplied, or is
  assumed explicitly and logged as an assumption (run.py --assume-occupied).

How it plugs in
- After the spine and need merge:
    python -B tools/den-core/run.py --out ABSDIR --synthetic evening --touch --policies need incumbent decide
  Expected: decide differs from den_lighting only from shortly after each reading block
  starts until up to about 35 s after it ends, and never during a game.
"""
import copy
import hashlib
import json

LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
LOOK = "reading@0"
OFF = {"on": False, "brightness": None, "kelvin": None}
NO_MODE = {"revision": 0, "gaming": False, "holds": [], "normal": {}}
BASE_LABEL = "den_lighting normal (mode.normal)"

DEFAULT_CONFIG = {
    "version": "decide-v0",
    # Parity with den_lighting's VACANCY_SECONDS.
    "vacancy_s": 120,
    "looks": {
        LOOK: {"scene": {"on": True, "brightness": 255, "kelvin": 2700},
               "status": "placeholder: the reading look values are the owner choice; never sent"},
    },
    # Parked architecture decisions, each labelled with where its value comes from.
    "decisions": {
        "gaming": {"value": "non-gaming-stratified",
                   "status": "register recommendation, not an owner choice",
                   "effect": "no decisions while mode.gaming"},
        "hold-lifetime": {"value": "keep", "status": "register recommendation",
                          "effect": "holds are read from the bulbs owner (mode.holds, den_lighting rule); "
                                    "decide never opens or ends a hold"},
        "normal-scene": {"value": "defer", "status": "brief recommendation; the register makes none",
                         "effect": "base scene is den_lighting normal from mode.normal"},
        "manual-vs-safety": {"value": "S0", "status": "brief recommendation for the Hue path",
                             "effect": "held bulbs never change; no glare model, so hard_conflicts stays empty"},
        "compensation": {"value": "none-v0",
                         "status": "not expressible without a light model; register recommends P1 for rung-9 replay later",
                         "effect": "free bulbs chosen without regard to held ones"},
    },
    # These need an executor, an aim, a light model or a gate, and v0 has none of them.
    "not_used": ["resolve-deadline", "local-completion", "mid-execution", "unset-hold-fields", "unknown-aim-on",
                 "moving-spot", "tau-keep", "envelope-parameters", "freshness", "colour-guardrails",
                 "comfort-policy", "attribution-thresholds", "promotion-margins"],
}


def _merge(base, override):
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _scene(s):
    return None if s is None else {"on": s["on"], "brightness": s.get("brightness"), "kelvin": s.get("kelvin")}


def matches(target, actual):
    """den_lighting's Scene.matches on scene dicts; a null target field matches anything."""
    if target is None or actual is None or target["on"] != actual["on"]:
        return False
    if not target["on"]:
        return True
    b, k, ab, ak = target.get("brightness"), target.get("kelvin"), actual.get("brightness"), actual.get("kelvin")
    return ((b is None or ab is not None and abs(b - ab) <= 1) and
            (k is None or ak is not None and abs(1e6 / k - 1e6 / ak) <= 2))


def reading(person):
    """Reading is engaged or ending, of kind reading, with observed (not expired) support."""
    if not person:
        return False
    engagement = person.get("focused_engagement") or {}
    support = (person.get("field_support") or {}).get("focused_engagement") or {}
    return (engagement.get("state") in ("engaged", "ending") and engagement.get("kind") == "reading"
            and support.get("support_status") == "observed")


def plan(state, now, config):
    """Pure. Return None, or {rule, requested_scene, candidates, skipped, chosen, why}."""
    occupancy = state.get("occupancy", "unknown")
    mode = {**NO_MODE, **(state.get("mode") or {})}
    if occupancy not in ("on", "off"):
        return None  # Fail closed, as den_lighting does.
    if mode["gaming"]:
        return None  # non-gaming-stratified: den_lighting owns games in both arms.
    held = {h["entity_id"]: h for h in mode["holds"] if h["entity_id"] in LIGHTS}
    free = [b for b in LIGHTS if b not in held]
    normal, look = mode["normal"], config["looks"][LOOK]["scene"]
    skipped = [{"channel": b, "reason": "held", "hold_ref": "hold:%s@%.3f" % (b, held[b]["since"])}
               for b in LIGHTS if b in held]
    if occupancy == "off":
        since = state.get("vacant_since")
        if since is None or now - since < config["vacancy_s"]:
            return None
        rule, target = "vacant_off", {b: OFF for b in free}
    elif reading(state.get("person")):
        rule, target = "reading_look", {b: look for b in free}
    else:
        rule, target = "base", {b: normal[b] for b in free if normal.get(b) is not None}
        skipped += [{"channel": b, "reason": "no_base_scene", "hold_ref": None} for b in free if b not in target]
    skipped.sort(key=lambda s: LIGHTS.index(s["channel"]))

    actuator = state.get("actuator_state") or {}
    options = (
        ("keep_current", {b: actuator.get(b) for b in free}, "Leave the free bulbs as last observed"),
        ("look:" + LOOK, {b: look for b in free}, "Put the placeholder reading look on every free bulb"),
        ("base", {b: normal.get(b) for b in free}, "Use den_lighting's normal scene from mode.normal"),
        ("off", {b: OFF for b in free}, "Turn every free bulb off"),
    )
    candidates = []
    for scene_id, scenes, text in options:
        cost = sum(1 for b in target if not matches(target[b], scenes[b]))
        candidates.append({"scene_id": scene_id, "cost": cost,
                           "why": "%s; %d of %d targeted bulbs miss the %s target." % (text, cost, len(target), rule)})
    chosen = min(candidates, key=lambda c: c["cost"])  # min keeps the first of equals, so ties go earlier.

    requested = {b: _scene(s) for b, s in target.items()}
    requested.update({b: _scene(h["pinned"]) for b, h in held.items()})
    why = "%s: chose %s at cost %d; %d held, %d skipped." % (
        rule, chosen["scene_id"], chosen["cost"], len(held), len(skipped))
    return {"rule": rule, "requested_scene": requested, "candidates": candidates,
            "skipped": skipped, "chosen": chosen["scene_id"], "why": why}


class Decide:
    id = "decide"
    version = "decide-v0"
    code_path = __file__

    def __init__(self, config):
        self.config = config
        self.config_sha256 = hashlib.sha256(
            json.dumps(config, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
        # Occupancy comes from occupancy events only; mode.occupancy is den_lighting's copy.
        self.state = {"occupancy": "unknown", "vacant_since": None, "actuator_state": {b: None for b in LIGHTS},
                      "person": None, "mode": None}
        self.last_requested = None
        self.decisions = 0

    def handle(self, event):
        kind, data, t, state = event["kind"], event["data"], event["t"], self.state
        if kind == "occupancy":
            value = data.get("value") if data.get("value") in ("on", "off") else "unknown"
            if value != "off":
                state["vacant_since"] = None
            elif state["occupancy"] != "off":
                state["vacant_since"] = t
            state["occupancy"], trigger = value, "mode change"
        elif kind == "light_observed":
            if data.get("entity_id") in LIGHTS:
                state["actuator_state"][data["entity_id"]] = _scene(data)
            trigger = "mode change"
        elif kind == "person_state":
            state["person"], trigger = copy.deepcopy(data), "person_state change"
        elif kind == "mode":
            previous = (state["mode"] or NO_MODE).get("holds", [])
            state["mode"] = copy.deepcopy(data)
            trigger = "hold change" if data.get("holds", []) != previous else "mode change"
        elif kind == "tick":
            trigger = "time step"
        else:
            return []  # facts, correction (holds arrive through mode), gaming_sample, decision.

        result = plan(state, t, self.config)
        if result is None or not result["requested_scene"] or result["requested_scene"] == self.last_requested:
            return []
        self.last_requested = result["requested_scene"]
        self.decisions += 1
        mode, person = {**NO_MODE, **(state["mode"] or {})}, state["person"]
        inputs = {
            "person_state": [person["state_id"]] if person else [],
            "field_support": {person["state_id"]: person.get("field_support")} if person else {},
            "hold_set": {"revision": mode["revision"], "held": mode["holds"]},
            "revisions": {"hold_set": mode["revision"], "mode": mode["revision"]},
            "actuator_state": state["actuator_state"],
            "config_sha256": self.config_sha256,
            "look": LOOK,
            "base": BASE_LABEL,
        }
        decision = {
            "decision_id": "decide-%d-%d" % (round(t * 1000), self.decisions),
            "ts": t,
            "trigger": trigger,
            "policy": "decide@v0",
            "actuator_domain": "hue-den-4",
            "inputs": inputs,
            "requested_scene": result["requested_scene"],
            "transitions": {},
            "candidates": result["candidates"],
            "skipped": result["skipped"],
            "hard_conflicts": [],  # v0 has no glare model.
            "status": "requested",
            "why": result["why"],
        }
        return [{"kind": "decision", "data": copy.deepcopy(decision)}]


def make_policy(config=None):
    return Decide(_merge(DEFAULT_CONFIG, config))
