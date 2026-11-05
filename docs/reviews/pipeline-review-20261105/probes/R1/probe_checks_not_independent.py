#!/usr/bin/env python3
"""Are rung 1's "two independent checks" independent? Two scenarios, end to end.

A. Coincident checks. The worksheet lists the marks of two fit points again as
   the two checks (same room_xyz_m, clicked on the same spot). Nothing refuses a
   check at (or next to) a fit point, or two checks at one place. With three
   fit points and an 8 cm tape error on one of them, perpendicular to their
   plane, the fit tilts to absorb it: residuals stay small, the "checks" equal
   two fit residuals, and the run prints REGISTERED. The probe then carries a
   genuinely independent room point through the written transform: it is off
   by more than 5 cm, and as a real check it would have refused the run.

B. Roles changed after a refusal. A check mismeasured by 7 cm refuses the fit
   (check_over_gate). The owner swaps that check with a fit point in the
   worksheet and fits again: REGISTERED. The CalibrationRecord's held_out block
   reports two passing checks and nothing in it refers to the refused attempt.

Synthetic data only; temp dirs only; contacts nothing.

    python3 -B probe_checks_not_independent.py <root of the rung-1-register checkout>
"""
import io
import json
import math
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

FAILS = []


def closed_method():
    text = (TOOL / "REGISTER-V1.md").read_text(encoding="utf-8")
    text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", lambda m: f"{m[1]} = {m[2]}  # closed for a probe",
                  text, flags=re.M)
    return method.parse(text)


def setup(folder, points, sheet):
    depth, left, seen = synthetic.render(points)
    take = synthetic.write_take(folder / "take", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
    work = folder / "work"
    work.mkdir()
    (work / "worksheet.json").write_text(json.dumps(sheet), encoding="utf-8")
    return take, work, seen


def click_and_fit(take, work, seen, now):
    m = closed_method()
    sheet = worksheet_mod.load(work / "worksheet.json", need_positions=False)
    session = register.ClickSession(work, take, register.frame_number(take, None, work), sheet, m)
    for p in sheet["points"]:
        if p["id"] in seen:
            u, v, _ = seen[p["id"]]
            session.click({"id": p["id"], "u": round(u, 1), "v": round(v, 1)})
    session.done()
    out = io.StringIO()
    code, report = register.run_fit(work, take, m=m, now=lambda: now, out=out)
    return code, report, out.getvalue()


def sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    # ---------- A ----------
    P = synthetic.POINTS
    pts = {"p1": P["p1"], "p2": P["p2"], "p3": P["p3"], "c1": list(P["p1"]), "c2": list(P["p2"])}
    n = cross(sub(P["p2"], P["p1"]), sub(P["p3"], P["p1"]))
    norm = math.sqrt(sum(v * v for v in n))
    n = [v / norm for v in n]
    offsets = {"p3": [0.08 * v for v in n]}
    sheet = synthetic.worksheet(pts, ("p1", "p2", "p3"), ("c1", "c2"), offsets)
    (tmp / "A").mkdir()
    take, work, seen = setup(tmp / "A", pts, sheet)
    code, report, text = click_and_fit(take, work, seen, 1_800_000_500.0)
    print(text.rstrip())
    tid = report["transform_id"]
    if code == 0:
        entry = records.resolve(records.read_jsonl(work / "room-model.jsonl", "x"), tid)["entry"]
        R, t = synthetic.camera_pose()
        worst = 0.0
        for name, X in (("independent mark at the old c1", P["c1"]), ("independent mark at the old c2", P["c2"]),
                        ("floor point under the camera's view", [2.0, 2.0, 0.0]),
                        ("a head at the far wall", [4.2, 3.4, 1.6])):
            cam = synthetic.to_camera(R, t, X)
            err = math.dist(records.to_room(entry, cam), X)
            worst = max(worst, err)
            print(f"RESULT A: {tid} puts the {name} {err * 100:.1f} cm from its true position")
        bad = worst > 0.05
        print(("FAIL" if bad else "PASS") + f" A: REGISTERED with checks coincident with fit points while an "
              f"independent point is {worst * 100:.1f} cm off (gate 5 cm)")
        if bad:
            FAILS.append("A")
    else:
        print("RESULT A: refused, scenario did not reproduce:", [r["reason"] for r in report["reasons"]])

    # ---------- A2: checks 15 cm from a fit point instead of on it ----------
    (tmp / "A2").mkdir()
    pts2 = {"p1": P["p1"], "p2": P["p2"], "p3": P["p3"],
            "c1": [P["p1"][0] + 0.15, P["p1"][1], P["p1"][2]], "c2": [P["p2"][0], P["p2"][1] - 0.15, P["p2"][2]]}
    sheet2 = synthetic.worksheet(pts2, ("p1", "p2", "p3"), ("c1", "c2"), offsets)
    take2, work2, seen2 = setup(tmp / "A2", pts2, sheet2)
    code_a2, report_a2, text_a2 = click_and_fit(take2, work2, seen2, 1_800_000_500.0)
    errs = [round(p["error_m"] * 100, 1) for p in report_a2.get("pairs", []) if "error_m" in p]
    print(f"RESULT A2 (checks 15 cm from p1 and p2): {report_a2['result']}, check errors {errs} cm")
    if code_a2 == 0:
        entry2 = records.resolve(records.read_jsonl(work2 / "room-model.jsonl", "x"), report_a2["transform_id"])["entry"]
        R, t = synthetic.camera_pose()
        far = max(math.dist(records.to_room(entry2, synthetic.to_camera(R, t, X)), X)
                  for X in (P["c1"], P["c2"], [4.2, 3.4, 1.6]))
        print(f"RESULT A2: worst independent point {far * 100:.1f} cm off")
        if far > 0.05:
            print("FAIL A2: REGISTERED with checks 15 cm from fit points while an independent point is "
                  f"{far * 100:.1f} cm off")
            FAILS.append("A2")

    # ---------- B ----------
    (tmp / "B").mkdir()
    sheet = synthetic.worksheet(P, synthetic.FIT, synthetic.CHECKS, {"c1": [0.0, 0.07, 0.0]})
    take, work, seen = setup(tmp / "B", P, sheet)
    code1, report1, text1 = click_and_fit(take, work, seen, 1_800_000_500.0)
    print("RESULT B first fit:", report1["result"], [r["reason"] for r in report1["reasons"]])
    swapped = json.loads((work / "worksheet.json").read_text(encoding="utf-8"))
    for row in swapped["points"]:
        if row["id"] == "c1":
            row["role"] = "fit"
        elif row["id"] == "p4":
            row["role"] = "check"
    (work / "worksheet.json").write_text(json.dumps(swapped), encoding="utf-8")
    code2, report2, text2 = click_and_fit(take, work, seen, 1_800_000_900.0)
    print("RESULT B after swapping c1 (fit) and p4 (check):", report2["result"], report2.get("transform_id"))
    print(text2.rstrip())
    if code2 == 0:
        record = records.read_jsonl(work / "calibration-records.jsonl", "x")[-1]
        held = record["checks"]["held_out"]
        dump = json.dumps(record)
        mentions = any(s in dump for s in ("fit-1", "refused", "check_over_gate", "attempt"))
        print("RESULT B held_out:", json.dumps(held))
        print(f"RESULT B record mentions the refused first attempt: {mentions}; fit files in the folder: "
              f"{sorted(p.name for p in work.glob('fit-*.json'))}")
        bad = report1["result"] == "refused" and not mentions
        print(("FAIL" if bad else "PASS") + " B: a refused held-out check was moved into the fit and the "
              "registration passed with no trace of it in the CalibrationRecord")
        if bad:
            FAILS.append("B")

print("RESULT", "FAIL" if FAILS else "PASS", FAILS)
sys.exit(1 if FAILS else 0)
