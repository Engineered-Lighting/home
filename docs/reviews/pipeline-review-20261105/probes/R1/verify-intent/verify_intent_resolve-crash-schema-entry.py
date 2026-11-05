#!/usr/bin/env python3
"""Verifier probe for finding `resolve-crash-schema-entry` (rung 1, den-register).

Builds, in a temp folder with synthetic data only, the exact room-model.jsonl
state that the tool's own test `test_the_photometric_scale_is_carried_over`
builds: a seeded schemas.json-shaped camera entry (pose a string,
error_bound_m null, version 2026-10-18) followed by a real fit. Then:

 1. records.resolve(..., "den-oak@2026-10-18") at the library level (the test
    asserts this works);
 2. the owner's command `register.py resolve <work> den-oak@2026-10-18`, run as
    a real subprocess, with its exit code and the last stderr line;
 3. the same command for the fitted transform_id, as a control.

Contacts nothing; the fit uses the method with its open slots closed in memory,
as the tool's tests do.

Run:  python3 -B verify_resolve-crash-schema-entry.py <root of the rung-1-register checkout>
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
tool = root / "tools" / "den-register"
sys.path.insert(0, str(tool))
import io  # noqa: E402
import method, records, register, synthetic  # noqa: E402,E401

text = (tool / "REGISTER-V1.md").read_text(encoding="utf-8")
text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", r"\1 = \2  # closed for the probe", text, flags=re.M)
M = method.parse(text)


def cli(*args):
    p = subprocess.run([sys.executable, "-B", str(tool / "register.py"), *args], capture_output=True, text=True,
                       timeout=120)
    last_err = (p.stderr.strip().splitlines() or [""])[-1]
    return p.returncode, p.stdout.strip(), last_err


with tempfile.TemporaryDirectory() as t:
    tmp = Path(t)
    depth, left, seen = synthetic.render(synthetic.POINTS)
    take = synthetic.write_take(tmp / "take1", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
    work = tmp / "work"
    work.mkdir()
    (work / "worksheet.json").write_text(json.dumps(synthetic.worksheet()), encoding="utf-8")
    # The seeded state, exactly as test_the_photometric_scale_is_carried_over writes it.
    photometric = {"k_L": 10.0, "unit": "(cd/m²)/nc", "normalisation_version": "meter-norm/v1",
                   "calibration_id": "cal-den-oak-kL-1", "status": "measured"}
    start = records.new_room_model("den")
    start["version"] = "den-v4"
    start["cameras"] = [{"camera_id": "den-oak", "intrinsics": "EEPROM (nominal)",
                         "pose": "approximate visual alignment", "method": "reviewed manual",
                         "version": "2026-10-18", "error_bound_m": None, "photometric": photometric}]
    records.append_jsonl(work / "room-model.jsonl", start)
    ws = register.worksheet_file.load(work / "worksheet.json", need_positions=False)
    s = register.ClickSession(work, take, register.frame_number(take, None, work), ws, M)
    for pid, (u, v, _) in seen.items():
        s.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
    s.done()
    code, report = register.run_fit(work, take, m=M, now=lambda: 1_800_000_500.0, out=io.StringIO())
    print(f"fit on top of the seeded entry: exit {code}, {report['result']} {report['transform_id']}")

    found = records.resolve(records.read_jsonl(work / "room-model.jsonl", "x"), "den-oak@2026-10-18")
    print(f"step1 library resolve den-oak@2026-10-18: ok, method {found['entry']['method']!r}, "
          f"invalidated {found['invalidated']}")

    rc, out, err = cli("resolve", str(work), "den-oak@2026-10-18")
    print(f"step2 CLI resolve den-oak@2026-10-18: exit {rc}; stdout {out!r}; last stderr line {err!r}")
    rc2, out2, err2 = cli("resolve", str(work), report["transform_id"])
    print(f"step3 CLI resolve {report['transform_id']} (control): exit {rc2}; stdout {out2!r}")
    if rc not in (0, 1, 2) or err.startswith(("TypeError", "KeyError")):
        print("RESULT CONFIRMED: the documented resolve command crashes on a state the tool's own test builds")
    elif "Traceback" in err or err:
        print(f"RESULT CONFIRMED: resolve exits {rc} with a traceback instead of a named refusal")
    else:
        print("RESULT REFUTED: resolve handles the seeded entry")
