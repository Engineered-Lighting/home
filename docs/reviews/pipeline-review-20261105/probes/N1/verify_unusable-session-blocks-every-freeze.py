#!/usr/bin/env python3
"""Verify probe for finding unusable-session-blocks-every-freeze (den-label, N1).

What it checks
  1. A staged take whose typed second-person span covers the whole activity
     gets R's real check: PASS, retained, 0 usable minutes; `sessions`
     registers it; boxes and labels passes refuse; freeze fixture refuses.
  2. Whether a careful hand edit of split.json (drop the entry and rebuild
     the head hash) is accepted by load_split():
       a. when the zero-usable session is the LAST entry;
       b. when a later session of the same script was registered after it.
  So: is there any way out short of breaking split.json's own rules?

How to run
  python3 -B verify_unusable-session-blocks-every-freeze.py <den-label checkout>
Synthetic takes built with R's own helpers, in a temp dir. No network.
"""
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path


def drop_and_rehash(n1, split_path, session):
    data = json.loads(split_path.read_text())
    entries = [e for e in data["entries"] if e["session"] != session]
    prev = None
    for e in entries:  # rebuild the chain as an editor would
        e["prev"] = prev
        prev = n1.entry_hash(e)
    split_path.write_text(json.dumps({"schema": data["schema"], "entries": entries, "head": prev}))


def main():
    root = Path(sys.argv[1]).resolve()
    sys.path[:0] = [str(root / "tools/den-label"), str(root / "tools/den-take")]
    import label
    import n1
    import test_den_label as T
    T.setUpModule()
    tmp = Path(tempfile.mkdtemp(prefix="n1-verify-unusable-")).resolve()
    takes = tmp / "takes"
    takes.mkdir()
    T.staged_take(takes, "a1", "hard-a", T.BASE_UNIX)
    T.staged_take(takes, "b1", "hard-b", T.BASE_UNIX + 3600, typed="00:00-09:59")
    rep = json.loads((takes / "b1" / "check-1.json").read_text())
    print(f"RESULT b1 check-1 verdict {rep['verdict']} retained {rep['retained']} usable min {rep['usable']['minutes']}")
    ws = n1.Workspace(takes, takes / "n1")
    added, _ = ws.scan()
    print("RESULT registered:", [(e["session"], e["split"]) for e in added])
    refusals = []
    for mode in ("boxes", "labels"):
        try:
            label.Pass(ws, mode, "b1", out=io.StringIO())
        except n1.Refused as exc:
            refusals.append(f"{mode}: {exc}")
    print("RESULT pass refusals:", refusals)
    base = T.Base("run")
    base.takes, base.n1root = takes, takes / "n1"
    base.surfaces_done(ws, "a1")
    base.boxes_done(ws, "a1")
    try:
        ws.freeze_fixture("")
        print("RESULT freeze fixture succeeded")
        stuck = False
    except n1.Refused as exc:
        print(f"RESULT freeze fixture refused: {exc}")
        stuck = True
    split = takes / "n1" / "split.json"
    keep = split.read_bytes()
    drop_and_rehash(n1, split, "b1")
    try:
        print("RESULT 2a drop last entry + rehash accepted:", [e["session"] for e in ws.entries()])
    except n1.Refused as exc:
        print(f"RESULT 2a drop last entry + rehash refused: {exc}")
    split.write_bytes(keep)
    T.staged_take(takes, "b2", "hard-b", T.BASE_UNIX + T.DAY)
    ws.scan()
    print("RESULT after b2:", [(e["session"], e["order"], e["split"]) for e in ws.entries()])
    drop_and_rehash(n1, split, "b1")
    try:
        print("RESULT 2b drop non-last entry + rehash accepted:", [e["session"] for e in ws.entries()])
    except n1.Refused as exc:
        print(f"RESULT 2b drop non-last entry + rehash refused: {exc}")
    T.tearDownModule()
    shutil.rmtree(tmp, ignore_errors=True)
    if stuck and len(refusals) == 2:
        print("FAIL a registered zero-usable session blocks both freezes; no label.py command removes it")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
