#!/usr/bin/env python3
"""Tiebreak probe for finding fine-recall-per-second-coverage-pooled (den-n2 score.py).

Checks, end to end through score.run() on the branch's own synthetic test World (8 sessions, 4 held out,
fake check.py, temp dirs only, no network), whether score.py's per-second fine_recall and its single
coverage averaged over fine and casual interval units together can give a `feasible` verdict where
the canon's per-band coverage (EvalRecord per_link.visual_demand.coverage_per_band, rung 6, E3) or
per-episode fine-demand recall (rung 6) would not.

  C  casual levels right but mostly stale (the support capture stops 2 s after each casual touch, so the
     level goes stale 10 s later and stays in force). Every floor at the suite's 0.6.
     Prints the verdict, pooled coverage, per-band coverage (with the same session bootstrap), and whether
     the held-out or development report shows per-band coverage anywhere.
  E  per-episode fine misses with unequal owner floors (floor.fine_recall 0.8, floor.competence.fine 0.55,
     the rest 0.6): in every held-out session one sorting_mail and one still_reader task read casual for
     their first 13 scored demand seconds and fine for the last 11 (median band casual = an episode miss).
     Prints the verdict, per-second fine_recall, per-episode recall and competence.fine.

Run:  python3 -B probe_tiebreak_fine-recall-per-second-coverage-pooled.py <root of the claude/den-n2 checkout>
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
import test_den_n2 as T  # noqa: E402

score = T.score


def held_units(world):
    gate = score.load_gate(world.gate)
    gold, meta, sessions = score.load_n1(world.n1, "held_out", world.freezes.read_text(encoding="utf-8"))
    per_session = []
    for s in sessions:
        s.asks = score.load_asks(world.n2, s, meta["frozen_unix"])
        score.load_logs(world.n2, s, T.OK_CHECK)
        per_session.append(score.analyse(s, gate, 4).units)
    return gold, per_session


def per_band_coverage(gold, per_session, rounds=10000):
    out = {}
    for band in ("fine", "casual"):
        sess = [[u.coverage for u in units if u.kind == "interval" and u.gold_band == band and u.coverage is not None]
                for units in per_session]
        value = score.mean([v for xs in sess for v in xs])
        draws = score.resamples(len(sess), rounds, gold["hash"])
        boot = sorted(score.mean([v for i in d for v in sess[i]]) for d in draws)
        interval = [score.percentile(boot, 0.025), score.percentile(boot, 0.975)]
        out[band] = (value, interval, score.judge("floor", 0.6, value, interval))
    return out


def world_with(tmp, mutate, gate_changes=None):
    world = T.World(tmp, T.FOUR_DAYS)
    if gate_changes:
        world.gate.write_text(T.gate_text(T.filled(**gate_changes)), encoding="utf-8")
    golds, _ = T.layout()
    gold_ivs = [T.iv(a, b, band, case) for a, b, band, case in golds]
    recs = [mutate(r, gold_ivs) for r in T.follower(gold_ivs, 0.0, T.LAYOUT_END)]
    for take in world.held_out():
        world.write_need(take, gold_ivs, records=recs)
    return world


def stale_casual(r, ivs):
    x = next((x for x in ivs if x["band"] == "casual" and x["on"] <= r.t < x["off"] + 1), None)
    if x is None or r.level != T.LEVEL["casual"]:
        return r
    return score.Rec(r.t, r.state, r.eng_conf, r.level, r.dem_conf, min(r.cap, x["on"] + 2.0))


def partial_fine(r, ivs):
    fine = [x for x in ivs if x["band"] == "fine"]
    chosen = (fine[0], fine[1])  # the first sorting_mail and the first still_reader task
    hit = any(x["on"] <= r.t < x["on"] + 19 for x in chosen)
    if hit and r.level == T.LEVEL["fine"]:
        return score.Rec(r.t, r.state, r.eng_conf, T.LEVEL["casual"], r.dem_conf, r.cap)
    return r


def show(tag, report, names):
    m = report["metrics"]
    print(f"RESULT {tag}: verdict={report['verdict']} reasons={report['reasons']}")
    for n in names:
        print(f"RESULT {tag}:   {n} value={m[n]['value']} interval={m[n]['interval']} "
              f"threshold={m[n]['threshold']} status={m[n]['status']} counted_per={m[n]['counted_per']!r}")


ok = True
tmp = Path(tempfile.mkdtemp(prefix="n2-tiebreak-"))
try:
    # ---- C: pooled coverage hides a mostly stale casual band ----
    world = world_with(tmp / "C", stale_casual)
    report, _ = world.run("held_out")
    show("C", report, ("coverage", "competence.casual", "competence.fine", "fine_recall", "ece.demand"))
    gold, per_session = held_units(world)
    cov = per_band_coverage(gold, per_session)
    for band, (v, iv_, st) in cov.items():
        print(f"RESULT C:   per-band coverage {band}={v:.3f} bootstrap={[round(x, 3) for x in iv_]} vs 0.6 -> {st}")
    keys = sorted(report["metrics"])
    shown = any("band" in k and "cover" in k for k in keys) or "coverage_per_band" in str(report)
    print(f"RESULT C:   held-out report metric keys={keys}; per-band coverage shown: {shown}")
    world.gate = score.GATE_FILE  # development with the committed gate (every slot empty), as the owner runs it
    dev, _ = world.run("development")
    dev_shown = "coverage_per_band" in str(dev) or any("band" in k and "cover" in k for k in dev["metrics"])
    print(f"RESULT C:   development report coverage={dev['metrics']['coverage']['value']}; "
          f"per-band coverage shown: {dev_shown}")
    bad_c = report["verdict"] == "feasible" and cov["casual"][2] == "failed" and not shown and not dev_shown
    ok &= not bad_c
    print(("FAIL" if bad_c else "PASS") + f" C: verdict={report['verdict']} while casual coverage "
          f"{cov['casual'][0]:.3f} fails a 0.6 floor per band; neither report shows per-band coverage")

    # ---- E: per-episode fine misses hidden when floor.fine_recall > floor.competence.fine ----
    world = world_with(tmp / "E", partial_fine, {"floor.fine_recall": 0.8, "floor.competence.fine": 0.55})
    report, _ = world.run("held_out")
    show("E", report, ("fine_recall", "competence.fine", "ece.demand", "coverage"))
    print(f"RESULT E:   cases: " + ", ".join(f"{c} {v['right']}/{v['units']} {v['status']}"
                                             for c, v in report["cases"].items()))
    gold, per_session = held_units(world)
    fine = [u for units in per_session for u in units if u.kind == "interval" and u.gold_band == "fine"]
    per_episode = sum(1 for u in fine if u.band == "fine") / len(fine)
    print(f"RESULT E:   per-episode fine recall (unit band fine; abstained or stale = miss) = "
          f"{sum(1 for u in fine if u.band == 'fine')}/{len(fine)} = {per_episode:.3f} vs floor.fine_recall 0.8")
    bad_e = report["verdict"] == "feasible" and per_episode < 0.8
    ok &= not bad_e
    print(("FAIL" if bad_e else "PASS") + f" E: verdict={report['verdict']} with per-episode fine recall "
          f"{per_episode:.3f} under the 0.8 recall floor (per-second fine_recall {report['metrics']['fine_recall']['value']})")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("RESULT overall: " + ("no wrong verdict" if ok else "a feasible verdict that the canon definitions would not give"))
