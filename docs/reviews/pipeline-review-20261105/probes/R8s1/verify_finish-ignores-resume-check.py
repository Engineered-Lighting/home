#!/usr/bin/env python3
"""Verifier for finish-ignores-resume-check (R8s1, reproduce lens).

    python3 -B verify_finish-ignores-resume-check.py <branch-checkout-root> [stuck-abort|stuck-complete|automatic-abort]

Runs probe.Session against the suite's own fakes (synthetic.FakeHA running den_lighting, FakeCamera),
both on 127.0.0.1, in temp folders, and makes the resume check fail in one way:
  stuck-abort     den_lighting's own turn_off for light.den_rear_left does not take effect after
                  resume_automatic (a lost Zigbee command); Ctrl+C at step 3 ends the session early.
  stuck-complete  the same fault, full session to outcome 'complete' (slow: the whole schedule).
  automatic-abort the switch turn_on after the probe is accepted by HA but den_lighting stays disabled
                  (automatic stays false); Ctrl+C at step 3.
It prints the outcome, the lines probe.main would print (main prints each line, then
'Outcome: <outcome>', and exits 0 when the outcome is complete), and session.json's resume check.
FAIL if the resume check recorded a failure and the printed lines still say every bulb was restored
and den_lighting resumed.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
MODE = sys.argv[2] if len(sys.argv) > 2 else "stuck-abort"
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import test_stage1 as T  # noqa: E402
import synthetic  # noqa: E402

STUCK = "light.den_rear_left"


class _Case(unittest.TestCase):
    def runTest(self):
        pass


def main():
    case = _Case()
    rig = T.Rig(case)
    state = {"probe_done": False, "resumed": False}
    real_call, real_apply = rig.ha.call, rig.ha._apply

    def call(domain, service, data):
        if (domain, service) == ("den_lighting", "resume_automatic"):
            state["resumed"] = True
        if MODE == "automatic-abort" and (domain, service, data.get("entity_id")) == ("switch", "turn_on",
                                                                                        synthetic.SWITCH):
            rig.ha.calls.append((rig.ha.now(), domain, service, dict(data), "x", "rest"))
            return []  # accepted, but den_lighting stays disabled
        return real_call(domain, service, data)

    def apply(e, on, brightness=None, kelvin=None):
        if MODE.startswith("stuck") and state["resumed"] and e == STUCK and not on:
            return  # den_lighting's own turn_off is lost
        return real_apply(e, on, brightness, kelvin)

    rig.ha.call, rig.ha._apply = call, apply
    hook = None if MODE == "stuck-complete" else T.at_step(3, lambda sess: sess.on_ctrl_c(None, None))
    outcome, lines, s = rig.run(hook)
    record = json.loads((rig.folder / "session.json").read_text())
    print(f"RESULT mode={MODE} outcome={outcome}")
    print("RESULT probe.main would print:", lines + [f"Outcome: {outcome}. Wrote <folder>/session.json"],
          "exit", 0 if outcome == "complete" else 1)
    print("RESULT session.json resume check:", record["resume"])
    print("RESULT lights now:", {e: rig.ha.lights[e]["on"] for e in rig.ha.lights},
          "occupancy:", rig.ha.states["binary_sensor.den_oak_person_occupancy"]["state"],
          "switch:", rig.ha.states[synthetic.SWITCH]["state"])
    r = record["resume"] or {}
    failed = r.get("automatic") is not True or r.get("holds_left") or r.get("bulbs_off") is False
    if failed and any("Every bulb was restored and den_lighting resumed" in l for l in lines):
        print("FAIL the resume check failed, yet the printed lines say every bulb was restored and den_lighting "
              "resumed")
    else:
        print("PASS")
    case.doCleanups()


if __name__ == "__main__":
    main()
