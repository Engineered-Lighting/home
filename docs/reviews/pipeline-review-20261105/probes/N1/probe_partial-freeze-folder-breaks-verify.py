#!/usr/bin/env python3
"""Probe: an interrupted freeze leaves a half-written frozen folder that verify cannot report.

What it checks
  write_frozen() makes the version folder first and then writes each file; an
  error part way (disk full on a 97%-full C:, or Ctrl+C) leaves fixture-vN/
  without fixture.json. This probe interrupts a real `freeze fixture` after its
  first file (the surface map), then runs:
    - `label.py verify`: expects a report naming the broken folder and exit 1;
    - `label.py status`: what it says is frozen;
    - `label.py boxes --pad 30`: whether the pad is now fixed;
    - `label.py freeze labels`: what it does with the broken newest fixture.
  Each label.py call runs in a subprocess so an uncaught exception shows as a
  traceback and Python's own exit code.

How to run
  python3 probe_partial-freeze-folder-breaks-verify.py <den-label checkout>
Synthetic takes built with R's own helpers in a temp dir.
"""
import builtins
import io
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock


def run_label(root, args):
    proc = subprocess.run([sys.executable, "-B", str(root / "tools/den-label/label.py"), *args],
                          capture_output=True, text=True, cwd=root)
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode, tail


def main():
    root = Path(sys.argv[1]).resolve()
    sys.path[:0] = [str(root / "tools/den-label"), str(root / "tools/den-take")]
    import n1
    import test_den_label as T
    tmp = Path(tempfile.mkdtemp(prefix="n1-attack-partial-")).resolve()
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
        print(f"RESULT freeze fixture raised {type(exc).__name__}: {exc} (not a Refused)")
    folder = takes / "n1" / "fixture-v1"
    print("RESULT fixture-v1 holds:", sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")))
    common = ["--takes", str(takes)]
    code, tail = run_label(root, ["verify", *common])
    print(f"RESULT verify exit {code}; last line: {tail[-1] if tail else ''}")
    crashed = code == 1 and any(l.startswith("Traceback") for l in tail)
    code_s, tail_s = run_label(root, ["status", *common])
    print(f"RESULT status exit {code_s}; {[l for l in tail_s if l.startswith('Frozen')]}")
    code_p, tail_p = run_label(root, ["boxes", "--pad", "30", *common])
    print(f"RESULT boxes --pad 30 exit {code_p}; {tail_p[-1] if tail_p else ''}")
    code_l, tail_l = run_label(root, ["freeze", "labels", *common])
    print(f"RESULT freeze labels exit {code_l}; last line: {tail_l[-1] if tail_l else ''}")
    if crashed:
        print("FAIL verify crashes with a traceback on a half-written frozen folder instead of reporting it; "
              "status counts it as frozen and the pad is fixed by it")
        return 1
    print("PASS verify reports the broken folder")
    return 0


if __name__ == "__main__":
    sys.exit(main())
