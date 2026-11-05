"""probe_cc_same_bulb_twice_refused: correction-capture -> den-core --corrections seam.

What it checks
  A person flicks one bulb off and back on within correction-capture's 3 s settle window (or drags an app
  slider, or holds a dimmer: several holds on ONE bulb in one action). correction-capture groups those changes
  into one action with one correction_id and writes one corrections.jsonl line per change -- so two lines with
  the same (correction_id, entity_id) and different `after`/t. den-core's sources.correction_capture() treats
  that pair as "repeats correction ... with other values" and run.py exits, so the owner's documented replay
  (tools/den-core/README.md "Replaying from the Home Assistant exports alone") cannot run on that export.

How to run (synthetic data only, temp folders only, no host contacted):
  python3 -B probe_cc_same_bulb_twice_refused.py /path/to/branch/checkout

Prints PASS/FAIL per step and a RESULT line. RESULT: DEFECT REPRODUCED means den-core refused
correction-capture's own output.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
T0 = 1793764800.0
LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
L0, L1, L2, L3 = LIGHTS
LIT = {"on": True, "brightness": 180, "kelvin": 2700}
OFF = {"on": False, "brightness": None, "kelvin": None}
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

    def bulb(self, t, e, on, user=False):
        c = self.context()
        self.bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off", "context_id": c,
                           "context_user_id": ("0F" * 16) if user else "", "context_parent_id": ""})
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


def main():
    tmp = Path(tempfile.mkdtemp(prefix="probe-r9-cc-"))
    ok = True
    try:
        x = Export()
        for e in LIGHTS:
            x.bulb(T0 - 3600, e, False)
        x.row(T0 - 600)                       # vacant
        x.row(T0, occupancy="on")             # arrival
        for e in LIGHTS:
            x.command(T0 + 0.5, e, LIT)       # den_lighting's arrival ON
        # A flicks the wall switch of rear_left off and straight back on (1.5 s apart): two holds, one bulb.
        x.bulb(T0 + 300.0, L2, False)
        x.row(T0 + 300.01, holds={L2: OFF})
        x.bulb(T0 + 301.5, L2, True)
        x.row(T0 + 301.51, holds={L2: LIT})
        x.row(T0 + 1000, occupancy="off")
        for e in (L0, L1, L3):
            x.command(T0 + 1120.5, e, OFF)
        x.row(T0 + 1120.5, holds={})
        status, bulbs, commands = x.write(tmp)

        cph = tmp / "cph"
        cap = subprocess.run([sys.executable, "-B", str(ROOT / "tools/correction-capture/capture.py"),
                              "--status", str(status), "--lights", str(bulbs), "--commands", str(commands),
                              "--out", str(cph), "--events"], capture_output=True, text=True)
        print("capture.py exit=%d" % cap.returncode)
        if cap.returncode != 0:
            print("FAIL: correction-capture itself refused:", cap.stderr.strip()[:300])
            print("RESULT: INCONCLUSIVE")
            return 2
        lines = [json.loads(l) for l in (cph / "corrections.jsonl").read_text("ascii").splitlines() if l.strip()]
        for l in lines:
            print("  corrections.jsonl: t=%+.2f id=%s %s after=%s source=%s" % (
                l["t"] - T0, l["data"]["correction_id"], l["data"]["entity_id"], l["data"]["after"],
                l["data"]["source"]))
        keys = [(l["data"]["correction_id"], l["data"]["entity_id"]) for l in lines]
        dup = len(keys) != len(set(keys))
        print("%s: correction-capture wrote %d line(s); same (correction_id, entity_id) twice: %s"
              % ("PASS" if dup else "FAIL", len(lines), dup))

        run = subprocess.run([sys.executable, "-B", str(ROOT / "tools/den-core/run.py"),
                              "--status", str(status), "--lights", str(bulbs), "--live-commands", str(commands),
                              "--corrections", str(cph / "corrections.jsonl"), "--policies", "incumbent",
                              "--out", str(tmp / "ha-1")], capture_output=True, text=True)
        print("den-core run.py exit=%d" % run.returncode)
        print("  stderr: %s" % run.stderr.strip()[-300:])
        refused = run.returncode != 0 and "repeats correction" in run.stderr
        print("%s: den-core refused correction-capture's own corrections.jsonl: %s"
              % ("PASS" if refused else "FAIL", refused))
        print("RESULT: %s" % ("DEFECT REPRODUCED" if dup and refused else "NOT REPRODUCED"))
        return 0 if dup and refused else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
