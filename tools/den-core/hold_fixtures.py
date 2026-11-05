"""Rung 9's deterministic hold fixtures, cases V6-V8 and V62-V64, built under each compensation option.

    python -B tools/den-core/hold_fixtures.py

prints what the coordinator does in each case under each option. **Nothing here is admitted as pass or fail**: the
ladder says the expected results depend on the compensation option, so they wait for the owner's decision
(`compensation` in docs/architecture/live-v1/decisions.json). test_hold_fixtures.py checks that each named option does
what its register entry says; that is a test of the code, not an admission, and nothing here counts toward any gate.

Every run is synthetic: the real den_lighting (incumbent.py) beside the coordinator, on the fake light model, with this
module's own slots, so a fixture's result never moves when the owner closes a slot in the gate file.

    V6   a dim held                           A reads at the counter and dims the bulb that serves it most
    V7   a brighten held                      the same, turned up
    V8   a hold between solve and send        a hold lands inside the send latency of a plan already solved
    V62  a shared bulb dimmed                 A dims it; B, reading in the armchair, still needs its light
    V63  gaming entry and exit under a hold   with game-time vacancy and return, a dim and a brighten
    V64  overlapping holds                    a dim, a brighten on another bulb, then a second touch on the first
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import coordinator  # noqa: E402
import events  # noqa: E402
import incumbent  # noqa: E402
import runner  # noqa: E402

T0 = 1793764800.0
FL, FR, RL, RR = events.LIGHTS
NOT_ADMITTED = "not admitted as pass or fail until the owner decides compensation (docs/architecture/live-v1/decisions.json)"
SLOTS = {"anticipation_floor": 0.8, "stability_floor": 0.9, "surrogate_definitions": "need-raise-v0",
         "anticipation_window_s": 10, "anticipation_rise_min": 0.05, "stability_dwell_s": 120,
         "look_brightness": 200, "look_kelvin": 2700, "look_fade_s": 2,
         "look_weights": {"shortfall": 1.0, "excess": 0.5, "change": 0.001}, "solve_steps": 9,
         "glare_limit": 1.0, "glare_sd_k": 2, "exposed_fraction": 0.1, "s_serve": 0.25, "met_tolerance": 0.05,
         "compensation_option": "P1", "compensation_form": "mode", "p3_fallback": "P1", "lambda_h": 1.0,
         "footprint_threshold": 0.05, "footprint_membership": "at_or_above", "local_completion": "not-allowed",
         "manual_vs_safety": "S0", "gaming": "non-gaming-stratified", "record_extensions": "coordinator-ext/v1"}
# Each named option: P0, then P1 and P2 strict and mode, and P3 in both forms with both fallbacks.
OPTIONS = {
    "P0": {"option": "P0"},
    "P1-strict": {"option": "P1", "form": "strict"}, "P1-mode": {"option": "P1", "form": "mode"},
    "P2-strict": {"option": "P2", "form": "strict"}, "P2-mode": {"option": "P2", "form": "mode"},
    "P3-strict/P1": {"option": "P3", "form": "strict", "p3_fallback": "P1"},
    "P3-mode/P1": {"option": "P3", "form": "mode", "p3_fallback": "P1"},
    "P3-strict/P0": {"option": "P3", "form": "strict", "p3_fallback": "P0"},
    "P3-mode/P0": {"option": "P3", "form": "mode", "p3_fallback": "P0"},
}


def person_state(t, track, state, surface):
    active = state != "none"
    return {"schema_version": "person_state/v1", "state_id": "%s@%.3f" % (track, t), "emitted_ts": t, "track_id": track,
            "identity": "unbound", "working_region": {"surface_id": surface},
            "focused_engagement": {"state": state, "since_s": 0.0 if active else None, "onset_ts": t if active else None,
                                   "confidence": None, "kind": "reading"},
            "visual_demand": {"level": None, "basis": None, "confidence": None}, "posture": "sitting",
            "field_support": {"focused_engagement": {"last_support_capture_ts": t - 1.0, "value_set_ts": t,
                                                     "support_status": "observed"}},
            "sources": {"focused_engagement": []}, "interpreter": {"path": "rules", "version": "scripted"}, "flags": []}


class ScriptedNeed:
    """A fixture's PersonStates, in need's place at the head of the policies."""
    id = "need"
    version = "scripted-need"
    code_path = __file__

    def __init__(self, config):
        self.config = config
        self.script = sorted(config["script"], key=lambda row: row[0])

    def handle(self, event):
        due = []
        while self.script and self.script[0][0] <= event["t"]:
            at, track, state, surface = self.script.pop(0)
            due.append({"kind": "person_state", "data": person_state(at, track, state, surface)})
        return due


def make_policy(config=None):
    return ScriptedNeed(config or {"script": []})


def scene(brightness, kelvin=2700):
    return {"on": True, "brightness": brightness, "kelvin": kelvin}


def touch(dt, entity_id, after, who):
    data = {"correction_id": "touch-%d" % dt, "entity_id": entity_id, "after": after, "source": "fixture"}
    if who:
        data["who"] = {"track_id": who}
    return (T0 + dt, "correction", data)


def _game(start, stop, step, status="playing"):
    return [(T0 + dt, "gaming_sample", {"status": status, "sampled_at": T0 + dt - 0.5}) for dt in range(start, stop, step)]


def _occupancy(dt, value):
    return (T0 + dt, "occupancy", {"value": value, "via": "fixture"})


def _v63(after):
    """A hold, a game, 130 s of game-time vacancy and the return, then the game's end."""
    return {"script": [(T0 + 6, "A", "none", "counter-1")],
            "touches": [(20, FL, after)],
            "inputs": (_game(30, 300, 10) + _game(300, 340, 10, "idle")
                       + [_occupancy(100, "off"), _occupancy(230, "on"), _occupancy(350, "on")]),
            "gaming": "complete-policy"}


CASES = {
    "V6": {"script": [(T0 + 10, "A", "engaged", "counter-1")], "touches": [(30, FL, scene(80))], "inputs": [_occupancy(60, "on")]},
    "V7": {"script": [(T0 + 10, "A", "engaged", "counter-1")], "touches": [(30, FL, scene(254))], "inputs": [_occupancy(60, "on")]},
    "V8": {"script": [(T0 + 10, "A", "engaged", "counter-1")], "touches": [(11, FL, scene(80))], "inputs": [_occupancy(30, "on")],
           "send_latency_s": 2},
    "V62": {"script": [(T0 + 8, "A", "none", "counter-1"), (T0 + 10, "B", "engaged", "armchair-book")],
            "touches": [(30, FL, scene(80))], "inputs": [_occupancy(60, "on")]},
    "V63-dim": _v63(scene(80)),
    "V63-brighten": _v63(scene(254)),
    "V64": {"script": [(T0 + 10, "A", "engaged", "counter-1")],
            "touches": [(30, FL, scene(80)), (40, RR, scene(254)), (50, FL, scene(150))], "inputs": [_occupancy(70, "on")]},
}


def run_case(case, option, local_completion="not-allowed", gaming=None, changer=True, light_model=None):
    """(header, log) for one case under one named option. changer=False leaves the changer unknown, for P3's fallback.
    light_model replaces the fake's small default, in a config's form, so a test can run a case on the full grid."""
    spec = CASES[case]
    inputs = [(T0, "light_observed", {"entity_id": e, **scene(180), "context": "unknown"}) for e in events.LIGHTS]
    inputs += [_occupancy(1, "on")] + list(spec["inputs"])
    inputs += [touch(dt, entity_id, after, "A" if changer else None) for dt, entity_id, after in spec["touches"]]
    config = {"gate": {"id": "hold-fixtures", "sha256": None, "slots": SLOTS, "open": []},
              "compensation": {**OPTIONS[option], "status": "named option; not admitted"},
              "local_completion": local_completion, "gaming": gaming or spec.get("gaming", "non-gaming-stratified"),
              "send_latency_s": spec.get("send_latency_s", 0)}
    if light_model:
        config["light_model"] = light_model
    policies = [ScriptedNeed({"script": spec["script"]}), incumbent.make_policy(), coordinator.make_policy(config)]
    return runner.run(runner.tagged(sorted(inputs, key=lambda item: item[0]), "synthetic"), policies, synthetic=True, tail_s=10.0,
                      sources=[{"name": "hold-fixture:%s" % case, "params": {"option": option, "changer": changer}}])


def decisions(log):
    return [e["data"] for e in log if e["kind"] == "decision" and e["source"] == "decide"]


def _scene_text(requested):
    return " ".join("%s=%s" % (b.replace("light.", "").replace("den_", ""), s["brightness"] if s["on"] else "off")
                    for b, s in sorted(requested.items(), key=lambda item: events.LIGHTS.index(item[0])))


def report_lines():
    lines = ["hold fixtures: " + NOT_ADMITTED, "synthetic: plumbing only, never a gate"]
    runs = [(case, option, {}) for case in CASES for option in OPTIONS]
    runs += [("V8", option, {"local_completion": "allowed"}) for option in OPTIONS]
    runs += [("V63-dim", "P0", {"gaming": "non-gaming-stratified"})]
    for case, option, kwargs in runs:
        _, log = run_case(case, option, **kwargs)
        out = decisions(log)
        last = out[-1]
        limited = [row["point_id"] for row in last.get("hold_limited", [])]
        note = " ".join("%s=%s" % pair for pair in sorted(kwargs.items()))
        lines.append("%-12s %-12s %s  %d decision(s); last: %s; predicted %s; hold_limited %s; status %s" % (
            case, option, note, len(out), _scene_text(last["requested_scene"]),
            {k: v for k, v in last["predicted"]["surfaces"].items() if k in ("counter-1", "armchair-book")}, limited or "none",
            last["status"]))
    return lines


if __name__ == "__main__":
    print("\n".join(report_lines()))
