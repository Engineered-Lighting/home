#!/usr/bin/env python3
"""Probe: does each decide decision stay under the 100 ms bound (ladder rung 9, A9) with a light model the size of
rung 8's real grid (32 x 18 blocks = 576 points), under each compensation option?

    python3 -B probe_v_full_grid_bounded_options_over_100ms.py <den-core-live checkout root> [repeats]

It builds the same 576-point model test_rung9.TimingTest.test_a_full_size_light_model_stays_under_100_ms builds
(the fake with 576 synthetic points), feeds the V64 hold fixture (holds.events_of('V64')) to decide under none-v0
and P0-P3, and measures each handle() call that makes a decision with BOTH wall time (perf_counter, inflated when
the host is loaded) and this process's CPU time (process_time, not inflated by other processes). It repeats the
whole case `repeats` times (default 5) and prints the slowest CPU and wall time per option.
Synthetic data only; it reads nothing outside the checkout, writes nothing, contacts nothing.
PASS when every option's slowest CPU time is under 100 ms; FAIL otherwise.
"""
import os
import sys
import time


def main():
    root = os.path.abspath(sys.argv[1])
    repeats = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.join(root, "tools", "den-core"))
    import decide  # noqa: E402
    import holds  # noqa: E402
    import light_model  # noqa: E402

    model = light_model.fake()
    names = ["b%03d" % n for n in range(576)]
    model["sample_points"] = [{"point_id": p, "kind": None, "xyz": None, "normal": None, "rect": None} for p in names]
    for e, temps in model["basis"].items():
        for k in temps:
            temps[k] = {p: round(0.001 * ((n * 7 + len(e)) % 997), 6) for n, p in enumerate(names)}
    slots = {"task_points": {"value": {"A": names[:4], "B": names[100:104], "room": names[:4],
                                       "desk-1": names[:4], "armchair-1": names[100:104]}, "status": "set"}}
    worst = {}
    for option in holds.OPTIONS:
        cpu_max = wall_max = 0.0
        made = 0
        for _ in range(repeats):
            policy = decide.make_policy({"light_model": model, "slots": slots, "compensation": {"option": option}})
            for n, e in enumerate(holds.events_of("V64")):
                e = dict(e, seq=n)
                c0, w0 = time.process_time(), time.perf_counter()
                out = policy.handle(e)
                c1, w1 = time.process_time(), time.perf_counter()
                if out:
                    made += 1
                    cpu_max, wall_max = max(cpu_max, c1 - c0), max(wall_max, w1 - w0)
        worst[option] = (cpu_max, wall_max, made)
        print("RESULT option=%-7s decisions=%3d slowest_cpu_ms=%7.1f slowest_wall_ms=%7.1f"
              % (option, made, cpu_max * 1000, wall_max * 1000))
    over = [o for o, (c, _, _) in worst.items() if c >= 0.1]
    if over:
        print("FAIL: decisions over 100 ms of CPU time on a 576-point model under: %s" % ", ".join(over))
    else:
        print("PASS: every option's slowest decision is under 100 ms of CPU time")
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main())
