"""R0corr probe: a person's dimmer touch within 8 s after a den_lighting command is not counted.

What it checks
  den_lighting (ha-config/custom_components/den_lighting/__init__.py) treats an id-less bulb report as its own only
  inside its command's phase: transition (2 s, 5 s on a restore) + 3 s. Inside the phase the report opens a
  provisional hold that read-back confirms when the bulb is not at the target; after the phase an id-less report is
  a manual hold straight away. correction-capture sets aside every id-less, non-flipping report from 2 s before to
  8 s after any den_lighting command (ACK_BEFORE_S, ACK_AFTER_S), whatever den_lighting decided. The status rows
  below follow den_lighting's own sequence for three cases, each one person dimming the bulb with a Hue dimmer
  (an id-less "on" report, no on/off flip) after den_lighting turned it on with a 2 s transition:
    a) dims 3.5 s after the command (inside den_lighting's phase): provisional hold, read-back confirms it
    b) dims 6.5 s after the command (after den_lighting's 5 s phase and its read-back): den_lighting opens a
       manual hold at once, the way it treats any person's touch
    c) dims 9.5 s after the command: control
  Every case is one human action that opened a hold (ladder rung 0 / 11 numerator). The probe prints what the tool
  counts for each.

How to run (synthetic data, temp dirs only, no host contacted):
    python3 -B probe_dimmer_after_command_not_counted.py /path/to/branch/checkout
"""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "correction-capture"))
import capture  # noqa: E402
import sources  # noqa: E402

L0 = "light.den_front_left"
WARM = {"on": True, "brightness": 180, "kelvin": 2700}
MID = {"on": True, "brightness": 120, "kelvin": 2700}   # a fade report on the way up
DIM = {"on": True, "brightness": 60, "kelvin": 2700}    # where the person dimmed it
T0 = datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc).timestamp()


class Ex:
    def __init__(self):
        self.status, self.bulbs, self.commands, self.rev, self.n = [], [], [], 40, 0
        self.now = {"holds": {}, "provisional": []}
        self.row(T0)

    def ctx(self):
        self.n += 1
        return "%032X" % (0x0194000000000000000000000000 + self.n)

    def row(self, t, **change):
        self.now.update(change)
        self.rev += 1
        attrs = {"automatic": True, "gaming_enabled": True, "gaming_session": False, "gaming_signal": "idle",
                 "occupancy": "on", "revision": self.rev, "error": None, "gaming_entity": "sensor.pc_gaming",
                 "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in self.now["holds"].items()},
                 "occupancy_source": "raw_person_count", "pending_fixtures": [],
                 "provisional_fixtures": list(self.now["provisional"])}
        self.status.append({"last_updated_ts": t, "entity_id": "sensor.den_lighting_control", "state": "occupancy",
                            "attributes": attrs})

    def bulb(self, t, on, context=None):
        context = context or self.ctx()
        self.bulbs.append({"last_updated_ts": t, "entity_id": L0, "state": "on" if on else "off",
                           "context_id": context, "context_user_id": "", "context_parent_id": ""})
        return context

    def command(self, t, target):
        context = self.bulb(t - 0.3, target["on"])  # its own state row while the call runs (its context)
        self.commands.append({"time_fired_ts": t, "event_data": {"entity_id": L0, "revision": self.rev,
                                                                 "target": target, "context_id": sources.ulid(context)}})

    def write(self, folder):
        out = []
        for name, rows in (("status", self.status), ("lights", self.bulbs), ("commands", self.commands)):
            p = Path(folder) / (name + ".jsonl")
            p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
            out.append([p])
        return out


def case(delay):
    ex = Ex()
    # The bulb was off; den_lighting turns it on (2 s transition) at T0+100.
    ex.bulb(T0 + 50, False)
    c = T0 + 100
    ex.command(c, WARM)
    if delay < 5:
        # a) inside den_lighting's phase: its own fade report, then the person's dimmer, both provisional
        ex.bulb(c + 1.0, True)
        ex.row(c + 1.05, holds={L0: MID}, provisional=[L0])
        ex.bulb(c + delay, True)
        ex.row(c + delay + 0.05, holds={L0: DIM}, provisional=[L0])
        ex.row(c + 6.0, holds={L0: DIM}, provisional=[])        # read-back finds DIM, not WARM: the hold stays
    else:
        # b), c) den_lighting reached WARM; read-back at about 6 s matched, nothing held; then the person dims:
        # outside den_lighting's phase, so den_lighting opens a manual hold at once.
        ex.bulb(c + delay, True)
        ex.row(c + delay + 0.05, holds={L0: DIM})
    ex.row(T0 + 3600)
    return ex


def main():
    bad = 0
    for label, delay in (("a) 3.5 s, provisional then confirmed", 3.5), ("b) 6.5 s, den_lighting opens a manual hold", 6.5),
                         ("c) 9.5 s, control", 9.5)):
        with tempfile.TemporaryDirectory(prefix="r0corr-") as tmp:
            record, _ = capture.capture(*case(delay).write(tmp))
        people = record["planning"]["strata"]["no_game"]["people"]["count"]
        skipped = [(x["hold"], x["reason"], x["source"]) for x in record["not_counted"]]
        ok = people == 1
        bad += not ok
        print("%s: %s -- people counted %d (expected 1); not_counted %s" % ("PASS" if ok else "FAIL", label, people,
                                                                            skipped))
    print("RESULT: %d of 3 human dimmer touches not counted" % bad)


if __name__ == "__main__":
    main()
