#!/usr/bin/env python3
"""probe_stats-recompute: rung 10's key statistics recomputed by hand and compared with den-verify.

What it checks (synthetic only, no network, nothing written outside temp):
  1. tolerance boundary relative to the PREDICTION: exactly 15% (230 vs 200),
     15.0001% (230.0002 vs 200), and 85 vs 100 (15% of the prediction, 17.6%
     of the measurement), through the full scorer, not just within();
  2. case V11: (357 - 368) / 368, the tool's residual_pct against the spec's -2.9 %;
  3. a zero-lux prediction (an all-off scene after dark);
  4. the all-excluded case (every point unverifiable) and no decisions at all;
  5. the session-bootstrap interval for three sessions, against the exact
     bootstrap distribution enumerated over all 27 equally likely resamples;
  6. delta_hit_rate: closing it at different values, does the verdict or the
     report's delta_status change?
Each comparison prints MATCH or MISMATCH; FAIL lines mark defects.

Run:
    python3 probe_stats-recompute.py <root of the rung-10-verify checkout>
"""
import itertools
import math
import sys
from fractions import Fraction
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-verify"))
import method  # noqa: E402
import synthetic as syn  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock  # noqa: E402
from score import Run, within  # noqa: E402

B0 = syn.HUE[0]
TEXT = method.lf_text(root / "tools" / "den-verify" / "VERIFY-V1.md")


def closed(over=None):
    return method.parse(method.closed_copy(TEXT, over)).scored()


def show(label, tool, hand, ok):
    print(f"{'MATCH' if ok else 'MISMATCH'} {label}: tool={tool} hand={hand}")
    return ok


def one_point_case(pred_full, measured, before_level=0.0, after_level=1.0, ref_value=0.0, over=None):
    """One bulb, one point, ref frame at before_level; verify frame value `measured`."""
    model = syn.hue_model({"p": {B0: pred_full}}, kelvins=(2700,))
    t = syn.T0
    ref = syn.light_state(t, {"p": ref_value}, anchored=False, ident="ls-ref")
    dec = syn.hue_decision("d", t + 0.5, {B0: before_level}, {B0: {"level": after_level,
                           "steps": [{"kind": "send", "sent_at": t + 0.5}]}}, "ls-ref",
                           targets={"A": {"surface_id": "p"}})
    after = syn.light_state(t + 5, {"p": measured}, anchored=False)
    d, s, r = Run(closed(over), LightModel(model), [dec], [ref, after], clock=Clock(syn.UTC_OFFSET_S)).run()
    return d[0]["verify"]["points"][0], r


bad = 0
# 1. boundary, hand rule: |m - p| <= 0.15 * p  (relative to the prediction), exact rationals
for pred, meas in ((200.0, 230.0), (200.0, 230.0002), (100.0, 85.0), (100.0, 115.0), (100.0, 115.0001)):
    hand = abs(Fraction(meas) - Fraction(pred)) <= Fraction(3, 20) * Fraction(pred)
    pt, _ = one_point_case(pred, meas)
    tool = pt["status"] == "within"
    bad += not show(f"boundary pred={pred} meas={meas} (residual {(meas - pred) / pred:+.6%})",
                    pt["status"], "within" if hand else "outside", tool == hand)
# relative to the measurement would make 85 vs 100 outside (15/85 = 17.6%): the tool uses the prediction
print(f"RESULT 85 vs 100 relative to the measurement would be {15 / 85:.4f} -> outside; the tool says within "
      f"(relative to the prediction, as VERIFY-V1 and schemas.json define residual)")

# 2. V11
hand = (357 - 368) / 368
print(f"RESULT V11 hand residual {hand:.6f} = {100 * hand:.4f}%: 2 dp {round(100 * hand, 2)}, "
      f"1 dp {round(100 * hand, 1)}, truncated {math.trunc(1000 * hand) / 10}")
d, f, c = syn.v11_evening()
_, states, _ = Run(closed(), LightModel(syn.v11_model()), d, f, corrections=c, clock=Clock(syn.UTC_OFFSET_S)).run()
ls = next(s for s in states if s["ts"] == syn.T0 + 40.0)
r = next(e for e in ls["residual"] if e["surface_id"] == "counter-1")
bad += not show("V11 residual_pct", r["residual_pct"], round(100 * hand, 2), r["residual_pct"] == round(100 * hand, 2))
print(f"RESULT V11 spec ladder says -2.9 %; correctly rounded to 1 dp it is {round(100 * hand, 1)} %; tool flags {r['flags']}")

# 3. zero-lux prediction: an off scene after dark
pt, rep = one_point_case(400.0, 0.0, before_level=0.5, after_level=0.0, ref_value=200.0)
bad += not show("zero prediction status", pt["status"], "unverifiable (relative residual undefined)",
                pt["status"] == "unverifiable")
pt, rep = one_point_case(400.0, 150.0, before_level=0.5, after_level=0.0, ref_value=200.0)
print(f"RESULT off scene where the bulb stayed on (150 measured, 0 predicted): status {pt['status']}, "
      f"verdict {rep['hit_rate']['verdict']} -> a failed OFF is never a miss")

# 4. all excluded / no decisions
model = syn.hue_model({"p": {B0: 100.0}}, kelvins=(2700,))
t = syn.T0
ref = syn.light_state(t, {"p": 0.0}, anchored=False, ident="ls-ref")
dec = syn.hue_decision("d", t + 0.5, {B0: 0.0}, {B0: {"level": 1.0, "steps": [{"kind": "send", "sent_at": t + 0.5}]}},
                       "ls-ref", targets={"A": {"surface_id": "p"}})
after = syn.light_state(t + 5, {"p": 100.0}, anchored=False, clipped={"p": 0.9})   # usable share 0.1 < v_min
_, _, rep = Run(closed(), LightModel(model), [dec], [ref, after], clock=Clock(syn.UTC_OFFSET_S)).run()
bad += not show("all points unverifiable verdict", rep["hit_rate"]["verdict"], "insufficient (no evidence)",
                rep["hit_rate"]["verdict"] == "insufficient")
_, _, rep = Run(closed(), LightModel(model), [], [ref, after], clock=Clock(syn.UTC_OFFSET_S)).run()
bad += not show("no decisions verdict", rep["hit_rate"]["verdict"], "insufficient (no evidence)",
                rep["hit_rate"]["verdict"] == "insufficient")

# 5. session bootstrap, exact enumeration
sessions = {1: (9, 10), 2: (7, 10), 3: (10, 12)}
fake = type("R", (), {"m": closed()})()
hr = Run.hit_rate(fake, sessions)
vals = list(sessions.values())
dist = []
for pick in itertools.product(vals, repeat=3):
    dist.append(sum(h for h, _ in pick) / sum(n for _, n in pick))
dist.sort()
lo_exact = dist[0]
# exact 2.5% / 97.5% quantiles of the discrete bootstrap distribution (27 equally likely)
cdf = lambda x: sum(v <= x + 1e-12 for v in dist) / 27
q = lambda p: min(v for v in dist if cdf(v) >= p)
lo, hi = q(0.025), q(0.975)
# atoms bracketing each quantile: a Monte Carlo percentile with interpolation must fall between them
below_lo = max([v for v in dist if v < lo], default=lo)
above_hi = min([v for v in dist if v > hi], default=hi)
ok_lo = below_lo - 1e-4 <= hr["interval"][0] <= lo + 1e-4
ok_hi = hi - 1e-4 <= hr["interval"][1] <= above_hi + 1e-4
bad += not show("bootstrap 95% lower", hr["interval"][0], f"{lo:.4f} (atom), bracket [{below_lo:.4f}, {lo:.4f}]", ok_lo)
bad += not show("bootstrap 95% upper", hr["interval"][1], f"{hi:.4f} (atom), bracket [{hi:.4f}, {above_hi:.4f}]", ok_hi)
bad += not show("pooled hit rate", hr["value"], round(26 / 32, 4), hr["value"] == round(26 / 32, 4))

# 6. delta_hit_rate: does closing it change anything?
two = {1: (20, 20), 2: (14, 20)}   # pooled 34/40 = 0.85
outs = []
for delta in (0.0, 0.05, 0.5):
    m = closed({"delta_hit_rate": delta})
    outs.append(Run.hit_rate(type("R", (), {"m": m})(), two))
print(f"RESULT delta 0.0/0.05/0.5 -> verdicts {[o['verdict'] for o in outs]}, intervals {[o['interval'] for o in outs]}")
print(f"RESULT delta_status after the owner closed delta at 0.05: {outs[1]['delta_status']!r}")
if outs[1]["interval"][0] < 0.85 - 0.05 and outs[1]["verdict"] == "pass":
    print(f"FAIL pass with the interval's lower bound {outs[1]['interval'][0]} below 0.85 - delta (0.80): "
          "delta_hit_rate is a required slot that the verdict never reads")
if "no delta set" in outs[1]["delta_status"]:
    print("FAIL the report says 'no delta set by the owner' after the owner closed delta_hit_rate")
print(f"RESULT {bad} mismatch(es) on the recomputed statistics")
