#!/usr/bin/env python3
"""Verifier probe for finding partial-freeze-folder-breaks-verify: what recovery is left after a
half-written fixture-v1 folder?

What it checks
  Builds one synthetic staged take with den-label's own test helpers, injects a write failure on the
  second file of `freeze fixture` (as the reporter did), then simply re-runs `freeze fixture` the way
  an owner would after a failed command, and records:
    - whether the re-run succeeds and which version it writes (expected fixture-v2);
    - whether `verify` still crashes on the partial fixture-v1 afterwards;
    - whether drafts in work/ were lost (they should not be).

How to run
  python3 verify_partial-freeze-recovery.py <den-label checkout>
Synthetic data in a temp dir only; no network, no camera.
"""
import builtins
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock


def main():
    root = Path(sys.argv[1]).resolve()
    sys.path[:0] = [str(root / "tools/den-label"), str(root / "tools/den-take")]
    import n1
    import test_den_label as T
    tmp = Path(tempfile.mkdtemp(prefix="n1-verify-partial-")).resolve()
    takes = tmp / "takes"
    takes.mkdir()
    T.staged_take(takes, "a1", "hard-a", T.BASE_UNIX)
    ws = n1.Workspace(takes, takes / "n1")
    ws.scan()
    base = T.Base("run")
    base.takes, base.n1root = takes, takes / "n1"
    base.surfaces_done(ws, "a1")
    base.boxes_done(ws, "a1")
    real_open, calls = builtins.open, []

    def failing_open(path, mode="r", *a, **k):
        if mode == "xb":
            calls.append(path)
            if len(calls) == 2:
                raise OSError(28, "No space left on device")
        return real_open(path, mode, *a, **k)
    try:
        with mock.patch("builtins.open", failing_open):
            ws.freeze_fixture("")
    except OSError as exc:
        print(f"RESULT first freeze fixture raised {type(exc).__name__}: {exc}")
    drafts = sorted(p.relative_to(takes / "n1").as_posix() for p in (takes / "n1" / "work").rglob("*.json"))
    print("RESULT drafts still in work/:", drafts)
    try:
        line, _ = ws.freeze_fixture("")
        print("RESULT re-run freeze fixture OK:", line.split(" sha256=")[0])
    except Exception as exc:  # noqa: BLE001
        print(f"RESULT re-run freeze fixture failed: {type(exc).__name__}: {exc}")
    proc = subprocess.run([sys.executable, "-B", str(root / "tools/den-label/label.py"), "verify",
                           "--takes", str(takes)], capture_output=True, text=True, cwd=root)
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    print(f"RESULT verify after re-freeze exit {proc.returncode}; last line: {tail[-1] if tail else ''}")
    crashed = any(l.startswith("Traceback") for l in tail)
    print(("FAIL" if crashed else "PASS") + " verify " + ("still crashes" if crashed else "reports")
          + " after the owner re-freezes; drafts lost: " + str(not drafts))
    return 1 if crashed else 0


if __name__ == "__main__":
    sys.exit(main())
