#!/usr/bin/env python3
"""Verify (R9, full-grid-p1-p2-near-100ms): intrinsic CPU cost of decide's slowest decision on a 576-point model.

    python3 -B verify_full-grid-p1-p2-near-100ms.py <den-core-live checkout root> [repeats]

Same 576-point model and V64 stream as test_rung9.TimingTest.test_a_full_size_light_model_stays_under_100_ms. For
each option it replays V64 `repeats` times (fresh policy each time), keeps per decision index the MINIMUM
process CPU time over the repeats (the least load-inflated estimate of the code's own cost), and prints the largest
of those minima, plus the slowest wall time seen. Synthetic only; writes nothing; contacts nothing.
RESULT lines only; FAIL when an option's slowest decision needs >= 80 ms of CPU even in its best repeat.
"""
import os
import sys
import time


def main():
    root = os.path.abspath(sys.argv[1])
    repeats = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.join(root, "tools", "den-core"))
    import decide
    import holds
    import light_model

    model = light_model.fake()
    names = ["b%03d" % n for n in range(576)]
    model["sample_points"] = [{"point_id": p, "kind": None, "xyz": None, "normal": None, "rect": None} for p in names]
    for e, temps in model["basis"].items():
        for k in temps:
            temps[k] = {p: round(0.001 * ((n * 7 + len(e)) % 997), 6) for n, p in enumerate(names)}
    slots = {"task_points": {"value": {"A": names[:4], "B": names[100:104], "room": names[:4],
                                       "desk-1": names[:4], "armchair-1": names[100:104]}, "status": "set"}}
    bad = []
    for option in ("P0", "P1", "P2", "P3"):
        best, wall = {}, 0.0
        for _ in range(repeats):
            policy = decide.make_policy({"light_model": model, "slots": slots, "compensation": {"option": option}})
            k = 0
            for n, e in enumerate(holds.events_of("V64")):
                e = dict(e, seq=n)
                c0, w0 = time.process_time(), time.perf_counter()
                out = policy.handle(e)
                c1, w1 = time.process_time(), time.perf_counter()
                if out:
                    best[k] = min(best.get(k, 9e9), c1 - c0)
                    wall = max(wall, w1 - w0)
                    k += 1
        worst = max(best.values())
        print("RESULT option=%s decisions=%d slowest decision's best-repeat CPU = %.1f ms; slowest wall seen = %.1f ms"
              % (option, len(best), worst * 1000, wall * 1000))
        if worst >= 0.08:
            bad.append(option)
    print("FAIL: near or over the 100 ms bound in CPU alone: %s" % bad if bad else "PASS: all well under 100 ms")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
