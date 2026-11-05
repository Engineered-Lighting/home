#!/usr/bin/env python3
"""probe_posthoc_heldout_edit: after a score has shown which held-out points are out of tolerance,
can the worksheet (or the day folders) be changed so that a re-score passes, with nothing in the new
score saying that a point which failed, or the fit itself, changed?

  A. drop a failing held-out point: blank its two readings, re-score.
  B. fit-shopping: blank the day-1 fit row so another day-1 counter-1 capture (one the first score
     excluded as "repeats the fit") becomes the fit, re-score.
  C. move day 1: make a new day folder dated before day 1 (photometry.new_day(date=...), which the
     CLI exposes as the hidden --date), capture counter-1 there, re-score.

End to end through photometry.capture / photometry.score_root with the branch's own FakeCamera on
127.0.0.1 and synthetic frames, in a temp folder. No real host is contacted.

    python3 -B probe_posthoc_heldout_edit.py <branch-root>

PASS means the tool refused or flagged the change; FAIL means a FAIL became a PASS silently.
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-photometry"))

import test_photometry as tp  # noqa: E402  (the branch's own workspace, fake camera and worksheet helpers)
import synthetic as syn  # noqa: E402


def capture(ws, fake, day, point, value, auto, illuminant, reading, surface=None):
    fake.scene, fake.auto = tp.scene(value, surface), auto
    _, record, _ = ws.capture(fake, day, point, illuminant, blinds="open" if illuminant == "daylight" else "closed",
                              light="daylight only" if illuminant == "daylight" else "four bulbs")
    assert record["outcome"] == "kept", record
    cells = {"card_box": tp.CARD_BOX}
    if reading:
        cells.update(E_before_lx=reading[0], E_after_lx=reading[1])
    if surface:
        cells["surface_box"] = tp.SURFACE_BOX
    tp.type_row(day, record["capture"], **cells)
    return record["capture"]


def summary(tag, report):
    held = report["result"]["held_out"]
    groups = report["result"]["groups"]
    pts = [(p["capture"], round(p["error_lx"], 2), p["pass"]) for g in groups.values() for p in g["points"]]
    fit = {k: (g["fit"], round(g["k_L"], 6) if g["k_L"] else None) for k, g in groups.items()}
    print(f"RESULT {tag}: gate={report['gate']} held_out={held['result']} fit={fit} points={pts}")
    print(f"RESULT {tag}: unused={[(u['capture'], u['reason']) for u in report['unused']]}")


def flagged(report, words=("score-1", "previous score", "changed", "dropped", "edited")):
    text = json.dumps(report)
    return [w for w in words if w in text]


def scenario_a(fake):
    with tp.workspace() as ws:
        d1 = ws.new_day("20261110")
        capture(ws, fake, d1, "counter-1", 100.0, (10, 100), "bulbs", ("174.533", "174.533"))
        capture(ws, fake, d1, "counter-1", 100.0, (6, 100), "bulbs", None, surface=194.4444)
        capture(ws, fake, d1, "desk", 50.0, (20, 100), "bulbs", ("87.3", "87.2"))
        bad = capture(ws, fake, d1, "sofa", 25.0, (40, 100), "bulbs", ("60", "60"))   # 16 lx off: OUT
        capture(ws, fake, d1, "sofa", 25.0, (40, 100), "bulbs", ("44", "43.8"))
        d2 = ws.new_day("20261111")
        capture(ws, fake, d2, "desk", 80.0, (16, 100), "daylight", ("140", "141"))
        capture(ws, fake, d2, "sofa", 40.0, (32, 100), "daylight", ("70", "70"))
        _, r1 = ws.score()
        summary("A score-1", r1)
        tp.type_row(d1, bad, E_before_lx="", E_after_lx="", card_box="")
        _, r2 = ws.score()
        summary("A score-2", r2)
        silent = r1["gate"] == "fail" and r2["gate"] == "pass" and not flagged(r2)
        print(("FAIL" if silent else "PASS") + " A: a held-out point that failed was dropped after the score "
              "showed it OUT; the re-score passes and names no earlier failure")
        return silent


def scenario_b(fake):
    with tp.workspace() as ws:
        d1 = ws.new_day("20261110")
        capture(ws, fake, d1, "counter-1", 100.0, (10, 100), "bulbs", ("174.533", "174.533"))
        second = capture(ws, fake, d1, "counter-1", 100.0, (10, 100), "bulbs", ("190", "190"))
        capture(ws, fake, d1, "counter-1", 100.0, (6, 100), "bulbs", None, surface=194.4444)
        capture(ws, fake, d1, "desk", 50.0, (20, 100), "bulbs", ("98", "98"))
        capture(ws, fake, d1, "sofa", 25.0, (40, 100), "bulbs", ("48", "48"))
        d2 = ws.new_day("20261111")
        capture(ws, fake, d2, "desk", 80.0, (16, 100), "daylight", ("150", "150"))
        capture(ws, fake, d2, "sofa", 40.0, (32, 100), "daylight", ("75", "75"))
        _, r1 = ws.score()
        summary("B score-1", r1)
        tp.type_row(d1, 1, E_before_lx="", E_after_lx="")   # keep its card box and surface box
        _, r2 = ws.score()
        summary("B score-2", r2)
        fit1 = r1["result"]["groups"]["all"]["fit"]
        fit2 = r2["result"]["groups"]["all"]["fit"]
        silent = r1["gate"] == "fail" and r2["gate"] == "pass" and fit1 != fit2 and not flagged(r2)
        print(("FAIL" if silent else "PASS") + f" B: the fit moved from {fit1} to {fit2} (capture {second}, "
              "excluded by score-1 as a repeat of the fit) after held-out errors were seen; re-score passes")
        return silent


def scenario_c(fake):
    with tp.workspace() as ws:
        d1 = ws.new_day("20261110")
        capture(ws, fake, d1, "counter-1", 100.0, (10, 100), "bulbs", ("174.533", "174.533"))
        capture(ws, fake, d1, "counter-1", 100.0, (6, 100), "bulbs", None, surface=194.4444)
        capture(ws, fake, d1, "desk", 50.0, (20, 100), "bulbs", ("98", "98"))
        capture(ws, fake, d1, "sofa", 25.0, (40, 100), "bulbs", ("48", "48"))
        d2 = ws.new_day("20261111")
        capture(ws, fake, d2, "desk", 80.0, (16, 100), "daylight", ("150", "150"))
        capture(ws, fake, d2, "sofa", 40.0, (32, 100), "daylight", ("75", "75"))
        _, r1 = ws.score()
        summary("C score-1", r1)
        try:
            early = ws.new_day("20261101")      # made after day 1's captures and after a failed score
        except Exception as exc:  # noqa: BLE001
            print(f"PASS C: a day dated before day 1 is refused ({exc})")
            return False
        capture(ws, fake, early, "counter-1", 100.0, (10, 100), "bulbs", ("190", "190"))
        _, r2 = ws.score()
        summary("C score-2", r2)
        fit1, fit2 = r1["result"]["groups"]["all"]["fit"], r2["result"]["groups"]["all"]["fit"]
        moved = fit2.startswith("20261101") and fit1 != fit2
        print(f"RESULT C: day folders now {r2['days']}; fit {fit1} -> {fit2}; score-2 gate {r2['gate']}")
        print(("FAIL" if moved else "PASS") + " C: a day folder dated before day 1, made after a failed score, "
              "was accepted, became day 1 and re-fitted k_L (the old fit is now a held-out point)")
        return moved


def main():
    fake = syn.FakeCamera(tp.scene(100.0))
    try:
        results = [scenario_a(fake), scenario_b(fake), scenario_c(fake)]
    finally:
        fake.close()
    n = sum(results)
    print("RESULT", "FAIL" if n else "PASS", f"({n} of 3 post-hoc changes went through silently)")
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
