#!/usr/bin/env python3
"""A complete Stage 0 sweep whose process is killed during the fit (after the
release, before response.json) cannot be rechecked: --recheck needs a
response.json, so the camera data is unusable and the sweep must be redone.

    python3 probe_killed_fit_unrecoverable.py <branch-checkout-root>

<root> is a checkout of claude/rung-8-stage0. A child process runs one sweep
against the suite's own FakeCamera on 127.0.0.1 (synthetic frames, temp dirs),
with meter.check() replaced by a slow stand-in so the kill lands in the fit,
as a closed PowerShell window, Ctrl+Break, a sleep or a reboot would. The
parent SIGKILLs it after "Released", then calls sweep.recheck() on the folder.
Nothing but 127.0.0.1 is contacted.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else sys.exit(__doc__)

CHILD = r'''
import sys, time, tempfile
from pathlib import Path
from unittest import mock
sys.path.insert(0, sys.argv[1])
import test_den_light as t
fake = t.FakeCamera()
method = t.sweep_method()
work = Path(tempfile.mkdtemp(prefix="killed-sweep-"))
stage0 = work / "den-light" / "stage0-20261110"
stage0.mkdir(parents=True)
token = work / "private" / "camera-token.txt"
token.parent.mkdir()
token.write_text(t.TOKEN)
import json
(stage0 / "install.json").write_text(json.dumps({"schema": t.sweep.SCHEMAS["install"],
                                                "meter_sha256": t.sweep.METER_SERVER_SHA256}))
camera = t.sweep.Camera(fake.url, t.TOKEN)
_, folder = t.sweep.start_run(stage0, camera, token, method)
print("FOLDER", folder, flush=True)
def slow_check(*a, **k):
    print("FITTING", flush=True)
    time.sleep(120)
with mock.patch.object(t.sweep.meter, "check", slow_check):
    t.sweep.Sweep(camera, method, folder, say=lambda *a: print(*a, flush=True)).run()
'''


def main():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        shutil.copytree(ROOT / "tools/den-light", base / "tools/den-light")
        (base / "tools/fixtures").mkdir(parents=True)
        shutil.copy2(ROOT / "tools/fixtures/installed-oak-capture-20261016.py", base / "tools/fixtures")
        child = subprocess.Popen([sys.executable, "-B", "-c", CHILD, str(base / "tools/den-light")],
                                 cwd=base / "tools/den-light", stdout=subprocess.PIPE, text=True)
        folder, released = None, False
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            line = child.stdout.readline()
            if not line:
                break
            if line.startswith("FOLDER "):
                folder = Path(line.split(" ", 1)[1].strip())
            if line.startswith("Released"):
                released = True
            if line.startswith("FITTING"):
                break
        os.kill(child.pid, signal.SIGKILL)
        child.wait()
        print(f"RESULT child killed in the fit (released={released}); folder holds "
              f"{sorted(p.name for p in folder.iterdir())}, {len(list((folder / 'frames').iterdir()))} frames, "
              f"frames.jsonl lines {len((folder / 'frames.jsonl').read_text().splitlines())}")
        sys.path.insert(0, str(base / "tools/den-light"))
        import meter
        import sweep
        method = meter.Method(dict(meter.load_method().values, countdown_s=0, still_span_s=0.15, still_wait_limit_s=3,
                                   poll_s=0.005, settle_limit_s=1.0, frames_per_step=2, run_limit_s=60, pixel_step=20,
                                   fit_iterations=10, region_min_code_samples=3, region_tolerance_percent=10), ())
        try:
            record, path = sweep.recheck(folder, method)
            outcome = f"recheck wrote {path.name}: {record['outcome']}"
        except sweep.Refused as exc:
            outcome = f"Refused: {exc}"
        print(f"RESULT --recheck on the killed sweep: {outcome}")
        print(("FAIL (defect shown)" if outcome.startswith("Refused") else "PASS")
              + ": the complete sweep's frames are kept but no tool path can produce its verdict")
        shutil.rmtree(folder.parent.parent.parent, ignore_errors=True)


if __name__ == "__main__":
    main()
