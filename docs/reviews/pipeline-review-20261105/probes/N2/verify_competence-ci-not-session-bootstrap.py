#!/usr/bin/env python3
"""Verify (independent of the reporter's probe): band competence is gated with a pooled-unit Clopper-Pearson
interval (score.py score_metrics, the "exact binomial" bound), not the owner's "two-sided 95% intervals from
10,000 session-bootstrap rounds" (owner-choices.md item 6). One bad session is then invisible to the gate.

What it does (synthetic data only, temp dirs, the branch's own test World, fake check.py, no network):
  * 8 sessions over 8 days from the suite's FOUR_DAYS list; the suite's perfect follower everywhere, except
    that in the LAST held-out session (the reporter used the first) every casual task is read as level 0.2
    (band none) with demand confidence 0.05.
  * floor.competence.casual = 0.5 (the reporter used 0.55); every other slot as the suite fills it.
  * runs score.run("held_out") end to end and prints the verdict and competence.casual's interval;
  * recomputes the per-session casual counts from score.py's own Analysis objects;
  * computes, with code written here (not score.bootstrap), the session-bootstrap 95% percentile interval
    from 10,000 rounds and the exact bootstrap distribution over all 4^4 ordered draws;
  * recomputes Clopper-Pearson with math.comb, independently of score.clopper_pearson.

Run:  python3 -B verify_competence-ci-not-session-bootstrap.py <root of the claude/den-n2 checkout>
"""
import itertools
import math
import random
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
import test_den_n2 as T  # noqa: E402
score = T.score

FLOOR = 0.5


def cdf(k, n, p):
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def cp(k, n, level=0.95):
    a = (1 - level) / 2

    def bisect(f):
        lo, hi = 0.0, 1.0
        for _ in range(200):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if f(mid) else (lo, mid)
        return (lo + hi) / 2
    lower = 0.0 if k == 0 else bisect(lambda p: 1 - cdf(k - 1, n, p) < a)
    upper = 1.0 if k == n else bisect(lambda p: cdf(k, n, p) > a)
    return lower, upper


def pct(ordered, q):  # linear interpolation, as numpy's default
    pos = q * (len(ordered) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


tmp = Path(tempfile.mkdtemp(prefix="n2-verify-cp-"))
try:
    world = T.World(tmp, T.FOUR_DAYS)
    golds, _ = T.layout()
    gold_ivs = [T.iv(a, b, band, case) for a, b, band, case in golds]
    held = sorted(world.held_out())
    bad = held[-1]
    recs = []
    for r in T.follower(gold_ivs, 0.0, T.LAYOUT_END):
        in_casual = any(x["band"] == "casual" and x["on"] <= r.t < x["off"] + 1 for x in gold_ivs)
        recs.append(score.Rec(r.t, r.state, r.eng_conf, 0.2, 0.05, r.cap) if in_casual and r.level == 0.5 else r)
    world.write_need(bad, gold_ivs, records=recs)
    world.gate.write_text(T.gate_text(T.filled(**{"floor.competence.casual": FLOOR})), encoding="utf-8")
    report, _ = world.run("held_out")
    m = report["metrics"]["competence.casual"]
    print(f"RESULT held-out sessions {held}; bad session {bad}")
    print(f"RESULT score.py verdict={report['verdict']} reasons={report['reasons']}")
    print(f"RESULT score.py competence.casual value={m['value']} bound={m['bound']} interval={m['interval']} "
          f"status={m['status']} (floor {FLOOR})")
    print("RESULT other gated statuses: " + ", ".join(f"{n}={v['status']}" for n, v in report["metrics"].items()
                                                     if v["status"] != "reported"))

    # per-session casual counts from score.py's own analysis
    gate = score.load_gate(world.gate)
    gold, meta, sessions = score.load_n1(world.n1, "held_out", world.freezes.read_text(encoding="utf-8"))
    per = []
    for s in sessions:
        s.asks = score.load_asks(world.n2, s, meta["frozen_unix"])
        score.load_logs(world.n2, s, T.OK_CHECK)
        an = score.analyse(s, gate, 4)
        units = [u for u in an.units if u.gold_band == "casual"]
        per.append((s.take, sum(u.right for u in units), len(units)))
    print(f"RESULT per-session casual (take, right, units): {per}")
    k, n = sum(p[1] for p in per), sum(p[2] for p in per)
    lo, hi = cp(k, n)
    print(f"RESULT independent Clopper-Pearson {k}/{n}: [{lo:.6f}, {hi:.6f}]")

    # session bootstrap written here
    rng = random.Random(20261105)
    vals = []
    for _ in range(10000):
        draw = [per[rng.randrange(len(per))] for _ in per]
        vals.append(sum(d[1] for d in draw) / sum(d[2] for d in draw))
    vals.sort()
    b_lo, b_hi = pct(vals, 0.025), pct(vals, 0.975)
    exact = sorted(sum(per[i][1] for i in d) / sum(per[i][2] for i in d)
                   for d in itertools.product(range(len(per)), repeat=len(per)))
    p_low = sum(1 for v in exact if v <= 0.25) / len(exact)
    print(f"RESULT session bootstrap (10,000 rounds, seed here): [{b_lo:.4f}, {b_hi:.4f}]; exact over 4^4 draws: "
          f"P(value <= 0.25) = {p_low:.4f} (> 0.025, so the 2.5th percentile is <= 0.25)")
    boot_status = "met" if b_lo >= FLOOR else "failed" if b_hi < FLOOR else "straddled"
    print(f"RESULT session-bootstrap status at floor {FLOOR}: {boot_status}")
    flip = report["verdict"] == "feasible" and m["status"] == "met" and boot_status != "met"
    print(("FAIL" if flip else "PASS") + ": score.py gates competence.casual 'met' (verdict feasible) where the "
          "owner's session bootstrap gives '" + boot_status + "' (verdict insufficient)")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
