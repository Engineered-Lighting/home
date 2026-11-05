#!/usr/bin/env python3
"""tiebreak_R8s0_two_findings: tiebreak verifier probe for two R8s0 findings.

Checks, from the pinned files only (no host, no network, nothing written outside a temp dir):

  verdict-omits-occupied-qualification
    1. ladder rung 8's gate holds the V_total / occupied hold-out clause and the rung is 'partial'.
    2. PLAN.md defers that clause (Context 'later step', 'Deliberately left out').
    3. PLAN.md's score.json caveat lines: absolute and day-to-day are there, occupied is not.
    4. Whether any downstream spec text (rung 9 / rung 10 gates, light_model schema) reads
       light_model.error's verdict or a V_total / visibility field (the failure path the finding names).

  stage0-bulb-change-counts-as-correction
    5. Scope of the probe rule (ladder rung 7): probes (non-human changes) only.
    6. The spec's Correction counting rule (rung 0, E1, rung 11): every human action that opened a hold.
    7. Stage 0 README step 5 discloses the normal-scene side effect.
    8. den-core's incumbent replay with and without correction-capture's --events line for an owner
       HA-app change (needs den-core's tools/den-core/incumbent.py and den_lighting policy.py).

Run:
  python3 -B tiebreak_R8s0_two_findings.py <live-records root> <plan-rung-8 root> <rung-8-stage0 root> <den-core root>

Prints PASS/FAIL/RESULT lines. PASS means the stated fact holds in the files.
"""
import sys
sys.dont_write_bytecode = True

import importlib.util
import json
from dataclasses import asdict
from pathlib import Path


def say(ok, text):
    print(("PASS " if ok else "FAIL ") + text)
    return ok


def main():
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    spec_root, plan_root, s0_root, core_root = (Path(a).resolve() for a in sys.argv[1:])
    live = spec_root / "docs/architecture/live-v1"
    ladder = json.loads((live / "ladder.json").read_text("utf-8"))
    rungs = {r["n"]: r for r in ladder["rungs"]}
    schemas = json.loads((live / "schemas.json").read_text("utf-8"))
    experiments = json.loads((live / "experiments.json").read_text("utf-8"))
    exps = experiments if isinstance(experiments, list) else experiments.get("experiments", [])
    plan = (plan_root / "docs/plans/rung-8-light-model/PLAN.md").read_text("utf-8")
    flat_plan = " ".join(plan.split())
    readme = (s0_root / "tools/den-light/README.md").read_text("utf-8")
    flat_readme = " ".join(readme.split())

    print("== verdict-omits-occupied-qualification")
    r8 = rungs[8]
    say("V_total" in r8["gate"] and "qualified on occupied hold-outs" in r8["gate"],
        "ladder rung 8 gate carries the V_total / occupied hold-out clause")
    say(r8["status"] == "partial", "ladder rung 8 status is %r (not built/passed)" % r8["status"])
    say("The occupied visibility term is a later step." in flat_plan,
        "PLAN.md Context defers the occupied visibility term ('a later step')")
    say("Occupied hold-outs and the visibility term V_total" in flat_plan,
        "PLAN.md 'Deliberately left out' lists occupied hold-outs and V_total")
    i = flat_plan.index("Nothing else enters the verdict.")
    caveats = flat_plan[i:i + 700]
    say("absolute: not run" in caveats and "day-to-day stability: not tested" in caveats,
        "PLAN.md score.json carries 'absolute: not run' and 'day-to-day stability: not tested'")
    say("occupied" not in caveats.lower() and "visibility" not in caveats.lower(),
        "PLAN.md score.json carries no occupied-visibility caveat line (the finding's factual claim)")
    lm = next(r for r in schemas["records"] if r["id"] == "light_model")
    err = next(f for f in lm["fields"] if f["name"] == "error")
    names = [f["name"] for f in lm["fields"]]
    print("RESULT light_model.error schema: type=%r meaning=%r" % (err["type"], err["meaning"]))
    say(not any("visib" in n.lower() or "v_total" in n.lower() for n in names),
        "light_model schema has no visibility / V_total field a consumer could misread: fields=%s" % names)
    downstream = {n: rungs[n]["gate"] + " " + rungs[n]["tested"] for n in (9, 10, 11) if n in rungs}
    for n, text in downstream.items():
        hits = [w for w in ("V_total", "visibility", "error.verdict", "occupied hold-out", "qualified model")
                if w in text]
        say(not hits, "rung %d gate/tested text reads no V_total, visibility or light-model verdict (hits=%s)"
            % (n, hits))
    say("Real-light use is not part of this gate" in rungs[9]["gate"],
        "rung 9 gate: replay only, real-light use comes at rung 11")

    print("== stage0-bulb-change-counts-as-correction")
    r7 = rungs[7]["tested"]
    say("probes must not look like people" in r7 and "every probe logged as a non-human change" in r7,
        "ladder rung 7 probe rule covers probes (the tool's non-human changes)")
    say("each human action that opened or replaced a hold as one Correction" in rungs[0]["tested"],
        "ladder rung 0 counts each human action that opened or replaced a hold as one Correction")
    e1 = next((e for e in exps if isinstance(e, dict) and e.get("id") == "E1"), {})
    say("Capture every human action on the real lights as one Correction" in e1.get("how", ""),
        "E1: capture every human action on the real lights as one Correction")
    say("human actions that opened or replaced a hold, one per Correction, as the numerator" in rungs[11]["gate"],
        "rung 11 CPH numerator: human actions that opened or replaced a hold (no carve-out for instructed ones)")
    corr = next(r for r in schemas["records"] if r["id"] == "correction")
    print("RESULT Correction purpose=%r producer=%r" % (corr["purpose"], corr["producer"]))
    say("den_lighting keeps that as its normal scene until it is changed again" in flat_readme,
        "Stage 0 README step 5 discloses the normal-scene side effect of the HA-app light-up")
    say("Nothing is paused and no light is written" in flat_readme,
        "Stage 0 README: nothing paused, no light written by the tool (so no tool probe to exclude)")

    # Replay fidelity: does the --events line for the owner's change keep den-core's incumbent with live?
    policy_path = core_root / "ha-config/custom_components/den_lighting/policy.py"
    spec = importlib.util.spec_from_file_location("den_lighting_policy_tb", policy_path)
    den = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = den
    spec.loader.exec_module(den)
    sys.path.insert(0, str(core_root / "tools/den-core"))
    import incumbent  # noqa: E402

    bulb = den.LIGHTS[1]
    usual = {"on": True, "brightness": 80, "kelvin": 2700}
    full = {"on": True, "brightness": 255, "kelvin": 2700}
    evs = [*[{"t": 0.0, "kind": "light_observed", "data": {"entity_id": e, **usual}} for e in den.LIGHTS],
           {"t": 1.0, "kind": "occupancy", "data": {"value": "off"}},
           {"t": 300.0, "kind": "occupancy", "data": {"value": "on"}},
           {"t": 360.0, "kind": "correction", "data": {"correction_id": "cc-360000", "entity_id": bulb,
                                                      "after": full, "source": "app"}},
           {"t": 420.0, "kind": "occupancy", "data": {"value": "off"}},
           {"t": 560.0, "kind": "tick", "data": {}},
           {"t": 86400.0, "kind": "occupancy", "data": {"value": "on"}}]

    def live_run():
        c = den.Controller()
        c.set_enabled(True, 0.0)
        last = {}
        for ev in evs:
            t, d = ev["t"], ev["data"]
            if ev["kind"] == "light_observed":
                c.observe(d["entity_id"], den.Scene(d["on"], d["brightness"], d["kelvin"]), t)
            elif ev["kind"] == "occupancy":
                c.set_occupancy(d["value"], t)
            elif ev["kind"] == "correction":
                c.manual(d["entity_id"], den.Scene(**d["after"]), t)
            for cmd in c.reconcile(t):
                last[cmd.entity_id] = asdict(cmd.target)
                c.observed[cmd.entity_id] = cmd.target
        return last

    def replay(events):
        inc = incumbent.Incumbent(den, {"manifest_version": "1.0.4"})
        last = {}
        for ev in events:
            for out in inc.handle(ev):
                if out["kind"] == "decision":
                    for e, scene in out["data"]["requested_scene"].items():
                        last[e] = scene
                        inc.controller.observed[e] = den.Scene(**scene)
        return last

    lv = live_run().get(bulb)
    with_line = replay(evs).get(bulb)
    without = replay([e for e in evs if e["kind"] != "correction"]).get(bulb)
    print("RESULT live next-arrival request %s; replay with --events line %s; replay without it %s"
          % (lv, with_line, without))
    say(with_line == lv, "replay WITH correction-capture's line for the owner's change matches live den_lighting")
    say(without != lv, "replay WITHOUT the line (the finding's 'exclude like a probe session' fix) diverges from live")


if __name__ == "__main__":
    main()
