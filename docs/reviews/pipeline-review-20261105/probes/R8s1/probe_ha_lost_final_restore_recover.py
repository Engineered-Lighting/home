#!/usr/bin/env python3
"""probe_ha_lost_final_restore_recover: verifier's follow-up to probe_ha_lost_in_final_restore.py
(finding ha-lost-final-restore-traceback).

What it checks: after a complete probe, HA stops answering at the first restore write, so probe.Abort
escapes Session.run() (the reported defect). It then brings HA back and runs Session.recover() on the
same folder, as README Stage 1 step 4 tells the owner to do when HA is lost, and reports whether the
session is finished: outcome, den_lighting's switch, holds, normal vs the snapshot, bulbs off.

Synthetic data and fakes on 127.0.0.1 only (the branch's own test Rig); temp dirs only.

How to run:
    python3 -B probe_ha_lost_final_restore_recover.py <root of the claude/rung-8-stage1 checkout>
"""
import json
import sys
import time
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
    s = rig.session()
    real = s.restore_send

    def send(e, target):
        rig.ha.down = True
        return real(e, target)
    s.restore_send = send
    try:
        rig.run(session=s)
        print("RESULT run returned normally (defect not present)")
    except probe.Abort as exc:
        print("RESULT run raised probe.Abort:", exc.reason)
    record = json.loads((rig.folder / "session.json").read_text())
    print("RESULT session.json phase/outcome after the escape:", record["phase"], record["outcome"])
    rig.ha.down = False
    time.sleep(0.5)
    r = rig.session()
    outcome, lines = r.recover()
    record = json.loads((rig.folder / "session.json").read_text())
    print("RESULT recover:", outcome, lines)
    print("RESULT session.json phase/outcome after recover:", record["phase"], record["outcome"],
          "unrestored:", record["unrestored"])
    print("RESULT switch:", rig.ha.states[synthetic.SWITCH]["state"], "holds:", sorted(rig.holds()))
    normal_ok = all(guard.hold_ok(rig.snapshot[e], rig.normal()[e]) for e in guard.LIGHTS)
    print("RESULT normal equals snapshot:", normal_ok,
          "bulbs on:", [e for e in guard.LIGHTS if rig.ha.lights[e]["on"]])
    if outcome == "complete" and record["phase"] == "done" and normal_ok:
        print("PASS (recoverable): --recover finishes the session from phase 'probed'")
    else:
        print("FAIL: --recover does not finish the session")
    case.doCleanups()


if __name__ == "__main__":
    main()
