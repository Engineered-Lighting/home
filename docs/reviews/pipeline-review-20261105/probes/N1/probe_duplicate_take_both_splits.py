#!/usr/bin/env python3
"""probe_duplicate_take_both_splits: can one recording be registered twice, once per split?

`label.py sessions` registers every folder under the takes root whose name matches TAKE_ID and
whose newest check is a retained PASS. It never compares take.json's own "take" field (the folder
name take.py wrote at recording time) or check-N.json's "take" field with the folder name, and
never de-duplicates by frames.csv sha256 or started_unix. A copy of a take saved under another
valid name (for example a backup made with Copy-Item ... a1-backup) therefore becomes a second
session; with the same started_unix it is the next in the same script's alternation, so it lands
in the other split: the same footage in development and held-out.

Synthetic takes from the branch's own test helpers; temp dirs only; nothing leaves 127.0.0.1.
Run:
    python3 probe_duplicate_take_both_splits.py /path/to/den-label-checkout
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-label"))
sys.path.insert(0, str(ROOT / "tools" / "den-take"))
import test_den_label as T  # noqa: E402
import n1  # noqa: E402


def main():
    T.setUpModule()
    tmp = Path(tempfile.mkdtemp(prefix="n1-probe-dup-")).resolve()
    try:
        takes = tmp / "takes"
        takes.mkdir()
        T.staged_take(takes, "a1", "hard-a", T.BASE_UNIX)
        shutil.copytree(takes / "a1", takes / "a1-backup")  # the same recording, another valid folder name
        ws = n1.Workspace(takes, takes / "n1")
        added, notes = ws.scan()
        rows = [(e["session"], e["split"]) for e in added]
        print("RESULT registered:", rows, notes)
        take_field = json.loads((takes / "a1-backup" / "take.json").read_text())["take"]
        same_frames = n1.file_sha256(takes / "a1" / "frames.csv") == n1.file_sha256(takes / "a1-backup" / "frames.csv")
        print(f"RESULT a1-backup/take.json says take={take_field!r}; frames.csv identical to a1: {same_frames}")
        splits = {s for _, s in rows}
        both = len(rows) == 2 and splits == {"development", "held_out"}
        print(("FAIL " if both else "PASS ") + "one recording is never in both development and held-out")
    finally:
        T.tearDownModule()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
