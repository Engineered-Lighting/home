#!/usr/bin/env python3
"""Probe: `label.py status` prints a held-out session's label contents (case and frame range).

What it checks
  The plan and PROTOCOL-V1 say `status` shows held-out counts, never held-out
  contents, and no times; agents read only `status` output and freeze lines.
  But every command (status included) first applies a newer accepted check to
  the drafts and prints each clipped interval as
  "<take> labels: interval <case> at frames <a> to <b>: ...".
  This probe registers hard-a #1 (development) and hard-a #2 (held_out), labels
  the held-out one, accepts a newer check with a new second-person cut inside
  one of its intervals, runs `label.py status`, and looks for that line.

How to run
  python3 probe_status-prints-held-out-label-contents.py <den-label checkout>
Synthetic takes built with R's own helpers in a temp dir; nothing else is touched.
"""
import io
import sys
import tempfile
from pathlib import Path


def main():
    root = Path(sys.argv[1]).resolve()
    sys.path[:0] = [str(root / "tools/den-label"), str(root / "tools/den-take")]
    import label
    import n1
    import test_den_label as T
    tmp = Path(tempfile.mkdtemp(prefix="n1-attack-status-")).resolve()
    takes = tmp / "takes"
    takes.mkdir()
    T.staged_take(takes, "a1", "hard-a", T.BASE_UNIX)
    held = T.staged_take(takes, "a2", "hard-a", T.BASE_UNIX + T.DAY)
    ws = n1.Workspace(takes, takes / "n1")
    ws.scan()
    print("RESULT splits:", {e["session"]: e["split"] for e in ws.entries()})
    base = T.Base("run")
    base.takes, base.n1root = takes, takes / "n1"
    base.boxes_done(ws, "a2")
    base.labels_done(ws, "a2", intervals=[base.interval(ws, "a2", 65, 75, "fine", "still_reader")], spans=[])
    T.recheck(held, "01:10")  # the owner re-ran check.py for a second person seen at 01:10
    out = io.StringIO()
    code = label.main(["status", "--takes", str(takes)], out=out)
    text = out.getvalue()
    print(f"RESULT label.py status exit {code}; output:")
    for line in text.splitlines():
        print("   |", line)
    leaked = [l for l in text.splitlines() if l.startswith("a2 labels: interval")]
    if ws.entry("a2")["split"] == "held_out" and leaked:
        print(f"FAIL status printed held-out label contents: {leaked[0]!r}")
        return 1
    print("PASS status printed counts only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
