#!/usr/bin/env python3
"""verify_killed-fit-unrecoverable.py: does a Stage 0 sweep whose process is hard-killed during the
fit (after the release, before response.json) end up with complete data that no tool path can score?

How to run:
  python3 -B verify_killed-fit-unrecoverable.py <root of the claude/rung-8-stage0 checkout>

What it does (synthetic data, temp dirs, the suite's own FakeCamera bound to 127.0.0.1 only):
1. Copies tools/den-light and the fixture to a temp dir, then runs ONE sweep in a child process
   against the suite's FakeCamera, with meter.check() replaced by a stand-in that hard-kills the
   process (SIGKILL on itself) -- the way a closed console window, Ctrl+Break (Python's default
   SIGBREAK action) or a power cut ends a process: no finally block runs.
2. In the parent: lists the sweep folder, then calls sweep.recheck() and sweep.main(['--recheck', ...])
   on it, and finally shows the frames on disk are complete by scoring them directly with
   sweep.load_kept() + meter.check() (the path recheck would take if it had a response.json).
3. Control: the same sweep where the stand-in raises an ordinary exception instead (the case the
   plan covers) -> outcome error, response.json written, recheck works.
Prints RESULT lines and a final FAIL (defect shown) / PASS line.
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else sys.exit(__doc__)

CHILD = r'''
import json, os, signal, sys, tempfile
from pathlib import Path
from unittest import mock
sys.path.insert(0, sys.argv[1])
import test_den_light as t
mode = sys.argv[2]
fake = t.FakeCamera()
method = t.sweep_method()
work = Path(sys.argv[3])
stage0 = work / "den-light" / "stage0-20261110"
stage0.mkdir(parents=True)
token = work / "private" / "camera-token.txt"
token.parent.mkdir()
token.write_text(t.TOKEN)
(stage0 / "install.json").write_text(json.dumps({"schema": t.sweep.SCHEMAS["install"],
                                                "meter_sha256": t.sweep.METER_SERVER_SHA256}))
camera = t.sweep.Camera(fake.url, t.TOKEN)
_, folder = t.sweep.start_run(stage0, camera, token, method)
print("FOLDER", folder, flush=True)
def stand_in(*a, **k):
    print("IN-FIT", flush=True)
    if mode == "kill":
        os.kill(os.getpid(), signal.SIGKILL)
    raise RuntimeError("fit bug")
with mock.patch.object(t.sweep.meter, "check", stand_in):
    r = t.sweep.Sweep(camera, method, folder, say=lambda *a: print(*a, flush=True)).run()
print("OUTCOME", r["outcome"], flush=True)
'''


def run_child(base, mode):
    work = Path(tempfile.mkdtemp(prefix=f"sweep-{mode}-", dir=base))
    proc = subprocess.run([sys.executable, "-B", "-c", CHILD, str(base / "tools/den-light"), mode, str(work)],
                          cwd=base / "tools/den-light", capture_output=True, text=True, timeout=600)
    folder = None
    for line in proc.stdout.splitlines():
        if line.startswith("FOLDER "):
            folder = Path(line.split(" ", 1)[1].strip())
    return proc, folder


def main():
    base = Path(tempfile.mkdtemp(prefix="verify-killed-"))
    try:
        shutil.copytree(ROOT / "tools/den-light", base / "tools/den-light")
        (base / "tools/fixtures").mkdir(parents=True)
        shutil.copy2(ROOT / "tools/fixtures/installed-oak-capture-20261016.py", base / "tools/fixtures")
        sys.path.insert(0, str(base / "tools/den-light"))
        import test_den_light as t  # noqa: E402  (the copy; FakeCamera binds 127.0.0.1)
        sweep, meter = t.sweep, t.meter
        method = t.sweep_method()

        proc, folder = run_child(base, "kill")
        out = proc.stdout.splitlines()
        print(f"RESULT kill child: returncode {proc.returncode}, saw 'Released' "
              f"{any(l.startswith('Released') for l in out)}, saw IN-FIT {'IN-FIT' in out}, "
              f"folder holds {sorted(p.name for p in folder.iterdir())}, "
              f"{len(list((folder / 'frames').iterdir()))} frames, "
              f"frames.jsonl lines {len((folder / 'frames.jsonl').read_text().splitlines())}")
        try:
            record, path = sweep.recheck(folder, method)
            recheck_outcome = f"wrote {path.name}: {record['outcome']}"
        except sweep.Refused as exc:
            recheck_outcome = f"Refused: {exc}"
        print(f"RESULT sweep.recheck on the killed sweep: {recheck_outcome}")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            import unittest.mock as mock
            with mock.patch.object(meter, "load_method", return_value=method):
                code = sweep.main(["--recheck", str(folder)])
        print(f"RESULT owner's --recheck line: exit {code}, stderr {err.getvalue().strip()!r}")
        steps = sweep.load_kept(folder, method)
        direct = meter.check(steps, method)
        print(f"RESULT the same folder scored directly (load_kept + meter.check): {len(steps)} steps, "
              f"verdict {direct['verdict']}, reasons {direct['reasons']}")

        proc2, folder2 = run_child(base, "error")
        files2 = sorted(p.name for p in folder2.iterdir())
        try:
            record2, _ = sweep.recheck(folder2, method)
            control = f"recheck {record2['outcome']}"
        except sweep.Refused as exc:
            control = f"Refused: {exc}"
        print(f"RESULT control (ordinary exception in the fit): child says "
              f"{[l for l in proc2.stdout.splitlines() if l.startswith('OUTCOME')]}, folder {files2}, {control}")

        shown = (proc.returncode == -9 and "response.json" not in [p.name for p in folder.iterdir()]
                 and recheck_outcome.startswith("Refused: no readable response.json") and direct["verdict"] in ("pass", "fail"))
        print(("FAIL (defect shown)" if shown else "PASS")
              + ": complete frames survive a hard kill in the fit, but --recheck refuses them for want of response.json")
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
