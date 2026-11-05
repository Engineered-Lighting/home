"""probe_v_intent_decision_line_smooth_basis: does a decide Decision still overflow den-event-1's 16384-byte line limit
when the 576-block light model has a physically smooth basis (each bulb's light falls off with distance across the
32 x 18 grid), not the pseudo-random values used by test_rung9's full-size timing test?

Verifier's check for finding atk-decision-line-limit (the counter-argument "the overflow only comes from a random
basis where every block is in every footprint").

What it does
  Builds light_model.fake() with 576 blocks named rRRcCC (rung 8's proposed block_px 20 -> 32 x 18). Each bulb sits
  at one corner of the view; its basis at a block is 1 / (1 + (d / 8)^2) of its corner distance d (in blocks),
  times the fake's per-temperature factor. The task_points slot gives each track 4 blocks.
  Part 1: hold fixtures V62 and V64 through decide.handle (as holds.py does), longest decision line per option.
  Part 2: the synthetic touch evening through runner.run (run.py's path) for none-v0 and P1.
  Also prints the size of each held bulb's footprint and how many candidates were rejected.

How to run (synthetic data only, nothing written, no host contacted):
  python3 -B probe_v_intent_decision_line_smooth_basis.py /path/to/den-core-live-checkout
"""
import sys
from pathlib import Path


def main():
    root = Path(sys.argv[1]).resolve()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root / "tools" / "den-core"))
    import decide
    import events
    import holds
    import light_model
    import runner
    import synthetic

    model = light_model.fake()
    cols, rows = 32, 18
    names = ["r%02dc%02d" % (r, c) for r in range(rows) for c in range(cols)]
    corners = {events.LIGHTS[0]: (0, 0), events.LIGHTS[1]: (0, cols - 1), events.LIGHTS[2]: (rows - 1, 0),
               events.LIGHTS[3]: (rows - 1, cols - 1)}
    factors = {"2202": 0.9, "3290": 1.0, "6500": 1.05}
    model["sample_points"] = [{"point_id": p, "kind": None, "xyz": None, "normal": None, "rect": None} for p in names]
    model["basis"] = {}
    for e, (r0, c0) in corners.items():
        model["basis"][e] = {}
        for k, f in factors.items():
            model["basis"][e][k] = {}
            for p in names:
                r, c = int(p[1:3]), int(p[4:6])
                d2 = (r - r0) ** 2 + (c - c0) ** 2
                model["basis"][e][k][p] = round(f / (1 + d2 / 64.0), 6)
    model.update(version="probe-smooth-576", fake=False)
    desk = ["r08c14", "r08c15", "r09c14", "r09c15"]
    chair = ["r14c26", "r14c27", "r15c26", "r15c27"]
    slots = {"task_points": {"value": {"A": desk, "B": chair, "room": desk, "desk-1": desk, "armchair-1": chair},
                             "status": "set"}}

    print("Part 1: hold fixtures through decide.handle, smooth 576-block basis, 4 task points per person")
    for case in ("V62", "V64"):
        for option in decide.OPTIONS:
            policy = decide.make_policy({"light_model": model, "slots": slots, "compensation": {"option": option}})
            longest, foot, rejected = 0, 0, 0
            for n, e in enumerate(holds.events_of(case)):
                for out in policy.handle(dict(e, seq=n)):
                    d = dict(out["data"], actuation="replay")
                    line = events.dumps({"schema": events.SCHEMA, "t": e["t"], "seq": 0, "kind": "decision",
                                         "source": "decide", "data": d})
                    if len(line) > longest:
                        longest = len(line)
                        foot = max([len(h.get("footprint") or []) for h in d["inputs"]["hold_set"]["held"]] or [0])
                        rejected = sum(1 for c in d["candidates"] if c["why"].startswith("rejected"))
            print("RESULT part1 %s %-7s longest line %6d bytes (largest footprint %3d of 576 blocks, %2d rejected "
                  "candidates) %s" % (case, option, longest, foot, rejected,
                                      "OVER 16384" if longest > events.MAX_LINE_BYTES else "ok"))

    print("Part 2: synthetic touch evening through runner.run (run.py's path)")
    items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True)
    for option in ("none-v0", "P1"):
        configs = {"decide": {"light_model": model, "slots": slots, "compensation": {"option": option}}}
        try:
            header, log = runner.run(runner.tagged(items, "synthetic"),
                                     runner.load_policies(["need", "incumbent", "decide"], configs), synthetic=True)
            longest = max(len(events.dumps(e)) for e in log if e["kind"] == "decision" and e["source"] == "decide")
            print("RESULT part2 %-7s runner.run OK, longest decide line %d bytes" % (option, longest))
        except ValueError as error:
            print("RESULT part2 %-7s runner.run RAISED: %s" % (option, error))


if __name__ == "__main__":
    main()
