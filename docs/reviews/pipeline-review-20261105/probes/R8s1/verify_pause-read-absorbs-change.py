#!/usr/bin/env python3
"""Verifier for pause-read-absorbs-change (R8s1, reproduce lens), arrival half.

    python3 -B verify_pause-read-absorbs-change.py <branch-checkout-root>

The person-change half is reproduced by the reporter's probe_restore_retry_overwrites_person.py in mode
'pause'. This one checks the arrival half: occupancy turns on right after probe.py turns den_lighting's
switch off (so the post-pause read already shows it). pause() checks only 'automatic'; does the tool still
send the first step's light writes (all four at full) before its first guard_read aborts?
Fakes from the suite (synthetic.FakeHA running den_lighting, FakeCamera) on 127.0.0.1, temp folders only.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import test_stage1 as T  # noqa: E402
import probe  # noqa: E402


class _Case(unittest.TestCase):
    def runTest(self):
        pass


def main():
    case = _Case()
    rig = T.Rig(case)
    marks = {}
    real_call = probe.HA.call

    def call(self_, domain, service, data):
        out = real_call(self_, domain, service, data)
        if (domain, service) == ("switch", "turn_off") and "t" not in marks:
            marks["t"] = rig.ha.now()
            rig.ha.set_occupancy("on")  # someone walks in, before the post-pause read
        return out

    probe.HA.call = call
    try:
        outcome, lines, s = rig.run()
    finally:
        probe.HA.call = real_call
    record = json.loads((rig.folder / "session.json").read_text())
    log = rig.log()
    abort_at = next((i for i, l in enumerate(log) if l["kind"] == "guard" and l.get("event") == "abort"), None)
    probe_writes = [l for l in log[:abort_at] if l["kind"] == "intent" and not l.get("restore")]
    print(f"RESULT outcome={outcome} abort_reason={record.get('abort_reason')!r}")
    print(f"RESULT probe light writes after the arrival and before the abort: "
          f"{[(l['entity_id'], l['data'].get('brightness'), l['data'].get('color_temp_kelvin')) for l in probe_writes]}")
    if probe_writes:
        print(f"FAIL {len(probe_writes)} probe write(s) went out after occupancy read on in the post-pause read")
    else:
        print("PASS no probe write after the arrival")
    case.doCleanups()


if __name__ == "__main__":
    main()
