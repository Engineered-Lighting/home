#!/usr/bin/env python3
"""Spec-lens verification of finding service-no-visibility-rule (rung 9, tools/den-core/score.py service()).

    python3 -B verify_spec_service-no-visibility-rule.py <den-core-live checkout root>

owner-choices.md 8: "area share: 70% of the visible patch at the floor level; at least 50% of the patch visible to
be scored". The gate's service_note cites owner choice 8 but service() divides by every point of the mark and
counts an unpredictable point (None) as below the floor, so an instant whose prediction is unknown is scored as
unserved instead of not scored. surrogates() in the same file excludes unknown episodes instead.
This probe imports score.py from the checkout read-only (bytecode off) and calls service() on hand-made, synthetic
timelines with an lx-unit copy of the fake light model:
  A: the mark's whole span has a lit bulb with no level (den_lighting's last-level ON, or the HA export's levels)
  B: first half unknown, second half the reading look (above the fine floor)
  C: the whole span the reading look (control)
Prints RESULT lines.
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True


def main():
    core = Path(sys.argv[1]).resolve() / "tools" / "den-core"
    sys.path.insert(0, str(core))
    import light_model  # noqa: E402
    import score  # noqa: E402
    m = light_model.fake()
    m.update(version="probe-lx", fake=False, basis_unit="lx")
    for bulb in m["basis"].values():
        for basis in bulb.values():
            for p in basis:
                basis[p] *= 300
    model = light_model.LightModel(m)
    L = light_model.LIGHTS
    off = {"on": False, "brightness": None, "kelvin": None}
    nolevel = {"on": True, "brightness": None, "kelvin": None}
    look = {"on": True, "brightness": 255, "kelvin": 2700}
    unknown = {L[0]: nolevel, L[1]: off, L[2]: off, L[3]: off}
    lit = {L[0]: look, L[1]: look, L[2]: off, L[3]: off}
    fixed = {"service_floors_lx": {"fine": 300, "casual": 200}, "quiet_window_s": 120, "delivered_area_share": 0.7}
    mark = {"mark_id": "m1", "track_id": "room", "start": 0.0, "end": 720.0, "band": "fine", "points": ["desk-1"]}
    print("RESULT predicted desk-1 lx: unknown scene %s, reading look %s"
          % (model.predict(unknown, ["desk-1"]), model.predict(lit, ["desk-1"])))
    for name, timeline in (("A all-unknown", [(0.0, unknown, "mode")]),
                           ("B half-unknown", [(0.0, unknown, "mode"), (420.0, lit, "need")]),
                           ("C all-lit", [(0.0, lit, "need")])):
        print("RESULT %-15s service=%s" % (name, score.service(timeline, [mark], model, fixed)))
    a = score.service([(0.0, unknown, "mode")], [mark], model, fixed)
    print("RESULT %s: an unpredictable mark is reported as missed service (coverage %s, missed %d of %d) rather than "
          "not scored" % ("REPRODUCED" if a["missed_service"] == 1 and a["coverage"] == 0.0 else "NOT REPRODUCED",
                          a["coverage"], a["missed_service"], a["marks"]))


if __name__ == "__main__":
    main()
