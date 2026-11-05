#!/usr/bin/env python3
"""Verifier probe for the five test-coverage findings on den-n2 (tc-sample-gate-mutants-survive,
tc-bootstrap-mutants-survive, tc-demand-definition-mutants-survive, tc-no-data-not-straddled-survives,
tc-refusal-mutants-survive).

A surviving mutant is only a coverage gap if the mutant really behaves differently. For each mutant of
probes/N2/mutate_score.py (same old/new text), this builds the mutated score module IN MEMORY (exec of the
patched source; nothing is written inside the checkout) and runs one minimal synthetic scenario on both the
real and the mutated module, printing both outputs. DIFFERS = the mutant is not equivalent.

Synthetic data only, temp dirs only, fake check.py, no network.

Run:  python3 -B verify_tc-mutants-are-not-equivalent.py <root of the claude/den-n2 checkout>
"""
import importlib.util
import json
import shutil
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
import test_den_n2 as T  # noqa: E402
real = T.score

spec = importlib.util.spec_from_file_location("mutate_score", HERE / "mutate_score.py")
mutate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mutate)
MUT = {m[0]: m for m in mutate.MUTANTS}
SRC_PATH = ROOT / "tools" / "den-n2" / "score.py"
SRC = SRC_PATH.read_text(encoding="utf-8")


def mutant(mid):
    _, _, old, new = MUT[mid]
    assert SRC.count(old) == 1, mid
    name = "score_" + mid.replace("-", "_")
    mod = types.ModuleType(name)
    mod.__file__ = str(SRC_PATH)
    sys.modules[name] = mod
    exec(compile(SRC.replace(old, new), str(SRC_PATH), "exec"), mod.__dict__)
    return mod


def show(mid, what, a, b):
    print(f"RESULT {mid}: {what}: real={a!r} mutant={b!r} -> {'DIFFERS' if a != b else 'SAME (equivalent here)'}")


G = T.filled()

# M01: a level of exactly A7's fine level 0.6
m = mutant("M01-cut-fine-strict")
show("M01-cut-fine-strict", "band_of(0.6) [A7 fine level]", real.band_of(0.6, G), m.band_of(0.6, G))

# M02: two units, confidences 0.32 (right) and 0.38 (wrong), engagement ECE through session_stats' binning
m = mutant("M02-ece-9-bins")


def ece_of(mod):
    units = [mod.Unit("t", "interval", "sorting_mail", "fine", True, True, "fine", 0.32, None, 1.0, False),
             mod.Unit("t", "interval", "sorting_mail", "fine", True, False, "fine", 0.38, None, 1.0, False)]
    an = types.SimpleNamespace(seconds=[], early_off=[], units=units, onsets=[])
    return round(mod.m_ece("engagement")([mod.session_stats(an, G)]), 6)


show("M02-ece-9-bins", "ECE.engagement of confs 0.32 right / 0.38 wrong", ece_of(real), ece_of(m))

# M03: one fine interval whose level is fine but stale (capture 20 s before, max_age 10 s)
m = mutant("M03-stale-is-a-hit")


def recall_of(mod):
    recs = [T.rec(0.5, "none", 0.2), T.rec(11, "starting", 0.8, cap=-30), T.rec(16, "engaged", 0.8, cap=-30)]
    s = T.session([T.iv(10, 40)], records=recs, segments=((0.0, 100.0),))
    an = mod.analyse(s, G, 4)
    return mod.session_stats(an, G)["ratio"]["fine_recall"]


show("M03-stale-is-a-hit", "fine_recall [hits, seconds] with a stale fine level", recall_of(real), recall_of(m))

# M04: 30 fine units from 3 sessions recorded on ONE day
m = mutant("M04-no-days-rule")
days = {"a": "2026-11-01", "b": "2026-11-01", "c": "2026-11-01"}
show("M04-no-days-rule", "units gate met, 30 units / 3 sessions / 1 day",
     real.sample_gate("units.fine", 30, 30, {"a", "b", "c"}, days, G)["met"],
     m.sample_gate("units.fine", 30, 30, {"a", "b", "c"}, days, G)["met"])

# M05: exactly 30 units, 3 sessions, 2 days
m = mutant("M05-units-off-by-one")
days = {"a": "2026-11-01", "b": "2026-11-01", "c": "2026-11-02"}
show("M05-units-off-by-one", "units gate met, exactly 30 units / 3 sessions / 2 days",
     real.sample_gate("units.fine", 30, 30, {"a", "b", "c"}, days, G)["met"],
     m.sample_gate("units.fine", 30, 30, {"a", "b", "c"}, days, G)["met"])

# M06 / M07: the suite's own bootstrap case (four sessions, early_off 0/10, 1/10, 3/10, 6/10)
parts = [{"ratio": {"early_off": [k, 10]}} for k in (0, 1, 3, 6)]
for mid in ("M06-bootstrap-90pct", "M07-identity-resample"):
    m = mutant(mid)
    a = real.bootstrap(parts, real.m_ratio("early_off"), real.resamples(4, 10000, "ab" * 32), 0.95)
    b = m.bootstrap(parts, m.m_ratio("early_off"), m.resamples(4, 10000, "ab" * 32), 0.95)
    show(mid, "early_off 95% bootstrap interval", [round(v, 4) for v in a], [round(v, 4) for v in b])
    if mid == "M07-identity-resample":
        print(f"RESULT {mid}: test_bootstrap_is_seeded_from_the_labels_hash asserts lo <= 0.25 <= hi: "
              f"mutant interval satisfies it: {b[0] <= 0.25 <= b[1]}")

# M08: a gated metric with no data (ece.engagement no_data), everything else met
m = mutant("M08-no-data-passes")
met = {"threshold": {"kind": "floor", "value": 0.6}, "status": "met"}
metrics = {"fine_recall": met, "ece.engagement": {"threshold": {"kind": "cap", "value": 0.1}, "status": "no_data"}}
gates, cases = [{"name": "units.fine", "met": True}], {"wiping": {"status": "not_failed"}}
show("M08-no-data-passes", "verdict with ece.engagement no_data", real.verdict(metrics, cases, gates, True)[0],
     m.verdict(metrics, cases, gates, True)[0])

# M11: three sessions on two days, a per-second metric (false_on) with data in one session only
m = mutant("M11-data-gate-vacuous")


def data_gate(mod):
    ans = [types.SimpleNamespace(session=types.SimpleNamespace(take=t, day=d), units=[], onsets=[])
           for t, d in (("a", "2026-11-01"), ("b", "2026-11-01"), ("c", "2026-11-02"))]
    stats = [{"ratio": {n: [0, 10 if (n != "false_on" or i == 0) else 0] for n in mod.PER_SECOND}}
             for i in range(3)]
    return next(g["met"] for g in mod.sample_gates(ans, stats, G, False) if g["name"] == "data.false_on")


show("M11-data-gate-vacuous", "data.false_on gate met with data in 1 of 3 sessions", data_gate(real), data_gate(m))

# M09 / M10: held-out refusals in a synthetic world (changed surface-map.json; replay tick 1.0 vs 2.0)
tmp = Path(tempfile.mkdtemp(prefix="n2-verify-tc-"))
try:
    world = T.World(tmp / "w", T.FOUR_DAYS[:4])
    held = sorted(world.held_out())

    def outcome(mod):
        T.score = mod
        try:
            world.run("held_out")
            return "SCORED"
        except real.Refused as exc:
            return "refused: " + str(exc)[:70]
        finally:
            T.score = real

    sm = world.n1 / "fixture-v1" / "surface-map.json"
    saved = sm.read_bytes()
    sm.write_bytes(saved + b" ")
    show("M09-skip-fixture-verify", "held-out run, surface-map.json changed after freeze", outcome(real),
         outcome(mutant("M09-skip-fixture-verify")))
    sm.write_bytes(saved)

    log = world.n2 / "replay" / held[0] / "seg-1" / "log.jsonl"
    saved = log.read_bytes()
    lines = saved.decode("ascii").splitlines()
    header = json.loads(lines[0])
    header["tick_s"] = 1.0
    log.write_bytes(("\n".join([json.dumps(header, sort_keys=True, separators=(",", ":"))] + lines[1:]) + "\n")
                    .encode("ascii"))
    show("M10-skip-tick-standin", "held-out run, replay log tick 1.0 vs gate 2.0", outcome(real),
         outcome(mutant("M10-skip-tick-standin")))
    log.write_bytes(saved)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
