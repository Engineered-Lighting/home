#!/usr/bin/env python3
"""Verifier probe for finding mutant-daylight-by-fit-survives (rung 7, tools/den-photometry/card.py:423).

What it checks:
  1. Behaviour: with a single-scope run whose day-1 fit capture (counter-1) is logged under daylight and whose
     held-out captures are all under bulbs, the original coverage() reports the daylight held-out as missing
     (gate incomplete); the M6 mutant (daylight requirement also satisfied by a group's fit capture) does not.
  2. Survival: the WHOLE den-photometry suite (not a narrow subset) is run against the mutant copy with
     failfast; "survived" means every test still passes.

The checkout is never modified: tools/den-photometry, tools/den-light and docs/architecture/live-v1 are copied
into a temp dir (with an empty .git folder, as the suite's photometry-folder test expects a checkout).
Synthetic data and the suite's own 127.0.0.1 fakes only.

Run:
    python3 verify_mutant-daylight-by-fit-survives.py <root of the branch checkout> [--no-suite]
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
RUN_SUITE = "--no-suite" not in sys.argv
OLD = 'if not any(c["illuminant"] == v["daylight_class"] for c in held):'
NEW = ('if not any(c["illuminant"] == v["daylight_class"] for c in held + '
       '[g["fit"] for g in selection["groups"].values() if g["fit"]]):')

SCENARIO = r'''
import math, sys
sys.path.insert(0, sys.argv[1])
import card, synthetic as syn
m = syn.photometry_method()
def box(C):
    return {"usable": True, "problem": None, "C_nc": C, "u_C_nc": 0.0, "clipped_frac": 0.0,
            "invariance_pct": 0.5, "u_invariance_pct": 0.1, "invariance_pass": True, "hold1": {}, "hold2": {}}
def cap(n, day, point, C, illuminant, surface=None):
    E = math.pi * 0.1 * C / 0.18
    return {"id": f"{day}-{n}", "day": day, "order": (day, 1000.0 + n, n), "point": point,
            "illuminant": illuminant, "blinds": "open" if illuminant == "daylight" else "closed", "light": "x",
            "ts": 1793000000.0 + n, "exposure": {"time_ms": 10.0, "gain": 1.0}, "card": box(C),
            "surface": box(surface) if surface else None, "reading": card.meter_reading(E, E, m), "note": ""}
caps = [cap(1, "20261110", "counter-1", 100, "daylight", surface=194.4444),  # the fit, under daylight
        cap(2, "20261110", "desk", 50, "bulbs"), cap(3, "20261110", "sofa", 30, "bulbs"),
        cap(4, "20261111", "desk", 80, "bulbs"), cap(5, "20261111", "sofa", 40, "bulbs")]
r = card.score(caps, m, ["meter-norm/v1"])
print("fit:", r["groups"]["all"]["fit"], "| held_out:", r["held_out"]["result"], "| missing:",
      r["held_out"]["missing"], "| gate:", r["gate"])
'''


def copy_tree(dst):
    for sub in ("tools/den-photometry", "tools/den-light", "docs/architecture/live-v1"):
        shutil.copytree(ROOT / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
    (dst / ".git").mkdir()


def scenario(co):
    proc = subprocess.run([sys.executable, "-B", "-c", SCENARIO, str(co / "tools/den-photometry")],
                          capture_output=True, text=True, timeout=300)
    return (proc.stdout + proc.stderr).strip()


with tempfile.TemporaryDirectory(prefix="r7verify-m6-") as tmp:
    tmp = Path(tmp)
    orig, mut = tmp / "orig", tmp / "mut"
    copy_tree(orig)
    copy_tree(mut)
    target = mut / "tools/den-photometry/card.py"
    text = target.read_text(encoding="utf-8")
    assert text.count(OLD) == 1, "mutant pattern not found exactly once"
    target.write_text(text.replace(OLD, NEW), encoding="utf-8")
    o, mm = scenario(orig), scenario(mut)
    print("ORIGINAL:", o)
    print("MUTANT:  ", mm)
    differs = ("daylight" in o and "incomplete" in o) and ("gate: pass" in mm)
    print("RESULT behaviour: " + ("mutant changes the verdict incomplete -> pass" if differs
                                  else "no verdict change seen"))
    if RUN_SUITE:
        start = time.time()
        proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s",
                               "tools/den-photometry"], cwd=mut, capture_output=True, text=True, timeout=1800)
        tail = (proc.stderr or proc.stdout).strip().splitlines()
        first_fail = next((l for l in tail if l.startswith(("FAIL:", "ERROR:"))), "")
        ran = next((l for l in tail if l.startswith("Ran ")), "")
        print(f"full suite on mutant: rc={proc.returncode} {ran} last={tail[-1] if tail else ''!r} "
              f"first_fail={first_fail!r} secs={time.time() - start:.0f}")
        print("RESULT suite: " + ("SURVIVED (whole suite passes with the mutant)" if proc.returncode == 0
                                  else "CAUGHT"))
