#!/usr/bin/env python3
"""Verify (independent of the reporter's probe): score.py bins a unit's demand confidence with
int(conf * 10) (session_stats), where conf is the plain float mean of identical per-second values
(make_unit -> mean()). For n = 24 seconds of 0.7 the mean is 0.6999999999999998 and the unit lands in bin 6,
not bin 7. This checks (1) the arithmetic for every decile and n in 1..60, and (2) whether the misbinning
can turn a failing demand ECE into a passing one (cap 0.1), using score.py's own analyse/session_stats/m_ece.

Session (synthetic, one segment, in memory): 13 fine tasks of 30 s read fine with demand confidence 0.7
every second (right), and 6 casual tasks of 30 s read fine with demand confidence 0.65 (wrong). Exact ECE
uses the decimal values every second carried (Fractions).

Run:  python3 -B verify_ece-mean-at-decile-edge-misbinned.py <root of the claude/den-n2 checkout>
"""
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
sys.path.insert(0, str(ROOT / "tools" / "den-label"))
import score  # noqa: E402

drift = {d: [n for n in range(1, 61) if int(score.mean([d] * n) * 10) < round(d * 10)]
         for d in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)}
print(f"RESULT score.mean of n copies of a decile, n in 1..60, that lands one bin low: {drift}")
print(f"RESULT score.mean([0.7]*24) = {score.mean([0.7] * 24)!r} -> bin {int(score.mean([0.7] * 24) * 10)}")

GATE = score.load_gate()
ivs, recs, t = [], [score.Rec(0.5, "none", 0.9, 0.2, 0.9, 0.0)], 100.0
plan = [("fine", 0.7)] * 13 + [("casual", 0.65)] * 6
for band, conf in plan:
    ivs.append({"on": t, "off": t + 30, "band": band, "case": "sorting_mail" if band == "fine" else "wiping",
                "seg": 0, "onset_kind": "touch", "offset_kind": "release",
                "sure": {"onset": True, "offset": True, "band": True}})
    for k in range(int(t) + 1, int(t) + 31):
        recs.append(score.Rec(float(k), "engaged", 0.9, 0.8, conf, k - 1.0))
    recs.append(score.Rec(t + 31, "none", 0.9, 0.2, 0.9, t + 30))
    t += 130
s = score.Session("v1", "development", 1_793_016_000.0, "2026-10-26", [(0.0, t)], ivs, [])
s.logs = [recs]
an = score.analyse(s, GATE, 4)
units = [u for u in an.units if u.kind == "interval"]
st = score.session_stats(an, GATE)
print("RESULT score.py demand bins (n, conf_sum, right): "
      + str({b: (x[0], round(x[1], 4), x[2]) for b, x in enumerate(st["ece"]["demand"]) if x[0]}))
tool = score.m_ece("demand")([st])
bins = {}
for u, (band, conf) in zip(units, plan):
    c = Fraction(str(conf))
    b = min(int(c * 10), 9)
    n, cs, a = bins.get(b, (0, Fraction(0), 0))
    bins[b] = (n + 1, cs + c, a + (u.band == u.gold_band))
exact = float(sum(abs(Fraction(a) - cs) for n, cs, a in bins.values()) / sum(n for n, _, _ in bins.values()))
st_tool = score.judge("cap", 0.1, tool, [tool, tool])
st_exact = score.judge("cap", 0.1, exact, [exact, exact])
print(f"RESULT demand ECE score.py={tool:.4f} ({st_tool}) exact={exact:.4f} ({st_exact}); cap 0.1")
print(("FAIL" if st_tool != st_exact else "PASS") + ": the decile-edge misbinning changes the ECE gate status")
