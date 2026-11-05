#!/usr/bin/env python3
"""Probe: score.py --split held_out accepts any value for GATE-V1's FIXED numbers, and a gate that is not
committed at all, so a verdict can be flipped by editing a "never a slot" value.

What it checks (synthetic N1 world built with the branch's own test helpers, temp dirs only, fake check.py):
  A. Two held-out sessions, canonical fixed values -> insufficient (units.fine unmet).
     Same data, same filled slots, but sample.units/sessions/days = 1 in the gate -> feasible, no refusal.
  B. Four held-out sessions, floor.competence.fine 0.95 -> insufficient (straddled, the test suite's case).
     Same data and slots, fixed interval.level 0.95 -> 0.5 -> feasible, no refusal.
  C. In both runs the gate file is outside any git repo (never committed): report gate.commit is None and
     the run still scores; the RESULTS.md line carries only the gate's sha256. A second held-out run on the
     same labels also scores (nothing marks the held-out set as opened).

Run:  python3 -B probe_gate-fixed-values-and-order-unenforced.py <root of the claude/den-n2 checkout>
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
import test_den_n2 as T  # noqa: E402  the branch's own synthetic world builder
score = T.score


def gate_with(**changes):
    g = T.filled()
    values = dict(g.values)
    values.update(changes)
    return score.Gate("GATE-V1.md", "d" * 64, values)


ok = True
tmp = Path(tempfile.mkdtemp(prefix="n2-probe-gate-"))
try:
    world = T.World(tmp / "a", T.FOUR_DAYS[:4])
    report, _ = world.run("held_out")
    print(f"RESULT A canonical fixed values: verdict={report['verdict']} reasons={report['reasons'][:2]}")
    world.gate.write_text(T.gate_text(gate_with(**{"sample.units": 1, "sample.sessions": 1, "sample.days": 1})),
                          encoding="utf-8")
    report2, path2 = world.run("held_out")
    print(f"RESULT A edited fixed sample.* = 1: verdict={report2['verdict']} gate.commit={report2['gate']['commit']}"
          f" dirty={report2['gate']['dirty']}")
    print("RESULT A RESULTS.md line: " + score.results_line(report2, path2))
    a = report["verdict"] == "insufficient" and report2["verdict"] == "feasible"
    print(("FAIL" if a else "PASS") + " A: an edited fixed sample gate turns insufficient into feasible without a refusal")
    ok &= not a

    world = T.World(tmp / "b", T.FOUR_DAYS)
    world.gate.write_text(T.gate_text(gate_with(**{"floor.competence.fine": 0.95})), encoding="utf-8")
    report, _ = world.run("held_out")
    print(f"RESULT B interval.level 0.95: verdict={report['verdict']} "
          f"competence.fine={report['metrics']['competence.fine']['interval']} "
          f"{report['metrics']['competence.fine']['status']}")
    world.gate.write_text(T.gate_text(gate_with(**{"floor.competence.fine": 0.95, "interval.level": 0.5})),
                          encoding="utf-8")
    report2, _ = world.run("held_out")
    print(f"RESULT B interval.level 0.5: verdict={report2['verdict']} "
          f"competence.fine={report2['metrics']['competence.fine']['interval']} "
          f"{report2['metrics']['competence.fine']['status']}")
    b = report["verdict"] == "insufficient" and report2["verdict"] == "feasible"
    print(("FAIL" if b else "PASS") + " B: an edited fixed interval.level turns insufficient into feasible without a refusal")
    ok &= not b
    c = report2["gate"]["commit"] is None
    print(("FAIL" if c else "PASS") + " C: a gate that was never committed still gets a held-out verdict "
          f"(gate.commit={report2['gate']['commit']}); held-out runs in this world: "
          f"{len(list((world.n2 / 'eval').glob('held_out-*.json')))}")
    ok &= not c
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("RESULT " + ("no defect seen" if ok else "defect reproduced: fixed values and the gate's commit order are not enforced"))
