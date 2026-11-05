#!/usr/bin/env python3
"""Probe: one truncated earlier check-N.json blocks a take for good, even after a clean newer PASS.

What it checks
  R's check.py writes check-N.json in place (open "x" then json.dump), so a
  crash or power cut mid-write leaves a truncated file, and check.py never
  deletes or edits an earlier one. n1.take_state() parses every check file and
  stops at the first that is not valid JSON, so the take stays unregistrable
  (or blocked, if registered) whatever newer checks say. This probe writes a
  truncated check-1.json, runs a clean check (check-2.json, PASS), and asks
  `label.py sessions` to register the take.

How to run
  python3 probe_truncated-earlier-check-blocks-take.py <den-label checkout>
Synthetic takes built with R's own helpers in a temp dir.
"""
import io
import json
import sys
import tempfile
from pathlib import Path


def main():
    root = Path(sys.argv[1]).resolve()
    sys.path[:0] = [str(root / "tools/den-label"), str(root / "tools/den-take")]
    import label
    import n1
    import test_den_label as T
    tmp = Path(tempfile.mkdtemp(prefix="n1-attack-truncated-")).resolve()
    takes = tmp / "takes"
    takes.mkdir()
    folder = T.staged_take(takes, "a1", "hard-a", T.BASE_UNIX)  # writes check-1.json (PASS)
    raw = (folder / "check-1.json").read_bytes()
    (folder / "check-1.json").write_bytes(raw[: len(raw) // 2])  # an interrupted write
    T.recheck(folder, "none")  # a clean check-2.json
    newest = json.loads((folder / "check-2.json").read_text())
    print(f"RESULT check-2.json verdict {newest['verdict']}, retained {newest['retained']}")
    out = io.StringIO()
    code = label.main(["sessions", "--takes", str(takes)], out=out)
    print(f"RESULT label.py sessions exit {code}: {out.getvalue().strip()!r}")
    state = n1.take_state(folder)
    if not state.ok and "check-1.json is not valid JSON" in state.reason:
        print(f"FAIL the take stays unregistrable after a clean newer PASS: {state.reason}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
