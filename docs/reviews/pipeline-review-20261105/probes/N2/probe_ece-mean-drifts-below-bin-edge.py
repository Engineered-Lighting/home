#!/usr/bin/env python3
"""Probe: score.py bins a unit's ECE confidence with int(conf * 10), where conf is the float MEAN of the
unit's per-second confidences. When every second carries the same table value at a decile edge (need v1's
confidence_table gives one value per label and row), the float mean drifts just below the edge and the unit
lands one bin low. A 30 s interval has exactly 24 demand seconds after the 6 s grace, and the mean of 24
copies of 0.7 is 0.6999999999999998 (bin 6, not bin 7). Mixed into a neighbouring bin, gaps of opposite
sign cancel and the ECE is understated.

Session (synthetic): 5 fine intervals read fine and right, demand confidence 0.7 every second; 5 casual
intervals read fine (wrong), demand confidence 0.65. Exact ECE (decimal bins) vs score.py's.

Run:  python3 -B probe_ece-mean-drifts-below-bin-edge.py <root of the claude/den-n2 checkout>
"""
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
sys.path.insert(0, str(ROOT / "tools" / "den-label"))
import score  # noqa: E402

GATE = score.load_gate()
ivs, recs, t = [], [score.Rec(0.5, "none", 0.9, 0.2, 0.9, 0.0)], 100.0
for i in range(10):
    fine = i < 5
    ivs.append({"on": t, "off": t + 30, "band": "fine" if fine else "casual", "case": "sorting_mail" if fine
                else "wiping", "seg": 0, "onset_kind": "touch", "offset_kind": "release",
                "sure": {"onset": True, "offset": True, "band": True}})
    recs.append(score.Rec(t + 1, "engaged", 0.9, 0.8, 0.7 if fine else 0.65, t))
    for k in range(int(t) + 2, int(t) + 31):
        recs.append(score.Rec(float(k), "engaged", 0.9, 0.8, 0.7 if fine else 0.65, k - 1.0))
    recs.append(score.Rec(t + 31, "none", 0.9, 0.2, 0.9, t + 30))
    t += 130
s = score.Session("c1", "development", 1_793_016_000.0, "2026-10-26", [(0.0, t)], ivs, [])
s.logs = [recs]
an = score.analyse(s, GATE, 4)
units = [u for u in an.units if u.kind == "interval"]
print(f"RESULT demand confidences of the fine units: {sorted({u.dem_conf for u in units if u.gold_band == 'fine'})}")
tool = score.m_ece("demand")([score.session_stats(an, GATE)])
groups = {}
for u in units:
    c = Fraction(str(round(u.dem_conf, 9)))  # the decimal value every second carried
    b = min(int(c * 10), 9)
    n, cs, a = groups.get(b, (0, Fraction(0), 0))
    groups[b] = (n + 1, cs + c, a + (u.band == u.gold_band))
exact = float(sum(abs(Fraction(a) - cs) for n, cs, a in groups.values()) / sum(n for n, _, _ in groups.values()))
print(f"RESULT demand ECE score.py={tool:.4f} exact={exact:.4f} (cap 0.1)")
print(("FAIL" if abs(tool - exact) > 1e-6 else "PASS") + " ECE binning of a mean confidence at a decile edge")
