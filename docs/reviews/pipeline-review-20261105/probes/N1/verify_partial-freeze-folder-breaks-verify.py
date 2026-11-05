#!/usr/bin/env python3
"""Verify probe for finding partial-freeze-folder-breaks-verify (den-label, N1).

What it checks
  The reporter's probe made the 2nd frozen file fail with ENOSPC at open().
  This probe uses the other trigger the finding names, Ctrl+C: a
  KeyboardInterrupt raised from the write of the second frozen file inside a
  real Workspace.freeze_fixture(). Then it checks, in-process:
    1. the half-written fixture-v1 has no fixture.json;
    2. Workspace.verify() raises (instead of returning a problem line);
    3. versions() / status count fixture-v1 as frozen;
    4. a retried freeze fixture writes fixture-v2 cleanly, and verify still
       raises afterwards (so the broken folder keeps verify unusable).

How to run
  python3 -B verify_partial-freeze-folder-breaks-verify.py <den-label checkout>
Synthetic takes built with R's own helpers, in a temp dir. No network.
"""
import builtins
import sys
import tempfile
from pathlib import Path
from unittest import mock


def main():
    root = Path(sys.argv[1]).resolve()
    sys.path[:0] = [str(root / "tools/den-label"), str(root / "tools/den-take")]
    import n1
    import test_den_label as T
    T.setUpModule()  # loopback-only HTTP guard, as the suite does
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

    class Interrupting:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.f.close()
            return False

        def write(self, data):
            self.f.write(data[: len(data) // 2])  # half the bytes reach disk
            raise KeyboardInterrupt

    def opener(path, mode="r", *a, **k):
        f = real_open(path, mode, *a, **k)
        if mode == "xb":
            calls.append(str(path))
            if len(calls) == 2:
                return Interrupting(f)
        return f

    try:
        with mock.patch("builtins.open", opener):
            ws.freeze_fixture("")
        print("RESULT freeze fixture completed (no interruption happened)")
    except KeyboardInterrupt:
        print("RESULT freeze fixture interrupted by Ctrl+C during the 2nd file:", Path(calls[1]).name)
    v1 = takes / "n1" / "fixture-v1"
    held = sorted(p.relative_to(v1).as_posix() for p in v1.rglob("*"))
    print("RESULT fixture-v1 holds:", held)
    no_manifest = not (v1 / "fixture.json").exists()
    try:
        ok, lines = ws.verify("")
        print("RESULT verify returned", ok, lines)
        verify_raises = False
    except Exception as exc:  # noqa: BLE001
        print(f"RESULT verify raised {type(exc).__name__}: {exc}")
        verify_raises = True
    frozen_line = [l for l in ws.status() if l.startswith("Frozen")]
    print("RESULT status:", frozen_line)
    line, _ = ws.freeze_fixture("")
    print("RESULT retried freeze:", line.split(" sha256=")[0])
    try:
        ok, lines = ws.verify("")
        print("RESULT verify after retry returned", ok, lines)
        still = False
    except Exception as exc:  # noqa: BLE001
        print(f"RESULT verify after retry raised {type(exc).__name__}")
        still = True
    T.tearDownModule()
    if no_manifest and verify_raises and still and frozen_line == ["Frozen: fixture v1; labels none."]:
        print("FAIL a Ctrl+C mid-freeze leaves fixture-v1 without its manifest; verify raises instead of "
              "reporting it, even after a clean retry, and status lists v1 as frozen")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
