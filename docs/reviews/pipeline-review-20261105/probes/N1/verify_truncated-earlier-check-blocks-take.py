#!/usr/bin/env python3
"""Verify finding truncated-earlier-check-blocks-take (den-label, tools/den-label/n1.py take_state).

What it checks
  A. Unregistered take: R's check.py is made to fail part-way through writing check-1.json
     the way a full disk does (json.dump writes part of the report, then OSError ENOSPC),
     leaving a truncated file. A clean re-run writes check-2.json PASS. Does
     `label.py sessions` register the take?
  B. Registered take: check-1 PASS registers it; check-2's write is interrupted the same way;
     a clean check-3 PASS follows. Is the session blocked (status BLOCKED, passes refused)?
  C. Recovery (info only): with the truncated file moved out of the take folder, does the take
     register? (Shows the block lasts only while the owner keeps R's never-change-a-check rule.)

Synthetic takes from the repo's own helpers, temp folders only, no network, checkout untouched.

How to run
  python3 -B verify_truncated-earlier-check-blocks-take.py <den-label checkout>
"""
import errno
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock


def main():
    root = Path(sys.argv[1]).resolve()
    sys.path[:0] = [str(root / "tools/den-label"), str(root / "tools/den-take")]
    import check
    import label
    import n1
    import test_den_label as T

    real_dump = json.dump

    def disk_full_dump(obj, fp, **kw):
        text = json.dumps(obj, **kw)
        fp.write(text[: len(text) // 3])
        raise OSError(errno.ENOSPC, "No space left on device")

    def interrupted_check(folder):
        with mock.patch.object(check.json, "dump", disk_full_dump):
            try:
                T.recheck(folder, "none")
            except OSError as exc:
                return str(exc)
        return "no error"

    tmp = Path(tempfile.mkdtemp(prefix="verify-truncated-")).resolve()
    rc = 0
    # A
    takes = tmp / "A"
    takes.mkdir()
    folder = T.staged_take(takes, "a1", "hard-a", T.BASE_UNIX, typed=None)
    err = interrupted_check(folder)
    print(f"INFO A: interrupted check.py: {err}; files {sorted(p.name for p in folder.glob('check-*.json'))}")
    report = T.recheck(folder, "none")
    print(f"INFO A: clean re-run verdict {report['verdict']}, retained {report['retained']}")
    out = io.StringIO()
    code = label.main(["sessions", "--takes", str(takes)], out=out)
    print(f"RESULT A: label.py sessions exit {code}: {out.getvalue().strip()!r}")
    if "not valid JSON" in out.getvalue():
        print("FAIL A: a clean newer PASS does not register the take while check-1.json is truncated")
        rc = 1
    # C
    aside = tmp / "aside"
    aside.mkdir()
    shutil.move(str(folder / "check-1.json"), str(aside / "check-1.json"))
    out = io.StringIO()
    label.main(["sessions", "--takes", str(takes)], out=out)
    print(f"INFO C: after moving the truncated check-1.json out of the take: {out.getvalue().strip()!r}")
    # B
    takes = tmp / "B"
    takes.mkdir()
    folder = T.staged_take(takes, "b1", "hard-b", T.BASE_UNIX)  # check-1.json PASS
    out = io.StringIO()
    label.main(["sessions", "--takes", str(takes)], out=out)
    print(f"INFO B: first registration: {out.getvalue().strip()!r}")
    err = interrupted_check(folder)
    report = T.recheck(folder, "none")
    print(f"INFO B: interrupted check-2 ({err}); clean check-3 verdict {report['verdict']}")
    ws = n1.Workspace(takes, takes / "n1")
    state = n1.take_state(folder)
    status = "\n".join(ws.status())
    print(f"RESULT B: take_state ok={state.ok} reason={state.reason!r}")
    print("RESULT B: status row:", [l for l in status.splitlines() if l.strip().startswith("b1")])
    try:
        label.Pass(ws, "boxes", "b1", out=io.StringIO())
        print("INFO B: boxes pass opens")
    except n1.Refused as exc:
        print(f"RESULT B: boxes pass refused: {exc}")
    if not state.ok and "not valid JSON" in state.reason:
        print("FAIL B: a registered session stays blocked after a clean newer PASS")
        rc = 1
    shutil.rmtree(tmp, ignore_errors=True)
    print("RESULT defect reproduced" if rc else "PASS no defect")
    return rc


if __name__ == "__main__":
    sys.exit(main())
