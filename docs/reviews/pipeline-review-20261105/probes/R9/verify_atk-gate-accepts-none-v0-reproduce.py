"""verify_atk-gate-accepts-none-v0-reproduce: does score.py give a scored verdict for a compensation value outside the register?

What it checks
  decisions.json 'compensation' (the register, pending) lists its options. rung9-gate-v0.json's
  parameters.compensation slot says the scored log must have used a register option. score.refusals() only checks
  that the gate's value equals the log's option. The probe:
    1. prints the register's option ids (from the checkout's docs/architecture/live-v1/decisions.json);
    2. builds a scored-shaped log (non-synthetic flag, decide's slots filled at their proposed values, fake model
       relabelled fake=False) from the synthetic evening with a touch hold and a game, with decide's default
       compensation option, and counts the decide decisions made while a hold was open;
    3. scores it with a gate whose compensation slot is set to that same option.
  Defect if the option is not a register option and the run is not refused (a pass or fail verdict is given).

How to run (synthetic data only; temp dirs only; no host contacted):
  python3 -B verify_atk-gate-accepts-none-v0-reproduce.py /path/to/den-core-live-checkout
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
    entries = register["decisions"] if isinstance(register, dict) else register
    comp = next(d for d in entries if d["id"] == "compensation")
    options = [o["id"] for o in comp["options"]]
    default = decide.DEFAULT_CONFIG["compensation"]["option"]
    print("INFO register compensation options: %s (status %s); decide's default option: %s"
          % (options, comp["status"], default))
    tmp = Path(tempfile.mkdtemp(prefix="verify-r9-none-v0-"))
    try:
        model = light_model.fake()
        model.update(version="verify-lm-1", fake=False)
        slots = {n: {**e, "value": e["proposed"], "status": "set"} for n, e in decide.DEFAULT_CONFIG["slots"].items()}
        configs = {"decide": {"slots": slots, "light_model": model}}
        items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True, game=True)
        header, log = runner.run(runner.tagged(items, "synthetic"),
                                 runner.load_policies(["need", "incumbent", "decide"], configs),
                                 synthetic=False, sources=[{"name": "synthetic:evening"}])
        path = tmp / "log.jsonl"
        events.write(path, header, log)
        held = sum(1 for e in log if e["kind"] == "decision" and e["source"] == "decide"
                   and e["data"]["inputs"]["hold_set"]["held"])
        total = sum(1 for e in log if e["kind"] == "decision" and e["source"] == "decide")
        print("INFO %d of %d decide decisions were made while a hold was open" % (held, total))
        gate = json.loads(score.GATE.read_text("utf-8"))
        for entry in list(gate["parameters"].values()) + [s["floor"] for s in gate["surrogates"].values()]:
            entry.update(value=entry["proposed"], status="set")
        gate["parameters"]["compensation"]["value"] = default
        gate_path = tmp / "gate.json"
        gate_path.write_text(json.dumps(gate), "ascii")
        record, reasons = score.score(path, gate_path)
        print("RESULT gate compensation %r: refusals=%s verdict=%s" % (default, reasons, record and record["verdict"]))
        if default not in options and not reasons:
            print("FAIL a scored verdict is given for compensation %r, which is not a register option: "
                  "DEFECT REPRODUCED" % default)
        else:
            print("PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
