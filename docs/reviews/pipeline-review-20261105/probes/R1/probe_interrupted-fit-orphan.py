#!/usr/bin/env python3
"""Probe: what an interrupted `fit` leaves behind, and `resolve` on a schema-shaped entry.

A. run_fit appends the RoomModel revision, then the CalibrationRecord, then writes
   fit-N.json. This probe makes the second append fail (as a crash or Ctrl+C between
   the two writes would) and then runs `fit` again with the same inputs, as the owner
   would. It reports whether the re-run notices the half-written registration.
B. `resolve` on a camera entry shaped like schemas.json's own RoomModel example
   (pose a string, error_bound_m null), e.g. a seeded den-oak@2026-10-18.

Synthetic data and temp folders only.
Run:  python3 probe_interrupted-fit-orphan.py <rung-1 checkout root>
"""
import io
import json
import re
import sys
import tempfile
import traceback
from pathlib import Path

root = Path(sys.argv[1]).resolve()
tool = root / "tools" / "den-register"
sys.path.insert(0, str(tool))
import method, records, register, synthetic  # noqa: E402

text = (tool / "REGISTER-V1.md").read_text(encoding="utf-8")
text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", r"\1 = \2  # closed for the probe", text, flags=re.M)
M = method.parse(text)
fails = 0

with tempfile.TemporaryDirectory() as t:
    tmp = Path(t)
    depth, left, seen = synthetic.render(synthetic.POINTS)
    take = synthetic.write_take(tmp / "take1", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
    work = tmp / "work"
    work.mkdir()
    (work / "worksheet.json").write_text(json.dumps(synthetic.worksheet()), encoding="utf-8")
    ws = register.worksheet_file.load(work / "worksheet.json", need_positions=False)
    s = register.ClickSession(work, take, register.frame_number(take, None, work), ws, M)
    for pid, (u, v, _) in seen.items():
        s.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
    s.done()

    real_append, calls = records.append_jsonl, []

    def failing_append(path, record):
        calls.append(Path(path).name)
        if len(calls) == 2:
            raise KeyboardInterrupt("simulated Ctrl+C between the two appends")
        real_append(path, record)

    records.append_jsonl = failing_append
    try:
        register.run_fit(work, take, m=M, now=lambda: 1_800_000_500.0, out=io.StringIO())
    except KeyboardInterrupt as exc:
        print("first fit interrupted:", exc)
    records.append_jsonl = real_append
    out = io.StringIO()
    code, report = register.run_fit(work, take, m=M, now=lambda: 1_800_000_600.0, out=out)
    rms = records.read_jsonl(work / "room-model.jsonl", "x")
    cals = records.read_jsonl(work / "calibration-records.jsonl", "x")
    cal_ids = {c["version"] for c in cals}
    entries = [c for rm in rms for c in rm["cameras"]]
    orphans = [c["calibration_id"] for c in entries if c["calibration_id"] not in cal_ids]
    print(f"re-run: exit {code}, {report['result']} {report['transform_id']}")
    print(f"room-model.jsonl revisions: {len(rms)}; calibration records: {len(cals)}; "
          f"camera entries without a CalibrationRecord: {orphans}")
    print(f"the re-run's CalibrationRecord supersedes {cals[-1].get('supersedes')!r}, "
          f"which {'exists' if cals[-1].get('supersedes') in cal_ids else 'does not exist'} in calibration-records.jsonl")
    if orphans:
        fails += 1
        print("FAIL A: an interrupted fit leaves a resolvable transform_id with no CalibrationRecord; "
              "the re-run registers again on top of it without saying so")

    # B. resolve on a schemas.json-shaped camera entry
    schema = json.loads((root / "docs/architecture/live-v1/schemas.json").read_text(encoding="utf-8"))
    example = next(r for r in schema["records"] if r["id"] == "room_model")["example"]
    seeded = tmp / "seeded"
    seeded.mkdir()
    (seeded / "room-model.jsonl").write_text(json.dumps(example) + "\n", encoding="utf-8")
    out = io.StringIO()
    try:
        code = register.main(["resolve", str(seeded), "den-oak@2026-10-18"], out=out)
        print(f"B: resolve exit {code}: {out.getvalue().strip()}")
    except Exception as exc:  # noqa: BLE001
        fails += 1
        print(f"FAIL B: resolve den-oak@2026-10-18 on the schema's own example entry crashes: "
              f"{type(exc).__name__}: {exc}")
print(f"RESULT {'FAIL' if fails else 'PASS'} ({fails} failures)")
