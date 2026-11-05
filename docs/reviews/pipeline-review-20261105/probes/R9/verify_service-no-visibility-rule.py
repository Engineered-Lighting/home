#!/usr/bin/env python3
"""Verify finding service-no-visibility-rule (rung 9, tools/den-core/score.py service()).

    python3 -B verify_service-no-visibility-rule.py <den-core-live checkout root>

Owner choice 8 (delivered-statistic, area-share: 'the share of valid pixels in R_eval at or above F(c) must reach
a_min'): 70% of the visible (valid) patch at the floor, and at least 50% of the patch valid to be scored. This calls
score.service() directly (imported from the checkout with bytecode writing off; nothing is written; synthetic data
only; no host) with a light model in lx and hand-made timelines:
 A. the whole mark falls while one bulb's level is unknown (as on the documented recorder-export path, which has
    no levels): every point predicts None;
 B. half the mark's span has the level unknown, half known and above the floor;
 C. a stub model that returns None per point (as rung 8 Stage 1's predict may): 2 of 4 points valid, both above
    the floor (50% valid: scored; 100% of the valid patch lit: served under owner choice 8);
 D. the stub with 1 of 4 points valid (25% < 50%: not scored under owner choice 8).
It prints score.service()'s result next to what owner choice 8 gives.
"""
import os
import sys


def main():
    root = os.path.abspath(sys.argv[1])
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.join(root, "tools", "den-core"))
    import json
    import light_model
    import score

    gate = json.loads(open(os.path.join(root, "tools", "den-core", "rung9-gate-v0.json"), encoding="utf-8").read())
    fixed = gate["fixed"]
    m = light_model.fake()
    m.update(version="t-lx", fake=False, basis_unit="lx")
    for bulb in m["basis"].values():
        for basis in bulb.values():
            for p in basis:
                basis[p] *= 600
    model = light_model.LightModel(m)
    L = light_model.LIGHTS
    OFF = {"on": False, "brightness": None, "kelvin": None}
    full = {"on": True, "brightness": 254, "kelvin": 2700}
    nolevel = {"on": True, "brightness": None, "kelvin": None}
    known = {L[0]: full, L[1]: full, L[2]: OFF, L[3]: OFF}
    unknown = {L[0]: full, L[1]: nolevel, L[2]: OFF, L[3]: OFF}
    print("INFO desk-1 predicted lx known=%s unknown=%s floor fine=%s" % (
        model.predict(known, ["desk-1"]), model.predict(unknown, ["desk-1"]), fixed["service_floors_lx"]["fine"]))

    mark = {"mark_id": "m", "track_id": "room", "start": 0.0, "end": 420.0, "band": "fine", "points": ["desk-1"]}
    a = score.service([(0.0, unknown, "mode")], [mark], model, fixed)
    print("A score.service=%s | owner choice 8: 0%% valid at every instant -> not scored (coverage n/a, not missed)" % a)
    b = score.service([(0.0, known, "mode"), (270.0, unknown, "mode")], [mark], model, fixed)
    print("B score.service=%s | owner choice 8: unknown half not scored -> coverage 1.0" % b)

    class Stub:
        basis_unit = "lx"

        def __init__(self, valid):
            self.valid = valid

        def predict(self, scenes, points):
            return {p: (400.0 if p in self.valid else None) for p in points}

    mark4 = dict(mark, points=["p1", "p2", "p3", "p4"])
    c = score.service([(0.0, known, "mode")], [mark4], Stub({"p1", "p2"}), fixed)
    print("C score.service=%s | owner choice 8: 50%% valid -> scored; 2/2 valid lit >= 70%% -> served, coverage 1.0" % c)
    d = score.service([(0.0, known, "mode")], [mark4], Stub({"p1"}), fixed)
    print("D score.service=%s | owner choice 8: 25%% valid < 50%% -> not scored" % d)
    ok = (a["coverage"] == 0.0 and a["missed_service"] == 1 and b["coverage"] < 1.0 and c["coverage"] == 0.0
          and d["missed_service"] == 1)
    print("RESULT %s: unpredictable (invalid) points/instants count as unserved; no 50%%-valid rule"
          % ("CONFIRMED" if ok else "NOT REPRODUCED"))


if __name__ == "__main__":
    main()
