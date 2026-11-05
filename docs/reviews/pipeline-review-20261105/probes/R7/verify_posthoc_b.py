#!/usr/bin/env python3
"""verify_posthoc_b (verifier): re-runs scenario B of probe_posthoc_heldout_edit.py (blank the day-1 fit row
so another day-1 counter-1 capture becomes the fit) N times and prints, for each score, the gate, the
exposure-invariance result and its worst boxes, and the k_L record's supersedes fields, to see whether the
FAIL->PASS flip is deterministic and whether score-2's calibration record carries a trace of the old fit.

Synthetic frames, the branch's own FakeCamera on 127.0.0.1, temp folders only.

    python3 -B verify_posthoc_b.py <branch-root> [runs]
"""
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
RUNS = int(sys.argv[2]) if len(sys.argv) > 2 else 2
sys.path.insert(0, str(ROOT / "tools" / "den-photometry"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_photometry as tp  # noqa: E402
import synthetic as syn  # noqa: E402
import probe_posthoc_heldout_edit as p  # noqa: E402


def show(tag, folder, report):
    inv = report["result"]["exposure_invariance"]
    bad = [(b["capture"], b["box"], round(b["d_pct"], 2)) for b in inv["boxes"] if not b["pass"]]
    print(f"RESULT {tag}: gate={report['gate']} invariance={inv['result']} worst={inv['worst_pct']} failing_boxes={bad}")
    recs = [json.loads(l) for l in (folder / "calibration_record.jsonl").read_text().splitlines()]
    for r in recs:
        if r["parameter"].endswith("photometric.k_L"):
            print(f"RESULT {tag}: k_L record value={r['value']} supersedes={r.get('supersedes')} "
                  f"supersedes_value={r.get('supersedes_value')}")


def run_b(fake):
    with tp.workspace() as ws:
        d1 = ws.new_day("20261110")
        p.capture(ws, fake, d1, "counter-1", 100.0, (10, 100), "bulbs", ("174.533", "174.533"))
        p.capture(ws, fake, d1, "counter-1", 100.0, (10, 100), "bulbs", ("190", "190"))
        p.capture(ws, fake, d1, "counter-1", 100.0, (6, 100), "bulbs", None, surface=194.4444)
        p.capture(ws, fake, d1, "desk", 50.0, (20, 100), "bulbs", ("98", "98"))
        p.capture(ws, fake, d1, "sofa", 25.0, (40, 100), "bulbs", ("48", "48"))
        d2 = ws.new_day("20261111")
        p.capture(ws, fake, d2, "desk", 80.0, (16, 100), "daylight", ("150", "150"))
        p.capture(ws, fake, d2, "sofa", 40.0, (32, 100), "daylight", ("75", "75"))
        f1, r1 = ws.score()
        p.summary("B score-1", r1)
        show("B score-1", f1, r1)
        tp.type_row(d1, 1, E_before_lx="", E_after_lx="")
        f2, r2 = ws.score()
        p.summary("B score-2", r2)
        show("B score-2", f2, r2)
        return r1["gate"], r2["gate"]


def main():
    fake = syn.FakeCamera(tp.scene(100.0))
    out = []
    try:
        for i in range(RUNS):
            out.append(run_b(fake))
    finally:
        fake.close()
    print("RESULT gates per run (score-1, score-2):", out)


if __name__ == "__main__":
    main()
