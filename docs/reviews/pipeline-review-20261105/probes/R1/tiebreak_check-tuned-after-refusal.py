#!/usr/bin/env python3
"""Tiebreak probe for finding check-tuned-after-refusal (rung 1, den-register).

The finding says a check can be "re-measured" to the fit's own prediction after a
check_over_gate refusal and the run then registers "with no trace". This probe asks:
  1. Does the console the owner reads show the predicted check position? (only fit-N.json?)
  2. Does an honest re-measure (same value) stay refused?
  3. Legit path the README's remedy is for: all fit points right, c1 mistyped by 12 cm
     -> refused; the owner fixes the typo -> registers. Is the fitted matrix the same
     before and after (so the tool cannot tell a typo fix from a substituted number)?
  4. The finding's path: p1 off by 10 cm, c1 overwritten with fit-1.json's prediction
     -> registers; what stays in the work folder (fit-1.json, its worksheet hash)?

Synthetic data and temp folders only; nothing contacts a host.
Run:  python3 -B tiebreak_check-tuned-after-refusal.py <rung-1 checkout root>
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
FIT = ("p1", "p2", "p3")


def setup(tmp, offsets):
    depth, left, seen = synthetic.render(synthetic.POINTS)
    take = synthetic.write_take(tmp / "take1", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
    work = tmp / "work"
    work.mkdir()
    sheet = synthetic.worksheet(offsets=offsets, fit=FIT)
    (work / "worksheet.json").write_text(json.dumps(sheet), encoding="utf-8")
    ws = register.worksheet_file.load(work / "worksheet.json", need_positions=False)
    s = register.ClickSession(work, take, register.frame_number(take, None, work), ws, M)
    for pid in FIT + ("c1", "c2"):
        u, v, _ = seen[pid]
        s.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
    s.done()
    return take, work, sheet


def fit(work, take, t):
    out = io.StringIO()
    code, rep = register.run_fit(work, take, m=M, now=lambda: t, out=out)
    return rep, out.getvalue()


def set_c1(work, sheet, xyz):
    for p in sheet["points"]:
        if p["id"] == "c1":
            p["room_xyz_m"] = xyz
    (work / "worksheet.json").write_text(json.dumps(sheet), encoding="utf-8")


# Case 3: legit typo in c1 (z 1.20 typed as 1.32), every fit point right.
with tempfile.TemporaryDirectory() as t:
    take, work, sheet = setup(Path(t), {"c1": [0.0, 0.0, 0.12]})
    r1, _ = fit(work, take, 1_800_000_500.0)
    print("RESULT typo case fit 1:", r1["result"], [x["reason"] for x in r1["reasons"]])
    set_c1(work, sheet, list(synthetic.POINTS["c1"]))
    r2, _ = fit(work, take, 1_800_000_600.0)
    print("RESULT typo case fit 2 (typo fixed):", r2["result"], r2["transform_id"],
          "| same matrix as fit 1:", r1.get("matrix") == r2.get("matrix"))

# Cases 1, 2, 4: p1 off by 10 cm in z (the finding's scene).
with tempfile.TemporaryDirectory() as t:
    take, work, sheet = setup(Path(t), {"p1": [0.0, 0.0, 0.10]})
    r1, console = fit(work, take, 1_800_000_500.0)
    saved = json.loads((work / "fit-1.json").read_text(encoding="utf-8"))
    pred = next(p for p in saved["pairs"] if p["id"] == "c1")["predicted_room_xyz_m"]
    print("RESULT fit 1:", r1["result"], [x["reason"] for x in r1["reasons"]])
    shown = any(f"{v:.2f}" in console or f"{v:.3f}" in console for v in pred)
    print("RESULT console shows c1's predicted position:", shown, "| fit-1.json carries it:", True)
    print("  console lines on c1:", [ln.strip() for ln in console.splitlines() if "c1" in ln])
    r_h, _ = fit(work, take, 1_800_000_550.0)
    print("RESULT honest re-measure of c1 (same value):", r_h["result"], [x["reason"] for x in r_h["reasons"]])
    set_c1(work, sheet, [round(v, 3) for v in pred])
    r2, _ = fit(work, take, 1_800_000_600.0)
    print("RESULT c1 overwritten with the fit's prediction:", r2["result"], r2["transform_id"],
          "| same matrix as fit 1:", r1.get("matrix") == r2.get("matrix"))
    print("  work folder:", sorted(p.name for p in work.iterdir()))
    [rec] = records.read_jsonl(work / "calibration-records.jsonl", "x")
    kept = json.loads((work / "fit-1.json").read_text(encoding="utf-8"))
    print(f"  fit-1.json kept: result {kept['result']!r}, reasons {[x['reason'] for x in kept['reasons']]}, "
          f"worksheet_sha256 differs from the record's: {kept['worksheet_sha256'] != rec['inputs']['worksheet_sha256']}")
    print("  CalibrationRecord mentions an earlier attempt:",
          any(w in json.dumps(rec) for w in ("fit-1", "refused", "check_over_gate")))
