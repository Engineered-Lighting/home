#!/usr/bin/env python3
"""Verifier probe: on synthetic evenings with the generator's default noise (unclear/invalid/failed fact lines),
does need emit false starts (starting -> none without engaged), and does score.py mutant M9 ('if engaged:' ->
'if True:' in episodes()) then change the episode count and the candidate's gated rates?

    python3 -B verify_mutant-episodes-without-engaged-noisy.py <den-core-live checkout root>

Copies the tools into a temp folder (TMPDIR); never touches the checkout. Synthetic data only.
"""
import json
import os
import shutil
import sys
import tempfile
import types

OLD = "            if engaged:\n                found.append((track, t0, e[\"t\"]))"
NEW = "            if True:\n                found.append((track, t0, e[\"t\"]))"


def main():
    root = os.path.abspath(sys.argv[1])
    work = tempfile.mkdtemp(prefix="verify-r9-m9-")
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
        mut = types.ModuleType("score_m9")
        mut.__file__ = os.path.join(core, "score.py")
        exec(compile(src.replace(OLD, NEW), mut.__file__, "exec"), mut.__dict__)
        gate = json.loads(score.GATE.read_text("utf-8"))
        for entry in list(gate["parameters"].values()) + [s["floor"] for s in gate["surrogates"].values()]:
            entry.update(value=entry["proposed"], status="set")
        gate["parameters"]["compensation"]["value"] = "none-v0"
        gate_path = os.path.join(work, "gate.json")
        open(gate_path, "w").write(json.dumps(gate))
        configs = {"decide": {"slots": tr.filled(decide.DEFAULT_CONFIG["slots"]), "light_model": tr.real_model()}}
        differ = 0
        for seed in (20261103, 1, 2, 3, 4, 5):
            items, _, _ = synthetic.evening(seed=seed, unclear_rate=0.15, invalid_rate=0.05, fail_rate=0.05, touch=True)
            h, log = runner.run(runner.tagged(items, "synthetic"), runner.load_policies(("need", "incumbent", "decide"),
                                                                                           configs), synthetic=False)
            path = os.path.join(work, "s%d.jsonl" % seed)
            events.write(path, h, log)
            nb, nm = len(score.episodes(log)), len(mut.episodes(log))
            out = []
            for mod in (score, mut):
                rec = mod.score(path, gate_path)[0]
                cand = rec["surrogates"]["arms"]["candidate"]
                out.append(((cand["anticipation"]["rate"], cand["stability"]["rate"]), rec["verdict"]))
            differ += out[0] != out[1]
            print("RESULT seed %-9d episodes unmutated %d mutant %d; candidate (anticipation, stability), verdict: "
                  "unmutated %s | mutant %s %s" % (seed, nb, nm, out[0], out[1], "DIFFER" if out[0] != out[1] else "same"))
        print("RESULT gated rates differ on %d of 6 noisy evenings" % differ)
    finally:
        os.chdir("/")
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
