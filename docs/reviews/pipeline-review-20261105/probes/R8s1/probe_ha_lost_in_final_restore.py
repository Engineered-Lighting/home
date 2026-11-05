#!/usr/bin/env python3
"""probe_ha_lost_in_final_restore: after a complete probe, HA lost during the restore raises probe.Abort out
of Session.run() (finish("complete") is outside run()'s try/except Abort), and probe.main() catches only
Refused, so the owner gets a Python traceback, exit 1 ("aborted or fail"), no "run --recover" line, and
session.json still says phase probed.

How to run (synthetic data, fakes on 127.0.0.1 only, temp dirs only):
    python3 -B probe_ha_lost_in_final_restore.py <root of the claude/rung-8-stage1 checkout>
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
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
        rig.ha.down = True  # HA stops answering just as the final restore starts
        return real(e, target)
    s.restore_send = send
    caught = None
    try:
        outcome, lines, s = rig.run(session=s)
        print("RESULT run returned:", outcome, lines)
    except BaseException as exc:  # what probe.main() would let through
        caught = exc
        print("RESULT run raised:", type(exc).__module__ + "." + type(exc).__name__, exc)
    record = json.loads((rig.folder / "session.json").read_text())
    print("RESULT session.json phase/outcome:", record["phase"], record["outcome"])
    rig.ha.down = False
    print("RESULT den_lighting automatic switch:", rig.ha.states[synthetic.SWITCH]["state"])
    handled = (probe.sweep.Refused, probe.model.Refused, probe.guard.Refused)
    if isinstance(caught, probe.Abort) and not isinstance(caught, handled):
        print("FAIL: probe.Abort escapes Session.run() after a complete probe; probe.main() does not catch it, so "
              "the owner sees a traceback instead of the 'run probe.py --recover' line, and session.json is "
              "not marked ha_lost")
    else:
        print("PASS")
    case.doCleanups()


if __name__ == "__main__":
    main()
