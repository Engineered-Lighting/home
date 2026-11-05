#!/usr/bin/env python3
"""Probe: a copy of a take folder registers as a new session, so one recording sits in both splits.

What it checks
  Sessions are keyed by take folder name only. `label.py sessions` registers
  every folder under the takes root whose name is a valid take id and whose
  newest check is a retained PASS; it never compares take.json's own "take"
  field, started_unix, or frames.csv's sha256 with sessions already registered.
  A backup copy made inside the takes root (for example with PowerShell
  `Copy-Item C:\\den-takes\\<take> C:\\den-takes\\<take>b -Recurse`) carries its check
  files with it, so it is retained too. This probe registers hard-b #1
  (development), copies it to a new folder name, runs `sessions` again, and
  reports the copy's split and whether take.json/frames.csv are identical.
  Also shows the split follows registration order, not recording order: a
  hard-a take recorded first but checked later is assigned after a later one.

How to run
  python3 probe_copied-take-lands-in-both-splits.py <den-label checkout>
Synthetic takes built with R's own helpers in a temp dir.
"""
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path


def main():
    root = Path(sys.argv[1]).resolve()
    sys.path[:0] = [str(root / "tools/den-label"), str(root / "tools/den-take")]
    import n1
    import test_den_label as T
    tmp = Path(tempfile.mkdtemp(prefix="n1-attack-copy-")).resolve()
    takes = tmp / "takes"
    takes.mkdir()
    T.staged_take(takes, "b1", "hard-b", T.BASE_UNIX)
    ws = n1.Workspace(takes, takes / "n1")
    ws.scan()
    shutil.copytree(takes / "b1", takes / "b1b")  # a backup copy inside the takes root
    added, notes = ws.scan()
    splits = {e["session"]: e["split"] for e in ws.entries()}
    same = all(hashlib.sha256((takes / "b1" / f).read_bytes()).digest()
               == hashlib.sha256((takes / "b1b" / f).read_bytes()).digest() for f in ("take.json", "frames.csv"))
    inner = json.loads((takes / "b1b" / "take.json").read_text())["take"]
    print(f"RESULT splits {splits}; copy's take.json names {inner!r}; take.json and frames.csv identical: {same}")
    leak = splits.get("b1") == "development" and splits.get("b1b") == "held_out" and same
    # registration order vs recording order
    T.staged_take(takes, "a_late", "hard-a", T.BASE_UNIX + 2 * T.DAY)
    early = T.staged_take(takes, "a_early", "hard-a", T.BASE_UNIX + T.DAY, typed=None)  # not checked yet
    ws.scan()
    T.recheck(early, "none")  # checked a day later
    ws.scan()
    order = {e["session"]: (e["order"], e["split"], e["started_unix"]) for e in ws.entries() if e["script"] == "hard-a"}
    print("RESULT hard-a entries (order, split, started_unix):", order)
    misorder = order["a_early"][0] > order["a_late"][0]
    if misorder:
        print("INFO hard-a recorded first is assigned second: the alternation follows registration, "
              "not recording order (take.json's started_unix) as PROTOCOL-V1 states")
    if leak:
        print("FAIL the same recording is registered as development (b1) and held_out (b1b)")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
