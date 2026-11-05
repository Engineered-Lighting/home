"""verify_atk-wallclock-timing: does score.py's 100 ms timing item measure decide, or what else is running?

What it checks
  Builds ONE scored-shaped log (non-synthetic flag, decide's slots filled at their proposed values, compensation P1,
  the fake light model relabelled fake=False, as test_rung9.ScoreTest does) from the synthetic evening with a touch
  hold and a game, and a filled gate (compensation P1). Then it scores that same log three times:
    run A: score.score as shipped (replay_timed times decide.handle with time.perf_counter), nothing else running;
    run B: as shipped, while 24 Python threads in this process spin (they compete for the interpreter lock, a
           stand-in for other load on the host; they use at most one CPU, so the shared machine is not loaded);
    run C: as run B, but with time.perf_counter swapped for time.thread_time (CPU time of the scoring thread).
  If A passes the timing item, B fails it and C passes it, the timing verdict on one log is set by wall-clock
  waiting, not by decide's work. (A 576-point P1 log cannot be scored at all: its decision lines exceed
  events.MAX_LINE_BYTES, so the full-size case is shown by probe_atk_full_grid_timing.py instead.)

How to run (synthetic data only; temp dirs only; no host contacted):
  python3 -B verify_atk-wallclock-timing.py /path/to/den-core-live-checkout
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
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

    tmp = Path(tempfile.mkdtemp(prefix="verify-r9-timing-"))
    stop = threading.Event()
    try:
        model = light_model.fake()
        model.update(version="verify-lm-1", fake=False)
        slots = {n: {**e, "value": e["proposed"], "status": "set"} for n, e in decide.DEFAULT_CONFIG["slots"].items()}
        configs = {"decide": {"slots": slots, "light_model": model, "compensation": {"option": "P1"}}}
        items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True, game=True)
        header, log = runner.run(runner.tagged(items, "synthetic"),
                                 runner.load_policies(["need", "incumbent", "decide"], configs),
                                 synthetic=False, sources=[{"name": "synthetic:evening"}])
        path = tmp / "p1.jsonl"
        events.write(path, header, log)
        gate = json.loads(score.GATE.read_text("utf-8"))
        for entry in list(gate["parameters"].values()) + [s["floor"] for s in gate["surrogates"].values()]:
            entry.update(value=entry["proposed"], status="set")
        gate["parameters"]["compensation"]["value"] = "P1"
        gate_path = tmp / "gate.json"
        gate_path.write_text(json.dumps(gate), "ascii")
        print("INFO host load average: %s" % (os.getloadavg(),))

        def spin():
            x = 0
            while not stop.is_set():
                x += 1

        results = {}
        for name in ("A", "B", "C"):
            spinners = []
            if name in ("B", "C"):
                stop.clear()
                spinners = [threading.Thread(target=spin, daemon=True) for _ in range(24)]
                for s in spinners:
                    s.start()
            real = time.perf_counter
            if name == "C":
                time.perf_counter = time.thread_time
            try:
                record, reasons = score.score(path, gate_path)
            finally:
                time.perf_counter = real
                stop.set()
                for s in spinners:
                    s.join()
            t = record["checks"]["timing"]
            results[name] = (t["ok"], record["verdict"])
            print("RESULT run %s (%s): refusals=%s timing ok=%s (%s); verdict=%s"
                  % (name, {"A": "perf_counter, as shipped, quiet", "B": "perf_counter, as shipped, 24 spinners",
                            "C": "thread_time, 24 spinners"}[name], reasons, t["ok"], t["text"], record["verdict"]))
        if results["A"][0] and not results["B"][0] and results["C"][0]:
            print("FAIL one log: timing OK when quiet, FAIL with other work running, OK on CPU time: "
                  "DEFECT REPRODUCED (the 100 ms item's verdict depends on load)")
        else:
            print("INFO no flip observed in this run: %s" % results)
    finally:
        stop.set()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
