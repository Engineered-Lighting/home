"""den-core decide v0: rung 9's coordinator v0 on hue-den-4, in shadow and replay only.

What v0 is
- A pure policy module in rung 9's 'Coordinator v0' shape: event-driven, held channels
  pinned at read-back, one Decision per change of requested scene, and candidates that
  each carry a cost and a why.
- It reads PersonState (from need), occupancy, and the mode of whoever owns the four den
  Hue bulbs: today den_lighting, through the incumbent's mode events (hold set, game flag,
  normal scene, revision). It writes Decision records in the hue-den-4 dialect, with
  scenes as {on, brightness, kelvin}.
- Its only behaviour beyond den_lighting is a labelled placeholder reading look
  (config looks['reading@0'], the one seed Look) on the free bulbs while reading is engaged.

Rung 9's record (see README.md, "Rung 9: coordinator v0 on replay")
- Every Decision carries its requested scene, its applied scene (nothing is sent: held
  channels at their pinned read-back, free ones at their last read-back, status not_sent),
  and the recipients of its scene from the light model (light_model.py, one adapter): each
  bulb's share of the predicted light at each person's task points.
- inputs log the light model version, the seed Look, the fixed Preference stand-in (level
  factor 1, mired_shift and contrast_shift 0, applied through the look's guardrails), the
  comfort-limits version, the body envelope version, t_exec per channel, and the exclusions
  that follow from them (none on hue-den-4; replay_exclusions() recomputes them).
- Hard constraints: the seed Look's glare limit on free channels. Rung 8 writes glare: null
  and the limit is an open slot, so every check is logged unchecked and hard_conflicts is
  always empty today.
- Compensation (pending owner decision, decisions.json 'compensation') is built as named
  options: none-v0 (the default: free bulbs at the rule's scene, as v0 always did), and P0-P3,
  which solve the free bulbs' level against each person's task light while a bulb is held.
- Numbers the contracts leave open are config slots {value, status: open, proposed}. While
  open, the proposed value is used and the decision lists the slot in inputs.open_slots;
  score.py refuses a scored run while any is open.

What v0 is not
- No lux (the model is relative until rung 7 anchors it), no glare model, no aims, no step
  executor or readback, no learned preferences. It never opens or ends a hold; holds come
  from mode.holds.
- Nothing is sent. handle() only returns Decision records for the runner to log.

Cost
- none-v0 scores its four candidates in scene units: the number of targeted free bulbs
  whose candidate scene misses the rule's target, by den_lighting's Scene.matches rule.
  P0-P3 score their candidates by the light model: the mean relative miss of the predicted
  light at each person's task points against what the rule's scene on all four bulbs
  would give, plus the option's terms.

Occupancy
- Unknown occupancy fails closed (no decision), as den_lighting does. Facts-only replays
  carry no occupancy, so decide stays silent on them unless occupancy is supplied, or is
  assumed explicitly and logged as an assumption (run.py --assume-occupied).

How it plugs in
    python -B tools/den-core/run.py --out ABSDIR --synthetic evening --touch --policies need incumbent decide
  Expected: decide differs from den_lighting only from shortly after each reading block
  starts until up to about 35 s after it ends, and never during a game.
"""
import copy
import hashlib
import json

import light_model

LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
LOOK = "reading@0"
OFF = {"on": False, "brightness": None, "kelvin": None}
NO_MODE = {"revision": 0, "gaming": False, "holds": [], "normal": {}}
BASE_LABEL = "den_lighting normal (mode.normal)"
OPTIONS = ("none-v0", "P0", "P1", "P2", "P3")
BOUNDED = ("P1", "P2", "P3")  # options whose hold_set.held[] carries direction and footprint
GRID = tuple(round(0.5 + 0.05 * n, 2) for n in range(21))  # level factors P0-P3 weigh for the free bulbs
NOT_SENT = "decide is never the shown policy: nothing is sent, so the bulb stays as last read back"


def _open(proposed, note, decision=None):
    slot = {"value": None, "status": "open", "proposed": proposed, "note": note}
    if decision:
        slot["decision"] = decision
    return slot


DEFAULT_CONFIG = {
    "version": "decide-v0",
    # Parity with den_lighting's VACANCY_SECONDS.
    "vacancy_s": 120,
    "looks": {
        LOOK: {
            "scene": {"on": True, "brightness": 255, "kelvin": 2700},
            "status": "placeholder: the reading look values are the owner choice; never sent",
            "role": "the one seed Look, a fixed stand-in until the Look rung (rung 9)",
            "targets": {"glare_at_eyes": "none visible", "glare_limit": "slot glare_limit"},
            "guardrails": {"level_factor": [0.6, 1.5], "mired_shift": None,
                           "mired_shift_status": "pending (colour-guardrails): null holds the colour offset at 0",
                           "contrast_shift": [-0.5, 0.5],
                           "fixed": ["glare_at_eyes", "eye-box exclusions", "walls_Ev_lx minimum"],
                           "note": "copied from schemas.json's illustrative look; never binding while the "
                                   "preference stand-in is neutral"},
        },
    },
    # Rung 9's fixed Preference stand-in, logged in every decision.
    "preference": {"scope": "household", "offsets": {"level_factor": 1, "mired_shift": 0, "contrast_shift": 0},
                   "status": "fixed stand-in until the Preference rung: level factor 1, mired and contrast shift 0",
                   "comfort_limits": {"version": "cl-den-0", "entries": []}},
    "body_envelope": {"version": "body-envelope/v1", "parameters": None,
                      "parameters_status": "pending (envelope-parameters)"},
    # The light model every prediction comes from (light_model.py); run.py --light-model replaces it.
    "light_model": light_model.fake(),
    "compensation": {
        "option": "none-v0",
        "status": "pending owner decision (compensation); named options, none admitted as pass or fail",
        "options": {
            "none-v0": "v0: free bulbs at the rule's scene, chosen without regard to held ones; no solve",
            "P0": "pin only: free light re-serves everyone, the changer's own region included",
            "P1": "direction-aware footprint bound: free light may not move against the change at the points it moved",
            "P2": "soft penalty: lambda_h times the free light moved against the change at those points",
            "P3": "changer-aware bound: P1's bound only at the changer's own task points",
        },
    },
    # Numbers the contracts leave open. While open, the proposed value is used and logged in inputs.open_slots.
    "slots": {
        "task_points": _open({"room": ["desk-1"], "desk-1": ["desk-1"], "armchair-1": ["armchair-1"]},
                             "per track without a working region (need v0's 'room') and per working_region "
                             "surface_id: the light model's points that are its task; rung 8's v1 blocks have no "
                             "kind, so the owner marks them on its grid. The proposal names the fake model's points"),
        "s_serve": _open(0.5, "a track is served by a bulb whose share of its task light is at least this",
                         "attribution-thresholds (deferred)"),
        "glare_limit": _open("unchecked", "the seed Look's hard glare limit at the eyes; rung 8 writes glare: null, "
                                          "so the proposal is to log the constraint unchecked on hue-den-4",
                             "comfort-policy"),
        "compensation_form": _open("mode", "strict, or mode: den_lighting's mode transitions sit outside the bound",
                                   "compensation sub-choice"),
        "footprint_threshold": _open(0.1, "a held bulb's footprint: the points its change moved by at least this "
                                          "share of the rule's predicted light there", "compensation parameter"),
        "membership": _open("at_or_above", "a point whose move equals the threshold is in the footprint",
                            "compensation parameter"),
        "lambda_h": _open(1.0, "P2's penalty weight on free light moved against a change", "compensation parameter"),
        "p3_fallback": _open("P1", "P3 with an unknown or uncertain changer falls back to P1 or P0",
                             "compensation sub-choice"),
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
                             "effect": "held bulbs never change; a held bulb that breaks a hard constraint is "
                                       "logged in hard_conflicts"},
        "compensation": {"value": "named options none-v0, P0-P3 (config compensation.option)",
                         "status": "pending owner decision; the register recommends P1 in mode form for rung-9 "
                                   "replay, with P0 and P3 as counterfactuals",
                         "effect": "see compensation"},
    },
    # These need an executor, an aim, or a gate, and v0 has none of them.
    "not_used": ["resolve-deadline", "local-completion", "mid-execution", "unset-hold-fields", "unknown-aim-on",
                 "moving-spot", "tau-keep", "promotion-margins"],
}


def _merge(base, override):
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict) and key != "light_model":
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


def slot(config, name, used=None):
    """A slot's value, or its proposed value while open (named in used)."""
    entry = config["slots"][name]
    if entry["status"] == "open":
        if used is not None:
            used.add(name)
        return entry["proposed"]
    return entry["value"]


def open_slots(config):
    return sorted(name for name, entry in config["slots"].items() if entry["status"] == "open")


def prefer(scene, config):
    """A look scene after the Preference stand-in, through the look's guardrails: T' = T * level_factor and
    CCT' = 1e6 / (1e6 / CCT + mired_shift), each offset clamped to its guardrail, and held neutral when the
    guardrail is null. With the stand-in's 1 and 0 the scene is unchanged."""
    if scene is None or not scene["on"]:
        return scene
    offsets, rails = config["preference"]["offsets"], config["looks"][LOOK]["guardrails"]
    factor, shift = offsets["level_factor"], offsets["mired_shift"]
    factor = 1 if rails.get("level_factor") is None else min(max(factor, rails["level_factor"][0]),
                                                             rails["level_factor"][1])
    shift = 0 if rails.get("mired_shift") is None else min(max(shift, rails["mired_shift"][0]),
                                                           rails["mired_shift"][1])
    b, k = scene["brightness"], scene["kelvin"]
    if b is not None and factor != 1:
        b = min(255, max(1, round(b * factor)))
    if k is not None and shift != 0:
        k = round(1e6 / (1e6 / k + shift))
    return {"on": True, "brightness": b, "kelvin": k}


def task_points(person, config, model, used=None):
    """The light model's points that are this person's task: by working_region surface_id, else by track id."""
    mapping = slot(config, "task_points", used) or {}
    region = (person.get("working_region") or {}).get("surface_id")
    names = mapping.get(region) if region is not None else None
    if names is None:
        names = mapping.get(person.get("track_id"), [])
    return [p for p in names if p in model.points]


def _footprint(model, before, pinned, reference, threshold, membership):
    """(direction, footprint points) of a held bulb's change from before to pinned, over every model point."""
    if before is None:
        return "unknown", []
    old = model.contribution(before[0], before[1], model.points)
    new = model.contribution(before[0], pinned, model.points)
    if any(v is None for v in list(old.values()) + list(new.values())):
        return "unknown", []
    moved = []
    for p in model.points:
        delta, ref = new[p] - old[p], reference.get(p)
        scale = abs(ref) if ref else 1e-9
        if abs(delta) > threshold * scale or membership == "at_or_above" and abs(delta) == threshold * scale:
            moved.append((p, delta))
    total = sum(d for _, d in moved)
    direction = "none" if not moved else ("dim" if total < 0 else "brighten")
    return direction, [p for p, _ in moved]


def _solve(rule_target, held, free, people, before, config, model, option, trigger, used, limit):
    """P0-P3: each free bulb's level as a factor from GRID of the rule's level, against each person's task light:
    every uniform factor, then a per-bulb descent from the best of them. Returns (requested free scenes,
    candidates, chosen id, held notes {bulb: {direction, footprint[, changer_track]}}, hold_limited or None)."""
    targets = [(person["track_id"], p) for person in people for p in task_points(person, config, model, used)]
    reference = model.predict({b: rule_target.get(b) for b in LIGHTS}, model.points)
    threshold = slot(config, "footprint_threshold", used)
    membership = slot(config, "membership", used)
    form = slot(config, "compensation_form", used)
    lam = slot(config, "lambda_h", used) if option == "P2" else 0.0
    notes, bounds = {}, []  # bounds: (held bulb, direction, points, kind)
    for b, h in sorted(held.items()):
        direction, foot = _footprint(model, before.get(b), h["pinned"], reference, threshold, membership)
        note, effective = {"direction": direction, "footprint": foot}, option
        if option == "P3":
            note["changer_track"] = h.get("changer_track")
            if note["changer_track"] is None:
                effective = note["fallback"] = slot(config, "p3_fallback", used)
        notes[b] = note
        if effective in BOUNDED and direction in ("dim", "brighten"):
            at = foot
            if effective == "P3":
                mine = {p for person in people if person["track_id"] == note["changer_track"]
                        for p in task_points(person, config, model, used)}
                at = [p for p in foot if p in mine]
            bounds.append((b, direction, at, "penalty" if effective == "P2" else "bound"))
    if form == "mode" and trigger == "mode change":  # den_lighting's mode transitions sit outside the bound
        bounds = [(b, d, at, "outside: mode transition") for b, d, at, _ in bounds]
    points = sorted({p for _, p in targets} | {p for _, _, at, _ in bounds for p in at})
    held_part = {p: sum(model.contribution(b, _scene(h["pinned"]), [p])[p] or 0.0 for b, h in held.items())
                 for p in points}
    cache = {}

    def scene_at(b, f):
        s = rule_target.get(b)
        if s is not None and s["on"] and s.get("brightness") is not None:
            s = {**s, "brightness": min(255, max(1, round(s["brightness"] * f)))}
        return s

    def part(b, f):
        if (b, f) not in cache:
            cache[(b, f)] = model.contribution(b, scene_at(b, f), points)
        return cache[(b, f)]

    def light_of(factors):
        free_light = {p: 0.0 for p in points}
        for b in free:
            for p, v in part(b, factors[b]).items():
                free_light[p] = None if v is None or free_light[p] is None else free_light[p] + v
        return free_light

    one = {b: 1.0 for b in free}
    base = light_of(one)  # the free bulbs' light at the rule's own level: what a bound measures moves from

    def evaluate(factors):
        free_light = light_of(factors)
        misses = [abs(free_light[p] + held_part[p] - reference[p]) / reference[p] for _, p in targets
                  if free_light[p] is not None and reference.get(p)]
        cost, against, broken = (sum(misses) / len(misses) if misses else 0.0), 0.0, []
        for b, direction, at, kind in bounds:
            for p in at:
                if free_light[p] is None or base[p] is None:
                    continue
                wrong = (free_light[p] - base[p]) * (1 if direction == "dim" else -1)
                if wrong > 1e-9 and kind == "bound":
                    broken.append("%s's %s at %s" % (b, direction, p))
                elif wrong > 1e-9 and kind == "penalty":
                    against += wrong / (reference[p] or 1e-9)
        cost += lam * against + 1e-6 * sum(abs(f - 1.0) for f in factors.values())  # ties keep the rule's level
        return cost, broken, free_light

    candidates, best = [], None

    def consider(factors, scene_id):
        nonlocal best
        cost, broken, _ = evaluate(factors)
        if not free_glare_ok(model, {b: scene_at(b, factors[b]) for b in free}, held, people, limit):
            broken = broken + ["the seed Look's glare limit"]
        if broken:
            why = "rejected: moves free light against %s" % ", ".join(sorted(set(broken)))
        else:
            why = "mean task miss %.4f%s" % (cost, " with the P2 penalty" if lam else "")
            if best is None or cost < best[0]:
                best = (cost, dict(factors), scene_id)
        candidates.append({"scene_id": scene_id, "cost": round(cost, 6), "why": why})

    for f in GRID:
        consider({b: f for b in free}, "uniform:%.2f" % f)
    factors = dict(best[1])
    for _ in range(3):
        moved = False
        for b in free:
            options = []
            for f in GRID:
                cost, broken, _ = evaluate({**factors, b: f})
                if not broken:
                    options.append((cost, abs(f - factors[b]), f))
            if options and min(options)[2] != factors[b]:
                factors[b], moved = min(options)[2], True
        if not moved:
            break
    if factors != best[1]:
        consider(factors, "per_bulb:" + ",".join("%.2f" % factors[b] for b in free))
    _, factors, chosen = best
    for c in candidates:
        if c["scene_id"] == chosen:
            c["why"] = "chosen: " + c["why"]
    requested = {b: scene_at(b, factors[b]) for b in free}
    _, _, light = evaluate(factors)
    limited = []
    for track, p in targets:
        if light[p] is None or reference.get(p) is None or light[p] + held_part[p] >= reference[p] - 1e-9:
            continue
        for b, direction, at, kind in bounds:
            if p in at and kind == "bound":
                limited.append({"point_id": p, "track_id": track, "target": round(reference[p], 6),
                                "predicted": round(light[p] + held_part[p], 6),
                                "reason": "bound by %s's hold (%s), %s" % (b, direction, option)})
                break
    if option not in BOUNDED:  # direction and footprint are present only under P1-P3 (Decision v2)
        notes = {}
    return requested, candidates, chosen, notes, (limited if option in BOUNDED else None)


def plan(state, now, config, model=None, trigger=None):
    """Pure. Return None, or {rule, requested_scene, candidates, skipped, chosen, why, held_notes, hold_limited}."""
    occupancy = state.get("occupancy", "unknown")
    mode = {**NO_MODE, **(state.get("mode") or {})}
    if occupancy not in ("on", "off"):
        return None  # Fail closed, as den_lighting does.
    if mode["gaming"]:
        return None  # non-gaming-stratified: den_lighting owns games in both arms.
    held = {h["entity_id"]: h for h in mode["holds"] if h["entity_id"] in LIGHTS}
    free = [b for b in LIGHTS if b not in held]
    normal, look = mode["normal"], prefer(config["looks"][LOOK]["scene"], config)
    people = [p for _, p in sorted((state.get("people") or {}).items())]
    skipped = [{"channel": b, "reason": "held", "hold_ref": "hold:%s@%.3f" % (b, held[b]["since"])}
               for b in LIGHTS if b in held]
    if occupancy == "off":
        since = state.get("vacant_since")
        if since is None or now - since < config["vacancy_s"]:
            return None
        rule, target = "vacant_off", {b: OFF for b in free}
    elif any(reading(p) for p in people):
        rule, target = "reading_look", {b: look for b in free}
    else:
        rule, target = "base", {b: normal[b] for b in free if normal.get(b) is not None}
        skipped += [{"channel": b, "reason": "no_base_scene", "hold_ref": None} for b in free if b not in target]
    skipped.sort(key=lambda s: LIGHTS.index(s["channel"]))

    option, used = config["compensation"]["option"], set()
    if option not in OPTIONS:
        raise ValueError("unknown compensation option %r" % (option,))
    limit = slot(config, "glare_limit", used)
    if type(limit) in (int, float):
        model = model or light_model.LightModel(config["light_model"])
    if option != "none-v0" and held and rule != "vacant_off" and target:
        model = model or light_model.LightModel(config["light_model"])
        rule_target = {**{b: (look if rule == "reading_look" else normal.get(b)) for b in LIGHTS}, **target}
        requested, candidates, chosen, notes, limited = _solve(
            rule_target, held, [b for b in free if b in target], people, state.get("held_before") or {}, config,
            model, option, trigger, used, limit)
        cost = next(c["cost"] for c in candidates if c["scene_id"] == chosen)
        requested = {b: _scene(s) for b, s in requested.items()}
        requested.update({b: _scene(h["pinned"]) for b, h in held.items()})
        why = "%s: chose %s at cost %s; %d held, %d skipped; compensation %s." % (
            rule, chosen, cost, len(held), len(skipped), option)
        return {"rule": rule, "requested_scene": requested, "candidates": candidates, "skipped": skipped,
                "chosen": chosen, "why": why, "held_notes": notes, "hold_limited": limited, "used": used}

    actuator = state.get("actuator_state") or {}
    options = (
        ("keep_current", {b: actuator.get(b) for b in free}, "Leave the free bulbs as last observed"),
        ("look:" + LOOK, {b: look for b in free}, "Put the placeholder reading look on every free bulb"),
        ("base", {b: normal.get(b) for b in free}, "Use den_lighting's normal scene from mode.normal"),
        ("off", {b: OFF for b in free}, "Turn every free bulb off"),
    )
    candidates, feasible = [], []
    for scene_id, scenes, text in options:
        cost = sum(1 for b in target if not matches(target[b], scenes[b]))
        why = "%s; %d of %d targeted bulbs miss the %s target." % (text, cost, len(target), rule)
        if model is not None and not free_glare_ok(model, scenes, held, people, limit):
            why = "rejected: a free bulb over the seed Look's glare limit. " + why
        else:
            feasible.append(len(candidates))
        candidates.append({"scene_id": scene_id, "cost": cost, "why": why})
    # min keeps the first of equals, so ties go earlier; "off" never glares, so one is always feasible.
    chosen = min((candidates[n] for n in feasible), key=lambda c: c["cost"])

    requested = {b: _scene(s) for b, s in target.items()}
    if len(feasible) < len(candidates):  # a hard constraint ruled the rule's own scene out: send what was chosen
        scenes = next(scenes for scene_id, scenes, _ in options if scene_id == chosen["scene_id"])
        requested = {b: _scene(scenes[b]) for b in target if scenes.get(b) is not None}
    requested.update({b: _scene(h["pinned"]) for b, h in held.items()})
    why = "%s: chose %s at cost %d; %d held, %d skipped." % (
        rule, chosen["scene_id"], chosen["cost"], len(held), len(skipped))
    notes = {b: {"direction": None, "footprint": None} for b in held} if option in BOUNDED else {}
    return {"rule": rule, "requested_scene": requested, "candidates": candidates,
            "skipped": skipped, "chosen": chosen["scene_id"], "why": why, "held_notes": notes,
            "hold_limited": [] if option in BOUNDED else None, "used": used}


def t_exec(ts, requested, transitions, held, actuator):
    """inputs.t_exec: per new or changed channel ts + its transition (t_settle; on a Hue bulb t_settle is later
    than t_chk + T_chk + T_react for any value freshness could give); null with a status for a kept or held one."""
    out = {}
    for b in sorted(requested):
        if b in held:
            out[b], out[b + "_status"] = None, "held: pinned at read-back, kept: ts + T_chk + T_react (pending, freshness)"
        elif matches(requested[b], actuator.get(b)):
            out[b], out[b + "_status"] = None, "kept: ts + T_chk + T_react (pending, freshness)"
        else:
            out[b] = ts + transitions.get(b, 0)
    return out


def replay_exclusions(inputs):
    """The exclusions a decision's own inputs give: its PersonState state_ids with their field_support, the body
    envelope version and t_exec. Every field is aged to each channel's t_exec. hue-den-4 has no narrow beam, and
    body-envelope/v1's parameters are pending, so nothing is excluded."""
    envelope = inputs["body_envelope"]
    if envelope["version"] != "body-envelope/v1":
        raise ValueError("unknown body envelope %r" % (envelope["version"],))
    times = {b: t for b, t in inputs["t_exec"].items() if not b.endswith("_status") and t is not None}
    ages = {}
    for state_id in inputs["person_state"]:
        for field, support in sorted((inputs["field_support"].get(state_id) or {}).items()):
            last = (support or {}).get("last_support_capture_ts")
            if last is None:
                continue
            ages.setdefault(state_id, {})[field] = {b: round(t - last, 3) for b, t in sorted(times.items())}
    return {"envelope": envelope["version"], "excluded": [], "ages": ages,
            "status": "none: hue-den-4 has no narrow beam; body-envelope/v1 parameters %s"
                      % envelope["parameters_status"]}


def glare_conflicts(model, requested, held, people, limit):
    """(hard_conflicts, status). A held light that alone breaks the seed Look's glare limit at a person's eyes is
    a conflict (S0: it stays as set). Unchecked while the model has no glare or the limit is not a number."""
    eyes = [p for p in people if (p.get("attention") or {}).get("head_xyz") is not None]
    if type(limit) not in (int, float):
        return [], "unchecked: no numeric glare limit (slot glare_limit is %r)" % (limit,)
    found = model.glare({b: requested[b] for b in requested}, eyes)
    if found is None:
        return [], "unchecked: the light model has no glare (rung 8 writes glare: null)"
    conflicts = []
    for (track, channel), g_hi in sorted(found.items()):
        if channel in held and g_hi is not None and g_hi > limit:
            conflicts.append({"constraint": "glare", "held_channel": channel, "track": track,
                              "eye_or_envelope": "eye", "G_hi": g_hi, "limit": limit, "model_status": "unvalidated"})
    return conflicts, "checked against %s" % limit


def free_glare_ok(model, scenes, held, people, limit):
    """False when a free channel of scenes breaks the seed Look's glare limit at a person's eyes: free channels may
    not raise their part of a hard constraint. Always True today: the limit is unchecked and rung 8 has no glare."""
    eyes = [p for p in people if (p.get("attention") or {}).get("head_xyz") is not None]
    if type(limit) not in (int, float) or not eyes:
        return True
    found = model.glare({b: s for b, s in scenes.items() if b not in held and s is not None}, eyes)
    return found is None or all(g is None or g <= limit for (_, ch), g in found.items() if ch not in held)


def recipients(model, requested, people, config, used):
    """Per channel that is on: its share of the predicted light at each person's task points, on the requested
    scene with held bulbs pinned (share = the bulb's part / the total), and the tracks it serves."""
    s_serve = slot(config, "s_serve", used)
    targets = [(person["track_id"], p) for person in people for p in task_points(person, config, model, used)]
    points = sorted({p for _, p in targets})
    parts = model.contributions(requested, points)
    total = model.predict(requested, points)
    unit = model.basis_unit
    out = {}
    for b in sorted(requested):
        if not requested[b]["on"]:
            continue
        rows = []
        for track, p in targets:
            share = None if parts[b][p] is None or not total[p] else round(parts[b][p] / total[p], 4)
            rows.append({"target_id": p, "kind": "task", "track_id": track, "share": share,
                         "e": None if total[p] is None else round(total[p], 6), "unit": unit,
                         "lx": round(total[p], 1) if unit == "lx" and total[p] is not None else None})
        served = sorted({r["track_id"] for r in rows if r["share"] is not None and r["share"] >= s_serve})
        out[b] = {"served": served, "exposed": [], "layers": [], "targets": rows,
                  "exposed_status": "no eye positions (need v0 has no attention) and no glare model"}
    return out


class Decide:
    id = "decide"
    version = "decide-v0"
    code_path = __file__

    def __init__(self, config):
        self.config = config
        self.config_sha256 = hashlib.sha256(
            json.dumps(config, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
        self.model = light_model.LightModel(config["light_model"])
        if config["compensation"]["option"] not in OPTIONS:
            raise ValueError("unknown compensation option %r" % (config["compensation"]["option"],))
        # Occupancy comes from occupancy events only; mode.occupancy is den_lighting's copy.
        self.state = {"occupancy": "unknown", "vacant_since": None, "actuator_state": {b: None for b in LIGHTS},
                      "people": {}, "mode": None, "held_before": {}}
        self.last_requested = None
        self.decisions = 0

    def _holds_seen(self, holds):
        """Remember each new or replaced hold's bulb as last read back before it: the change's 'before'."""
        state, now = self.state, {h["entity_id"]: h for h in holds}
        for b in list(state["held_before"]):
            if b not in now:
                del state["held_before"][b]
        for b, h in now.items():
            known = state["held_before"].get(b)
            if known is None or known[2] != (h["since"], h["pinned"]):
                state["held_before"][b] = (b, state["actuator_state"].get(b), (h["since"], h["pinned"]))

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
            state["people"][data.get("track_id")] = copy.deepcopy(data)
            trigger = "person_state change"
        elif kind == "mode":
            previous = (state["mode"] or NO_MODE).get("holds", [])
            self._holds_seen(data.get("holds", []))
            state["mode"] = copy.deepcopy(data)
            if data.get("occupancy", state["occupancy"]) != state["occupancy"]:
                # The incumbent's mode for an occupancy change reaches decide before the occupancy event itself,
                # which follows in the same chain and plans with this mode; planning now would use the old one.
                return []
            trigger = "hold change" if data.get("holds", []) != previous else "mode change"
        elif kind == "tick":
            trigger = "time step"
        else:
            return []  # facts, correction (holds arrive through mode), gaming_sample, decision.

        before = {b: (b, s[1]) for b, s in state["held_before"].items()}
        result = plan({**state, "held_before": before}, t, self.config, self.model, trigger)
        if result is None or not result["requested_scene"] or result["requested_scene"] == self.last_requested:
            return []
        self.last_requested = result["requested_scene"]
        self.decisions += 1
        return [{"kind": "decision", "data": copy.deepcopy(self.record(t, trigger, result))}]

    def record(self, t, trigger, result):
        """The Decision for one plan: rung 9's fields around v0's."""
        state, config, model, used = self.state, self.config, self.model, set(result["used"])
        mode = {**NO_MODE, **(state["mode"] or {})}
        people = [p for _, p in sorted(state["people"].items())]
        requested = result["requested_scene"]
        held = {h["entity_id"]: h for h in mode["holds"] if h["entity_id"] in requested}
        transitions = {}
        held_rows = []
        for h in mode["holds"]:
            row = dict(h)
            row.update(result["held_notes"].get(h["entity_id"], {}))
            held_rows.append(row)
        limit = slot(config, "glare_limit", used)
        conflicts, glare_status = glare_conflicts(model, requested, held, people, limit)
        inputs = {
            "person_state": [p["state_id"] for p in people],
            "field_support": {p["state_id"]: p.get("field_support") for p in people},
            "hold_set": {"revision": mode["revision"], "held": held_rows},
            "revisions": {"hold_set": mode["revision"], "mode": mode["revision"]},
            "actuator_state": state["actuator_state"],
            "config_sha256": self.config_sha256,
            "look": LOOK,
            "base": BASE_LABEL,
            "light_model": model.describe(),
            "preference": {"scope": config["preference"]["scope"], "offsets": config["preference"]["offsets"],
                           "status": config["preference"]["status"]},
            "comfort_limits": config["preference"]["comfort_limits"]["version"],
            "body_envelope": config["body_envelope"],
            "compensation": config["compensation"]["option"],
            "hard_constraints": {"glare": {"limit": limit, "status": glare_status}},
        }
        inputs["t_exec"] = t_exec(t, requested, transitions, held, state["actuator_state"])
        inputs["exclusions"] = replay_exclusions(inputs)
        shares = recipients(model, requested, people, config, used)
        points = sorted({p for person in people for p in task_points(person, config, model, used)})
        surfaces = {p: (None if e is None else round(e, 6)) for p, e in model.predict(requested, points).items()}
        inputs["open_slots"] = sorted(used)
        applied = {}
        for b in sorted(requested):
            if b in held:
                applied[b] = {"status": "held", "readback": _scene(held[b]["pinned"]), "steps": [],
                              "reason": "held: pinned at its read-back"}
            else:
                applied[b] = {"status": "not_sent", "readback": state["actuator_state"].get(b), "steps": [],
                              "reason": NOT_SENT}
        decision = {
            "decision_id": "decide-%d-%d" % (round(t * 1000), self.decisions),
            "ts": t,
            "trigger": trigger,
            "policy": "decide@v0",
            "actuator_domain": "hue-den-4",
            "inputs": inputs,
            "requested_scene": requested,
            "applied_scene": applied,
            "transitions": transitions,
            "candidates": result["candidates"],
            "skipped": result["skipped"],
            "hard_conflicts": conflicts,
            "recipients": shares,
            "recipients_basis": "the requested scene with held bulbs pinned: nothing is sent, so no actual state "
                                "comes back to rewrite it for",
            "predicted": {"surfaces": {**surfaces, "unit": model.basis_unit}, "eyes": [],
                          "eyes_note": "no eye positions (need v0 has no attention) and no glare model (rung 8 "
                                       "writes glare: null)",
                          "model_status": "fake: plumbing only" if model.fake else "unvalidated"},
            "status": "requested",
            "why": result["why"],
        }
        if result["hold_limited"] is not None:
            decision["hold_limited"] = result["hold_limited"]
        return decision


def make_policy(config=None):
    return Decide(_merge(DEFAULT_CONFIG, config))
