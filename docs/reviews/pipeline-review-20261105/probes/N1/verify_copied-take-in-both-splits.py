#!/usr/bin/env python3
"""verify_copied-take-in-both-splits: does a copy of a take under another folder name register as a
second session in the other split, with take.json written as take.py writes it ("take" = the original
folder name)?

Cases (each in its own fresh takes folder):
  1. copy named a1-backup (Copy-Item with an explicit name): registered? which split?
  2. copy named "a1 - Copy" (the File Explorer default for a copy in the same folder): registered?
  3. a1 registered and labelled first, the copy appears at a later `sessions` run: registered then?
Synthetic takes from the branch's own test helpers; temp dirs only; nothing leaves 127.0.0.1.
Run:
    python3 verify_copied-take-in-both-splits.py /path/to/den-label-checkout [/scratch/dir]
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
SCRATCH = sys.argv[2] if len(sys.argv) > 2 else None
sys.path.insert(0, str(ROOT / "tools" / "den-label"))
sys.path.insert(0, str(ROOT / "tools" / "den-take"))
import test_den_label as T  # noqa: E402
import n1  # noqa: E402
import take as den_take  # noqa: E402


def make(takes):
    folder = T.staged_take(takes, "a1", "hard-a", T.BASE_UNIX)
    record = json.loads((folder / "take.json").read_text(encoding="utf-8"))
    record["take"] = "a1"  # what take.py writes: the folder name at recording time
    den_take.write_json(folder / "take.json", record)
    return folder


def main():
    T.setUpModule()
    tmp = Path(tempfile.mkdtemp(prefix="n1-verify-copy-", dir=SCRATCH)).resolve()
    bad = 0
    try:
        for case, copy_name, late in ((1, "a1-backup", False), (2, "a1 - Copy", False), (3, "a1.bak", True)):
            takes = tmp / f"case{case}"
            takes.mkdir()
            make(takes)
            ws = n1.Workspace(takes, takes / "n1")
            first = []
            if late:
                first, _ = ws.scan()
            shutil.copytree(takes / "a1", takes / copy_name)
            added, notes = ws.scan()
            rows = [(e["session"], e["split"]) for e in first + added]
            take_field = json.loads((takes / copy_name / "take.json").read_text())["take"]
            print(f"RESULT case {case} copy {copy_name!r} (take.json take={take_field!r}): registered {rows} notes {notes}")
            if len(rows) == 2 and {s for _, s in rows} == {"development", "held_out"}:
                bad += 1
                print(f"FAIL case {case}: the same recording is in development and held-out")
            else:
                print(f"PASS case {case}: the copy is not a second session")
        print(f"RESULT {bad} of 3 copy cases put one recording in both splits")
    finally:
        T.tearDownModule()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
