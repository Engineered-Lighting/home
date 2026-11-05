"""probe_latency_determinism: replay determinism (run twice, compare bytes; check.py) and per-decision latency
against rung 9's 100 ms bound, on two synthetic replays.

What it checks
  1. Synthetic evening (--synthetic evening --touch --game --policies need incumbent decide): run.py twice into
     two temp folders, the two log.jsonl files must be byte-identical, and check.py must print OK.
  2. An evening from synthetic Home Assistant exports written here in the READMEs' formats (den_lighting status
     sensor, bulb states, den_lighting_command events) plus a hand-written corrections.jsonl: --status --lights
     --live-commands --corrections --policies incumbent decide, twice, bytes compared, check.py.
  3. Latency: the same two replays in-process, with every policy's handle() wrapped in time.perf_counter; for every
     handle() call that returned a decision the elapsed time is recorded. Prints n, median, p99, max in ms and how
     many exceed 100 ms. (den-core itself measures no latency; this is the probe's own measurement.)

How to run
  python3 -B probe_latency_determinism.py /path/to/den-core-live-checkout
  Synthetic data only; temp folders only; nothing contacts a host.
"""
import contextlib
import io
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
STATUS = "sensor.den_lighting_control"
LIT = {"on": True, "brightness": 180, "kelvin": 2700}
DIM = {"on": True, "brightness": 40, "kelvin": 2700}
OFF = {"on": False, "brightness": None, "kelvin": None}
CROCK = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
T0 = 1793764800.0


def ulid_text(n):
    return "".join(CROCK[(n >> (5 * i)) & 31] for i in reversed(range(26)))


def exports(folder):
    status, bulbs, commands = [], [], []
    state = {"rev": 50, "occ": "off", "holds": {}, "n": 0}

    def row(t, **kw):
        state.update(kw)
        state["rev"] += 1
        attrs = {"automatic": True, "gaming_enabled": True, "gaming_session": False, "gaming_signal": "idle",
                 "occupancy": state["occ"], "revision": state["rev"], "error": None,
                 "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in state["holds"].items()},
                 "gaming_entity": "sensor.pc_gaming", "occupancy_source": "raw_person_count",
                 "pending_fixtures": [], "provisional_fixtures": [], "friendly_name": "Den lighting control"}
        status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs})

    def bulb(t, e, on, user=False):
        state["n"] += 1
        ctx = 0x0190BB00000000000000000000000000 + state["n"]
        bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off",
                      "context_id": "%032X" % ctx, "context_user_id": "0F" * 16 if user else "",
                      "context_parent_id": ""})
        return ctx

    def command(t, e, target):
        ctx = bulb(t - 0.3, e, target["on"])
        commands.append({"time_fired_ts": t, "event_data": {"entity_id": e, "revision": state["rev"],
                                                            "target": target, "context_id": ulid_text(ctx)}})

    for e in LIGHTS:
        bulb(T0 - 3600, e, False)
    row(T0 - 900)
    for k in range(6):                       # six arrivals and vacancies over an evening
        a = T0 + k * 1200
        row(a, occ="on")
        for e in LIGHTS:
            command(a + 0.5, e, LIT)
        bulb(a + 300, LIGHTS[1], True, user=True)
        row(a + 299.99, holds={LIGHTS[1]: DIM})
        row(a + 700, occ="off")
        for e in (LIGHTS[0], LIGHTS[2], LIGHTS[3]):
            command(a + 820.5, e, OFF)
        command(a + 820.5, LIGHTS[1], OFF)
        row(a + 820.5, holds={})
    paths = []
    for name, rows in (("status", status), ("bulbs", bulbs), ("commands", commands)):
        p = Path(folder) / ("den-%s.jsonl" % name)
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="ascii")
        paths.append(p)
    corr = Path(folder) / "corrections.jsonl"
    corr.write_text("".join(json.dumps({"schema": "den-event-1", "t": T0 + k * 1200 + 299.99, "seq": k,
                                        "kind": "correction", "source": "correction_capture",
                                        "data": {"correction_id": "cc-%d" % k, "entity_id": LIGHTS[1],
                                                 "after": DIM, "source": "app"}},
                                       sort_keys=True, separators=(",", ":")) + "\n" for k in range(6)),
                    encoding="ascii")
    return paths, corr


def run_cli(tool, argv):
    return subprocess.run([sys.executable, "-B", str(tool / "run.py"), *argv], capture_output=True, text=True)


def main(root):
    root = Path(root).resolve()
    tool = root / "tools" / "den-core"
    tmp = Path(tempfile.mkdtemp(prefix="probe-r9-lat-"))
    (tmp / "ha").mkdir()
    (status, bulbs, commands), corr = exports(tmp / "ha")
    cases = {
        "synthetic": ["--synthetic", "evening", "--touch", "--game", "--policies", "need", "incumbent", "decide"],
        "ha_exports": ["--status", str(status), "--lights", str(bulbs), "--live-commands", str(commands),
                       "--corrections", str(corr), "--policies", "incumbent", "decide"],
    }
    bad = 0
    for name, argv in cases.items():
        logs = []
        for k in (1, 2):
            out = tmp / ("%s-%d" % (name, k))
            done = run_cli(tool, argv + ["--out", str(out)])
            if done.returncode:
                print("FAIL %s run %d exited %d: %s" % (name, k, done.returncode, done.stderr[-300:]))
                bad += 1
                break
            logs.append((out / "log.jsonl").read_bytes())
        if len(logs) == 2:
            same = logs[0] == logs[1]
            print("%s determinism %s: two runs, %d and %d bytes, identical=%s"
                  % ("PASS" if same else "FAIL", name, len(logs[0]), len(logs[1]), same))
            bad += not same
            chk = subprocess.run([sys.executable, "-B", str(tool / "check.py"), str(tmp / ("%s-1" % name) / "log.jsonl")],
                                 capture_output=True, text=True)
            print("%s check.py %s: %s" % ("PASS" if chk.returncode == 0 else "FAIL", name, chk.stdout.strip()))
            bad += chk.returncode != 0

    # Latency, in-process.
    sys.path.insert(0, str(tool))
    import run as den_run  # noqa: E402
    import runner  # noqa: E402
    original = runner.load_policies
    timings = {}

    def timed_load(names, configs=None):
        policies = original(names, configs)
        for p in policies:
            handle = p.handle

            def wrapped(event, _h=handle, _id=p.id):
                t = time.perf_counter()
                out = _h(event)
                dt = time.perf_counter() - t
                if any(o["kind"] == "decision" for o in out):
                    timings.setdefault(_id, []).append(dt)
                return out
            p.handle = wrapped
        return policies

    runner.load_policies = timed_load
    for name, argv in cases.items():
        timings.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            den_run.main(argv + ["--out", str(tmp / ("%s-lat" % name))])
        for pid, values in sorted(timings.items()):
            ms = sorted(v * 1000 for v in values)
            p99 = ms[min(len(ms) - 1, int(round(0.99 * (len(ms) - 1))))]
            over = sum(v >= 100.0 for v in ms)
            print("RESULT latency %s %s: n=%d decisions, median %.3f ms, p99 %.3f ms, max %.3f ms, >=100 ms: %d"
                  % (name, pid, len(ms), statistics.median(ms), p99, ms[-1], over))
            if over:
                bad += 1
                print("FAIL latency %s %s: %d decision(s) at or over 100 ms" % (name, pid, over))
    runner.load_policies = original
    print("PASS all" if not bad else "FAIL %d problem(s)" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
