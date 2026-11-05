#!/usr/bin/env python3
"""Verifier probe for finding check-tuned-after-refusal (rung 1, den-register).

Re-runs the finding's scenario (synthetic scene, temp folders only):
  fit 1 with p1 mis-measured by 10 cm in z -> refused check_over_gate on c1;
  the owner overwrites c1's measured position with fit-1.json's predicted_room_xyz_m;
  fit 2.
Then it checks what trace is left: does fit-1.json (the refusal) survive in the work
folder, does its worksheet_sha256 differ from the CalibrationRecord's, and does the
CalibrationRecord itself mention the refusal? It also re-runs the honest path
(c1 re-measured with the tape, i.e. unchanged) to show the README's advice alone does
not register.

Run:  python3 -B verify_check-tuned-after-refusal.py <rung-1 checkout root>
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
import method, records, register, synthetic  # noqa: E402

text = (tool / "REGISTER-V1.md").read_text(encoding="utf-8")
text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", r"\1 = \2  # closed for the probe", text, flags=re.M)
M = method.parse(text)

with tempfile.TemporaryDirectory() as t:
    tmp = Path(t)
    depth, left, seen = synthetic.render(synthetic.POINTS)
    take = synthetic.write_take(tmp / "take1", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
    work = tmp / "work"
    work.mkdir()
    sheet = synthetic.worksheet(offsets={"p1": [0.0, 0.0, 0.10]}, fit=("p1", "p2", "p3"))
    (work / "worksheet.json").write_text(json.dumps(sheet), encoding="utf-8")
    ws = register.worksheet_file.load(work / "worksheet.json", need_positions=False)
    s = register.ClickSession(work, take, register.frame_number(take, None, work), ws, M)
    for pid in ("p1", "p2", "p3", "c1", "c2"):
        u, v, _ = seen[pid]
        s.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
    s.done()
    out = io.StringIO()
    code1, first = register.run_fit(work, take, m=M, now=lambda: 1_800_000_500.0, out=out)
    print("RESULT fit 1:", first["result"], [r["reason"] for r in first["reasons"]])
    print("  printed to the owner:", [l.strip() for l in out.getvalue().splitlines() if "c1" in l])
    # Honest path: the owner re-measures c1 with the tape and gets the same number.
    code_h, honest = register.run_fit(work, take, m=M, now=lambda: 1_800_000_550.0, out=io.StringIO())
    print("RESULT honest re-measure of c1 (same value):", honest["result"], [r["reason"] for r in honest["reasons"]])
    saved = json.loads((work / "fit-1.json").read_text(encoding="utf-8"))
    c1 = next(p for p in saved["pairs"] if p["id"] == "c1")
    print("  fit-1.json carries c1 predicted_room_xyz_m:", "predicted_room_xyz_m" in c1)
    for p in sheet["points"]:
        if p["id"] == "c1":
            p["room_xyz_m"] = [round(v, 3) for v in c1["predicted_room_xyz_m"]]
    (work / "worksheet.json").write_text(json.dumps(sheet), encoding="utf-8")
    code2, second = register.run_fit(work, take, m=M, now=lambda: 1_800_000_600.0, out=io.StringIO())
    print("RESULT fit 3 (c1 overwritten with the prediction):", second["result"], second["transform_id"])
    files = sorted(p.name for p in work.iterdir())
    print("  work folder now holds:", files)
    kept = json.loads((work / "fit-1.json").read_text(encoding="utf-8"))
    [rec] = records.read_jsonl(work / "calibration-records.jsonl", "x")
    print(f"  fit-1.json still says {kept['result']!r} with reasons {[r['reason'] for r in kept['reasons']]}")
    print(f"  fit-1.json worksheet_sha256 == CalibrationRecord worksheet_sha256: "
          f"{kept['worksheet_sha256'] == rec['inputs']['worksheet_sha256']}")
    trace = json.dumps(rec)
    print("  CalibrationRecord mentions the refusal:", any(w in trace for w in ("fit-1", "refused", "check_over_gate")))
