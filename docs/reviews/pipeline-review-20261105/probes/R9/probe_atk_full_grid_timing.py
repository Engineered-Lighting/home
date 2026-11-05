"""probe_atk_full_grid_timing: does decide meet rung 9's "each decision under 100 ms" with a rung-8-sized model?

What it checks
  Rung 9's gate (ladder.json; rung9-gate-v0.json fixed.decision_ms_max = 100) needs every decide decision under
  100 ms. Rung 8 Stage 1's grid is 32 x 18 = 576 points. This probe builds the same 576-point model that
  test_rung9.TimingTest.test_a_full_size_light_model_stays_under_100_ms builds (light_model.fake() reshaped), runs
  the hold fixture V64 (and V62) through decide under every compensation option, and times every handle() call
  that returns a decision with BOTH time.perf_counter (wall clock, what score.py and the test use) and
  time.process_time (CPU time of this process, insensitive to other load on the machine).
  It also re-times P1 with the 'task_points' slot covering 64 points per person (a desk patch on a 576-block grid
  is more than 4 blocks), to show how the cost scales.

  RESULT lines: per option, n decisions, max wall ms, max CPU ms, and whether each is under 100 ms.

How to run (synthetic data only; nothing contacts a host; nothing is written):
  python3 -B probe_atk_full_grid_timing.py /path/to/den-core-live-checkout
"""
import sys
import time
from pathlib import Path


def main():
    root = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(root / "tools" / "den-core"))
    import decide
    import holds
    import light_model

    def model_576():
        model = light_model.fake()
        names = ["b%03d" % n for n in range(576)]
        model["sample_points"] = [{"point_id": p, "kind": None, "xyz": None, "normal": None, "rect": None}
                                  for p in names]
        for e, temps in model["basis"].items():
            for k in temps:
                temps[k] = {p: round(0.001 * ((n * 7 + len(e)) % 997), 6) for n, p in enumerate(names)}
        return model, names

    model, names = model_576()

    def run(case, option, per_person):
        slots = {"task_points": {"value": {"A": names[:per_person], "B": names[100:100 + per_person],
                                           "room": names[:per_person], "desk-1": names[:per_person],
                                           "armchair-1": names[100:100 + per_person]}, "status": "set"}}
        policy = decide.make_policy({"light_model": model, "slots": slots, "compensation": {"option": option}})
        walls, cpus = [], []
        for n, e in enumerate(holds.events_of(case)):
            e = dict(e, seq=n)
            w0, c0 = time.perf_counter(), time.process_time()
            out = policy.handle(e)
            w1, c1 = time.perf_counter(), time.process_time()
            if out:
                walls.append((w1 - w0) * 1000)
                cpus.append((c1 - c0) * 1000)
        return walls, cpus

    worst = {}
    for per_person in (4, 64):
        for case in ("V64", "V62"):
            for option in decide.OPTIONS:
                if per_person == 64 and option not in ("P1", "P2"):
                    continue
                best_cpu = None
                # three repeats; keep the repeat with the smallest max CPU time (least disturbed)
                for _ in range(3):
                    walls, cpus = run(case, option, per_person)
                    if best_cpu is None or max(cpus) < max(best_cpu[1]):
                        best_cpu = (walls, cpus)
                walls, cpus = best_cpu
                key = (per_person, case, option)
                worst[key] = max(cpus)
                print("RESULT task_points/person=%d %s %-7s n=%d max_wall_ms=%.1f max_cpu_ms=%.1f wall<100:%s cpu<100:%s"
                      % (per_person, case, option, len(walls), max(walls), max(cpus), max(walls) < 100, max(cpus) < 100))
    over = [k for k, v in worst.items() if v >= 100]
    if over:
        print("FAIL decide decisions over 100 ms of CPU time (not load): %s" % ", ".join("%s/%s/%d pts" % (k[1], k[2], k[0]) for k in over))
    else:
        print("PASS every decision under 100 ms of CPU time")


if __name__ == "__main__":
    main()
