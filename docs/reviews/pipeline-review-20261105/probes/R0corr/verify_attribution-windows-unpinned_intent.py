"""verify_attribution-windows-unpinned: checks that the two surviving attribution mutants (ACK_AFTER_S 8 -> 30,
WINDOW_S 5 -> 1) are not equivalent mutants: on realistic synthetic rows each one changes people's count.

Case A (ACK_AFTER_S): den_lighting commands L0 on, then 10 s later a wall switch with no ids dims it (the bulb stays
on, so it is not a flip against the command) and den_lighting opens a hold. Original: a switch, counted as people.
Mutant ACK_AFTER_S = 30: the switch row falls inside the widened acknowledgement window and is set aside as
den_lighting's own report.

Case B (WINDOW_S): a wall switch turns L2 off; the bulb's id-less state row lands 2 s before the status row that
shows the hold. Original: a switch, counted as people. Mutant WINDOW_S = 1: no bulb row near the hold, so the
source is 'unknown' (every hold, not people).

The mutants are applied by setting the module globals at run time; no file is changed.
Synthetic data only, temp dirs only, never contacts a host.
Run:  python3 -B verify_attribution-windows-unpinned.py <root of the claude/correction-capture checkout>
"""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "correction-capture"))
import capture  # noqa: E402
import events  # noqa: E402
import sources  # noqa: E402

L0, L1, L2, L3 = events.LIGHTS
STATUS = "sensor.den_lighting_control"
T0 = datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc).timestamp()
WARM = {"on": True, "brightness": 100, "kelvin": 2700}
DIM = {"on": True, "brightness": 40, "kelvin": 2700}
OFF = {"on": False, "brightness": None, "kelvin": None}


class Den:
    def __init__(self):
        self.status, self.bulbs, self.commands, self.rev, self.n = [], [], [], 100, 0
        self.holds = {}
        self.row(T0)

    def ctx(self):
        self.n += 1
        return "%032X" % (0x0190000000000000000000000000 + self.n)

    def row(self, t, **holds):
        self.holds.update(holds)
        self.rev += 1
        attrs = {"automatic": True, "gaming_enabled": True, "gaming_session": False, "gaming_signal": "idle",
                 "occupancy": "on", "revision": self.rev, "error": None, "gaming_entity": "sensor.pc_gaming",
                 "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in self.holds.items()},
                 "occupancy_source": "raw_person_count", "pending_fixtures": [], "provisional_fixtures": [],
                 "friendly_name": "Den lighting control"}
        self.status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs})

    def bulb(self, t, e, on, context=None):
        context = context or self.ctx()
        self.bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off",
                           "context_id": context, "context_user_id": "", "context_parent_id": ""})
        return context

    def command(self, t, e, target):
        context = self.bulb(t - 0.3, e, target["on"])
        self.commands.append({"time_fired_ts": t, "event_data": {"entity_id": e, "revision": self.rev,
                                                                 "target": target,
                                                                 "context_id": sources.ulid(context)}})

    def run(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name, rows in (("s", self.status), ("b", self.bulbs), ("c", self.commands)):
                p = Path(tmp) / (name + ".jsonl")
                p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
                paths.append([p])
            record, _ = capture.capture(*paths)
        s = record["planning"]["strata"]["no_game"]
        return s["people"]["count"], s["every_hold"]["count"], [c["source"] for c in record["corrections"]], \
            [(c["entity_id"], c["reason"], c["source"]) for c in record["not_counted"]]


def case_a():
    den = Den()
    den.command(T0 + 100, L0, WARM)
    den.bulb(T0 + 110, L0, True)          # a switch dims it 10 s later: id-less, stays on
    den.row(T0 + 110.05, **{L0: DIM})
    den.row(T0 + 3600)
    return den


def case_b():
    den = Den()
    den.bulb(T0 + 300, L2, False)         # the switch's bulb row lands 2 s before the hold
    den.row(T0 + 302, **{L2: OFF})
    den.row(T0 + 3600)
    return den


bad = 0
for name, build, attr, value in (("A ACK_AFTER_S", case_a, "ACK_AFTER_S", 30.0),
                                 ("B WINDOW_S", case_b, "WINDOW_S", 1.0)):
    original = getattr(capture, attr)
    base = build().run()
    setattr(capture, attr, value)
    try:
        mutant = build().run()
    finally:
        setattr(capture, attr, original)
    differs = base[:2] != mutant[:2]
    print("RESULT case %s original (people, every)=%s sources=%s | mutant %s=%s (people, every)=%s sources=%s "
          "not_counted=%s" % (name, base[:2], base[2], attr, value, mutant[:2], mutant[2], mutant[3]))
    print("%s case %s: the mutant %s people's count" % ("PASS" if differs else "FAIL", name,
                                                         "changes" if differs else "does NOT change"))
    bad += not differs
print("RESULT %d equivalent mutant(s)" % bad)
