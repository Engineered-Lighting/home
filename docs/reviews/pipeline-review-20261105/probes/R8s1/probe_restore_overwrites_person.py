#!/usr/bin/env python3
"""probe_restore_overwrites_person: the restore's second attempt rewrites a bulb that a person set by
hand during the restore, although the README promises "A bulb someone changed is never written again"
(A12: no restore overwrites a light a person changed).

What it checks: Session.restore() writes the snapshot, sleeps hold_check_wait_s, reads den_lighting's
holds and rewrites every bulb whose hold does not equal the snapshot. It never runs guard.classify or
any own/foreign check during the restore, so a person's manual change (which den_lighting records as a
hold) looks like a failed restore and is overwritten.

Scenario: a person walks in at step 4 (occupancy abort), the tool restores, and during the ~5 s hold
check wait the person sets light.den_rear_right by hand (37 / 3100 K).

How to run (synthetic data, fakes on 127.0.0.1 only, temp dirs only):
    python3 -B probe_restore_overwrites_person.py <root of the claude/rung-8-stage1 checkout>
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import guard  # noqa: E402
import test_stage1 as T  # noqa: E402

PERSON_BULB = "light.den_rear_right"
PERSON = {"on": True, "brightness": 37, "kelvin": 3100}


class _Case(unittest.TestCase):
    def runTest(self):
        pass


def main():
    case = _Case()
    rig = T.Rig(case)
    s = rig.session()
    state = {"restore_writes": 0, "person_at": None}
    real_send, real_sleep = s.restore_send, s.sleep

    def counting_send(e, target):
        state["restore_writes"] += 1
        return real_send(e, target)

    def sleep(dt):
        if state["restore_writes"] and state["person_at"] is None and dt == rig.m1["hold_check_wait_s"]:
            state["person_at"] = rig.ha.now()
            rig.ha.set_light(PERSON_BULB, on=True, brightness=PERSON["brightness"], kelvin=PERSON["kelvin"])
        return real_sleep(dt)
    s.restore_send, s.sleep = counting_send, sleep
    outcome, lines, s = rig.run(T.at_step(4, lambda _s: rig.ha.set_occupancy("on")), session=s)
    after = [c for c in rig.ha.calls if c[5] == "rest" and c[0] >= (state["person_at"] or 0)
             and c[1] == "light" and c[3].get("entity_id") == PERSON_BULB]
    record = json.loads((rig.folder / "session.json").read_text())
    final = rig.ha.lights[PERSON_BULB]
    print("RESULT outcome:", outcome, lines)
    print("RESULT person set", PERSON_BULB, "to", PERSON, "at HA time", state["person_at"])
    print("RESULT tool REST writes to that bulb after the person's change:", [(c[2], c[3]) for c in after])
    print("RESULT hold checks:", [(l["attempt"], l["failures"]) for l in rig.log() if l["kind"] == "hold_check"])
    print("RESULT session.json restore:", record["restore"], "unrestored:", record["unrestored"])
    print("RESULT bulb now:", {k: final[k] for k in ("on", "brightness", "kelvin")})
    if state["person_at"] is None:
        print("RESULT the hook never ran (no restore sleep seen); inconclusive")
    elif after:
        print("FAIL: the restore wrote", PERSON_BULB, "again after a person changed it by hand; it was not "
              "listed as unrestored, and the person's setting is gone")
    else:
        print("PASS: the person's bulb was not written again")
    case.doCleanups()


if __name__ == "__main__":
    main()
