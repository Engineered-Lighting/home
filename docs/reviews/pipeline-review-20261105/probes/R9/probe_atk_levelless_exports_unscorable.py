"""probe_atk_levelless_exports_unscorable: on bulb states without levels (what the HA recorder export gives:
den-core's recorder_lights emits light_observed with brightness and kelvin null), can score.py score the
candidate's anticipation and stability at all, and what verdict does it print?

What it checks
  The synthetic evening (touch) is replayed the way a --lights replay runs: plant off, and every light_observed
  with brightness/kelvin null, as sources.recorder_lights emits them. den_lighting then seeds its normal scenes from
  level-less states, decide's 'base' rule requests mode.normal (no levels), so the light model predicts None before
  each reading onset. The probe scores the log (decide's slots filled, fake() with fake=False, gate filled at the
  proposed values, as the suite's ScoreTest does) and prints the surrogate counts and the verdict.
  Control: the same evening with levels (plant on) scores both episodes.

How to run (synthetic data only; temp folder only; no host contacted):
  python3 -B probe_atk_levelless_exports_unscorable.py /path/to/den-core-live-checkout
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path


def main():
    root = Path(sys.argv[1]).resolve()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root / "tools" / "den-core"))
    import decide
    import events
    import light_model
    import runner
    import score
    import synthetic

    slots = {n: {**e, "value": e["proposed"], "status": "set"} for n, e in decide.DEFAULT_CONFIG["slots"].items()}
    model = light_model.fake()
    model.update(version="probe-lm-1", fake=False)
    configs = {"decide": {"slots": slots, "light_model": model}}
    gate = json.loads(score.GATE.read_text("utf-8"))
    for entry in list(gate["parameters"].values()) + [s["floor"] for s in gate["surrogates"].values()]:
        entry.update(value=entry["proposed"], status="set")
    gate["parameters"]["compensation"]["value"] = "none-v0"
    tmp = Path(tempfile.mkdtemp(prefix="probe-r9-levelless-"))
    try:
        gate_path = tmp / "gate.json"
        gate_path.write_text(json.dumps(gate), "ascii")
        for label, levelless in (("control: levels, plant on", False), ("recorder-style: no levels, plant off", True)):
            items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True)
            if levelless:
                items = [(t, k, ({**d, "brightness": None, "kelvin": None} if k == "light_observed" else d))
                         for t, k, d in items]
            header, log = runner.run(runner.tagged(items, "synthetic"),
                                     runner.load_policies(["need", "incumbent", "decide"], configs),
                                     synthetic=False, plant=not levelless)
            path = tmp / ("%d.jsonl" % levelless)
            events.write(path, header, log)
            record, reasons = score.score(path, gate_path)
            cand = record["surrogates"]["arms"]["candidate"]
            print("RESULT %s: verdict %s; anticipation %s (%d of %d scored, %d unknown); stability %s; checks: %s"
                  % (label, record["verdict"], cand["anticipation"]["rate"], cand["anticipation"]["scored"],
                     cand["anticipation"]["episodes"], cand["anticipation"]["unknown"], cand["stability"]["rate"],
                     {k: v["ok"] for k, v in record["checks"].items()}))
            if levelless:
                print("INFO anticipation line: %s" % record["checks"]["anticipation"]["text"])
                if cand["anticipation"]["scored"] == 0 and record["verdict"] == "fail":
                    print("FAIL level-less bulb states make every episode unknown and score.py prints verdict 'fail' "
                          "(a candidate failure), not 'cannot be scored'")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
