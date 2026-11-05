"""verify_atk_decision_line_limit: independent check that decide's Decision lines overflow den-event-1's
MAX_LINE_BYTES (16384) on a rung-8-sized model, using a smooth, physically shaped basis (not pseudo-random).

What it checks
  - Builds a den-core-format light model with 32 x 18 = 576 blocks (rung 8 METHOD-V1's proposed block_px 20),
    each bulb's basis a smooth falloff peak / (1 + d^2 / r^2) from a plausible spot (the shape den-light's own
    synthetic.py uses), fake=False.
  - Fills the task_points slot (status set) with only 4 blocks per region.
  - Runs the synthetic evening with the touch (a hold on rear-left) through runner.run (run.py's path), under
    compensation none-v0 and P1: prints OK or the ValueError.
  - Re-runs P1 with events.MAX_LINE_BYTES raised in-process (no file changed) to measure which fields make
    the largest decide Decision big.

How to run (synthetic data only, nothing written, no host contacted):
  python3 -B verify_atk_decision_line_limit.py /path/to/den-core-live-checkout
"""
import json
import sys
from pathlib import Path


def main():
    root = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(root / "tools" / "den-core"))
    import events
    import light_model
    import runner
    import synthetic

    cols, rows = 32, 18
    names = ["r%02dc%02d" % (b // cols, b % cols) for b in range(cols * rows)]
    spots = {"light.den_front_left": (0.2, 0.25), "light.front_right": (0.8, 0.25),
             "light.den_rear_left": (0.2, 0.8), "light.den_rear_right": (0.8, 0.8)}
    model = light_model.fake()
    model["sample_points"] = [{"point_id": p, "kind": None, "xyz": None, "normal": None, "rect": None} for p in names]
    for e, (sx, sy) in spots.items():
        for k, f in (("2202", 0.9), ("3290", 1.0), ("6500", 1.05)):
            model["basis"][e][k] = {
                p: round(f / (1 + (((b % cols + 0.5) / cols - sx) ** 2 + ((b // cols + 0.5) / rows - sy) ** 2) / 0.3 ** 2), 6)
                for b, p in enumerate(names)}
    model.update(version="verify-576-smooth", fake=False)
    desk = [names[4 * cols + 6], names[4 * cols + 7], names[5 * cols + 6], names[5 * cols + 7]]
    chair = [names[13 * cols + 24], names[13 * cols + 25], names[14 * cols + 24], names[14 * cols + 25]]
    slots = {"task_points": {"value": {"room": desk, "desk-1": desk, "armchair-1": chair}, "status": "set"}}
    items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True)

    def go(option):
        configs = {"decide": {"light_model": model, "slots": slots, "compensation": {"option": option}}}
        return runner.run(runner.tagged(items, "synthetic"),
                          runner.load_policies(["need", "incumbent", "decide"], configs), synthetic=True)

    for option in ("none-v0", "P1"):
        try:
            _, log = go(option)
            longest = max(len(events.dumps(e)) for e in log if e["kind"] == "decision" and e["source"] == "decide")
            print("RESULT %-7s runner.run OK, longest decide line %d bytes" % (option, longest))
        except ValueError as error:
            print("RESULT %-7s runner.run RAISED: %s" % (option, error))

    events.MAX_LINE_BYTES = 10 ** 9  # in-process only, to measure
    _, log = go("P1")
    decs = [e for e in log if e["kind"] == "decision" and e["source"] == "decide"]
    over = [e for e in decs if len(events.dumps(e)) > 16384]
    print("INFO P1: %d decide decisions, %d over 16384 bytes" % (len(decs), len(over)))
    if over:
        big = max(over, key=lambda e: len(events.dumps(e)))
        d = big["data"]
        size = lambda v: len(json.dumps(v, sort_keys=True, separators=(",", ":")))
        held = d["inputs"]["hold_set"]["held"]
        print("INFO biggest: %s trigger=%r total=%d bytes; candidates=%d (%d rejected), held footprints=%s, "
              "recipients=%d, inputs=%d" % (d["decision_id"], d["trigger"], len(events.dumps(big)), size(d["candidates"]),
                                            sum(c["why"].startswith("rejected") for c in d["candidates"]),
                                            [len(h.get("footprint") or []) for h in held], size(d["recipients"]),
                                            size(d["inputs"])))
        print("INFO triggers of the oversize decisions: %s" % sorted({e["data"]["trigger"] for e in over}))
        print("FAIL a P1 run with a 576-block smooth model and 4-block task regions cannot be logged")
    else:
        print("PASS no oversize decision")


if __name__ == "__main__":
    main()
