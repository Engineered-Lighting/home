#!/usr/bin/env python3
"""Verifier (intent lens): does each reported score.py mutant change rung 9's VERDICT, or only one check's 'ok'?

    python3 -B verify_intent_mutant_impact.py <den-core-live checkout root> [work parent dir]

It copies tools/den-core, den_lighting's custom component, perception-lab and the tools/*-latency folder into a fresh temp folder
(under [work parent dir] if given), never touching the checkout. A driver then runs scenarios on synthetic,
non-synthetic-flagged logs (the ScoreTest construct: decide's slots filled, the fake model relabelled non-fake, a
filled gate) once on the unmutated score.py and once per mutant (only the mutant's own scenarios):
  timing      every decide.handle call reads 150 ms (score.time.perf_counter patched)       -> M6b
  zero        the evening without occupancy events: decide never decides                     -> M7, M15
  zero_floor0 the same with the anticipation floor 0 (no surrogate masks it)                 -> M7, M15
  noepisodes  occupancy and bulbs only, no facts: decide decides, need has no episode        -> M8
  falsestart  score.episodes/surrogates on hand-made PersonStates with false starts           -> M9
  recipients  decide's Decision drops one recipient (decide patched in-process for log and replay) -> M10
  gameover    the evening with the game moved over the second reading block, lx model, a mark -> M11
It prints one RESULT line per scenario and variant, and PASS/FAIL lines comparing each mutant with the base.
Synthetic data only; nothing binds or reads a host.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

DRIVER = r'''
import json, sys, types
from pathlib import Path
core, cache, which = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3].split(",")
sys.path.insert(0, str(core))
import decide, events, light_model, runner, score, synthetic
T0 = synthetic.T0
out = {}

def filled(slots):
    return {n: {**e, "value": e["proposed"], "status": "set"} for n, e in slots.items()}

def model(lx=False):
    m = light_model.fake(); m.update(version="probe-lm-1", fake=False)
    if lx:
        m["basis_unit"] = "lx"
        for bulb in m["basis"].values():
            for basis in bulb.values():
                for p in basis:
                    basis[p] *= 300
    return m

def gate(name, **floors):
    g = json.loads(score.GATE.read_text("utf-8"))
    for entry in list(g["parameters"].values()) + [s["floor"] for s in g["surrogates"].values()]:
        entry.update(value=entry["proposed"], status="set")
    for k, v in floors.items():
        g["surrogates"][k]["floor"]["value"] = v
    g["parameters"]["compensation"]["value"] = "none-v0"
    p = cache / name; p.write_text(json.dumps(g), "ascii"); return p

def log(name, items, lx=False):
    path = cache / name
    if not path.exists():
        cfg = {"decide": {"slots": filled(decide.DEFAULT_CONFIG["slots"]), "light_model": model(lx)}}
        header, lg = runner.run(runner.tagged(items, "synthetic"), runner.load_policies(["need", "incumbent", "decide"], cfg),
                                synthetic=False, sources=[{"name": "probe", "params": {}}])
        events.write(path, header, lg)
    return path

def summary(rec, reasons):
    if rec is None:
        return {"refused": reasons}
    return {"verdict": rec["verdict"], "checks": {k: v["ok"] for k, v in rec["checks"].items()},
            "texts": {k: v["text"] for k, v in rec["checks"].items() if k in ("timing", "record", "anticipation", "stability")}}

def evening(**kw):
    items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, **kw)
    return items

if "timing" in which:
    path = log("evening.jsonl", evening(touch=True, game=True))
    clock = [0.0]
    def fake():
        clock[0] += 0.15
        return clock[0]
    real_time = score.time
    score.time = types.SimpleNamespace(perf_counter=fake)
    out["timing"] = summary(*score.score(path, gate("g.json")))
    score.time = real_time

if "zero" in which or "zero_floor0" in which:
    items = [it for it in evening(touch=True) if it[1] != "occupancy"]
    path = log("zero.jsonl", items)
    n = sum(1 for e in events.read(path)[1] if e["kind"] == "decision" and e["source"] == "decide")
    out["zero"] = dict(summary(*score.score(path, gate("g.json"))), decide_decisions=n)
    out["zero_floor0"] = dict(summary(*score.score(path, gate("g0.json", anticipation=0))), decide_decisions=n)

if "noepisodes" in which:
    lit = {"on": True, "brightness": 180, "kelvin": 2700, "context": "unknown"}
    items = [(T0 - 600, "light_observed", {"entity_id": e, **lit}) for e in events.LIGHTS]
    items += [(T0, "occupancy", {"value": "on", "via": "synthetic"}), (T0 + 400, "occupancy", {"value": "off", "via": "synthetic"})]
    path = log("noepisodes.jsonl", items)
    n = sum(1 for e in events.read(path)[1] if e["kind"] == "decision" and e["source"] == "decide")
    ps = sum(1 for e in events.read(path)[1] if e["kind"] == "person_state")
    out["noepisodes"] = dict(summary(*score.score(path, gate("g.json"))), decide_decisions=n, person_states=ps)

if "falsestart" in which:
    def ps(t, state):
        return {"kind": "person_state", "source": "need", "t": float(t), "data": {"track_id": "A", "focused_engagement": {"state": state}}}
    simple = [ps(10, "starting"), ps(13, "none"), ps(20, "starting"), ps(26, "engaged"), ps(100, "none")]
    stream, timeline = [], [(0.0, {"x": 100.0}, "mode")]
    for k in range(10):          # ten real episodes, each lit at engaged; two of them re-lit later (unstable)
        t0 = 1000.0 * (k + 1)
        stream += [ps(t0, "starting"), ps(t0 + 6, "engaged"), ps(t0 + 100, "none")]
        timeline.append((t0 + 6, {"x": 200.0}, "need"))
        if k < 2:
            timeline.append((t0 + 30, {"x": 150.0}, "need"))
        timeline.append((t0 + 100, {"x": 100.0}, "need"))
        stream += [ps(t0 + 500, "starting"), ps(t0 + 503, "none")]   # and one false start (a brief touch)
    class Stub:
        def predict(self, scenes, points):
            return {p: scenes["x"] for p in points}
    found = score.episodes(stream)
    rates = score.surrogates(timeline, found, lambda track: ["p"], Stub(), 10, 0.2, 0.1)
    floors = {"anticipation": 0.5, "stability": 0.9}
    ok = {k: rates[k]["rate"] is not None and rates[k]["rate"] >= floors[k] for k in floors}
    out["falsestart"] = {"simple_episodes": score.episodes(simple), "episodes": len(found),
                         "rates": {k: rates[k]["rate"] for k in rates}, "surrogate_ok": ok}

if "recipients" in which:
    original = decide.Decide.record
    def record(self, t, trigger, result):
        d = original(self, t, trigger, result)
        if d["recipients"]:
            d["recipients"].pop(sorted(d["recipients"])[0])
        return d
    decide.Decide.record = record
    path = log("recipients.jsonl", evening(touch=True, game=True))
    out["recipients"] = summary(*score.score(path, gate("g.json")))
    decide.Decide.record = original

if "gameover" in which:
    items = [it for it in evening(touch=True) if it[1] != "gaming_sample"]
    samples = [(dt, "playing") for dt in range(700, 891, 10)] + [(dt, "idle") for dt in range(900, 961, 10)]
    items += [(T0 + dt, "gaming_sample", {"status": s, "sampled_at": T0 + dt - 0.5}) for dt, s in samples]
    items.sort(key=lambda it: it[0])
    path = log("gameover.jsonl", items, lx=True)
    marks = cache / "marks.jsonl"
    marks.write_text(json.dumps({"mark_id": "m1", "track_id": "room", "start": T0 + 560, "end": T0 + 960,
                                 "band": "fine", "points": ["desk-1"]}) + "\n", "ascii")
    rec, reasons = score.score(path, gate("g.json"), marks)
    hdr, lg = events.read(path)
    tl = score.arms(hdr, lg)
    games = [(e["t"] - T0, e["data"]["gaming"]) for e in lg if e["kind"] == "mode" and e["source"] == "incumbent"]
    edges = [g for i, g in enumerate(games) if i == 0 or games[i - 1][1] != g[1]]
    out["gameover"] = dict(summary(rec, reasons), game_edges=edges,
                           rates={arm: {k: v["rate"] for k, v in rec["surrogates"]["arms"][arm].items()} for arm in ("candidate", "incumbent")},
                           episodes=[(round(a - T0), round(b - T0)) for _, a, b in score.episodes(lg)],
                           coverage={arm: (v or {}).get("coverage") for arm, v in rec["reported"]["arms"].items()},
                           candidate_entries_in_game=len([x for x in tl["candidate"] if T0 + 700 <= x[0] <= T0 + 960]))
print("JSON " + json.dumps(out, sort_keys=True))
'''

MUTANTS = {
    "M6b-timing-bound-10x": ("max(ms) < fixed[\"decision_ms_max\"]", "max(ms) < 10 * fixed[\"decision_ms_max\"]", ["timing"]),
    "M15-timing-empty-passes": ("\"timing\": {\"ok\": bool(ms) and max(ms)", "\"timing\": {\"ok\": not ms or max(ms)",
                                ["zero", "zero_floor0"]),
    "M7-record-empty-passes": ("\"ok\": problem is None and checked > 0,", "\"ok\": problem is None,", ["zero", "zero_floor0"]),
    "M8-surrogate-none-passes": ("checks[name] = {\"ok\": rate is not None and rate >= floors[name],",
                                 "checks[name] = {\"ok\": rate is None or rate >= floors[name],", ["noepisodes"]),
    "M9-episodes-without-engaged": ("            if engaged:\n                found.append((track, t0, e[\"t\"]))",
                                    "            if True:\n                found.append((track, t0, e[\"t\"]))", ["falsestart"]),
    "M10-record-skip-recipients": ("        if set(d.get(\"recipients\") or {}) != lit:", "        if False:", ["recipients"]),
    "M11-arms-ignore-gaming": ("mine = shown if gaming else {b: requested.get(b, shown[b]) for b in events.LIGHTS}",
                               "mine = {b: requested.get(b, shown[b]) for b in events.LIGHTS}", ["gameover"]),
}
COPY = ["tools/den-core", "ha-config/custom_components/den_lighting", "stack/services/perception-lab"]
ALL = ["timing", "zero", "zero_floor0", "noepisodes", "falsestart", "recipients", "gameover"]


def drive(core, cache, driver, which):
    proc = subprocess.run([sys.executable, "-B", driver, core, cache, ",".join(which)], cwd=core,
                          capture_output=True, text=True, timeout=1800)
    line = [l for l in proc.stdout.splitlines() if l.startswith("JSON ")]
    if not line:
        print("ERROR driver failed:\n" + proc.stderr[-3000:])
        return {}
    return json.loads(line[0][5:])


def main():
    root = os.path.abspath(sys.argv[1])
    parent = sys.argv[2] if len(sys.argv) > 2 else None
    work = tempfile.mkdtemp(prefix="r9-intent-impact-", dir=parent)
    try:
        extra = ["tools/" + d for d in sorted(os.listdir(os.path.join(root, "tools"))) if d.endswith("-latency")]
        for rel in COPY + extra:
            if os.path.isdir(os.path.join(root, rel)):
                shutil.copytree(os.path.join(root, rel), os.path.join(work, rel), ignore=shutil.ignore_patterns("__pycache__"))
        core = os.path.join(work, "tools", "den-core")
        cache = os.path.join(work, "cache")
        os.mkdir(cache)
        driver = os.path.join(work, "driver.py")
        open(driver, "w").write(DRIVER)
        base = drive(core, cache, driver, ALL)
        for name in ALL:
            print("RESULT base %-12s %s" % (name, json.dumps(base.get(name), sort_keys=True)))
        path = os.path.join(core, "score.py")
        original = open(path, encoding="utf-8").read()
        for mid, (old, new, which) in MUTANTS.items():
            assert original.count(old) == 1, mid
            open(path, "w", encoding="utf-8").write(original.replace(old, new))
            got = drive(core, cache, driver, which)
            open(path, "w", encoding="utf-8").write(original)
            for name in which:
                b, m = base.get(name, {}), got.get(name, {})
                print("RESULT %-28s %-12s %s" % (mid, name, json.dumps(m, sort_keys=True)))
                bv = b.get("verdict", b.get("surrogate_ok"))
                mv = m.get("verdict", m.get("surrogate_ok"))
                print("%s %s %s: base verdict %s, mutant verdict %s" % ("VERDICT-CHANGES" if bv != mv else "VERDICT-SAME",
                                                                      mid, name, bv, mv))
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
