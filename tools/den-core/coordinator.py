"""den-core rung 9: coordinator v0, decide's rules with rung 8's light model behind them. In shadow only.

What it adds to decide v0
- Every prediction goes through light_adapter.py. A reading scene is solved, not just requested: each free bulb's level
  is searched for the lowest cost against the seed Look's target at each reader's surface, with held bulbs pinned.
- Every Decision carries requested_scene, applied_scene and recipients, plus predicted, targets and inputs.t_exec.
  Nothing is sent: an applied scene is what a bulb would report if the step went out, and says so.
- The seed Look's glare limit binds free channels. A held light that alone breaks it goes in hard_conflicts, stays as
  set (manual-vs-safety S0, the only option built) and free channels may not raise their part of it.
- The compensation options P0 to P3 and their strict and mode forms are all built, as named options. Which one the
  coordinator should use is the owner's, so the hold fixtures that depend on it are not admitted as pass or fail. A
  hold's footprint and bound work per block, the register's "points it moved", since a surface's mean can hide them;
  a Decision writes the footprint as a mask over the model's blocks.

What stays decide v0's: the rule (decide.plan), the triggers, the wire format and policy id 'decide'. decide.py itself
is imported and unchanged, because check.py pins each policy's file hash: an edit there would make every existing
log refuse with `code changed: decide`. This module's dependencies are pinned in its own config instead.

Stand-ins, all logged in the header: one seed Look, a Preference with level factor 1 and mired_shift and contrast_shift
at 0. A track's surface is its working_region.surface_id, else 'room': the blocks the gate's surface_map names for it on
a real model, else the whole view. An eye is named by its track_id, because need v0 has no attention and rung 8 writes `glare: null`.

Standard library only. handle() reads no clock, randomness, file or network; make_policy reads the gate file and a
light-model file, once.
"""
import copy
import json
from pathlib import Path

import decide
import events
import fake_light
import light_adapter

HERE = Path(__file__).resolve().parent
GATE_FILE = HERE / "rung9-gate-v0.json"
VERSION = "coordinator-k16-v0"
POLICY = "coordinator@k16-v0"
LIGHTS = decide.LIGHTS
OFF = decide.OFF
ENVELOPE = {"version": "body-envelope/v1", "parameters": None, "parameters_status": "pending (envelope-parameters)"}
NEUTRAL = {"level_factor": 1, "mired_shift": 0, "contrast_shift": 0}
PINS = {"decide": decide, "light_adapter": light_adapter, "fake_light": fake_light}
OPTIONS = ("P0", "P1", "P2", "P3")
EPS = 1e-9
HARD_PENALTY = 1000.0  # added to a candidate's cost when a hard constraint or a bound rejects it
SUPPORT_KEYS = ("last_support_capture_ts", "support_status")  # what the schema's Decision.inputs.field_support carries
LINE_ROOM = events.MAX_LINE_BYTES - 200  # a Decision's own bytes, leaving room for the runner's envelope and actuation


def load_gate(path=None):
    gate = json.loads(Path(path or GATE_FILE).read_bytes().decode("utf-8-sig"))
    for name, slot in gate["slots"].items():
        if slot["status"] not in ("open", "closed") or (slot["status"] == "closed" and slot["value"] is None):
            raise ValueError("gate slot %s: closed needs a value, else status open" % name)
    chosen = gate["slots"]["surface_map"]["value"]
    if chosen is not None and not (isinstance(chosen, dict) and all(
            isinstance(blocks, list) and all(isinstance(p, str) for p in blocks) for blocks in chosen.values())):
        raise ValueError("gate slot surface_map: its value must be {surface_id: [point_id, ...]}")
    return gate


def gate_config(gate):
    """What a run logs of the gate: its hash, each slot's value (an open one at its proposal), and which are open."""
    slots = {name: slot["proposed"] if slot["status"] == "open" else slot["value"] for name, slot in gate["slots"].items()}
    return {"id": gate["gate"], "sha256": events.sha256_json(gate), "slots": slots,
            "open": sorted(name for name, slot in gate["slots"].items() if slot["status"] == "open")}


def seed_look(slots):
    scene = {"on": True, "brightness": slots["look_brightness"], "kelvin": slots["look_kelvin"]}
    return {"look_id": decide.LOOK,
            "status": "seed stand-in until the Look rung; every value is a target to validate",
            "applies_when": {"room": "occupied", "focused_engagement": ["engaged", "ending"], "kind": "reading"},
            "layers": [{"layer": "task", "targets": "the reference scene's light at each reader's surface"}],
            "targets": {"reference_scene": scene, "cct_k": [scene["kelvin"], scene["kelvin"]],
                        "glare_at_eyes": {"limit": slots["glare_limit"], "unit": "the light model's G"}},
            "weights": slots["look_weights"],
            "transitions": {"fade_s": slots["look_fade_s"], "min_dwell_s": None,
                            "min_dwell_status": "not enforced in v0; stability is measured instead"},
            "guardrails": {"level_factor": [1, 1], "mired_shift": None,
                           "mired_shift_status": "pending (colour-guardrails)", "contrast_shift": [0, 0],
                           "fixed": ["glare_at_eyes"]}}


def _defaults(gate):
    slots = gate["slots"]
    scene = {"on": True, "brightness": slots["look_brightness"], "kelvin": slots["look_kelvin"]}
    config = copy.deepcopy(decide.DEFAULT_CONFIG)
    config.update({
        "version": VERSION,
        "policy": POLICY,
        "gate": gate,
        "looks": {decide.LOOK: {"scene": scene, "status": "seed stand-in; the look values are the owner's choice"}},
        "look": seed_look(slots),
        "preference": {"scope": "household", "offsets": dict(NEUTRAL), "n": 0,
                       "comfort_limits": {"version": "cl-den-0", "entries": []},
                       "status": "fixed stand-in until the Preference rung"},
        "compensation": {"option": slots["compensation_option"], "form": slots["compensation_form"],
                         "p3_fallback": slots["p3_fallback"], "lambda_h": slots["lambda_h"],
                         "footprint_threshold": slots["footprint_threshold"],
                         "footprint_membership": slots["footprint_membership"], "status": "pending (compensation)"},
        "local_completion": slots["local_completion"],
        "manual_vs_safety": slots["manual_vs_safety"],
        "gaming": slots["gaming"],
        "send_latency_s": 0,
        "body_envelope": ENVELOPE,
        "light_model": {"source": "fake", "params": {}},
        "code": {name: events.sha256_file(module.__file__) for name, module in PINS.items()},
    })
    config["decisions"]["compensation"] = {"value": "see compensation", "status": "pending",
                                           "effect": "the named option below; its hold fixtures are not admitted"}
    config["decisions"]["manual-vs-safety"] = {"value": "see manual_vs_safety", "status": "pending",
                                               "effect": "held bulbs never change; a conflict goes in hard_conflicts"}
    config["decisions"]["gaming"] = {"value": "see gaming", "status": "pending",
                                     "effect": "no decisions in a game, unless complete-policy"}
    config["not_used"] = ["resolve-deadline", "mid-execution", "unset-hold-fields", "unknown-aim-on", "moving-spot",
                          "tau-keep", "envelope-parameters", "freshness", "colour-guardrails", "comfort-policy",
                          "promotion-margins"]
    return config


def _level(scene):
    """The command as a share of Hue's full; 0 when off, None with no brightness."""
    if not scene or not scene["on"]:
        return 0.0
    if scene.get("brightness") is None:
        return None
    return min(light_adapter.HUE_FULL, scene["brightness"]) / light_adapter.HUE_FULL


def surface_of(person, light):
    """A person's task surface: their working region's, else 'room'."""
    surface = ((person or {}).get("working_region") or {}).get("surface_id")
    return surface if surface in light.surfaces else light_adapter.ROOM


def eye_rows(inputs, scene, light, slots):
    """predicted.eyes, from what a Decision logs and nothing else: the state ids and their field_support, the envelope
    version and t_exec, for an actual resulting scene. A row is `excluded` when its channel breaks the glare limit at
    that eye. Envelope parameters are pending, so nothing grows with age yet; support_age is logged for when it does."""
    if inputs["body_envelope"]["version"] != ENVELOPE["version"]:
        raise ValueError("unknown body envelope %r" % (inputs["body_envelope"]["version"],))
    rows = []
    for state_id in inputs["person_state"]:
        track = state_id.split("@")[0]
        support = inputs["field_support"].get(state_id) or {}
        field = support.get("attention") or support.get("focused_engagement") or {}
        last = field.get("last_support_capture_ts")
        for channel in LIGHTS:
            one = scene.get(channel)
            if not one or not one["on"]:
                continue
            t_exec = inputs["t_exec"].get(channel)
            row, level = light.glare(channel, track), _level(one)
            known = row is not None and level is not None
            g = round(row["G"] * level, 6) if known else None
            g_hi = round(row["G"] * level + slots["glare_sd_k"] * row["sd"], 6) if known else None
            limit = slots["glare_limit"] if known else None
            age = round(t_exec - last, 3) if t_exec is not None and last is not None else None
            rows.append({"track_id": track, "channel": channel, "G": g, "G_hi": g_hi, "limit": limit,
                         "model_status": row["model_status"] if row else "unvalidated", "t_exec": t_exec,
                         "support_age": age, "support_status": field.get("support_status"),
                         "excluded": bool(known and g_hi > limit)})
    return rows


def recipients_of(scene, tasks, rows, light, slots):
    """Decision.recipients for an actual resulting scene: per channel that is on, the tracks it serves (share of a task
    at least s_serve) and exposes. V_c(i) is taken as 1: visibility comes after rung 8."""
    total = light.predict(scene, tasks.values())
    p90 = light.error.get("p90_pct")
    out = {}
    for channel in LIGHTS:
        one = scene.get(channel)
        if not one or not one["on"]:
            continue
        part, targets, served = light.contribution(channel, one), [], []
        for track, surface in sorted(tasks.items()):
            value = None if part is None else part[surface]
            share = round(value / total[surface], 4) if value is not None and total[surface] else None
            interval = None
            if share is not None and p90 is not None:
                interval = [round(max(0.0, share * (1 - p90 / 100)), 4), round(min(1.0, share * (1 + p90 / 100)), 4)]
            value = None if value is None else round(value, 3)
            targets.append({"target_id": surface, "kind": "task", "track_id": track, "share": share, "interval": interval,
                            "lx": value if light.unit == "lx" else None, "value": value, "unit": light.unit})
            if share is not None and share >= slots["s_serve"]:
                served.append(track)
        exposed = sorted({r["track_id"] for r in rows if r["channel"] == channel and (
            r["G_hi"] is None or r["G_hi"] > slots["exposed_fraction"] * r["limit"]
            or str(r["support_status"]).startswith("held"))})  # a held head never drops a person from it
        out[channel] = {"served": served, "exposed": exposed, "layers": ["task"], "targets": targets,
                        "light_model": light.version}
    return out


class Coordinator:
    id = "decide"  # decide's place in the runner's order and in run.py's and validate.py's comparisons
    version = VERSION
    code_path = __file__

    def __init__(self, config, light):
        self.config, self.light = config, light
        self.config_sha256 = events.sha256_json(config)
        self.slots = config["gate"]["slots"]
        self.look = config["looks"][decide.LOOK]["scene"]
        self.state = {"occupancy": "unknown", "vacant_since": None, "actuator_state": {b: None for b in LIGHTS},
                      "person": None, "mode": None}
        self.people, self.changers, self.refs = {}, {}, {}
        self.previous, self.den_moves = {}, {}
        self.applied = dict.fromkeys(LIGHTS)
        self.solved, self.pending, self.last_rule = None, None, None
        self.made = 0
        self._memo = {}  # each hold's bound and what a channel's setting does against it, while holds and people stand still

    # What each event changes -----------------------------------------------------------------------------------------

    def _observe(self, event):
        """decide.Decide.handle's state rules, with people per track; the trigger, or None for no plan."""
        kind, data, t, state = event["kind"], event["data"], event["t"], self.state
        if kind == "occupancy":
            value = data.get("value") if data.get("value") in ("on", "off") else "unknown"
            if value != "off":
                state["vacant_since"] = None
            elif state["occupancy"] != "off":
                state["vacant_since"] = t
            state["occupancy"] = value
            return "mode change"
        if kind == "light_observed":
            if data.get("entity_id") in LIGHTS:
                seen = decide._scene(data)
                if seen != state["actuator_state"][data["entity_id"]]:
                    self.previous[data["entity_id"]] = state["actuator_state"][data["entity_id"]]
                state["actuator_state"][data["entity_id"]] = seen
            return "mode change"
        if kind == "person_state":
            self.people.pop(data["track_id"], None)  # latest last, so the line knows who was heard from longest ago
            self.people[data["track_id"]] = copy.deepcopy(data)
            return "person_state change"
        if kind == "mode":
            previous = (state["mode"] or decide.NO_MODE).get("holds", [])
            state["mode"] = copy.deepcopy(data)
            opened = self._sync_holds(data)
            if data.get("occupancy", state["occupancy"]) != state["occupancy"]:
                return None  # the occupancy event follows in this chain and plans with this mode, as decide waits
            if opened:
                return None  # its correction follows in this chain and says who changed the bulb, so plan on that
            if data.get("gaming") and self.config["gaming"] == "complete-policy":
                return None  # den_lighting's own decision follows in this chain, with the moves to follow
            return "hold change" if data.get("holds", []) != previous else "mode change"
        if kind == "tick":
            return "time step"
        if kind == "correction":
            self.changers[data["entity_id"]] = (data.get("who") or {}).get("track_id")
            return "hold change"
        if kind == "decision" and event["source"] == "incumbent":
            self.den_moves.update(data["requested_scene"])
            return "mode change" if self.config["gaming"] == "complete-policy" else None
        return None

    def _current(self, bulb):
        """The state the candidate's own last decision left, else what the bulb was last seen at."""
        return self.applied[bulb] or self.state["actuator_state"][bulb]

    def _sync_holds(self, mode):
        """Open a reference for each new hold: which way the person moved the light, where, and every other bulb's level
        at that moment. A hold that has gone drops its reference. True when a hold opened."""
        holds = {h["entity_id"]: h for h in mode.get("holds", []) if h["entity_id"] in LIGHTS}
        for bulb in [b for b in self.refs if b not in holds]:
            del self.refs[bulb]
        opened = False
        for bulb, hold in holds.items():
            if bulb in self.refs and self.refs[bulb]["since"] == hold["since"]:
                continue
            opened = True
            seen = self.state["actuator_state"][bulb]
            before = self.previous.get(bulb) if decide.matches(hold["pinned"], seen) else seen
            if bulb in self.refs:  # a touch on a held bulb moved it from the old hold's setting
                before = self.refs[bulb]["pinned"]
            direction, footprint, whole = self._moved(bulb, before, hold["pinned"])
            self.refs[bulb] = {"since": hold["since"], "pinned": hold["pinned"], "direction": direction,
                               "footprint": footprint, "mask": self.light.mask(footprint), "whole": whole,
                               "scene": {b: self._current(b) for b in LIGHTS if b != bulb}}
        return opened

    def _moved(self, bulb, before, after):
        """('dim' | 'brighten' | 'unknown', the footprint's block indexes, the light at each block before). A block is in
        the footprint when the change moved its light by at least footprint_threshold of what was there; with no known
        before, by the share the bulb gives. With no level on the held bulb it is every block."""
        comp, light = self.config["compensation"], self.light
        new, old, size = light.row(bulb, after), light.row(bulb, before), len(light.points)
        if new is None:
            return "unknown", list(range(size)), [0.0] * size
        others = [light.row(b, self._current(b)) for b in LIGHTS if b != bulb and self._current(b)]
        known = others and all(row is not None for row in others)
        rest = [sum(column) for column in zip(*others)] if known else [0.0] * size  # as predict: unknown if any bulb is
        at_or_above = comp["footprint_membership"] == "at_or_above"
        footprint, signs, whole = [], set(), [0.0] * size
        for n in range(size):
            moved = new[n] if old is None else abs(new[n] - old[n])
            whole[n] = rest[n] + (new[n] if old is None else old[n])
            share = moved / whole[n] if whole[n] > EPS else 0.0
            if share > comp["footprint_threshold"] or (at_or_above and share == comp["footprint_threshold"]):
                footprint.append(n)
                if old is not None and new[n] != old[n]:
                    signs.add(new[n] > old[n])
        direction = {frozenset({False}): "dim", frozenset({True}): "brighten"}.get(frozenset(signs), "unknown")
        return direction, footprint, whole

    def _footprint(self, ref):
        """What a Decision writes of a footprint. Every block of the grid fits, as a mask over the model's sample_points;
        `surfaces` says which of today's task surfaces it touches."""
        tasks = {surface_of(person, self.light) for person in self.people.values()}
        return {"points": len(ref["footprint"]), "of": len(self.light.points), "mask": ref["mask"],
                "surfaces": sorted(s for s in tasks if not self.light.members[s].isdisjoint(ref["footprint"]))}

    # The compensation options ----------------------------------------------------------------------------------------

    def _bound(self, hold_bulb):
        """(option in force for this hold, the blocks where free light may not move against the change)."""
        if hold_bulb in self._memo:
            return self._memo[hold_bulb]
        comp, ref = self.config["compensation"], self.refs[hold_bulb]
        option, blocks = comp["option"], ref["footprint"]
        if option == "P3":
            changer = self.changers.get(hold_bulb)
            if changer is None:
                option = comp["p3_fallback"]
            else:
                own = self.light.members[surface_of(self.people.get(changer), self.light)]
                blocks = [n for n in blocks if n in own]
        self._memo[hold_bulb] = option, [] if option == "P0" else blocks
        return self._memo[hold_bulb]

    def _against(self, hold_bulb, channel, scene):
        """How far a free channel's scene moves against the change at the bound blocks: {block: amount > 0}, measured from
        the reference, which only ever moves with the person's change."""
        if scene is None:
            return {}
        key = (hold_bulb, channel, scene["on"], scene.get("brightness"), scene.get("kelvin"), scene.get("aim"))
        if key not in self._memo:
            ref = self.refs[hold_bulb]
            new, old = self.light.row(channel, scene), self.light.row(channel, ref["scene"].get(channel))
            out = {}
            if new is not None and old is not None:
                direction = ref["direction"]
                for n in self._bound(hold_bulb)[1]:
                    delta = new[n] - old[n]
                    if direction == "dim" and delta > EPS or direction == "brighten" and delta < -EPS or (
                            direction == "unknown" and abs(delta) > EPS):
                        out[n] = abs(delta)
            self._memo[key] = out
        return self._memo[key]

    def _breaks_bound(self, channel, scene):
        return any(self._bound(h)[0] in ("P1", "P3") and self._against(h, channel, scene) for h in self.refs)

    def _against_at_surfaces(self, hold_bulb, channel, scene):
        """_against, summed over each surface's blocks."""
        if scene is None:
            return {}
        key = ("surfaces", hold_bulb, channel, scene["on"], scene.get("brightness"), scene.get("kelvin"), scene.get("aim"))
        if key not in self._memo:
            amounts = self._against(hold_bulb, channel, scene)
            found = {name: sum(amounts.get(n, 0.0) for n in members) for name, members in self.light.members.items()} if amounts else {}
            self._memo[key] = {name: amount for name, amount in found.items() if amount}
        return self._memo[key]

    def _light_at_surfaces(self, hold_bulb):
        """The light that was at each surface's blocks in this hold's footprint."""
        key = ("whole", hold_bulb)
        if key not in self._memo:
            ref = self.refs[hold_bulb]
            footprint = set(ref["footprint"])
            self._memo[key] = {name: sum(ref["whole"][n] for n in members if n in footprint)
                               for name, members in self.light.members.items()}
        return self._memo[key]

    def _penalty(self, free_scene):
        """P2's soft penalty: lambda_h times the squared share of the light at each surface's footprint blocks that free
        light moved against each hold. Against a squared shortfall, that makes up part of a gap, as P2's rule moves do."""
        total = 0.0
        for hold in self.refs:
            if self._bound(hold)[0] != "P2":
                continue
            moved, whole = {}, self._light_at_surfaces(hold)
            for channel, scene in free_scene.items():
                for name, amount in self._against_at_surfaces(hold, channel, scene).items():
                    moved[name] = moved.get(name, 0.0) + amount
            total += sum((amount / max(whole[name], 1.0)) ** 2 for name, amount in moved.items())
        return self.config["compensation"]["lambda_h"] * total

    def _outside_bound(self, rule):
        """In the mode form, den_lighting's mode transitions sit outside the bound: vacancy-off, the arrival after it, and
        the moves of a game. A return to base because reading ended is the coordinator's own move, so it stays inside."""
        if self.config["compensation"]["form"] != "mode":
            return False
        return rule in ("vacant_off", "game") or (rule == "base" and self.last_rule in (None, "vacant_off", "game"))

    def _ratchet(self, moved):
        """A free channel's move with the person's change becomes its new reference, so the bound never lets it back."""
        for ref in self.refs.values():
            for channel, scene in moved.items():
                if channel not in ref["scene"]:
                    continue
                new, old = self.light.row(channel, scene), self.light.row(channel, ref["scene"][channel])
                if new is None or old is None:
                    ref["scene"][channel] = ref["scene"][channel] or scene
                    continue
                deltas = [new[n] - old[n] for n in ref["footprint"]]
                if (ref["direction"] == "dim" and all(d <= EPS for d in deltas)
                        or ref["direction"] == "brighten" and all(d >= -EPS for d in deltas)):
                    ref["scene"][channel] = scene
        self._memo = {}

    # Glare --------------------------------------------------------------------------------------------------------------

    def _conflicts(self, held):
        """hard_conflicts: each held light that alone breaks the seed Look's glare limit at an eye."""
        out = []
        for bulb in sorted(held, key=LIGHTS.index):
            level = _level(held[bulb]["pinned"])
            for track in sorted(self.people):
                row = self.light.glare(bulb, track)
                if not row or not level:
                    continue
                g_hi = row["G"] * level + self.slots["glare_sd_k"] * row["sd"]
                if g_hi > self.slots["glare_limit"]:
                    out.append({"constraint": "glare_at_eyes", "held_channel": bulb, "track": track,
                                "eye_or_envelope": "eye-" + track, "G_hi": round(g_hi, 6),
                                "limit": self.slots["glare_limit"], "model_status": row["model_status"]})
        return out

    def _glare_ok(self, channel, scene, conflicts):
        """The hard constraint on a free channel: under the limit at every eye, and no higher than now at an eye a held
        light is already over, where it may not raise its part of the conflict."""
        level = _level(scene)
        if not level:
            return True
        now = _level(self._current(channel)) or 0.0
        for track in self.people:
            row = self.light.glare(channel, track)
            if not row:
                continue
            if row["G"] * level + self.slots["glare_sd_k"] * row["sd"] > self.slots["glare_limit"]:
                return False
            if row["G"] > 0 and level > now and any(c["track"] == track for c in conflicts):
                return False
        return True

    def _capped(self, channel, scene, conflicts):
        """A rule's scene brought under the hard constraint: the highest level that passes, else off."""
        if self._glare_ok(channel, scene, conflicts):
            return scene
        for brightness in range(scene["brightness"] - 1, 0, -1):
            lower = {**scene, "brightness": brightness}
            if self._glare_ok(channel, lower, conflicts):
                return lower
        return dict(OFF)

    # The plan -----------------------------------------------------------------------------------------------------------

    def _cost(self, free_scene, pinned, targets):
        """The look's weighted total at the targets: squared relative shortfall and excess, distance from the look's level
        and P2's penalty. None while a free bulb has no level."""
        if any(self.light.contribution(b, s) is None for b, s in free_scene.items()):
            return None
        weights = self.slots["look_weights"]
        known = {b: s for b, s in pinned.items() if self.light.contribution(b, s) is not None}
        total = self.light.predict({**known, **free_scene}, targets)
        cost = 0.0
        for surface, target in targets.items():
            gap = (target - (total[surface] or 0.0)) / target if target > EPS else 0.0
            cost += (weights["shortfall"] if gap > 0 else weights["excess"]) * gap ** 2
        cost += weights["change"] * sum(abs(self.look["brightness"] - (s.get("brightness") or 0)) / 255.0
                                        for s in free_scene.values())
        return cost + self._penalty(free_scene)

    def _solve(self, free, pinned, targets, conflicts):
        """Coordinate descent over the free channels, two passes, in bulb order: the lowest cost per channel, others fixed.
        A channel may always stay where it is, even if a bound would refuse a move there. A channel with a spot model
        tries its aims first, then the levels at the best one; a Hue bulb has no aim."""
        steps = self.slots["solve_steps"]
        levels = sorted({round(255 * (n + 1) / steps) for n in range(steps)} | {self.look["brightness"]})
        scene = {}
        for b in free:
            start = dict(self.look)
            if not self._glare_ok(b, start, conflicts) or self._breaks_bound(b, start):
                start = self._current(b)
            known = start is not None and self.light.contribution(b, start) is not None
            scene[b] = self._capped(b, start, conflicts) if known else dict(OFF)  # the hard limit outranks staying put
        for _ in range(2):
            for b in free:
                aims = self.light.aims(b)
                if aims != [None]:
                    scene[b] = min(({**scene[b], "aim": aim} for aim in aims),
                                   key=lambda s: self._cost({**scene, b: s}, pinned, targets))
                best, best_cost = scene[b], self._cost(scene, pinned, targets)
                for brightness in levels:
                    option = {**self.look, "brightness": brightness}
                    if aims != [None]:
                        option["aim"] = scene[b]["aim"]
                    if not self._glare_ok(b, option, conflicts) or self._breaks_bound(b, option):
                        continue
                    cost = self._cost({**scene, b: option}, pinned, targets)
                    if cost is not None and (best_cost is None or cost < best_cost - EPS):
                        best, best_cost = option, cost
                scene[b] = best
        return scene

    def _candidates(self, free, pinned, targets, conflicts):
        """decide's four candidates with light-model costs, plus the solved scene. Rejected ones keep a cost, HARD_PENALTY
        over, so the first lowest cost stays the choice (validate.py reads it that way)."""
        actuator, normal = self.state["actuator_state"], self.state["mode"] and self.state["mode"].get("normal") or {}
        solve_id = "solve:" + decide.LOOK
        options = (
            ("keep_current", {b: actuator.get(b) for b in free}, "Leave the free bulbs as last observed"),
            ("look:" + decide.LOOK, {b: dict(self.look) for b in free}, "Put the seed Look on every free bulb"),
            (solve_id, self._solve(free, pinned, targets, conflicts), "Solve each free bulb for the seed Look's targets"),
            ("base", {b: normal.get(b) for b in free}, "Use den_lighting's normal scene from mode.normal"),
            ("off", {b: dict(OFF) for b in free}, "Turn every free bulb off"),
        )
        out = []
        for scene_id, scene, text in options:
            reason = None
            if any(s is None for s in scene.values()):
                reason, cost = "no scene known", None
            else:
                cost = self._cost(scene, pinned, targets)
                if cost is None:
                    reason = "no level known"
                elif not all(self._glare_ok(b, s, conflicts) for b, s in scene.items()):
                    reason = "over the glare limit"
                elif scene_id != solve_id and any(self._breaks_bound(b, s) for b, s in scene.items()):
                    reason = "moves against a hold"  # the solve may keep a channel where it is, even out of bound
            total = (cost or 0.0) + (HARD_PENALTY if reason else 0.0)
            out.append({"scene_id": scene_id, "scene": scene, "cost": round(total, 6),
                        "why": "%s; %s." % (text, "rejected: " + reason if reason else "cost %.6f" % total)})
        return out

    def _follow(self, rule, target, conflicts):
        """A rule's own scene (base, vacancy-off, a game's moves): capped under the glare limit and, inside the bound, held
        back from moving against a hold: a P1 or P3 channel stays where it is, a P2 channel moves part of the way."""
        scene = {}
        for b, wanted in target.items():
            one = self._capped(b, wanted, conflicts)
            if self.refs and not self._outside_bound(rule):
                if self._breaks_bound(b, one) and self._current(b) is not None:
                    one = self._capped(b, self._current(b), conflicts)  # the hard limit outranks staying put
                else:
                    soft = [h for h in self.refs if self._bound(h)[0] == "P2" and self._against(h, b, one)]
                    if soft:
                        one = self._partway(self.refs[soft[0]]["scene"].get(b), one)
            scene[b] = one
        return scene

    def _partway(self, reference, target):
        """P2 in a rule's own move: 1 / (1 + lambda_h) of the way from the reference to the target."""
        if reference is None:
            return target
        start = reference.get("brightness") if reference["on"] else 0
        end = target.get("brightness") if target["on"] else 0
        if start is None or end is None:
            return target
        brightness = int(round(start + (end - start) / (1.0 + self.config["compensation"]["lambda_h"])))
        if brightness <= 0:
            return dict(OFF)
        return {"on": True, "brightness": brightness, "kelvin": target.get("kelvin") or reference.get("kelvin")}

    def _hold_limited(self, requested, targets):
        """Target surfaces a hold's bound leaves under target: the candidate's own diagnostic, never a delivery miss."""
        bound = {s: h for h in sorted(self.refs) for s in targets if not self.light.members[s].isdisjoint(self._bound(h)[1])}
        total = self.light.predict({b: s for b, s in requested.items() if self.light.contribution(b, s) is not None}, targets)
        return [{"point_id": s, "target": round(t, 3),
                 "reason": "%s held (%s): the bound keeps free light from making up for it" % (
                     bound[s], self.refs[bound[s]]["direction"])}
                for s, t in sorted(targets.items())
                if s in bound and (total[s] or 0.0) < t * (1 - self.slots["met_tolerance"])]

    def _plan(self, t):
        """None, or the plan for this instant: the rule, its scene and everything a Decision says about why."""
        state, mode = self.state, {**decide.NO_MODE, **(self.state["mode"] or {})}
        readers = {track: surface_of(p, self.light) for track, p in sorted(self.people.items()) if decide.reading(p)}
        view = {**state, "person": self.people[next(iter(readers))] if readers else None}
        held = {h["entity_id"]: h for h in mode["holds"] if h["entity_id"] in LIGHTS}
        free = [b for b in LIGHTS if b not in held]
        conflicts = self._conflicts(held)
        pinned = {b: decide._scene(h["pinned"]) for b, h in held.items()}
        if mode["gaming"]:
            if self.config["gaming"] != "complete-policy" or state["occupancy"] == "unknown":
                return None
            base = {"rule": "game", "candidates": [], "chosen": "den_lighting's moves",
                    "skipped": [{"channel": b, "reason": "held", "hold_ref": "hold:%s@%.3f" % (b, held[b]["since"])}
                                for b in LIGHTS if b in held]}
            # In a game held bulbs follow den_lighting's moves too: game-time vacancy turns them off, holds intact.
            pinned = {b: self.den_moves.get(b, pinned[b]) for b in held}
            target = {b: self.den_moves[b] for b in free if b in self.den_moves}
        else:
            base = decide.plan(view, t, self.config)
            if base is None:
                return None
            target = {b: s for b, s in base["requested_scene"].items() if b in free}
        rule = base["rule"]
        targets = {}
        if rule == "reading_look":
            reference = self.light.predict({b: self.look for b in LIGHTS}, readers.values())
            targets = {s: reference[s] for s in readers.values() if reference[s] is not None}  # none on a dead surface
            candidates = self._candidates(free, pinned, targets, conflicts)
            best = min(candidates, key=lambda c: c["cost"])
            chosen, cost, scene = best["scene_id"], best["cost"], dict(best["scene"])
        else:
            candidates = base["candidates"]
            scene = self._follow(rule, target, conflicts)
            chosen, cost = base["chosen"], next((c["cost"] for c in candidates if c["scene_id"] == base["chosen"]), 0)
        requested = {b: decide._scene(s) for b, s in scene.items()}
        for b, s in scene.items():
            if s.get("aim") is not None:
                requested[b]["aim"] = s["aim"]
        requested.update(pinned)
        if not requested:
            return None
        return {"rule": rule, "requested": requested, "candidates": candidates, "chosen": chosen, "cost": cost,
                "skipped": sorted(base["skipped"], key=lambda s: LIGHTS.index(s["channel"])), "targets": targets,
                "readers": readers, "hard_conflicts": conflicts, "pinned": set(pinned),
                "hold_limited": self._hold_limited(requested, targets) if rule == "reading_look" else []}

    # The Decision ---------------------------------------------------------------------------------------------------------

    def _inputs(self):
        mode = {**decide.NO_MODE, **(self.state["mode"] or {})}
        comp = self.config["compensation"]
        people = [self.people[track] for track in sorted(self.people)]
        held = []
        for hold in mode["holds"]:
            entry = copy.deepcopy(hold)
            if comp["option"] != "P0" and hold["entity_id"] in self.refs:  # the fields only P1 to P3 carry
                ref = self.refs[hold["entity_id"]]
                entry.update(direction=ref["direction"], footprint=self._footprint(ref))
                if comp["option"] == "P3":
                    entry["changer_track"] = self.changers.get(hold["entity_id"])
            held.append(entry)
        return {
            "person_state": [p["state_id"] for p in people],
            "field_support": {p["state_id"]: {name: {key: support.get(key) for key in SUPPORT_KEYS}
                                              for name, support in (p.get("field_support") or {}).items()} for p in people},
            "hold_set": {"revision": mode["revision"], "held": held},
            "revisions": {"hold_set": mode["revision"], "mode": mode["revision"]},
            "actuator_state": copy.deepcopy(self.state["actuator_state"]),
            "config_sha256": self.config_sha256,
            "look": decide.LOOK,
            "base": decide.BASE_LABEL,
            "preference": {"scope": "household", "offsets": self.config["preference"]["offsets"]},
            "comfort_limits": self.config["preference"]["comfort_limits"]["version"],
            "light_model": self.light.version,
            "light_state": None,
            "light_state_status": "no light_state until rung 7",
            "body_envelope": self.config["body_envelope"],
            "coordinator": VERSION,
            "recipient_rule": "recipients/v1",
            "compensation": option_name(comp),
        }

    def _record(self, plan, t, trigger):
        self.made += 1
        lux = self.light.unit == "lx"
        targets = {track: {"surface_id": surface, "lx": round(plan["targets"][surface], 3) if lux else None,
                           "value": round(plan["targets"][surface], 3), "unit": self.light.unit,
                           "cct_k": self.look["kelvin"],
                           "note": "target to validate: the seed Look's reference scene at this surface"}
                   for track, surface in plan["readers"].items() if surface in plan["targets"]}
        return {"decision_id": "decide-%d-%d" % (round(t * 1000), self.made), "ts": t, "trigger": trigger, "policy": POLICY,
                "actuator_domain": "hue-den-4", "inputs": self._inputs(), "targets": targets,
                "requested_scene": plan["requested"], "candidates": plan["candidates"], "skipped": plan["skipped"],
                "hard_conflicts": plan["hard_conflicts"], "why": "%s: chose %s at cost %s; %d held, %d skipped." % (
                    plan["rule"], plan["chosen"], plan["cost"],
                    sum(s["reason"] == "held" for s in plan["skipped"]), len(plan["skipped"]))}

    def _finish(self, plan, t_send, superseded):
        """K17's part, simulated: what each channel's step does, then the Decision for the actual resulting state. When a
        revision moved between the solve and the send, the held bulb's step is dropped under every option; another bulb's
        goes out only if local-completion allows it and its fresh check passes."""
        mode = {**decide.NO_MODE, **(self.state["mode"] or {})}
        held_now = {h["entity_id"]: h for h in mode["holds"] if h["entity_id"] in LIGHTS}
        fade = self.slots["look_fade_s"]
        record, revisions = plan["record"], plan["record"]["inputs"]["revisions"]
        conflicts = self._conflicts(held_now)
        local = self.config["local_completion"] == "allowed"
        applied, transitions, t_exec, moved, result = {}, {}, {}, {}, {}
        for bulb, scene in plan["requested"].items():
            now, entry = self._current(bulb), {"status": "sent", "aim_status": None, "aim_source": None, "steps": []}
            if bulb in plan["pinned"] or bulb in held_now:
                entry["status"] = "held"
                final = scene if bulb in plan["pinned"] else decide._scene(held_now[bulb]["pinned"])
                if bulb not in plan["pinned"]:
                    entry["reason"] = "a hold arrived between solve and send: its step is dropped under every option"
                t_exec[bulb], t_exec[bulb + "_status"] = None, "held: kept at the person's setting"
            elif superseded and not (local and self._glare_ok(bulb, scene, conflicts) and not self._breaks_bound(bulb, scene)):
                entry["status"], entry["reason"] = "superseded", "the revision moved between solve and send"
                final = now
                t_exec[bulb], t_exec[bulb + "_status"] = None, "superseded: nothing went out"
            elif decide.matches(scene, now) and scene.get("aim") == (now or {}).get("aim"):
                final = scene
                t_exec[bulb], t_exec[bulb + "_status"] = None, "kept: t + T_chk + T_react (pending, freshness)"
            else:
                entry["status"] = "completed_after_check" if superseded else "sent"
                entry["steps"].append({
                    "kind": "send", "revisions": revisions, "sent_at": t_send, "transition_s": fade, "reason": None,
                    "result": {"status": "simulated: nothing is sent", "at": t_send + fade,
                               "readback": {key: scene[key] for key in ("on", "brightness", "kelvin")}},
                    "checks": {"revision_current": not superseded, "fresh_check": superseded, "not_held": True}})
                final, transitions[bulb], t_exec[bulb] = scene, fade, t_send + fade
                moved[bulb] = scene
            # The schema's level and cct_k beside hue-den-4's three keys, for rung 10 and K20, which read them.
            entry["readback"] = None if final is None else {**final, "level": _level(final), "cct_k": final.get("kelvin")}
            entry["readback_source"] = "simulated: nothing is sent"
            applied[bulb] = entry
            if final is not None:
                result[bulb] = final
        record["status"] = ("partial" if moved else "superseded") if superseded else "applied"
        self.applied.update({b: result[b] for b, entry in applied.items() if b in result and entry["status"] != "superseded"})
        self._ratchet(moved)
        record["inputs"]["t_exec"], record["applied_scene"], record["transitions"] = t_exec, applied, transitions
        tasks = {track: surface_of(p, self.light) for track, p in sorted(self.people.items())}
        if self.config["compensation"]["option"] != "P0":
            record["hold_limited"] = plan["hold_limited"]
        self._rows(record, result, tasks)
        self._fit_the_line(record, result, tasks)
        return {"kind": "decision", "data": copy.deepcopy(record)}

    def _rows(self, record, result, tasks):
        """What a Decision says per track about the actual resulting state."""
        rows = eye_rows(record["inputs"], result, self.light, self.slots)
        record["recipients"] = recipients_of(result, tasks, rows, self.light, self.slots)
        at = sorted({light_adapter.ROOM, *tasks.values()})  # a map can be wide; any other surface follows from the scene
        record["predicted"] = {
            "surfaces": {**{s: None if v is None else round(v, 3) for s, v in self.light.predict(result, at).items()},
                         "unit": self.light.unit},
            "eyes": rows,
            "eyes_note": ("predictions only; no camera verifies eyes" if self.light.glare_table else
                          "no numbers: rung 8 writes glare null, and no glare model or numeric limit exists yet"),
            "daylight": "excluded: rung 8 does not model it"}

    def _fit_the_line(self, record, result, tasks):
        """events.validate refuses a line over its limit and the run then stops with no log, and a track is never forgotten:
        its id is never reused and a PersonState says nothing when it ends. So when a Decision would not fit, the rows of the
        tracks longest unheard from go, readers last, and inputs.tracks_not_logged counts them. They stay in the plan: this
        only decides what the line says. Every row of a track goes together, so predicted.eyes still reproduces."""
        inputs = record["inputs"]
        order = sorted(self.people, key=lambda track: decide.reading(self.people[track]))  # oldest first, each group
        for track in order[:-1]:
            if len(events.dumps(record)) <= LINE_ROOM:
                break
            inputs["person_state"] = [s for s in inputs["person_state"] if s.split("@")[0] != track]
            inputs["field_support"] = {s: v for s, v in inputs["field_support"].items() if s.split("@")[0] != track}
            inputs["tracks_not_logged"] = inputs.get("tracks_not_logged", 0) + 1
            record["targets"].pop(track, None)
            record["hard_conflicts"] = [row for row in record["hard_conflicts"] if row["track"] != track]
            tasks.pop(track, None)
            kept = set(tasks.values())
            for held in inputs["hold_set"]["held"]:
                if "footprint" in held:
                    held["footprint"]["surfaces"] = [s for s in held["footprint"]["surfaces"] if s in kept]
            if "hold_limited" in record:
                record["hold_limited"] = [row for row in record["hold_limited"] if row["point_id"] in kept]
            self._rows(record, result, tasks)

    def handle(self, event):
        t, out = event["t"], []
        if self.pending and t >= self.pending["send_at"]:  # the send comes before whatever this event changes
            out.append(self._finish(self.pending, self.pending["send_at"], False))
            self.pending = None
        trigger = self._observe(event)
        self._memo = {}
        if trigger is None:
            return out
        plan = self._plan(t)
        if plan is None:
            return out
        self.last_rule = plan["rule"]
        if plan["requested"] == self.solved:
            return out
        if self.pending:
            out.append(self._finish(self.pending, t, True))
            self.pending = None
        self.solved = plan["requested"]
        plan["record"], plan["send_at"] = self._record(plan, t, trigger), t + self.config["send_latency_s"]
        if self.config["send_latency_s"] > 0:
            self.pending = plan
        else:
            out.append(self._finish(plan, t, False))
        return out


def option_name(comp):
    """The named option, as hold_fixtures.OPTIONS writes it."""
    if comp["option"] == "P0":
        return "P0"
    name = "%s-%s" % (comp["option"], comp["form"])
    return name + "/" + comp["p3_fallback"] if comp["option"] == "P3" else name


def make_policy(config=None):
    """The coordinator. Rebuilt by check.py from a logged config, whose `gate` then saves reading the gate file; a changed
    dependency or light model refuses, as den_lighting's changed policy.py does."""
    given = copy.deepcopy(config or {})
    gate = given.get("gate") or gate_config(load_gate())
    effective = decide._merge(_defaults(gate), given)
    for name, module in PINS.items():
        if effective["code"][name] != events.sha256_file(module.__file__):
            raise ValueError("code changed: %s" % name)
    if effective["preference"]["offsets"] != NEUTRAL:
        raise ValueError("Preference is a fixed stand-in: offsets must stay %s" % NEUTRAL)
    comp = effective["compensation"]
    if comp["option"] not in OPTIONS or comp["form"] not in ("strict", "mode") or comp["p3_fallback"] not in ("P0", "P1"):
        raise ValueError("unknown compensation option: %s" % comp)
    if effective["manual_vs_safety"] != "S0":
        raise ValueError("only manual-vs-safety S0 is built")
    if effective["local_completion"] not in ("not-allowed", "allowed"):
        raise ValueError("unknown local-completion option: %s" % effective["local_completion"])
    if effective["gaming"] not in ("non-gaming-stratified", "complete-policy"):
        raise ValueError("unknown gaming option: %s" % effective["gaming"])
    model, logged = light_adapter.describe(effective["light_model"])
    chosen = effective["gate"]["slots"].get("surface_map")
    if chosen and not logged.get("surfaces") and not logged["fake"]:  # a run may name another, which score.py refuses
        logged["surfaces"] = chosen  # the owner's map is over rung 8's blocks: a fake has its own, and is never a gate
    effective["light_model"] = logged
    return Coordinator(effective, light_adapter.LightModel(model, surfaces=logged.get("surfaces")))
