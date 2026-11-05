#!/usr/bin/env python3
"""Verify finding interrupted-fit-orphan-entry (rung 1, register.run_fit lines 453-457).

run_fit appends room-model.jsonl, then calibration-records.jsonl, then writes
fit-N.json, with nothing tying the three together. Two independent ways to
reach the state "RoomModel line written, CalibrationRecord not":
  A. the second append raises PermissionError (a file held open by another
     program on Windows, or a full disk) -- run_fit does not catch it;
  B. a successful fit, then the disk state a power cut could leave: the
     CalibrationRecord line and fit-1.json gone, the RoomModel line kept.
Then the fit is re-run unchanged. Checks: does the re-run notice? Does the
first transform_id still resolve as valid with no CalibrationRecord? What does
the new record's `supersedes` name?

Synthetic data only; temp dirs only; contacts nothing.

    python3 -B verify_interrupted-fit-orphan-entry.py <root of the rung-1-register checkout>
"""
import io
import json
import re
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
TOOL = ROOT / "tools" / "den-register"
sys.path.insert(0, str(TOOL))
import method  # noqa: E402
import records  # noqa: E402
import register  # noqa: E402
import synthetic  # noqa: E402
import worksheet as worksheet_mod  # noqa: E402

text = (TOOL / "REGISTER-V1.md").read_text(encoding="utf-8")
M = method.parse(re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$",
                        lambda g: f"{g[1]} = {g[2]}  # closed for a probe", text, flags=re.M))


def prepared(tmp):
    depth, left, seen = synthetic.render()
    take = synthetic.write_take(tmp / "take", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
    work = tmp / "work"
    work.mkdir()
    (work / "worksheet.json").write_text(json.dumps(synthetic.worksheet()), encoding="utf-8")
    sheet = worksheet_mod.load(work / "worksheet.json", need_positions=False)
    session = register.ClickSession(work, take, register.frame_number(take, None, work), sheet, M)
    for pid, (u, v, _) in seen.items():
        session.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
    session.done()
    return work, take


def report_state(label, work):
    models = records.read_jsonl(work / "room-model.jsonl", "x")
    cals = records.read_jsonl(work / "calibration-records.jsonl", "x")
    versions = [c["version"] for rm in models for c in rm["cameras"] if c["camera_id"] == "den-oak"]
    first = f"den-oak@{versions[0]}"
    found = records.resolve(models, first)
    record_ids = {c["version"] for c in cals}
    has_record = any(c["inputs"]["transform_id"] == first for c in cals)
    dangling = [c["supersedes"] for c in cals if c.get("supersedes") and c["supersedes"] not in record_ids]
    print(f"RESULT {label}: entries {versions}; {first} resolves (invalidated {found['invalidated']}), "
          f"its calibration_id {found['entry']['calibration_id']!r} has a record: {has_record}; "
          f"records {sorted(record_ids)}; dangling supersedes {dangling}; "
          f"fit files {sorted(p.name for p in work.glob('fit-*.json'))}")
    return (not has_record) and found["invalidated"] is None and bool(dangling)


bad = []
# A. PermissionError on the second append
with tempfile.TemporaryDirectory() as tmp:
    work, take = prepared(Path(tmp))
    real, calls = records.append_jsonl, []

    def locked(path, record):
        calls.append(Path(path).name)
        if Path(path).name == "calibration-records.jsonl":
            raise PermissionError(13, "The process cannot access the file because it is being used by another process")
        real(path, record)

    records.append_jsonl = locked
    try:
        register.run_fit(work, take, m=M, now=lambda: 1_800_000_500.0, out=io.StringIO())
        print("RESULT A: first fit returned normally")
    except PermissionError as exc:
        print(f"RESULT A: first fit raised {type(exc).__name__} after appending {calls[:-1]}")
    finally:
        records.append_jsonl = real
    out = io.StringIO()
    code, again = register.run_fit(work, take, m=M, now=lambda: 1_800_000_600.0, out=out)
    print(f"RESULT A: re-run -> {again['result']} {again['transform_id']} (exit {code})")
    if report_state("A", work):
        bad.append("A")

# B. power-cut state rebuilt from a successful fit
with tempfile.TemporaryDirectory() as tmp:
    work, take = prepared(Path(tmp))
    code, first = register.run_fit(work, take, m=M, now=lambda: 1_800_000_500.0, out=io.StringIO())
    (work / "calibration-records.jsonl").unlink()  # its only line never reached the disk
    (work / "fit-1.json").unlink()                 # written after the appends, so also lost
    code, again = register.run_fit(work, take, m=M, now=lambda: 1_800_000_600.0, out=io.StringIO())
    print(f"RESULT B: first {first['transform_id']}; re-run -> {again['result']} {again['transform_id']} (exit {code})")
    if report_state("B", work):
        bad.append("B")

print(("PASS(defect reproduced)" if bad else "FAIL(not reproduced)")
      + f": orphan RoomModel entry without CalibrationRecord, and a dangling supersedes, in cases {bad}")
