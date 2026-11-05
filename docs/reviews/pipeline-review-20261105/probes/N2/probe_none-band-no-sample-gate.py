#!/usr/bin/env python3
"""Probe: N2's sample gates require >= 30 units for the fine and casual bands only. The owner's numbers
(owner-choices.md, demand-gates) are ">= 30 episodes per band from >= 3 sessions over >= 2 days", and the
canon EvalRecord counts episodes_per_band for fine, casual AND none. GATE-V1's own prose says "at least 30
units per band". A held-out set with far fewer than 30 none-band (quiet) units still gets `feasible`.

What it checks (synthetic world from the branch's own test helpers, temp dirs only, fake check.py):
  the suite's perfect follower on 4 held-out sessions; counts held-out none-band units; lists the sample
  gates score.py applied; prints the verdict.

Run:  python3 -B probe_none-band-no-sample-gate.py <root of the claude/den-n2 checkout>
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
import test_den_n2 as T  # noqa: E402
score = T.score

tmp = Path(tempfile.mkdtemp(prefix="n2-probe-none-"))
try:
    world = T.World(tmp, T.FOUR_DAYS)
    report, _ = world.run("held_out")
    held = set(world.held_out())
    # recount none-band units the way score.py builds them, on the held-out sessions it scored
    gold, meta, sessions = score.load_n1(world.n1, "held_out", world.freezes.read_text(encoding="utf-8"))
    gate = score.load_gate(world.gate)
    none_units = 0
    for s in sessions:
        s.asks = score.load_asks(world.n2, s, meta["frozen_unix"])
        score.load_logs(world.n2, s, T.OK_CHECK)
        an = score.analyse(s, gate, 4)
        none_units += sum(1 for u in an.units if u.gold_band == "none")
    names = [g["name"] for g in report["sample_gates"] if g["name"].startswith("units.")]
    print(f"RESULT held-out sessions={len(held)} units gates applied={names} none-band units={none_units} "
          f"(owner: >= 30 per band) verdict={report['verdict']}")
    bad = report["verdict"] == "feasible" and none_units < 30 and "units.none" not in names
    print(("FAIL" if bad else "PASS") + " a held-out set with fewer than 30 none-band units is scored feasible")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
