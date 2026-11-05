#!/usr/bin/env python3
"""verify_resume-check-ignored-in-outcome: Session.finish() reads only the resume check's occupancy, so
a resume that did not take still prints "Every bulb was restored and den_lighting resumed."

Variant here: HA accepts the tool's switch.turn_on (200) but den_lighting's automatic stays off (the
switch did not change), so after resume_check_s the check holds automatic False and the restored bulbs
are still lit and held. Ctrl+C at step 3 makes the session restore and resume early.
Fake HA and fake camera on 127.0.0.1, temp dirs only.

    python3 -B verify_resume-check-ignored-in-outcome.py <root of the claude/rung-8-stage1 checkout>
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import guard  # noqa: E402
import synthetic  # noqa: E402
import test_stage1 as T  # noqa: E402


class _Case(unittest.TestCase):
    def runTest(self):
        pass


case = _Case()
rig = T.Rig(case)
real_call = rig.ha.call


def call(domain, service, data):
    if domain == "switch" and service == "turn_on":
        with rig.ha.lock:
            rig.ha.calls.append((rig.ha.now(), domain, service, dict(data), "x", "rest"))
        return []  # 200, but the switch does not turn on
    return real_call(domain, service, data)


rig.ha.call = call
outcome, lines, s = rig.run(T.at_step(3, lambda sess: sess.on_ctrl_c(None, None)))
record = json.loads((rig.folder / "session.json").read_text())
lit = [e for e in guard.LIGHTS if rig.ha.lights[e]["on"]]
print("RESULT outcome:", outcome, "lines:", lines)
print("RESULT session.json resume:", record["resume"])
print("RESULT switch:", rig.ha.states[synthetic.SWITCH]["state"], "bulbs lit:", lit,
      "holds:", sorted(rig.ha.store()["data"]["holds"]))
if record["resume"]["automatic"] is not True and any("den_lighting resumed" in l for l in lines):
    print("FAIL: automatic is not on after the resume, yet the tool prints that den_lighting resumed")
else:
    print("PASS")
case.doCleanups()
