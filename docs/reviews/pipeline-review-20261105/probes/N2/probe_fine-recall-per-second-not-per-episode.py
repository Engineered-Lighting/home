#!/usr/bin/env python3
"""Probe: score.py's fine_recall is per SECOND and its coverage is POOLED over fine and casual units, while
the ladder (rung 6, whose gate re-runs N2's frozen gate on production need) defines "fine-demand recall per
episode (abstaining or stale counts as a miss)" and "per-band coverage", and the canon EvalRecord carries
coverage_per_band. Long caught episodes outweigh short missed ones in a per-second rate.

Session (synthetic, one segment): 3 long fine episodes (60 s) read fine and fresh throughout, 3 short fine
episodes (12 s) read casual (missed); 4 casual episodes with no level at all (abstained).
Compares score.py's numbers with per-episode recall and per-band coverage computed here.

Run:  python3 -B probe_fine-recall-per-second-not-per-episode.py <root of the claude/den-n2 checkout>
"""
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
sys.path.insert(0, str(ROOT / "tools" / "den-label"))
import score  # noqa: E402

GATE = score.load_gate()


def iv(on, off, band, case):
    return {"on": on, "off": off, "band": band, "case": case, "seg": 0, "onset_kind": "touch",
            "offset_kind": "release", "sure": {"onset": True, "offset": True, "band": True}}


ivs, recs = [], [score.Rec(0.5, "none", 0.9, 0.2, 0.9, 0.0)]
t = 100.0
for i in range(6):
    long_ = i < 3
    off = t + (60 if long_ else 12)
    ivs.append(iv(t, off, "fine", "still_reader" if long_ else "sorting_mail"))
    k = t + 1
    while k < off + 1:  # a record every 2 s with a fresh level (capture 1 s before)
        recs.append(score.Rec(k, "engaged", 0.9, 0.8 if long_ else 0.5, 0.9, k - 1))
        k += 2
    recs.append(score.Rec(off + 1, "none", 0.9, 0.2, 0.9, off))
    t = off + 100
for i in range(4):  # casual episodes with no level (abstained): engaged, level None
    ivs.append(iv(t, t + 30, "casual", "wiping"))
    recs.append(score.Rec(t + 1, "engaged", 0.9, None, None, None))
    recs.append(score.Rec(t + 31, "none", 0.9, 0.2, 0.9, t + 30))
    t += 130
s = score.Session("e1", "development", 1_793_016_000.0, "2026-10-26", [(0.0, t + 100)], ivs, [])
s.logs = [recs]
an = score.analyse(s, GATE, 4)
st = score.session_stats(an, GATE)
fr = score.m_ratio("fine_recall")([st])
cov = score.m_list("coverage", score.mean)([st])
fine_units = [u for u in an.units if u.gold_band == "fine"]
per_episode = sum(1 for u in fine_units if u.band == "fine") / len(fine_units)
per_band = {b: score.mean([u.coverage for u in an.units if u.gold_band == b]) for b in ("fine", "casual")}
print(f"RESULT score.py fine_recall (per second) = {fr:.3f}; per episode = {per_episode:.3f}")
print(f"RESULT score.py coverage (pooled) = {cov:.3f}; per band = {per_band}")
bad = fr >= 0.6 > per_episode
print(("FAIL" if bad else "PASS") + " with a 0.6 floor the per-second recall passes while half the fine episodes "
      "were missed; the pooled coverage hides a casual band with coverage 0")
