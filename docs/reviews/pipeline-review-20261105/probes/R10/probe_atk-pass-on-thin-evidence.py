"""Probe (attack lens): the hit-rate verdict is the pooled point estimate alone.

What it checks
  score.py hit_rate() returns "pass" when hits/n >= 0.85 for any n >= 1. The
  owner's two-sided 95% session-bootstrap interval (owner choice 6) and the
  margin delta_hit_rate (a slot that must be closed before a scored run) are
  reported but never enter the verdict, and there is no minimum count.
    1. one scored point, within: verdict pass, no interval;
    2. two sessions at 20/20 and 14/20 (pooled 34/40 = 0.85): verdict pass with
       interval [0.70, 1.00], i.e. the data cannot rule out a 70% hit rate;
    3. delta_hit_rate 0.05 vs 0.30: same verdict (the closed slot changes nothing).

Run
  python3 probe_atk-pass-on-thin-evidence.py <den-verify branch checkout root>
Synthetic data only; no network; writes nothing.
"""
import sys

sys.dont_write_bytecode = True
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-verify"))

import method  # noqa: E402
from score import Run  # noqa: E402

TEXT = method.lf_text(ROOT / "tools/den-verify/VERIFY-V1.md")


def hr(days, **over):
    m = method.parse(method.closed_copy(TEXT, over)).scored()
    return Run.hit_rate(type("R", (), {"m": m})(), days)


a = hr({1: (1, 1)})
b = hr({1: (20, 20), 2: (14, 20)})
c = hr({1: (20, 20), 2: (14, 20)}, delta_hit_rate=0.30)
print(f"RESULT one point: verdict={a['verdict']} interval={a['interval']} ({a['interval_status']})")
print(f"RESULT 20/20 + 14/20: value={b['value']} verdict={b['verdict']} interval={b['interval']}")
print(f"RESULT same with delta 0.30: verdict={c['verdict']} delta={c['delta']}")
bad = a["verdict"] == "pass" and b["verdict"] == "pass" and b["interval"][0] < 0.85 and c["verdict"] == "pass"
print("FAIL: pass on one point, and pass with a 95% interval reaching 0.70; delta never used" if bad else "PASS")
