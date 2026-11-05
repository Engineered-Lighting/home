"""probe_hold_before_cut_dropped: a hold opened before the --facts span is lost, so decide changes a held bulb.

What it checks
  rung 9 (ladder): "Each evening's hold set and actuator state come from rung 0's log, and held channels are
  pinned at read-back in both arms". With --facts + --status + --corrections (README: "Add --facts with the
  evening's fact files to run need and decide beside it"), run.py cuts corrections to [start, last] of the
  other inputs and status_span carries only occupancy and live_status to the start -- never the hold set the
  status rows list. A bulb A dimmed before the first fact (and that den_lighting still held through the whole
  span) is therefore free in the replay: decide puts its reading look on it, and validate.py's holds check
  has nothing to check. Control: the same hold opened inside the span is pinned and skipped.
  corrections.jsonl is written by correction-capture's own capture.py from the synthetic exports.

How to run (synthetic data only, temp folders, no host contacted):
  python3 -B probe_hold_before_cut_dropped.py /path/to/branch/checkout
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "tools" / "den-core"))
import synthetic  # noqa: E402
import events  # noqa: E402

T0 = synthetic.T0
LIGHTS = events.LIGHTS
L0, L1, L2, L3 = LIGHTS
LIT = {"on": True, "brightness": 180, "kelvin": 2700}
DIM = {"on": True, "brightness": 80, "kelvin": 2202}
STATUS = "sensor.den_lighting_control"
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid(hex_bytes):
    n = int.from_bytes(bytes.fromhex(hex_bytes), "big")
    return "".join(CROCKFORD[(n >> (5 * i)) & 31] for i in reversed(range(26)))


class Export:
    def __init__(self):
        self.status, self.bulbs, self.commands, self.n, self.revision = [], [], [], 0, 100
        self.now = {"automatic": True, "gaming_session": False, "occupancy": "off", "holds": {}}

    def context(self):
        self.n += 1
        return "%032X" % (0x0190000000000000000000000000 + self.n)

    def row(self, t, **change):
        self.now.update(change)
        self.revision += 1
        attrs = {**self.now, "gaming_enabled": True, "gaming_signal": "idle", "revision": self.revision,
                 "error": None, "gaming_entity": "sensor.pc_gaming", "occupancy_source": "raw_person_count",
                 "pending_fixtures": [], "provisional_fixtures": [], "friendly_name": "Den lighting control",
                 "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in self.now["holds"].items()}}
        self.status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs})

    def bulb(self, t, e, on):
        c = self.context()
        self.bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off", "context_id": c,
                           "context_user_id": "", "context_parent_id": ""})
        return c

    def command(self, t, e, target):
        c = self.bulb(t - 0.3, e, target["on"])
        self.commands.append({"time_fired_ts": t, "event_data": {"entity_id": e, "revision": self.revision,
                                                                 "target": target, "context_id": ulid(c)}})

    def write(self, folder):
        out = []
        for name, rows in (("status", self.status), ("bulbs", self.bulbs), ("commands", self.commands)):
            p = Path(folder) / ("den-%s.jsonl" % name)
            p.write_bytes("".join(json.dumps(r) + "\n" for r in rows).encode("ascii"))
            out.append(p)
        return out


def case(tmp, name, facts, hold_at):
    folder = tmp / name
    folder.mkdir()
    x = Export()
    for e in LIGHTS:
        x.bulb(T0 - 3700, e, False)
    x.row(T0 - 3600)
    x.row(T0 - 1000, occupancy="on")
    for e in LIGHTS:
        x.command(T0 - 999.5, e, LIT)
    x.bulb(hold_at, L2, True)              # A dims rear_left at its wall dimmer (the bulb reports, still on)
    x.row(hold_at + 0.01, holds={L2: DIM})  # den_lighting holds it at what it read; the hold lasts all evening
    status, bulbs, commands = x.write(folder)
    cap = subprocess.run([sys.executable, "-B", str(ROOT / "tools/correction-capture/capture.py"), "--status",
                          str(status), "--lights", str(bulbs), "--commands", str(commands), "--out",
                          str(folder / "cph"), "--events"], capture_output=True, text=True)
    if cap.returncode:
        raise SystemExit("capture.py failed: " + cap.stderr)
    p = subprocess.run([sys.executable, "-B", str(ROOT / "tools/den-core/run.py"), "--facts", str(facts),
                        "--status", str(status), "--lights", str(bulbs), "--live-commands", str(commands),
                        "--corrections", str(folder / "cph" / "corrections.jsonl"),
                        "--policies", "need", "incumbent", "decide", "--out", str(folder / "out")],
                       capture_output=True, text=True)
    if p.returncode:
        raise SystemExit("run.py failed: " + p.stderr)
    _, log = events.read(folder / "out" / "log.jsonl")
    held_in_replay = any(h["entity_id"] == L2 for e in log if e["kind"] == "mode" for h in e["data"]["holds"])
    looks = [round(e["t"] - T0, 1) for e in log if e["kind"] == "decision" and e["source"] == "decide"
             and e["data"]["requested_scene"].get(L2, {}).get("brightness") == 255]
    pinned = [round(e["t"] - T0, 1) for e in log if e["kind"] == "decision" and e["source"] == "decide"
              and events.scene_matches(DIM, e["data"]["requested_scene"].get(L2))
              and L2 in {s["channel"] for s in e["data"]["skipped"]}]
    v = subprocess.run([sys.executable, "-B", str(ROOT / "tools/den-core/validate.py"),
                        str(folder / "out" / "log.jsonl")], capture_output=True, text=True)
    kept = [l for l in p.stdout.splitlines() if l.startswith("correction_capture: kept")]
    holds_line = [l for l in v.stdout.splitlines() if "holds" in l.lower()]
    print("[%s] hold at %+.0f s; %s" % (name, hold_at - T0, kept[0] if kept else "?"))
    print("  replayed mode lists rear_left held: %s" % held_in_replay)
    print("  decide decisions putting the look (255) on rear_left at t-T0: %s" % looks[:6])
    print("  decide decisions pinning rear_left at the hold and skipping it: %s" % pinned[:6])
    print("  validate.py exit=%d; %s" % (v.returncode, " | ".join(holds_line)[:200]))
    return held_in_replay, looks, pinned, v.returncode


def main():
    tmp = Path(tempfile.mkdtemp(prefix="probe-r9-hold-"))
    try:
        _, _, fact_lines = synthetic.evening(unclear_rate=0.0, invalid_rate=0.0, fail_rate=0.0)
        facts = tmp / "la-fact-log-1-2026-11-04.jsonl"
        facts.write_text("".join(line + "\n" for line in fact_lines), "ascii")
        held_b, looks_b, pinned_b, v_b = case(tmp, "hold-before-facts", facts, T0 - 900)
        held_c, looks_c, pinned_c, v_c = case(tmp, "hold-inside-facts", facts, T0 + 100)
        bad = (not held_b) and looks_b and not pinned_b
        ctrl = held_c and pinned_c and not looks_c
        print("%s: hold opened 900 s before the first fact (held live all evening) is free in the replay and "
              "decide puts its look on it" % ("PASS" if bad else "FAIL"))
        print("%s: control, the same hold inside the span is pinned and skipped" % ("PASS" if ctrl else "FAIL"))
        print("%s: validate.py exits 0 on the broken replay (exit=%d)" % ("PASS" if v_b == 0 else "FAIL", v_b))
        print("RESULT: %s" % ("DEFECT REPRODUCED" if bad and ctrl else "NOT REPRODUCED"))
        return 0 if bad and ctrl else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
