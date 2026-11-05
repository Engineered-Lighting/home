#!/usr/bin/env python3
"""probe_restore_overwrites_person_change_svc: verifier's deterministic variant of
probe_restore_overwrites_person.py (finding restore-overwrites-person-change).

What it checks: during the restore's hold-check wait (after an occupancy abort at step 4), person A
sets light.den_rear_right to 37 / 3100 K through a Home Assistant service call (what the HA app
sends). den_lighting's service_called() learns that as a hold and as normal, independent of its 3 s
report window, so the outcome does not depend on thread timing. The probe then reports whether
probe.py's restore writes that bulb again, what session.json says, and where the bulb and
den_lighting's normal end up.

Synthetic data and fakes on 127.0.0.1 only (the branch's own test Rig); temp dirs only.

How to run:
    python3 -B probe_restore_overwrites_person_change_svc.py <root of the claude/rung-8-stage1 checkout>
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import test_stage1 as T  # noqa: E402

BULB = "light.den_rear_right"
PERSON = {"entity_id": BULB, "transition": 0, "brightness": 37, "color_temp_kelvin": 3100}


class _Case(unittest.TestCase):
    def runTest(self):
        pass


def main():
    case = _Case()
    rig = T.Rig(case)
    s = rig.session()
    state = {"writes": 0, "person_index": None}
    real_send, real_sleep = s.restore_send, s.sleep

    def counting_send(e, target):
        state["writes"] += 1
        return real_send(e, target)

    def sleep(dt):
        if state["writes"] and state["person_index"] is None and dt == rig.m1["hold_check_wait_s"]:
            state["person_index"] = len(rig.ha.calls)
            rig.ha.call("light", "turn_on", dict(PERSON))  # A, from the HA app
        return real_sleep(dt)

    s.restore_send, s.sleep = counting_send, sleep
    outcome, lines, s = rig.run(T.at_step(4, lambda _s: rig.ha.set_occupancy("on")), session=s)
    i = state["person_index"]
    if i is None:
        print("RESULT inconclusive: the hook never ran")
        return
    later = [c for c in rig.ha.calls[i + 1:] if c[5] == "rest" and c[1] == "light"
             and c[3].get("entity_id") == BULB]
    record = json.loads((rig.folder / "session.json").read_text())
    light = rig.ha.lights[BULB]
    normal = rig.normal()[BULB]
    print("RESULT printed:", lines)
    print("RESULT hold checks:", [(l["attempt"], l["failures"]) for l in rig.log() if l["kind"] == "hold_check"])
    print("RESULT tool light writes to", BULB, "after A's change:", [(c[2], c[3]) for c in later])
    print("RESULT session.json unrestored:", record["unrestored"], "restore:", record["restore"])
    print("RESULT bulb now:", {k: light[k] for k in ("on", "brightness", "kelvin")}, "den_lighting normal:", normal)
    if later:
        print("FAIL: the restore wrote", BULB, "after A changed it by hand, and reported it restored")
    else:
        print("PASS: no restore write after A's change")
    case.doCleanups()


if __name__ == "__main__":
    main()
