"""verify_atk-placeholder-look-not-slotted: does rung 9's scored verdict rest on decide's placeholder reading look?
(finding atk-placeholder-look-not-slotted, verifier lens: intent and spec)

What it checks
  Builds the same test-only non-synthetic log test_rung9.ScoreTest.scored_log builds (a synthetic evening with
  touch and game, the synthetic flag off, every decide slot set to its proposed value, a non-fake copy of the fake
  light model), but with compensation P1 in both the log and the gate (the gate's proposed value), and fills every
  gate slot with its proposed value. It scores it with score.score() as the owner would after filling the slots,
  once with the default placeholder look (reading@0 = 255 @ 2700 K) and once each with other look levels. It prints
  the refusals (none expected: the look is not a slot) and the anticipation rate and verdict per look.

How to run (synthetic data only; temp dirs only; nothing contacts a host):
  python3 -B verify_atk-placeholder-look-not-slotted.py /path/to/den-core-live-checkout [255,220,200]
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path


def main():
    root = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(root / "tools" / "den-core"))
    import decide
    import events
    import light_model
    import runner
    import score
    import synthetic

    def filled(slots):
        return {n: {**e, "value": e["proposed"], "status": "set"} for n, e in slots.items()}

    model = light_model.fake()
    model.update(version="probe-lm-1", fake=False)
    tmp = Path(tempfile.mkdtemp(prefix="r9-look-"))
    try:
        gate = json.loads(score.GATE.read_text("utf-8"))
        for entry in list(gate["parameters"].values()) + [s["floor"] for s in gate["surrogates"].values()]:
            entry.update(value=entry["proposed"], status="set")
        gate_path = tmp / "gate.json"
        gate_path.write_text(json.dumps(gate), "ascii")
        print("INFO gate compensation %s, anticipation floor %s, rise %s"
              % (gate["parameters"]["compensation"]["value"], gate["surrogates"]["anticipation"]["floor"]["value"],
                 gate["parameters"]["anticipation_rise"]["value"]))
        print("INFO decide's default look: %s (%s)" % (decide.DEFAULT_CONFIG["looks"]["reading@0"]["scene"],
                                                     decide.DEFAULT_CONFIG["looks"]["reading@0"]["status"]))
        for brightness in [int(x) for x in (sys.argv[2].split(",") if len(sys.argv) > 2 else ["255", "220", "200"])]:
            config = {"slots": filled(decide.DEFAULT_CONFIG["slots"]), "light_model": model,
                      "compensation": {"option": "P1"}}
            if brightness != 255:
                config["looks"] = {"reading@0": {"scene": {"on": True, "brightness": brightness, "kelvin": 2700}}}
            items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True, game=True)
            header, log = runner.run(runner.tagged(items, "synthetic"),
                                     runner.load_policies(("need", "incumbent", "decide"), {"decide": config}),
                                     synthetic=False, sources=[{"name": "synthetic:evening", "params": {}}])
            path = tmp / ("look-%d.jsonl" % brightness)
            events.write(path, header, log)
            record, reasons = score.score(path, gate_path)
            if record is None:
                print("RESULT look %d@2700: refused: %s" % (brightness, reasons))
                continue
            cand = record["surrogates"]["arms"]["candidate"]
            print("RESULT look %d@2700: refusals %s; anticipation %s, stability %s; verdict %s"
                  % (brightness, reasons or "none", cand["anticipation"]["rate"], cand["stability"]["rate"],
                     record["verdict"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
