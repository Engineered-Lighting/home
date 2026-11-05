#!/usr/bin/env python3
"""Verify (R9, recorder-levels-unknown-episodes): end to end through score.score() with a FILLED gate and a non-fake
model (the same test-only construct test_rung9.ScoreTest uses), does a replay whose light_observed rows carry
brightness/kelvin null (as sources.recorder_lights writes them) end with verdict 'fail', and what do the check lines
say?

    python3 -B verify_recorder-levels-unknown-episodes.py <den-core-live checkout root>

Synthetic evening (need, incumbent, decide), synthetic flag off, plant off, written to a temp dir only. No host.
Prints RESULT lines; FAIL when the level-less run gets verdict 'fail' with 0 episodes scored (i.e. the finding holds).
"""
import json
import os
import shutil
import sys
import tempfile


def main():
    root = os.path.abspath(sys.argv[1])
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.join(root, "tools", "den-core"))
    import decide
    import events
    import light_model
    import runner
    import score
    import synthetic

    tmp = tempfile.mkdtemp(prefix="r9-verify-rec-")
    try:
        gate = json.loads(score.GATE.read_text("utf-8"))
        for entry in list(gate["parameters"].values()) + [s["floor"] for s in gate["surrogates"].values()]:
            entry.update(value=entry["proposed"], status="set")
        gate["parameters"]["compensation"]["value"] = "none-v0"
        gpath = os.path.join(tmp, "gate.json")
        with open(gpath, "w") as f:
            json.dump(gate, f)
        model = light_model.fake()
        model.update(version="test-lm-1", fake=False)
        slots = {n: {**e, "value": e["proposed"], "status": "set"} for n, e in decide.DEFAULT_CONFIG["slots"].items()}
        configs = {"decide": {"slots": slots, "light_model": model}}
        items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True)
        n_obs = sum(1 for _, k, _ in items if k == "light_observed")
        print("RESULT evening light_observed rows: %d" % n_obs)
        verdicts = {}
        for label, strip in (("levels known", False), ("levels null (recorder-shaped)", True)):
            rows = [(t, k, dict(d, brightness=None, kelvin=None) if (k == "light_observed" and strip) else d)
                    for t, k, d in items]
            header, log = runner.run(runner.tagged(rows, "synthetic"),
                                     runner.load_policies(["need", "incumbent", "decide"], configs),
                                     synthetic=False, plant=False, sources=[{"name": "synthetic:evening"}])
            path = os.path.join(tmp, "%s.jsonl" % ("null" if strip else "known"))
            events.write(path, header, log)
            record, reasons = score.score(path, gpath)
            verdicts[label] = record and record["verdict"]
            print("RESULT %-30s refusals=%s verdict=%s" % (label, reasons, record and record["verdict"]))
            if record:
                for line in score.lines(record):
                    if line.startswith(("anticipation", "stability", "verdict")):
                        print("RESULT   %s" % line)
        if verdicts["levels null (recorder-shaped)"] == "fail" and verdicts["levels known"] == "pass":
            print("FAIL (finding holds): the level-less replay is scored 'fail', the same verdict as a missed floor")
            return 1
        print("PASS (finding refuted)")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
