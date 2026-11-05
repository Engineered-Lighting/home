#!/usr/bin/env python3
"""verify_daylight-coverage-one-capture: does ONE held-out capture logged `--illuminant daylight --blinds closed`
complete rung 7's daylight coverage, so the gate prints PASS with no daylight-day series?

    python3 -B verify_daylight-coverage-one-capture.py <branch-root>

Copies tools/den-photometry, tools/den-light and docs/architecture/live-v1 to a temp dir and drives the real
photometry.capture/score_root with the tests' own fake camera on 127.0.0.1 and synthetic frames.
Day 1 (bulbs): counter-1 fit + its reflectance, desk, sofa. Day 2: a single capture, desk, daylight, blinds closed.
"""
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
SCRIPT = textwrap.dedent('''
    import test_photometry as tp  # imports card first (den-light on the path)
    import photometry, sweep
    photometry.check_labels("desk", "daylight", "closed", "daylight only", tp.syn.photometry_method())
    print("LABELS daylight + blinds closed accepted by check_labels")
    t = tp.ScoreTests("test_two_days_pass_and_write_the_records")
    t.setUp()
    try:
        with tp.workspace() as ws:
            day1 = ws.new_day("20261110")
            day2 = ws.new_day("20261111")
            plan = [(day1, "counter-1", 100.0, None, (10, 100), "bulbs", "closed", ("174.533", "174.533")),
                    (day1, "counter-1", 100.0, 194.4444, (6, 100), "bulbs", "closed", None),
                    (day1, "desk", 50.0, None, (20, 100), "bulbs", "closed", ("87.3", "87.2")),
                    (day1, "sofa", 25.0, None, (40, 100), "bulbs", "closed", ("44", "43.8")),
                    (day2, "desk", 80.0, None, (16, 100), "daylight", "closed", ("140", "141"))]
            for day, point, value, surface, auto, illuminant, blinds, reading in plan:
                t.set_scene(value, auto, surface)
                folder, record, _ = ws.capture(t.fake, day, point, illuminant, blinds=blinds, light="as it is")
                assert record["outcome"] == "kept", record
                cells = {"card_box": tp.CARD_BOX}
                if reading:
                    cells.update(E_before_lx=reading[0], E_after_lx=reading[1])
                if surface:
                    cells["surface_box"] = tp.SURFACE_BOX
                tp.type_row(day, record["capture"], **cells)
            folder, report = ws.score()
            res = report["result"]
            daylight = [p for g in res["groups"].values() for p in g["points"] if p["illuminant"] == "daylight"]
            print("DAYLIGHT held-out points:", len(daylight), "missing:", res["held_out"]["missing"])
            print("GATE", report["gate"].upper())
    finally:
        t.doCleanups()
''')

tmp = Path(tempfile.mkdtemp(prefix="r7-daylight-"))
for rel in ("tools/den-photometry", "tools/den-light", "docs/architecture/live-v1"):
    shutil.copytree(ROOT / rel, tmp / rel, ignore=shutil.ignore_patterns("__pycache__"))
(tmp / ".git").mkdir()
p = subprocess.run([sys.executable, "-B", "-c", SCRIPT], cwd=tmp / "tools/den-photometry", capture_output=True,
                   text=True, timeout=1200)
print(p.stdout.strip() or p.stderr.strip()[-1500:])
ok = "GATE PASS" in p.stdout and "DAYLIGHT held-out points: 1 " in p.stdout
print("RESULT one daylight capture behind closed blinds completes coverage and the gate passes: " + ("YES" if ok else "NO"))
shutil.rmtree(tmp)
