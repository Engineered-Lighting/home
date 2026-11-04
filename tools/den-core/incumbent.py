"""den_lighting's own policy.py, loaded unchanged, as the frozen incumbent and comparator.

policy.py is found by walking up from this folder and is never copied or
edited. Its Controller sends nothing until enabled, and never turns ON a bulb
it has never observed (normal is seeded only by observe or manual), so a run
must observe all four bulbs first. Vacancy OFF is requested even for unobserved
bulbs; only den_lighting's HA runtime drops a command for a bulb with no state.
"""
import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path

import events

RELATIVE = Path("ha-config") / "custom_components" / "den_lighting" / "policy.py"
TRIGGERS = {"correction": "correction", "tick": "time step"}  # anything else: 'mode change'


def find_policy(start=None):
    start = Path(start or __file__).resolve()
    for folder in (start, *start.parents):
        if (folder / RELATIVE).is_file():
            return folder / RELATIVE
    return None


def load_policy(path):
    spec = importlib.util.spec_from_file_location("den_lighting_policy", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclasses look their module up while being built
    spec.loader.exec_module(module)
    return module


class Incumbent:
    id = "incumbent"
    code_path = __file__

    def __init__(self, den, config):
        self.den, self.config = den, config
        self.version = "den_lighting@" + config["manifest_version"]
        self.config_sha256 = events.sha256_json(config)
        self.controller = den.Controller()
        self.started, self.last_mode, self.decisions = False, None, 0

    def handle(self, event):
        t, kind, data = event["t"], event["kind"], event["data"]
        c, Scene = self.controller, self.den.Scene
        if not self.started:
            self.started = True
            c.set_enabled(True, t)
            c.set_gaming_enabled(True, t)
        if kind == "occupancy":
            c.set_occupancy(data["value"], t)
        elif kind == "light_observed":
            c.observe(data["entity_id"], Scene(data["on"], data["brightness"], data["kelvin"]), t)
        elif kind == "correction":
            c.manual(data["entity_id"], Scene(**data["after"]), t)
        elif kind == "gaming_sample":
            c.gaming_sample(data["status"], data["sampled_at"], t)
        commands = c.reconcile(t)
        lifetime = "game_ends" if c.gaming else "room_vacant_2_minutes"
        holds = [{"entity_id": e, "pinned": asdict(h.scene), "since": h.since, "lifetime": lifetime}
                 for e, h in sorted(c.holds.items())]
        mode = {"policy": self.version, "revision": c.revision, "gaming": c.gaming, "occupancy": c.occupancy,
                "holds": holds, "normal": {e: asdict(s) for e, s in c.normal.items()}}
        out = []
        if mode != self.last_mode:
            self.last_mode = mode
            out.append({"kind": "mode", "data": mode})
        if commands:
            self.decisions += 1
            out.append({"kind": "decision", "data": {
                "decision_id": "%s-%d-%d" % (self.id, round(t * 1000), self.decisions),
                "ts": t, "trigger": TRIGGERS.get(kind, "mode change"), "policy": self.version,
                "actuator_domain": "hue-den-4",
                "inputs": {"person_state": [], "field_support": {},
                           "hold_set": {"revision": c.revision, "held": holds},
                           "revisions": {"hold_set": c.revision, "mode": c.revision},
                           "actuator_state": {e: asdict(c.observed[e]) if e in c.observed else None
                                              for e in self.den.LIGHTS},
                           "config_sha256": self.config_sha256},
                "requested_scene": {cmd.entity_id: asdict(cmd.target) for cmd in commands},
                "transitions": {cmd.entity_id: cmd.transition for cmd in commands},
                "candidates": [], "skipped": [], "hard_conflicts": [], "status": "requested",
                "why": "den_lighting command"}})
        return out


def make_policy(config=None):
    """The incumbent. Its config is read from den_lighting, never set: a given
    config must equal it, so a logged run refuses a changed policy.py."""
    path = find_policy()
    if path is None:
        raise FileNotFoundError("den_lighting policy.py not found above %s" % Path(__file__).parent)
    sha = events.sha256_file(path)
    config = dict(config or {})
    if config.get("den_lighting_policy_sha256", sha) != sha:
        raise ValueError("den_lighting policy.py changed")
    den = load_policy(path)
    manifest = json.loads((path.parent / "manifest.json").read_text("utf-8"))
    effective = {"den_lighting_policy_sha256": sha, "manifest_version": manifest["version"],
                 "vacancy_seconds": den.VACANCY_SECONDS, "gaming_ttl": den.GAMING_TTL}
    changed = sorted(key for key in config if key not in effective or config[key] != effective[key])
    if changed:
        raise ValueError("incumbent config differs from den_lighting's own: %s" % ", ".join(changed))
    return Incumbent(den, effective)


class Shadow:
    """den_lighting re-run in memory beside a logged run, so a comparison can ask what it wanted at a logged
    instant. feed() every logged event in log order: it hands the incumbent what the runner handed it (all but
    reference kinds and the outputs of the incumbent and the policies after it), so at the end of each instant
    its state is the logged run's.

    acted is, per bulb, the target den_lighting last acted on: Controller._emitted, which policy.py sets both
    when it sends a command and when it finds the bulb already there, kept when policy.py clears it."""

    def __init__(self, header):
        ids = [p["id"] for p in header["policies"]]
        self.policy = make_policy(next(p["config"] for p in header["policies"] if p["id"] == Incumbent.id))
        self.later = set(ids[ids.index(Incumbent.id):])
        self.acted = {}

    def feed(self, event):
        """The incumbent's outputs, or [] for an event it was never handed."""
        if event["source"] in self.later or event["kind"] in events.REFERENCE_KINDS:
            return []
        out = self.policy.handle(event)
        self.acted.update((e, asdict(scene)) for e, scene in self.policy.controller._emitted.items())
        return out

    def wants(self, entity_id, t):
        """Controller.desired() for the bulb at t, or while it has none (vacancy wait, unknown occupancy) the
        target it last acted on; None when it has neither."""
        scene = self.policy.controller.desired(entity_id, t)
        return asdict(scene) if scene is not None else self.acted.get(entity_id)
