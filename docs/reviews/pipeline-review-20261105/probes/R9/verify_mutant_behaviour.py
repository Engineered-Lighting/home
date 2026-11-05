#!/usr/bin/env python3
"""Verifier's equivalence check for the seven surviving score.py mutants (claude/den-core-live).

    python3 -B verify_mutant_behaviour.py <den-core-live checkout root>

A surviving mutant is only a test gap if the mutant behaves differently on some input. For each mutant this builds
a synthetic input on which the unmutated and the mutated score.py should disagree, runs both in-process, and prints
PASS (they disagree: the mutant is not equivalent, so its survival is a real gap) or FAIL (no difference found).
It also prints whether the difference reaches the gate's verdict. It copies tools/den-core, den_lighting, perception-lab and la-latency into a
temp folder (set TMPDIR to choose where) and never touches the checkout. Synthetic data only, temp files only.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import types
from unittest import mock

MUTANTS = {
    "M6b timing x10": ("max(ms) < fixed[\"decision_ms_max\"]", "max(ms) < 10 * fixed[\"decision_ms_max\"]"),
    "M15 timing empty": ("\"timing\": {\"ok\": bool(ms) and max(ms)", "\"timing\": {\"ok\": not ms or max(ms)"),
    "M7 record empty": ("\"ok\": problem is None and checked > 0,", "\"ok\": problem is None,"),
    "M8 surrogate None": ("checks[name] = {\"ok\": rate is not None and rate >= floors[name],",
                          "checks[name] = {\"ok\": rate is None or rate >= floors[name],"),
    "M9 episodes w/o engaged": ("            if engaged:\n                found.append((track, t0, e[\"t\"]))",
                                "            if True:\n                found.append((track, t0, e[\"t\"]))"),
    "M10 skip recipients": ("        if set(d.get(\"recipients\") or {}) != lit:", "        if False:"),
    "M11 arms ignore gaming": (
        "mine = shown if gaming else {b: requested.get(b, shown[b]) for b in events.LIGHTS}",
        "mine = {b: requested.get(b, shown[b]) for b in events.LIGHTS}"),
}


def main():
    root = os.path.abspath(sys.argv[1])
    work = tempfile.mkdtemp(prefix="verify-r9-behaviour-")
    try:
        for rel in ("tools/den-core", "ha-config/custom_components/den_lighting", "stack/services/perception-lab",
                    "tools/la-latency"):
            shutil.copytree(os.path.join(root, rel), os.path.join(work, rel),
                            ignore=shutil.ignore_patterns("__pycache__"))
        core = os.path.join(work, "tools", "den-core")
        sys.dont_write_bytecode = True
        sys.path.insert(0, core)
        os.chdir(core)
        import decide
        import events
        import light_model
        import runner
        import score
        import synthetic
        import test_rung9 as tr
        source = open(os.path.join(core, "score.py"), encoding="utf-8").read()

        def load(*names):
            text = source
            for name in names:
                old, new = MUTANTS[name]
                assert text.count(old) == 1, name
                text = text.replace(old, new)
            mod = types.ModuleType("score_mut")
            mod.__file__ = os.path.join(core, "score.py")
            exec(compile(text, mod.__file__, "exec"), mod.__dict__)
            return mod

        tmp = tempfile.mkdtemp(prefix="logs-", dir=work)
        gate = json.loads(score.GATE.read_text("utf-8"))
        for entry in list(gate["parameters"].values()) + [s["floor"] for s in gate["surrogates"].values()]:
            entry.update(value=entry["proposed"], status="set")
        gate["parameters"]["compensation"]["value"] = "none-v0"
        gate_path = os.path.join(tmp, "gate.json")
        open(gate_path, "w").write(json.dumps(gate))
        configs = {"decide": {"slots": tr.filled(decide.DEFAULT_CONFIG["slots"]), "light_model": tr.real_model()}}

        def write(header, log, name):
            path = os.path.join(tmp, name)
            events.write(path, header, log)
            return path

        def summary(record):
            return {k: v["ok"] for k, v in record["checks"].items()}, record["verdict"]

        scored = write(*tr.evening_log(synthetic_flag=False, configs=configs, touch=True, game=True), "scored.jsonl")
        base = score.score(scored, gate_path)
        print("INFO baseline on ScoreTest's scored log: reasons=%s checks/verdict=%s" % (base[1], summary(base[0])))

        # --- M6b: decide made 120 ms slower per decision -------------------------------------------------------
        orig = decide.make_policy

        def slow_make(config=None):
            policy = orig(config)
            inner = policy.handle

            def slow(e):
                out = inner(e)
                if out:
                    time.sleep(0.12)
                return out
            policy.handle = slow
            return policy
        with mock.patch.object(decide, "make_policy", slow_make):
            b = summary(score.score(scored, gate_path)[0])
            m = summary(load("M6b timing x10").score(scored, gate_path)[0])
        print("RESULT M6b  slow decide (120 ms/decision): unmutated %s | mutant %s" % (b, m))
        print("%s M6b mutant not equivalent (timing check differs: %s vs %s; verdict %s vs %s)" % (
            "PASS" if b[0]["timing"] != m[0]["timing"] else "FAIL", b[0]["timing"], m[0]["timing"], b[1], m[1]))

        # --- M15 / M7 / M8: a non-synthetic log where decide never decides ------------------------------------
        lit = {"on": True, "brightness": 180, "kelvin": 2700, "context": "unknown"}
        quiet = [(synthetic.T0 + dt, "light_observed", {"entity_id": e, **lit}) for dt in (0, 600) for e in events.LIGHTS]
        h, log = runner.run(runner.tagged(quiet, "synthetic"), runner.load_policies(("need", "incumbent", "decide"),
                                                                                       configs), synthetic=False)
        nd = sum(1 for e in log if e["kind"] == "decision" and e["source"] == "decide")
        empty = write(h, log, "empty.jsonl")
        print("INFO zero-decision log: %d decide decision(s), %d event(s)" % (nd, len(log)))
        b = summary(score.score(empty, gate_path)[0])
        for name in ("M15 timing empty", "M7 record empty", "M8 surrogate None"):
            m = summary(load(name).score(empty, gate_path)[0])
            diff = {k: (b[0][k], m[0][k]) for k in b[0] if b[0][k] != m[0][k]}
            print("RESULT %-18s zero-decision log: unmutated %s | mutant %s" % (name, b, m))
            print("%s %s not equivalent (checks that differ: %s; verdict %s -> %s)" % (
                "PASS" if diff else "FAIL", name, diff, b[1], m[1]))
        m = summary(load("M15 timing empty", "M7 record empty", "M8 surrogate None").score(empty, gate_path)[0])
        print("RESULT M15+M7+M8 together on the zero-decision log: verdict %s -> %s" % (b[1], m[1]))

        # --- M8 alone: decisions, but no need episode (facts stripped) ----------------------------------------
        items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True)
        nofacts = [i for i in items if i[1] != "facts"]
        h, log = runner.run(runner.tagged(nofacts, "synthetic"), runner.load_policies(("need", "incumbent", "decide"),
                                                                                         configs), synthetic=False)
        nd = sum(1 for e in log if e["kind"] == "decision" and e["source"] == "decide")
        path = write(h, log, "nofacts.jsonl")
        print("INFO no-facts log: %d decide decision(s), %d episode(s)" % (nd, len(score.episodes(log))))
        b = summary(score.score(path, gate_path)[0])
        m = summary(load("M8 surrogate None").score(path, gate_path)[0])
        print("RESULT M8 alone on a log with decisions and no episode: unmutated %s | mutant %s" % (b, m))
        print("%s M8 alone flips the verdict (%s -> %s)" % ("PASS" if b[1] != m[1] else "FAIL", b[1], m[1]))

        # --- M8 alone on the documented export path: bulbs' brightness/kelvin null (recorder_lights shape) -------
        rows = [(t, k, dict(d, brightness=None, kelvin=None) if k == "light_observed" else d) for t, k, d in items]
        h, log = runner.run(runner.tagged(rows, "synthetic"), runner.load_policies(("need", "incumbent", "decide"),
                                                                                      configs), synthetic=False,
                            plant=False, sources=[{"name": "synthetic:evening"}])
        path = write(h, log, "recorder.jsonl")
        rb = score.score(path, gate_path)[0]
        rm = load("M8 surrogate None").score(path, gate_path)[0]
        print("INFO recorder-shaped log: %d decide decision(s), %d episode(s), candidate anticipation %s" % (
            sum(1 for e in log if e["kind"] == "decision" and e["source"] == "decide"), len(score.episodes(log)),
            rb["surrogates"]["arms"]["candidate"]["anticipation"]))
        b, m = summary(rb), summary(rm)
        print("RESULT M8 alone on a recorder-shaped (null-level) log: unmutated %s | mutant %s" % (b, m))
        print("%s M8 alone flips the verdict on the export path (%s -> %s)" % (
            "PASS" if b[1] != m[1] else "FAIL", b[1], m[1]))

        # --- M9: a false start (starting -> none) then a real episode ------------------------------------------
        def ps(t, state):
            return {"t": t, "kind": "person_state", "source": "need",
                    "data": {"track_id": "room", "state_id": "room@%.3f" % t, "focused_engagement": {"state": state}}}
        hand = [ps(10, "starting"), ps(12, "none"), ps(20, "starting"), ps(26, "engaged"), ps(40, "ending"),
                ps(50, "none")]
        b, m = score.episodes(hand), load("M9 episodes w/o engaged").episodes(hand)
        print("RESULT M9 episodes(): unmutated %s | mutant %s" % (b, m))
        print("%s M9 not equivalent" % ("PASS" if b != m else "FAIL"))

        # --- M10: a decide decision with one recipient removed -------------------------------------------------
        header, log = events.read(scored)
        config = next(p["config"] for p in header["policies"] if p["id"] == "decide")
        model = light_model.LightModel(config["light_model"])
        bad = json.loads(json.dumps(log))
        target = next(e for e in bad if e["kind"] == "decision" and e["source"] == "decide" and e["data"]["recipients"])
        dropped = sorted(target["data"]["recipients"])[0]
        del target["data"]["recipients"][dropped]
        b = score.record_problems(bad, config, model)
        m = load("M10 skip recipients").record_problems(bad, config, model)
        print("RESULT M10 record_problems() with recipient %s removed: unmutated %s | mutant %s" % (dropped, b, m))
        print("%s M10 not equivalent" % ("PASS" if b[0] != m[0] else "FAIL"))

        # --- M11: candidate arm during den_lighting's game -----------------------------------------------------
        mut = load("M11 arms ignore gaming")
        b, m = score.arms(header, log)["candidate"], mut.arms(header, log)["candidate"]
        t_game = [e["t"] for e in log if e["kind"] == "mode" and e["source"] == "incumbent" and e["data"]["gaming"]]
        print("INFO game mode events from den_lighting at t-T0 = %s" % [round(t - synthetic.T0, 1) for t in t_game][:4])

        def at(timeline, t):
            return [s for tt, s, _ in timeline if tt <= t][-1]
        if t_game:
            probe_t = t_game[0] + 1
            print("RESULT M11 candidate scene 1 s into the game: unmutated %s | mutant %s" % (
                at(b, probe_t), at(m, probe_t)))
        print("%s M11 not equivalent (candidate timelines differ: %s)" % ("PASS" if b != m else "FAIL", b != m))
        rb = score.score(scored, gate_path)[0]["surrogates"]["arms"]["candidate"]
        rm = mut.score(scored, gate_path)[0]["surrogates"]["arms"]["candidate"]
        print("INFO M11 candidate rates on ScoreTest's log (no episode overlaps the game): unmutated %s | mutant %s"
              % ({k: v["rate"] for k, v in rb.items()}, {k: v["rate"] for k, v in rm.items()}))
    finally:
        os.chdir("/")
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
