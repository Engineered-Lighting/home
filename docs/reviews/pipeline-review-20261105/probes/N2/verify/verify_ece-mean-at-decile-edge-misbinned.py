#!/usr/bin/env python3
"""Verifier probe for finding ece-mean-at-decile-edge-misbinned.

The finding's own probe shows score.py's demand ECE 0.175 against 0.475 when every second carries 0.7:
both above the 0.1 cap, so it shows a misbin but not a pass/fail flip. This probe checks whether the
misbin can flip the ECE status across the cap, at the metric level, with 4 identical sessions (so the
session bootstrap interval is degenerate and the status equals the point value's).

Session (synthetic): 4 fine intervals read fine (right), demand confidence 0.65 every second; 2 fine
intervals read casual (wrong), casual confidence 0.7 every second (24 demand seconds after the 6 s grace).
Exact binning puts the 0.7 units in bin 7; score.py's float mean (0.6999999999999998) puts them in bin 6,
where their negative gap cancels the 0.65 units' positive gap.

Synthetic data only, no files, no network.
Run:  python3 -B verify_ece-mean-at-decile-edge-misbinned.py <root of the claude/den-n2 checkout>
"""
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
sys.path.insert(0, str(ROOT / "tools" / "den-label"))
import score  # noqa: E402

GATE = score.load_gate()


def session(take):
    ivs, recs, t = [], [score.Rec(0.5, "none", 0.9, 0.2, 0.9, 0.0)], 100.0
    for i in range(6):
        right = i < 4
        ivs.append({"on": t, "off": t + 30, "band": "fine", "case": "sorting_mail", "seg": 0,
                    "onset_kind": "touch", "offset_kind": "release",
                    "sure": {"onset": True, "offset": True, "band": True}})
        level, conf = (0.8, 0.65) if right else (0.5, 0.7)
        recs.append(score.Rec(t + 1, "engaged", 0.9, level, conf, t))
        for k in range(int(t) + 2, int(t) + 31):
            recs.append(score.Rec(float(k), "engaged", 0.9, level, conf, k - 1.0))
        recs.append(score.Rec(t + 31, "none", 0.9, 0.2, 0.9, t + 30))
        t += 130
    s = score.Session(take, "development", 1_793_016_000.0, "2026-10-26", [(0.0, t)], ivs, [])
    s.logs = [recs]
    return score.analyse(s, GATE, 4)


ans = [session(f"c{i}") for i in range(4)]
stats = [score.session_stats(an, GATE) for an in ans]
units = [u for an in ans for u in an.units if u.kind == "interval"]
print(f"RESULT unit demand confidences: {sorted({u.dem_conf for u in units})}")
tool = score.m_ece("demand")(stats)
draws = score.resamples(len(stats), 2000, "ab" * 32)
ci = score.bootstrap(stats, score.m_ece("demand"), draws, 0.95)
groups = {}
for u in units:
    c = Fraction(str(round(u.dem_conf, 9)))
    b = min(int(c * 10), 9)
    n, cs, a = groups.get(b, (0, Fraction(0), 0))
    groups[b] = (n + 1, cs + c, a + (u.band == u.gold_band))
exact = float(sum(abs(Fraction(a) - cs) for n, cs, a in groups.values()) / sum(n for n, _, _ in groups.values()))
print(f"RESULT demand ECE score.py={tool:.4f} interval={[round(v, 4) for v in ci]} "
      f"status={score.judge('cap', 0.1, tool, ci)}; exact={exact:.4f} status={'met' if exact <= 0.1 else 'failed'}")
flip = score.judge("cap", 0.1, tool, ci) == "met" and exact > 0.1
print(("FAIL" if flip else "PASS") + " misbinned decile-edge mean flips demand ECE from failed to met (cap 0.1)")
