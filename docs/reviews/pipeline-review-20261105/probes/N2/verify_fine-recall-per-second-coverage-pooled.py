#!/usr/bin/env python3
"""Verify (independent of the reporter's probe): score.py gates fine_recall per second and one coverage
averaged over fine and casual interval units together, where the ladder (rung 6) speaks of fine-demand
recall per episode and per-band coverage and the canon EvalRecord carries per_link.visual_demand
.coverage_per_band. Checks whether either choice changes the VERDICT end to end, or whether band
competence (per unit, abstained = wrong) already catches it.

Two held-out scenarios on the branch's own test World (8 sessions, 4 held out, fake check.py, temp dirs,
synthetic only), every slot as the suite fills it (all floors 0.6):
  A  half the fine episodes missed: in every held-out session the 4 still_reader fine tasks are read casual
     (level 0.5) throughout. Per-second fine recall vs per-episode recall, and the verdict.
  B  casual tasks mostly stale: in every held-out session each casual task's level is right (casual) but its
     support capture stops 3 s after the touch, so the level goes stale 10 s later (hold rows keep it in
     force). Per-band coverage vs the pooled coverage, and the verdict.

Run:  python3 -B verify_fine-recall-per-second-coverage-pooled.py <root of the claude/den-n2 checkout>
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
import test_den_n2 as T  # noqa: E402
score = T.score


def per_band(world):
    gate = score.load_gate(world.gate)
    gold, meta, sessions = score.load_n1(world.n1, "held_out", world.freezes.read_text(encoding="utf-8"))
    units = []
    for s in sessions:
        s.asks = score.load_asks(world.n2, s, meta["frozen_unix"])
        score.load_logs(world.n2, s, T.OK_CHECK)
        units += score.analyse(s, gate, 4).units
    cov = {b: score.mean([u.coverage for u in units if u.kind == "interval" and u.gold_band == b])
           for b in ("fine", "casual")}
    fine = [u for u in units if u.kind == "interval" and u.gold_band == "fine"]
    return cov, sum(1 for u in fine if u.band == "fine") / len(fine)


def scenario(name, mutate):
    tmp = Path(tempfile.mkdtemp(prefix="n2-verify-cov-"))
    try:
        world = T.World(tmp, T.FOUR_DAYS)
        golds, _ = T.layout()
        gold_ivs = [T.iv(a, b, band, case) for a, b, band, case in golds]
        recs = [mutate(r, gold_ivs) for r in T.follower(gold_ivs, 0.0, T.LAYOUT_END)]
        for take in world.held_out():
            world.write_need(take, gold_ivs, records=recs)
        report, _ = world.run("held_out")
        m = report["metrics"]
        cov, per_episode = per_band(world)
        print(f"RESULT {name}: verdict={report['verdict']} reasons={report['reasons']}")
        for n in ("fine_recall", "coverage", "competence.fine", "competence.casual", "ece.demand", "false_fine"):
            print(f"RESULT {name}:   {n} value={m[n]['value']} interval={m[n]['interval']} status={m[n]['status']}")
        print(f"RESULT {name}:   per-episode fine recall={per_episode:.3f}; per-band coverage="
              f"{ {b: round(v, 3) for b, v in cov.items()} }; report has per-band coverage: "
              f"{'coverage_per_band' in str(report)}")
        return report, cov, per_episode
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def miss_still_reader(r, ivs):
    hit = any(x["case"] == "still_reader" and x["on"] <= r.t < x["off"] + 1 for x in ivs)
    return score.Rec(r.t, r.state, r.eng_conf, 0.5, r.dem_conf, r.cap) if hit and r.level == 0.8 else r


def stale_casual(r, ivs):
    x = next((x for x in ivs if x["band"] == "casual" and x["on"] <= r.t < x["off"] + 1), None)
    if x is None or r.level != 0.5:
        return r
    return score.Rec(r.t, r.state, r.eng_conf, r.level, r.dem_conf, min(r.cap, x["on"] + 3.0))


ra, _, pe = scenario("A", miss_still_reader)
rb, cov, _ = scenario("B", stale_casual)
print(("FAIL" if ra["verdict"] == "feasible" else "PASS")
      + f": A (per-episode fine recall {pe:.2f}) verdict={ra['verdict']}: band competence "
      + ("does not catch" if ra["verdict"] == "feasible" else "catches") + " the missed fine episodes")
print(("FAIL" if rb["verdict"] == "feasible" and cov["casual"] < 0.6 else "PASS")
      + f": B verdict={rb['verdict']} with casual coverage {cov['casual']:.3f} (< the 0.6 floor per band) "
      "hidden by the pooled coverage")
