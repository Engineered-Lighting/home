#!/usr/bin/env python3
"""finish-ignores-resume-check, clean path: a full probe session whose resume does not take
(HA answers the tool's switch.turn_on with 200 and no state change, so den_lighting stays paused).
Does probe.py still end 'complete' and print "Every bulb was restored and den_lighting resumed."?

    python3 -B probe_finish-ignores-resume-check.py <root of the claude/rung-8-stage1 checkout>

Uses the tool's own fakes (synthetic.FakeHA running den_lighting 1.0.4, synthetic.FakeCamera), both on
127.0.0.1, and test_stage1's Rig and fast METHOD numbers (resume_check_s 3). Temp dirs, synthetic data.
FAIL if the outcome is 'complete' with the 'resumed' line while session.json's resume check shows
automatic not True or bulbs still lit with occupancy off.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import guard  # noqa: E402
import probe  # noqa: E402
import synthetic  # noqa: E402
import test_stage1 as T  # noqa: E402


class _Case(unittest.TestCase):
    def runTest(self):
        pass


def main():
    case = _Case()
    rig = T.Rig(case)
    real_call = probe.HA.call

    def call(self_, domain, service, data):
        if (domain, service) == ("switch", "turn_on"):
            return []  # HA answered 200 with no changed states: the switch did not turn on
        return real_call(self_, domain, service, data)

    probe.HA.call = call
    try:
        outcome, lines, s = rig.run()
        record = json.loads((rig.folder / "session.json").read_text())
        lit = [e for e in guard.LIGHTS if rig.ha.lights[e]["on"]]
        switch = rig.ha.states[synthetic.SWITCH]["state"]
    finally:
        probe.HA.call = real_call
        case.doCleanups()
    print("RESULT outcome:", outcome)
    print("RESULT printed lines:", lines)
    print("RESULT session.json resume:", record["resume"])
    print("RESULT switch:", switch, "| bulbs lit at the end:", lit)
    resume = record["resume"] or {}
    bad = resume.get("automatic") is not True or (resume.get("occupancy") == "off" and not resume.get("bulbs_off"))
    if outcome == "complete" and bad and any("den_lighting resumed" in l for l in lines):
        print("FAIL 'complete' and 'den_lighting resumed' printed (exit 0) although the resume check failed")
        return 1
    print("PASS the failed resume check was reported")
    return 0


if __name__ == "__main__":
    sys.exit(main())
