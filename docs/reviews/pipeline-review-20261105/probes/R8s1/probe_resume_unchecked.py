#!/usr/bin/env python3
"""probe_resume_unchecked: the resume check's result is recorded but never decides the outcome, so a bulb
that den_lighting failed to turn off after resume_automatic is left lit in the empty den while the tool
prints "Every bulb was restored and den_lighting resumed." (and, for a full session, outcome complete,
exit 0).

What it checks: Session.resume() returns {automatic, holds_left, bulbs_off, occupancy} after
resume_check_s; Session.finish() only looks at its occupancy (for the walk-through). Here den_lighting's
own turn_off for light.den_rear_left does not take effect after the resume (as a Zigbee command that
fails would leave it), and a Ctrl+C at step 3 makes the session restore and resume early.

How to run (synthetic data, fakes on 127.0.0.1 only, temp dirs only):
    python3 -B probe_resume_unchecked.py <root of the claude/rung-8-stage1 checkout>
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import test_stage1 as T  # noqa: E402

STUCK = "light.den_rear_left"


class _Case(unittest.TestCase):
    def runTest(self):
        pass


def main():
    case = _Case()
    rig = T.Rig(case)
    flag = {"resumed": False}
    real_call, real_apply = rig.ha.call, rig.ha._apply

    def call(domain, service, data):
        if domain == "den_lighting" and service == "resume_automatic":
            flag["resumed"] = True
        return real_call(domain, service, data)

    def apply(e, on, brightness=None, kelvin=None):
        if flag["resumed"] and e == STUCK and not on:
            return  # den_lighting's own turn_off does not take effect
        return real_apply(e, on, brightness, kelvin)
    rig.ha.call, rig.ha._apply = call, apply
    outcome, lines, s = rig.run(T.at_step(3, lambda sess: sess.on_ctrl_c(None, None)))
    record = json.loads((rig.folder / "session.json").read_text())
    print("RESULT outcome:", outcome)
    print("RESULT printed lines:", lines)
    print("RESULT session.json resume check:", record["resume"])
    print("RESULT", STUCK, "now:", {k: rig.ha.lights[STUCK][k] for k in ("on", "brightness", "kelvin")},
          "occupancy:", rig.ha.states["binary_sensor.den_oak_person_occupancy"]["state"])
    if rig.ha.lights[STUCK]["on"] and any("Every bulb was restored" in l for l in lines):
        print("FAIL: the resume check recorded bulbs_off false, yet the tool prints that every bulb was restored "
              "and den_lighting resumed; the bulb stays lit in the empty den")
    else:
        print("PASS")
    case.doCleanups()


if __name__ == "__main__":
    main()
