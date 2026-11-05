#!/usr/bin/env python3
"""Probe: one registered session with no usable frames blocks both freezes, with no way out.

What it checks
  `label.py sessions` registers every retained staged take whose newest check
  is a PASS; it never looks at how much of the take is usable. split.json is
  append-only and refuses any edit, and both freezes require every registered
  session to have an audited boxes draft and an end-to-end labels draft.
  If a second person was in view for the whole activity, the owner types a span
  covering it at check.py's prompt (the README's own instruction), the check is
  still a PASS, and the session registers with zero usable frames. Its passes
  then refuse to open ("has no usable frames"), so no draft can ever exist, and
  `freeze fixture` and `freeze labels` refuse forever. There is no command to
  drop a session.

How to run
  python3 probe_unusable-session-blocks-every-freeze.py <den-label checkout>
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
    tmp = Path(tempfile.mkdtemp(prefix="n1-attack-unusable-")).resolve()
    takes = tmp / "takes"
    takes.mkdir()
    T.staged_take(takes, "a1", "hard-a", T.BASE_UNIX)
    T.staged_take(takes, "b1", "hard-b", T.BASE_UNIX + 3600, typed="00:00-09:59")  # B in view throughout
    report = json.loads((takes / "b1" / "check-1.json").read_text())
    print(f"RESULT b1 check-1: verdict {report['verdict']}, retained {report['retained']}, "
          f"usable minutes {report['usable']['minutes']}, segments with frames "
          f"{sum(1 for s in report['usable']['segments'] if s['frames'])}")
    ws = n1.Workspace(takes, takes / "n1")
    added, notes = ws.scan()
    print("RESULT registered:", [(e["session"], e["split"]) for e in added], "notes:", notes)
    base = T.Base("run")
    base.takes, base.n1root = takes, takes / "n1"
    base.surfaces_done(ws, "a1")
    base.boxes_done(ws, "a1")
    blocked = []
    for mode in ("boxes", "labels"):
        try:
            label.Pass(ws, mode, "b1", out=io.StringIO())
            print(f"RESULT {mode} pass on b1 opened")
        except n1.Refused as exc:
            print(f"RESULT {mode} pass on b1 refused: {exc}")
            blocked.append(mode)
    try:
        ws.freeze_fixture("")
        print("RESULT freeze fixture succeeded")
    except n1.Refused as exc:
        print(f"RESULT freeze fixture refused: {exc}")
        blocked.append("freeze")
    split = takes / "n1" / "split.json"
    data = json.loads(split.read_text())
    data["entries"] = [e for e in data["entries"] if e["session"] != "b1"]
    split.write_text(json.dumps(data))
    try:
        ws.entries()
        print("RESULT dropping b1 from split.json was accepted")
    except n1.Refused as exc:
        print(f"RESULT dropping b1 from split.json refused: {exc}")
        blocked.append("drop")
    commands = sorted(label.parser()._subparsers._group_actions[0].choices)
    print("RESULT label.py commands:", commands)
    if blocked == ["boxes", "labels", "freeze", "drop"]:
        print("FAIL a registered session with no usable frames can never be boxed, labelled or dropped, "
              "so no fixture or labels freeze is possible")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
