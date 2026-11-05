"""probe_status_alone_drops_tail: "--status alone, the whole export replays" is not what run.py does.

What it checks
  README (Replaying from the Home Assistant exports alone): "Alone, the whole export replays". run.py sets the
  replay span from the inputs; with --status alone those are only den_lighting_status's occupancy and
  live_status CHANGES. Status rows that change only the hold set (or revision) add no input, so the span ends
  150 s after the last occupancy/automatic/game change. Corrections and bulb rows after `last`, and live
  commands after last+150 s, are dropped. An export taken while the den is still occupied (or any evening
  whose last occupancy change is its arrival) loses everything after the arrival: here a hold A made at +1200 s
  and den_lighting's resume command at +2400 s, while the export runs to +3600 s.

How to run (synthetic data only, temp folders, no host contacted):
  python3 -B probe_status_alone_drops_tail.py /path/to/branch/checkout
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "tools" / "den-core"))
import events  # noqa: E402

T0 = 1793764800.0
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
            p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
            out.append(p)
        return out


def main():
    tmp = Path(tempfile.mkdtemp(prefix="probe-r9-tail-"))
    try:
        x = Export()
        for e in LIGHTS:
            x.bulb(T0 - 3700, e, False)
        x.row(T0 - 600)
        x.row(T0, occupancy="on")
        for e in LIGHTS:
            x.command(T0 + 0.5, e, LIT)
        x.bulb(T0 + 1200, L2, True)               # A dims rear_left at its dimmer
        x.row(T0 + 1200.01, holds={L2: DIM})
        x.row(T0 + 2400, holds={})                # A asks den_lighting to resume rear_left (resume_automatic)
        x.command(T0 + 2400.5, L2, LIT)
        x.row(T0 + 3600)                          # the export ends with A still in the den
        status, bulbs, commands = x.write(tmp)
        cap = subprocess.run([sys.executable, "-B", str(ROOT / "tools/correction-capture/capture.py"), "--status",
                              str(status), "--lights", str(bulbs), "--commands", str(commands), "--out",
                              str(tmp / "cph"), "--events"], capture_output=True, text=True)
        if cap.returncode:
            raise SystemExit("capture.py failed: " + cap.stderr)
        p = subprocess.run([sys.executable, "-B", str(ROOT / "tools/den-core/run.py"), "--status", str(status),
                            "--lights", str(bulbs), "--live-commands", str(commands), "--corrections",
                            str(tmp / "cph" / "corrections.jsonl"), "--policies", "incumbent", "--out",
                            str(tmp / "out")], capture_output=True, text=True)
        print("run.py exit=%d" % p.returncode)
        for l in p.stdout.splitlines():
            if l.startswith(("correction_capture", "recorder_", "den_lighting_status", "diff live")):
                print("  " + l)
        _, log = events.read(tmp / "out" / "log.jsonl")
        end = log[-1]["t"] - T0
        corr = re.search(r"correction_capture: kept (\d+) of (\d+)", p.stdout)
        cmds = re.search(r"recorder_commands: kept (\d+) of (\d+)", p.stdout)
        print("  log spans %+.1f s .. %+.1f s from T0; the export runs to +3600 s" % (log[0]["t"] - T0, end))
        dropped = corr and corr.group(1) == "0" and cmds and int(cmds.group(1)) < int(cmds.group(2)) and end < 1200
        print("%s: --status alone stops 150 s after the last occupancy change; the hold at +1200 s and the command "
              "at +2400 s are not replayed" % ("PASS" if dropped else "FAIL"))
        print("RESULT: %s" % ("DEFECT REPRODUCED" if dropped else "NOT REPRODUCED"))
        return 0 if dropped else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
