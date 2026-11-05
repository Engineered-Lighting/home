#!/usr/bin/env python3
"""verify_restore-overwrites-person-change: deterministic re-run of the finding that Session.restore()
writes a bulb a person set by hand during the restore (README: "A bulb someone changed is never written
again"; ladder rung 8: "no ... restore overwrites a light a person changed (A12)").

The reporter's probe sets the bulb at the start of the hold-check sleep, which usually lands inside
den_lighting's 3 HA-second window after the tool's own write (the fake runs at speed 600, so 3 HA s is
about 5 ms real), so den_lighting attributes the change to the tool's call and the probe passes most
runs. This probe removes the race:

  variant A (attempt 2): the person sets light.den_rear_right at the END of the hold-check wait, i.e.
     well after den_lighting's 3 s window (the real slot proposes hold_check_wait_s = 5 s, window 3 s).
  variant B (attempt 1): the person sets light.den_rear_right just before the tool's first restore
     write (after the abort's last guard read). The tool writes rear_right later in attempt 1.

Both: occupancy abort at step 4 (A walks in). Fake HA and fake camera on 127.0.0.1, temp dirs only.

    python3 -B verify_restore-overwrites-person-change.py <root of the claude/rung-8-stage1 checkout>
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import test_stage1 as T  # noqa: E402

BULB = "light.den_rear_right"
PERSON = dict(on=True, brightness=37, kelvin=3100)


class _Case(unittest.TestCase):
    def runTest(self):
        pass


def run(variant):
    case = _Case()
    rig = T.Rig(case)
    s = rig.session()
    mark = {"t": None}
    real_sleep, real_send = s.sleep, s.restore_send

    def sleep(dt):
        real_sleep(dt)
        if variant == "A" and mark["t"] is None and dt == rig.m1["hold_check_wait_s"]:
            mark["t"] = rig.ha.now()
            rig.ha.set_light(BULB, **PERSON)  # after the wait: past den_lighting's 3 s window

    def send(e, target):
        if variant == "B" and mark["t"] is None:
            mark["t"] = rig.ha.now()
            rig.ha.set_light(BULB, **PERSON)  # after the abort's guard read, before any restore write
        return real_send(e, target)
    s.sleep, s.restore_send = sleep, send
    outcome, lines, s = rig.run(T.at_step(4, lambda _s: rig.ha.set_occupancy("on")), session=s)
    writes = [(c[2], c[3]) for c in rig.ha.calls if c[5] == "rest" and c[1] == "light"
              and c[3].get("entity_id") == BULB and mark["t"] is not None and c[0] >= mark["t"]]
    record = json.loads((rig.folder / "session.json").read_text())
    checks = [(l["attempt"], l["failures"]) for l in rig.log() if l["kind"] == "hold_check"]
    final = {k: rig.ha.lights[BULB][k] for k in ("on", "brightness", "kelvin")}
    print(f"RESULT [{variant}] outcome {outcome}; lines {lines}")
    print(f"RESULT [{variant}] tool writes to {BULB} after the person's change: {writes}")
    print(f"RESULT [{variant}] hold checks {checks}; unrestored {record['unrestored']}; bulb now {final}")
    bad = bool(writes) and record["unrestored"] == []
    print(("FAIL" if bad else "PASS") + f" [{variant}]: " + ("the person's bulb was written again and not listed"
                                                            if bad else "not reproduced"))
    case.doCleanups()
    return bad


if __name__ == "__main__":
    results = [run(v) for v in ("A", "A", "A", "B", "B")]
    print("RESULT summary (True = defect shown):", results)
