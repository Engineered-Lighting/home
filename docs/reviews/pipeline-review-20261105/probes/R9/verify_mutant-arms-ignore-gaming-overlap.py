#!/usr/bin/env python3
"""Verifier probe: does score.py mutant M11 (candidate arm ignores den_lighting's game) change the GATED surrogates
when a game overlaps a need episode, or only the candidate's timeline and the reported (never gated) service?

    python3 -B verify_mutant-arms-ignore-gaming-overlap.py <den-core-live checkout root>

It builds synthetic evenings (non-synthetic flag, filled decide slots, a non-fake model) with den_lighting gaming
samples placed at several offsets around the second reading block, scores each with the unmutated and the mutated
score.py, and prints the candidate rates and verdicts side by side. Copies the tools into a temp folder (TMPDIR);
never touches the checkout. Synthetic data only.
"""
import json
import os
import shutil
import sys
import tempfile
import types

OLD = "mine = shown if gaming else {b: requested.get(b, shown[b]) for b in events.LIGHTS}"
NEW = "mine = {b: requested.get(b, shown[b]) for b in events.LIGHTS}"


def main():
    root = os.path.abspath(sys.argv[1])
    work = tempfile.mkdtemp(prefix="verify-r9-m11-")
    try:
        for rel in ("tools/den-core", "ha-config/custom_components/den_lighting", "stack/services/perception-lab",
                    "tools/la-latency"):
            shutil.copytree(os.path.join(root, rel), os.path.join(work, rel),
                            ignore=shutil.ignore_patterns("__pycache__"))
        core = os.path.join(work, "tools", "den-core")
        sys.dont_write_bytecode = True
        sys.path.insert(0, core)
        os.chdir(core)
        import decide, events, runner, score, synthetic  # noqa: E401
        import test_rung9 as tr
        src = open(os.path.join(core, "score.py"), encoding="utf-8").read()
        assert src.count(OLD) == 1
        mut = types.ModuleType("score_m11")
        mut.__file__ = os.path.join(core, "score.py")
        exec(compile(src.replace(OLD, NEW), mut.__file__, "exec"), mut.__dict__)
        gate = json.loads(score.GATE.read_text("utf-8"))
        for entry in list(gate["parameters"].values()) + [s["floor"] for s in gate["surrogates"].values()]:
            entry.update(value=entry["proposed"], status="set")
        gate["parameters"]["compensation"]["value"] = "none-v0"
        gate_path = os.path.join(work, "gate.json")
        open(gate_path, "w").write(json.dumps(gate))
        configs = {"decide": {"slots": tr.filled(decide.DEFAULT_CONFIG["slots"]), "light_model": tr.real_model()}}
        items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True)
        differ = 0
        for start in (560, 600, 640, 655, 700, 800):  # b4 reading is T0+660..T0+960
            samples = [(dt, "playing") for dt in range(start, start + 111, 10)] + \
                      [(dt, "idle") for dt in range(start + 120, start + 181, 10)]
            rows = items + [(synthetic.T0 + dt, "gaming_sample", {"status": s, "sampled_at": synthetic.T0 + dt - 0.5})
                            for dt, s in samples]
            h, log = runner.run(runner.tagged(rows, "synthetic"), runner.load_policies(("need", "incumbent", "decide"),
                                                                                          configs), synthetic=False)
            path = os.path.join(work, "g%d.jsonl" % start)
            events.write(path, h, log)
            found = [(round(a - synthetic.T0), round(b - synthetic.T0)) for _, a, b in score.episodes(log)]
            games = [round(e["t"] - synthetic.T0) for e in log if e["kind"] == "mode" and e["source"] == "incumbent"
                     and e["data"]["gaming"]][:1]
            out = []
            for mod in (score, mut):
                rec = mod.score(path, gate_path)[0]
                cand = rec["surrogates"]["arms"]["candidate"]
                out.append(((cand["anticipation"]["rate"], cand["stability"]["rate"]), rec["verdict"]))
            differ += out[0] != out[1]
            print("RESULT game samples from T0+%d (game mode at %s), episodes %s: unmutated %s | mutant %s %s" % (
                start, games, found, out[0], out[1], "DIFFER" if out[0] != out[1] else "same"))
        print("RESULT gated candidate rates differ in %d of 6 overlap layouts" % differ)
    finally:
        os.chdir("/")
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
