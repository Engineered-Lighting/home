#!/usr/bin/env python3
"""Probe: band competence (and the case criterion) use a pooled-unit Clopper-Pearson interval that treats
every unit as independent, while the owner's interval choice (owner-choices.md, promotion-margins) is
"two-sided 95% intervals from 10,000 session-bootstrap rounds". Units of one session share one evening's
light, one estimator run and one person, so one bad session is invisible to the pooled interval.

Scenario (synthetic world from the branch's own test helpers, temp dirs, fake check.py): 4 held-out
sessions, the suite's perfect follower, except that in ONE held-out session need v1 reads every casual task
as no demand (level 0.2) with an honest low demand confidence (0.05). Per-second metrics do not see a
casual task read as none (no false-fine, no fine miss, levels fresh, engagement right), so only
competence.casual carries it. floor.competence.casual = 0.55 (an owner slot, any value in 0..1).

Prints score.py's verdict, then the same competence statistic under a session bootstrap (resampling
unit = session, 10,000 rounds, percentile, seeded from the labels hash, using score.py's own resampler).

Run:  python3 -B probe_competence-ci-not-session-bootstrap.py <root of the claude/den-n2 checkout>
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
import test_den_n2 as T  # noqa: E402
score = T.score

tmp = Path(tempfile.mkdtemp(prefix="n2-probe-cp-"))
try:
    world = T.World(tmp, T.FOUR_DAYS)
    golds, _ = T.layout()
    gold_ivs = [T.iv(a, b, band, case) for a, b, band, case in golds]
    bad = sorted(world.held_out())[0]
    recs = []
    for r in T.follower(gold_ivs, 0.0, T.LAYOUT_END):
        in_casual = any(x["band"] == "casual" and x["on"] <= r.t < x["off"] + 1 for x in gold_ivs)
        recs.append(score.Rec(r.t, r.state, r.eng_conf, 0.2, 0.05, r.cap) if in_casual and r.level == 0.5 else r)
    world.write_need(bad, gold_ivs, records=recs)
    gate = T.filled(**{"floor.competence.casual": 0.55})
    world.gate.write_text(T.gate_text(gate), encoding="utf-8")
    report, _ = world.run("held_out")
    m = report["metrics"]["competence.casual"]
    print(f"RESULT score.py: verdict={report['verdict']} competence.casual value={m['value']} "
          f"Clopper-Pearson={m['interval']} {m['status']}; ece.demand={report['metrics']['ece.demand']['interval']} "
          f"{report['metrics']['ece.demand']['status']}; fine_recall {report['metrics']['fine_recall']['status']}, "
          f"false_fine {report['metrics']['false_fine']['status']}, coverage {report['metrics']['coverage']['status']}")
    # the same statistic with the owner's session bootstrap
    loaded = score.load_gate(world.gate)
    gold, meta, sessions = score.load_n1(world.n1, "held_out", world.freezes.read_text(encoding="utf-8"))
    stats = []
    for s in sessions:
        s.asks = score.load_asks(world.n2, s, meta["frozen_unix"])
        score.load_logs(world.n2, s, T.OK_CHECK)
        stats.append(score.session_stats(score.analyse(s, loaded, 4), loaded))
    per_session = [st["competence"]["casual"] for st in stats]
    interval = score.bootstrap(stats, score.m_competence("casual"), score.resamples(len(stats), 10000, gold["hash"]),
                               0.95)
    status = score.judge("floor", 0.55, 0.75, interval)
    print(f"RESULT per-session casual competence [right, units]: {per_session}")
    print(f"RESULT session bootstrap (10,000 rounds, 95%): {[round(v, 6) for v in interval]} -> {status}")
    flip = report["verdict"] == "feasible" and status != "met"
    print(("FAIL" if flip else "PASS") + " the pooled-unit interval calls competence.casual met (feasible) where "
          "the owner's session bootstrap calls it " + status)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
