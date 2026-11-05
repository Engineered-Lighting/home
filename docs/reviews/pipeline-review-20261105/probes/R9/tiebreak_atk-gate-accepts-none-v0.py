#!/usr/bin/env python3
"""tiebreak_atk-gate-accepts-none-v0: does a gate set to 'none-v0' give a scored verdict, and does the option matter?

What it checks
  On the synthetic evening with a touch hold and a game (synthetic flag off, decide's slots filled at their
  proposed values, the fake model relabelled fake=False: a test-only scored-shaped log, as test_rung9 builds), it
  makes one log per compensation option none-v0, P0, P1 and scores each with a gate whose compensation slot equals
  the log's option (all other gate slots at their proposed values). For each it prints refusals, verdict, the
  candidate's anticipation and stability rates and the record's 'compensation' field. Then it scores the none-v0 log
  with a gate set to P1 and prints the refusal text (the message the owner sees with run.py's default option).
  It prints RESULT lines; FAIL when a non-register option (none-v0) is scored to a verdict without refusal.

How to run (synthetic data only, temp dirs only, no host contacted):
  python3 -B tiebreak_atk-gate-accepts-none-v0.py /path/to/den-core-live-checkout
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path


def main():
    sys.dont_write_bytecode = True
    root = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(root / "tools" / "den-core"))
    import decide
    import events
    import light_model
    import runner
    import score
    import synthetic

    register = json.loads((root / "docs" / "architecture" / "live-v1" / "decisions.json").read_text("utf-8"))
    comp = next(d for d in register["decisions"] if d["id"] == "compensation")
    options = [o["id"] for o in comp["options"]]
    tmp = Path(tempfile.mkdtemp(prefix="tiebreak-r9-none-v0-"))
    failed = False
    try:
        def make_log(option):
            model = light_model.fake()
            model.update(version="tiebreak-lm-1", fake=False)
            slots = {n: {**e, "value": e["proposed"], "status": "set"}
                     for n, e in decide.DEFAULT_CONFIG["slots"].items()}
            configs = {"decide": {"slots": slots, "light_model": model, "compensation": {"option": option}}}
            items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True, game=True)
            header, log = runner.run(runner.tagged(items, "synthetic"),
                                     runner.load_policies(["need", "incumbent", "decide"], configs),
                                     synthetic=False, sources=[{"name": "synthetic:evening"}])
            path = tmp / ("log-%s.jsonl" % option)
            events.write(path, header, log)
            held = sum(1 for e in log if e["kind"] == "decision" and e["source"] == "decide"
                       and e["data"]["inputs"]["hold_set"]["held"])
            return path, held

        def make_gate(option):
            gate = json.loads(score.GATE.read_text("utf-8"))
            for entry in list(gate["parameters"].values()) + [s["floor"] for s in gate["surrogates"].values()]:
                entry.update(value=entry["proposed"], status="set")
            gate["parameters"]["compensation"]["value"] = option
            path = tmp / ("gate-%s.json" % option)
            path.write_text(json.dumps(gate), "ascii")
            return path

        logs = {}
        for option in ("none-v0", "P0", "P1"):
            path, held = make_log(option)
            logs[option] = path
            record, reasons = score.score(path, make_gate(option))
            cand = record["surrogates"]["arms"]["candidate"] if record else {}
            print("RESULT option %-7s in register=%s held-decisions=%d refusals=%s verdict=%s "
                  "anticipation=%s stability=%s record.compensation=%s"
                  % (option, option in options, held, reasons, record and record["verdict"],
                     cand.get("anticipation", {}).get("rate"), cand.get("stability", {}).get("rate"),
                     record and record["compensation"]))
            if option not in options and record is not None and record["verdict"] is not None:
                failed = True
        _, reasons = score.score(logs["none-v0"], make_gate("P1"))
        print("RESULT none-v0 log against a P1 gate: refusals=%s" % reasons)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failed:
        print("FAIL: a log under compensation none-v0 (not a register option %s) gets a scored verdict" % options)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
