#!/usr/bin/env python3
"""Verifier probe for finding `interrupted-fit-orphan` (rung 1, den-register).

What it checks, on synthetic data in temp folders only (contacts nothing):

 1. A realistic interruption: the CalibrationRecord append fails with an OSError
    (PermissionError, as a Windows sharing violation would give) after the
    RoomModel revision was appended. It drives the owner's CLI entry point
    `register.main(["fit", ...])` (with the method's open slots closed in memory,
    as the tool's own tests do) and reports what the owner sees and what is left.
 2. The re-run with the same inputs: does it say anything about the orphan, which
    version does it write, and does its `supersedes` name a record that exists?
 3. The consequence: are the orphan entry's pose and the re-run's pose the same
    transform (same inputs), and does the orphan still resolve as valid?
 4. The reporter's suggested fix (CalibrationRecord first, RoomModel last): the
    end state an interruption would leave under that order is simulated, and the
    re-run is checked for whether it would then block the owner.

Run:  python3 -B verify_interrupted-fit-orphan.py <root of the rung-1-register checkout>
"""
import io
import json
import re
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
tool = root / "tools" / "den-register"
sys.path.insert(0, str(tool))
import method, records, register, synthetic  # noqa: E402,E401

text = (tool / "REGISTER-V1.md").read_text(encoding="utf-8")
text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", r"\1 = \2  # closed for the probe", text, flags=re.M)
M = method.parse(text)
register.method_file.load = lambda: M  # the CLI would refuse slots_open otherwise
real_append = records.append_jsonl


def scene(tmp):
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
    return work, take


def state(work):
    rms = records.read_jsonl(work / "room-model.jsonl", "x")
    cals = records.read_jsonl(work / "calibration-records.jsonl", "x")
    fits = sorted(p.name for p in work.glob("fit-*.json"))
    return rms, cals, fits


with tempfile.TemporaryDirectory() as t:
    work, take = scene(Path(t))

    def locked_calibrations(path, record):
        if Path(path).name == "calibration-records.jsonl":
            raise PermissionError(13, "The process cannot access the file because it is being used by another process",
                                  str(path))
        real_append(path, record)

    records.append_jsonl = locked_calibrations
    out = io.StringIO()
    try:
        code = register.main(["fit", str(work), "--take", str(take)], out=out)
        print(f"step1: main returned {code} (no exception)")
    except Exception as exc:  # noqa: BLE001
        print(f"step1: main raised {type(exc).__name__} out of the CLI (an uncaught traceback, process exit 1); "
              f"printed before it: {out.getvalue().strip()!r}")
    records.append_jsonl = real_append
    rms, cals, fits = state(work)
    print(f"step1 state: room-model revisions {len(rms)}, calibration records {len(cals)}, fit files {fits}")

    out = io.StringIO()
    code = register.main(["fit", str(work), "--take", str(take)], out=out)
    rms, cals, fits = state(work)
    printed = out.getvalue()
    print(f"step2: re-run exit {code}; first line {printed.splitlines()[0]!r}; "
          f"mentions the orphan: {'2027' in printed and '.1' in printed.split('REGISTERED')[-1].split(chr(10))[0]}")
    cal_ids = {c["version"] for c in cals}
    orphans = [c["version"] for rm in rms for c in rm["cameras"] if c.get("calibration_id") not in cal_ids]
    print(f"step2 state: revisions {len(rms)}, calibration records {len(cals)}, fit files {fits}, "
          f"entries without a CalibrationRecord {orphans}, supersedes {cals[-1]['supersedes']!r} "
          f"({'exists' if cals[-1]['supersedes'] in cal_ids else 'missing'})")
    e1, e2 = rms[0]["cameras"][-1], rms[1]["cameras"][-1]
    same_pose = e1["pose"]["room_from_camera"] == e2["pose"]["room_from_camera"]
    found = records.resolve(rms, records.transform_id("den-oak", e1["version"]))
    print(f"step3: orphan {e1['version']} vs re-run {e2['version']}: same room_from_camera {same_pose}, "
          f"same error_bound {e1['error_bound_m'] == e2['error_bound_m']}; orphan resolves, "
          f"invalidated={found['invalidated']}")
    if orphans and not same_pose:
        print("RESULT: orphan carries a different transform than the re-run")
    elif orphans:
        print("RESULT CONFIRMED (mechanics): orphan entry without CalibrationRecord + dangling supersedes; "
              "the orphan's transform is identical to the re-run's, so no wrong transform is served")
    else:
        print("RESULT REFUTED: no orphan left")

with tempfile.TemporaryDirectory() as t:
    work, take = scene(Path(t))

    def calibration_first_then_crash(path, record):
        if Path(path).name == "room-model.jsonl":
            return  # simulated: the CalibrationRecord was written first, the crash came before the RoomModel line
        real_append(path, record)

    records.append_jsonl = calibration_first_then_crash
    try:
        register.main(["fit", str(work), "--take", str(take)], out=io.StringIO())
    finally:
        records.append_jsonl = real_append
    out = io.StringIO()
    code = register.main(["fit", str(work), "--take", str(take)], out=out)
    named = re.search(r"registered as (\S+)", out.getvalue())
    resolved = None
    if named:
        try:
            records.resolve(records.read_jsonl(work / "room-model.jsonl", "x"), named[1])
            resolved = "resolves"
        except register.Refused as exc:
            resolved = exc.reason
    print(f"step4 (suggested order): re-run exit {code}, {out.getvalue().splitlines()[1].strip()!r}; "
          f"named id {named[1] if named else None} -> {resolved}")
    if code == 1 and resolved == "transform_unknown":
        print("NOTE: under the suggested write order an interruption would block re-registration from the same "
              "inputs (already_registered naming an id that does not resolve)")
