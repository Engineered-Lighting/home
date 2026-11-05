"""verify_atk-placeholder-look-not-slotted-reproduce: does score.py ignore the seed Look's placeholder scene?

What it checks
  decide's DEFAULT_CONFIG looks['reading@0'].scene is labelled "placeholder: the reading look values are the owner
  choice". It is not under config['slots'], and score.refusals() looks only at config['slots'] and the gate.
  The probe builds scored-shaped logs (non-synthetic flag, every decide slot filled at its proposed value,
  compensation P1, fake model relabelled fake=False, synthetic evening with a touch hold and a game) that differ ONLY
  in the reading look's brightness: the shipped placeholder (255) and other values an owner might choose
  (BRIGHTNESSES: 200, 190, 180, 150; den_lighting's normal scene in the synthetic evening is 180).
  Each is scored with the same filled gate (compensation P1). It prints, for each: score's refusals, the
  logged open_slots, the candidate's anticipation and stability rates, and the verdict.
  Defect if no log is refused and the verdict changes with the placeholder alone.

How to run (synthetic data only; temp dirs only; no host contacted):
  python3 -B verify_atk-placeholder-look-not-slotted-reproduce.py /path/to/den-core-live-checkout
"""
import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path

BRIGHTNESSES = (255, 200, 190, 180, 150)


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

    look = decide.DEFAULT_CONFIG["looks"][decide.LOOK]
    print("INFO shipped look %s scene %s; status %r" % (decide.LOOK, look["scene"], look["status"]))
    print("INFO 'looks' under config['slots']? %s" % ("looks" in decide.DEFAULT_CONFIG["slots"]))
    tmp = Path(tempfile.mkdtemp(prefix="verify-r9-look-"))
    try:
        gate = json.loads(score.GATE.read_text("utf-8"))
        for entry in list(gate["parameters"].values()) + [s["floor"] for s in gate["surrogates"].values()]:
            entry.update(value=entry["proposed"], status="set")
        gate["parameters"]["compensation"]["value"] = "P1"
        gate_path = tmp / "gate.json"
        gate_path.write_text(json.dumps(gate), "ascii")
        verdicts = {}
        for brightness in BRIGHTNESSES:
            model = light_model.fake()
            model.update(version="verify-lm-1", fake=False)
            slots = {n: {**e, "value": e["proposed"], "status": "set"}
                     for n, e in decide.DEFAULT_CONFIG["slots"].items()}
            looks = copy.deepcopy(decide.DEFAULT_CONFIG["looks"])
            looks[decide.LOOK]["scene"]["brightness"] = brightness
            configs = {"decide": {"slots": slots, "light_model": model, "compensation": {"option": "P1"},
                                  "looks": looks}}
            items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True, game=True)
            header, log = runner.run(runner.tagged(items, "synthetic"),
                                     runner.load_policies(["need", "incumbent", "decide"], configs),
                                     synthetic=False, sources=[{"name": "synthetic:evening"}])
            path = tmp / ("look-%d.jsonl" % brightness)
            events.write(path, header, log)
            opened = sorted({s for e in log if e["kind"] == "decision" and e["source"] == "decide"
                             for s in e["data"]["inputs"]["open_slots"]})
            record, reasons = score.score(path, gate_path)
            cand = record["surrogates"]["arms"]["candidate"]
            verdicts[brightness] = (reasons, record["verdict"])
            print("RESULT look brightness %d: refusals=%s logged open_slots=%s anticipation=%s stability=%s "
                  "verdict=%s" % (brightness, reasons, opened, cand["anticipation"]["rate"],
                                  cand["stability"]["rate"], record["verdict"]))
        if not any(r for r, _ in verdicts.values()) and len({v for _, v in verdicts.values()}) > 1:
            print("FAIL the scored verdict flips with the placeholder look alone, and no run is refused: "
                  "DEFECT REPRODUCED")
        elif not verdicts[255][0]:
            print("FAIL the placeholder look is not refused (the verdict did not flip over BRIGHTNESSES)")
        else:
            print("PASS a scored run is refused while the look is a placeholder")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
