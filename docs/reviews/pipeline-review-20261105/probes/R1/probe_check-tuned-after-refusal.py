#!/usr/bin/env python3
"""Probe: can a held-out check be tuned to the fit after a refusal, without a trace?

1. A fit is refused with check_over_gate on c1 (c1 really is 8 cm off: the fit is wrong).
2. fit-1.json, which the owner keeps in the work folder, holds the fit's own
   `predicted_room_xyz_m` for c1. README's reason table says for check_over_gate:
   "Re-measure the named point, and check its click."
3. The owner "re-measures" c1 and writes the predicted value into the worksheet (or,
   variant B, clears c1's click and lets another check stand in).
4. fit again: if REGISTERED with checks.held_out "pass" and nothing in the
   CalibrationRecord recording the earlier refusal or the change, the held-out check
   was not blind to the fit.

Synthetic data and temp folders only.
Run:  python3 probe_check-tuned-after-refusal.py <rung-1 checkout root>
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
fails = 0

with tempfile.TemporaryDirectory() as t:
    tmp = Path(t)
    depth, left, seen = synthetic.render(synthetic.POINTS)
    take = synthetic.write_take(tmp / "take1", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
    work = tmp / "work"
    work.mkdir()
    # p1 is mis-measured by 10 cm in z: the fit is wrong, and c1 shows it.
    sheet = synthetic.worksheet(offsets={"p1": [0.0, 0.0, 0.10]}, fit=("p1", "p2", "p3"))
    (work / "worksheet.json").write_text(json.dumps(sheet), encoding="utf-8")
    ws = register.worksheet_file.load(work / "worksheet.json", need_positions=False)
    s = register.ClickSession(work, take, register.frame_number(take, None, work), ws, M)
    for pid in ("p1", "p2", "p3", "c1", "c2"):
        u, v, _ = seen[pid]
        s.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
    s.done()
    code, first = register.run_fit(work, take, m=M, now=lambda: 1_800_000_500.0, out=io.StringIO())
    print("fit 1:", first["result"], [r["reason"] for r in first["reasons"]])
    saved = json.loads((work / "fit-1.json").read_text(encoding="utf-8"))
    c1 = next(p for p in saved["pairs"] if p["id"] == "c1")
    print(f"fit-1.json gives c1 error {c1['error_m'] * 100:.1f} cm and predicted_room_xyz_m "
          f"{c1['predicted_room_xyz_m']} (measured {c1['room_xyz_m']})")
    # The owner "re-measures" c1 and writes the prediction.
    for p in sheet["points"]:
        if p["id"] == "c1":
            p["room_xyz_m"] = [round(v, 3) for v in c1["predicted_room_xyz_m"]]
    (work / "worksheet.json").write_text(json.dumps(sheet), encoding="utf-8")
    code, second = register.run_fit(work, take, m=M, now=lambda: 1_800_000_600.0, out=io.StringIO())
    print("fit 2:", second["result"], second["transform_id"], [r["reason"] for r in second["reasons"]])
    if second["result"] == "registered":
        [rec] = records.read_jsonl(work / "calibration-records.jsonl", "x")
        trace = json.dumps(rec)
        mentions = ("fit-1" in trace) or ("refused" in trace) or ("check_over_gate" in trace)
        print(f"CalibrationRecord checks.held_out: {rec['checks']['held_out']['result']}; "
              f"mentions the earlier refusal: {mentions}")
        true_r, true_t = synthetic.camera_pose()
        import math, rigid  # noqa: E401
        r, tr = rigid.split4(rec["value"])
        real = synthetic.POINTS["c1"]
        off = math.dist(rigid.apply(r, tr, synthetic.to_camera(true_r, true_t, real)), real)
        print(f"the registered transform misplaces the real c1 cross by {off * 100:.1f} cm")
        if not mentions:
            fails += 1
            print("FAIL: a check tuned to the fit's own prediction after a refusal registers with held_out "
                  "'pass' and no trace of the refusal in the record")
print(f"RESULT {'FAIL' if fails else 'PASS'}")
