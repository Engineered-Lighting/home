#!/usr/bin/env python3
"""An interrupted fit leaves a resolvable RoomModel entry with no CalibrationRecord.

run_fit appends the RoomModel revision, then the CalibrationRecord, then writes
fit-N.json, with no transaction between them. This probe makes the second
append fail (as a crash or power cut between the two writes would), then
re-runs the fit: `already_registered` only looks in calibration-records.jsonl,
so a second version is written, and the first transform_id still resolves as a
valid, non-invalidated registration that has no CalibrationRecord and no fit file.

Synthetic data only; temp dirs only; contacts nothing.

    python3 -B probe_interrupted_fit_orphan_entry.py <root of the rung-1-register checkout>
"""
import io
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
TOOL = ROOT / "tools" / "den-register"
sys.path.insert(0, str(TOOL))
import method  # noqa: E402
import records  # noqa: E402
import register  # noqa: E402
import synthetic  # noqa: E402
import worksheet as worksheet_mod  # noqa: E402


def closed_method():
    text = (TOOL / "REGISTER-V1.md").read_text(encoding="utf-8")
    text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", lambda m: f"{m[1]} = {m[2]}  # closed for a probe",
                  text, flags=re.M)
    return method.parse(text)


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    depth, left, seen = synthetic.render()
    take = synthetic.write_take(tmp / "take", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
    work = tmp / "work"
    work.mkdir()
    (work / "worksheet.json").write_text(json.dumps(synthetic.worksheet()), encoding="utf-8")
    m = closed_method()
    sheet = worksheet_mod.load(work / "worksheet.json", need_positions=False)
    session = register.ClickSession(work, take, register.frame_number(take, None, work), sheet, m)
    for pid, (u, v, _) in seen.items():
        session.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
    session.done()

    real = records.append_jsonl
    calls = []

    def crash_on_second(path, record):
        calls.append(Path(path).name)
        if len(calls) == 2:
            raise KeyboardInterrupt("simulated crash between the two appends")
        real(path, record)

    records.append_jsonl = crash_on_second
    try:
        register.run_fit(work, take, m=m, now=lambda: 1_800_000_500.0, out=io.StringIO())
    except KeyboardInterrupt as exc:
        print("RESULT first fit interrupted:", exc)
    records.append_jsonl = real
    code, report = register.run_fit(work, take, m=m, now=lambda: 1_800_000_600.0, out=io.StringIO())
    print("RESULT re-run:", report["result"], report["transform_id"])
    models = records.read_jsonl(work / "room-model.jsonl", "x")
    cals = records.read_jsonl(work / "calibration-records.jsonl", "x")
    first = "den-oak@" + models[0]["cameras"][-1]["version"]
    found = records.resolve(models, first)
    has_record = any(c["inputs"]["transform_id"] == first for c in cals)
    print(f"RESULT {first}: resolves {found['entry']['version']}, invalidated {found['invalidated']}, "
          f"CalibrationRecord {has_record}; fit files {sorted(p.name for p in work.glob('fit-*.json'))}; "
          f"second record supersedes {cals[-1]['supersedes']!r}")
    bad = not has_record and found["invalidated"] is None
    print(("FAIL" if bad else "PASS") + " no resolvable camera entry without its CalibrationRecord after an "
          "interrupted fit")
    sys.exit(1 if bad else 0)
