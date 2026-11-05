"""probe_atk_decision_line_limit: does a decide Decision fit den-event-1's 16384-byte line limit with a rung-8-sized
light model and a realistic task region?

What it checks
  events.MAX_LINE_BYTES = 16384: runner.run validates every logged event and raises ValueError on a longer line, so
  run.py stops ("run.py: decision line over 16384 bytes") and writes no log. decide's Decision carries, per channel
  that is on, one recipients row per (track, task point), predicted surfaces per task point, and under P1-P3 each
  held bulb's footprint (point ids over EVERY model point). Rung 8 Stage 1's grid is 32 x 18 = 576 blocks, so a
  desk or armchair region is many blocks, not the fake model's one named point.

  Part 1: the hold fixtures V62 and V64 through decide directly (as holds.py and test_rung9 do, which never
          validate line length), with the 576-point model of test_rung9's full-size timing test; prints the
          longest Decision line per option.
  Part 2: a synthetic evening (need, incumbent, decide) through runner.run -- the path run.py uses -- with the
          576-point model and the 'task_points' slot giving need v0's track 'room' N blocks, N in 4..64; prints
          whether runner.run completes or raises the line-limit ValueError.

How to run (synthetic data only; temp-free, nothing written, no host contacted):
  python3 -B probe_atk_decision_line_limit.py /path/to/den-core-live-checkout
"""
import sys
from pathlib import Path


def main():
    root = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(root / "tools" / "den-core"))
    import decide
    import events
    import holds
    import light_model
    import runner
    import synthetic

    model = light_model.fake()
    names = ["r%02dc%02d" % (n // 32, n % 32) for n in range(576)]  # rung 8 Stage 1 names blocks rRRcCC
    model["sample_points"] = [{"point_id": p, "kind": None, "xyz": None, "normal": None, "rect": None} for p in names]
    for e, temps in model["basis"].items():
        for k in temps:
            temps[k] = {p: round(0.001 * ((n * 7 + len(e)) % 997), 6) for n, p in enumerate(names)}
    model.update(version="probe-576", fake=False)

    print("Part 1: hold fixtures through decide directly (no line validation on this path)")
    for per in (4, 16):
        slots = {"task_points": {"value": {"A": names[:per], "B": names[300:300 + per], "room": names[:per],
                                           "desk-1": names[:per], "armchair-1": names[300:300 + per]},
                                 "status": "set"}}
        for case in ("V62", "V64"):
            for option in decide.OPTIONS:
                policy = decide.make_policy({"light_model": model, "slots": slots, "compensation": {"option": option}})
                longest = 0
                for n, e in enumerate(holds.events_of(case)):
                    for out in policy.handle(dict(e, seq=n)):
                        line = events.dumps({"schema": events.SCHEMA, "t": e["t"], "seq": 0, "kind": "decision",
                                             "source": "decide", "data": dict(out["data"], actuation="replay")})
                        longest = max(longest, len(line))
                print("RESULT part1 task_points/person=%-3d %s %-7s longest decision line %6d bytes %s"
                      % (per, case, option, longest, "OVER 16384" if longest > events.MAX_LINE_BYTES else "ok"))

    print("Part 2: synthetic evening through runner.run (run.py's path), compensation none-v0 and P1")
    items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True)
    first_fail = {}
    for option in ("none-v0", "P1"):
        for per in (4, 8, 12, 16, 24, 32, 48, 64):
            slots = {"task_points": {"value": {"room": names[:per], "desk-1": names[:per],
                                               "armchair-1": names[300:300 + per]}, "status": "set"}}
            configs = {"decide": {"light_model": model, "slots": slots, "compensation": {"option": option}}}
            try:
                header, log = runner.run(runner.tagged(items, "synthetic"),
                                         runner.load_policies(["need", "incumbent", "decide"], configs),
                                         synthetic=True)
                longest = max(len(events.dumps(e)) for e in log if e["kind"] == "decision" and e["source"] == "decide")
                print("RESULT part2 %-7s task_points=%-3d runner.run OK, longest decide line %d bytes" % (option, per, longest))
            except ValueError as error:
                first_fail.setdefault(option, per)
                print("RESULT part2 %-7s task_points=%-3d runner.run RAISED: %s" % (option, per, error))
    if first_fail:
        print("FAIL decide cannot log its decisions once the task region has %s blocks: run.py stops, no log, no score"
              % ", ".join("%s>=%d" % kv for kv in first_fail.items()))
    else:
        print("PASS every run logged")


if __name__ == "__main__":
    main()
