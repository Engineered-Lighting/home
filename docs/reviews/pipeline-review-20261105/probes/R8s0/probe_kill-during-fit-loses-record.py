#!/usr/bin/env python3
"""probe_kill-during-fit-loses-record.py

What it checks (rung 8 Stage 0, tools/den-light/sweep.py):
  After the release, sweep.py fits the response (METHOD's real numbers: tens of seconds to minutes
  of pure Python) and only then writes response.json. Ctrl+C is trapped in that window, but closing
  the PowerShell window, Ctrl+Break, a crash or a sleeping laptop end the process without a record.
  The probe runs a full synthetic sweep in a child process (the suite's FakeCamera on 127.0.0.1),
  makes the fit slow, kills the child after the release (SIGKILL, the analogue of the window being
  closed), and then asks the tool's own recovery path, `sweep.py --recheck <sweep folder>`, to score
  the complete, saved data.

How to run:
  python3 -B probe_kill-during-fit-loses-record.py <root of the claude/rung-8-stage0 checkout>

Synthetic data only; temp dirs; nothing in the checkout is modified. Exit 0 = reproduced (a
complete sweep's frames survive, but no response.json exists and --recheck refuses the folder).
"""
import contextlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CHILD = r'''
import sys, json, time
from pathlib import Path
from unittest import mock
tool, stage0, token = map(Path, sys.argv[1:4])
sys.path.insert(0, str(tool))
import meter, sweep, test_den_light as T
fake = T.FakeCamera()
camera = sweep.Camera(fake.url, T.TOKEN)
method = meter.load_method()
_, folder = sweep.start_run(stage0, camera, token, method)
print("FOLDER", folder, flush=True)
def slow_check(steps, m):
    print("FITTING", flush=True)
    time.sleep(120)            # the real fit takes this order of time with METHOD's numbers
    return meter.check(steps, m)
with mock.patch.object(sweep.meter, "check", slow_check):
    sweep.Sweep(camera, method, folder, say=lambda *a, **k: print(*a, flush=True)).run()
'''


def set_number(text, name, value):
    new, count = re.subn(rf"^{name} = [^\s#]+", f"{name} = {value}", text, flags=re.M)
    assert count == 1, name
    return new


def main():
    root = Path(sys.argv[1]).resolve()
    tmp = Path(tempfile.mkdtemp(prefix="r8s0-kill-"))
    try:
        tool = tmp / "tools" / "den-light"
        shutil.copytree(root / "tools" / "den-light", tool)
        (tmp / "tools" / "fixtures").mkdir()
        shutil.copy(root / "tools/fixtures/installed-oak-capture-20261016.py", tmp / "tools/fixtures/")
        text = (tool / "METHOD-V1.md").read_text(encoding="utf-8").replace("# open", "# confirmed")
        for name, value in dict(countdown_s=0, still_span_s=0.15, still_wait_limit_s=3, poll_s=0.005,
                                settle_limit_s=1.0, frames_per_step=2, run_limit_s=60, pixel_step=20,
                                fit_iterations=10, region_min_code_samples=3,
                                region_tolerance_percent=10).items():
            text = set_number(text, name, value)
        (tool / "METHOD-V1.md").write_text(text, encoding="utf-8")
        sys.path.insert(0, str(tool))
        import meter, sweep, test_den_light as T  # noqa: E402
        stage0 = tmp / "den-light" / "stage0-20261110"
        stage0.mkdir(parents=True)
        token = tmp / "private" / "camera-token.txt"
        token.parent.mkdir()
        token.write_text(T.TOKEN)
        (stage0 / "install.json").write_text(json.dumps(
            {"schema": sweep.SCHEMAS["install"], "meter_sha256": sweep.METER_SERVER_SHA256}))
        child = subprocess.Popen([sys.executable, "-B", "-c", CHILD, str(tool), str(stage0), str(token)],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        folder, seen = None, []
        deadline = time.time() + 120
        while time.time() < deadline:
            line = child.stdout.readline()
            if not line:
                break
            seen.append(line.rstrip())
            if line.startswith("FOLDER"):
                folder = Path(line.split(" ", 1)[1].strip())
            if line.startswith("FITTING"):
                break
        released = any(l.startswith("Released") for l in seen)
        print("child said:", " | ".join(seen))
        os.kill(child.pid, signal.SIGKILL)     # the window is closed during the fit
        child.wait()
        lines = (folder / "frames.jsonl").read_text().splitlines() if folder else []
        frames = sorted((folder / "frames").iterdir()) if folder else []
        steps = sorted({json.loads(l)["step"] for l in lines})
        print(f"after the kill: {folder.name if folder else None}: released={released} "
              f"frames.jsonl lines={len(lines)} frame files={len(frames)} steps={steps[:1]}..{steps[-1:]} "
              f"response.json exists={(folder / 'response.json').exists() if folder else None}")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sweep.main(["--recheck", str(folder)])
        print(f"--recheck exit {code}: {(out.getvalue() + err.getvalue()).strip()}")
        complete = released and len(steps) == sweep.meter.load_method()["step_count"] + 1 and len(frames) == len(lines)
        ok = complete and not (folder / "response.json").exists() and code == 2
        print("RESULT REPRODUCED: the data was complete and the camera released, but a kill during the fit leaves "
              "no response.json and --recheck refuses the folder" if ok else "RESULT not reproduced")
        print("PASS (reproduced)" if ok else "FAIL (not reproduced)")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
