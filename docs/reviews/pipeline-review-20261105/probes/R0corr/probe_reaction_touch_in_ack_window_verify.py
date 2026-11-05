"""probe_reaction_touch_in_ack_window_verify: verifier's check of finding reaction-touch-in-ack-window.
(1) Sweeps a person's non-flip dimmer press at +1..+7 s after a 2 s-transition den_lighting command, as den_lighting
    would record it (provisional hold, then confirmed by read-back at the person's level). Reports whether any is
    counted: if none is, the README's "If read-back then keeps it held, it is a candidate" can only ever count a
    flip for ordinary 2 s commands.
(2) Compares the tool's own late-own-report case (hold target == command target) with a person's press at the same
    time (hold target != command target): whether capture.py tells them apart with the status hold target it has.

Synthetic data only, temp dirs only, never contacts a host.
Run:  python3 -B probe_reaction_touch_in_ack_window_verify.py <root of the claude/correction-capture checkout>
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

L0 = events.LIGHTS[0]
STATUS = "sensor.den_lighting_control"
T0 = datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc).timestamp()
WARM = {"on": True, "brightness": 100, "kelvin": 2700}
DIM = {"on": True, "brightness": 40, "kelvin": 2700}


class Den:
    def __init__(self):
        self.status, self.bulbs, self.commands, self.rev, self.n = [], [], [], 100, 0
        self.holds, self.prov = {}, []
        self.row(T0)

    def ctx(self):
        self.n += 1
        return "%032X" % (0x0190000000000000000000000000 + self.n)

    def row(self, t):
        self.rev += 1
        attrs = {"automatic": True, "gaming_session": False, "occupancy": "on", "revision": self.rev,
                 "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in self.holds.items()},
                 "occupancy_source": "raw_person_count", "provisional_fixtures": list(self.prov)}
        self.status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs})

    def bulb(self, t, on, context=None):
        context = context or self.ctx()
        self.bulbs.append({"last_updated_ts": t, "entity_id": L0, "state": "on" if on else "off",
                           "context_id": context, "context_user_id": "", "context_parent_id": ""})
        return context

    def command(self, t, target):
        context = self.bulb(t - 0.3, target["on"])
        self.commands.append({"time_fired_ts": t, "event_data": {"entity_id": L0, "revision": self.rev,
                              "target": target, "context_id": sources.ulid(context)}})

    def run(self):
        self.row(T0 + 3600)
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name, rows in (("s", self.status), ("b", self.bulbs), ("c", self.commands)):
                p = Path(tmp) / (name + ".jsonl")
                p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
                paths.append([p])
            record, _ = capture.capture(*paths)
        return ([(c["source"], c["changes"][0]["hold"]) for c in record["corrections"]],
                [(c["reason"], c["source"], c["hold"]) for c in record["not_counted"]])


counted_any = False
for dt in (1, 2, 3, 4, 5, 6, 7):
    den = Den()
    den.command(T0 + 100, WARM)       # lights on at arrival, 2 s transition
    den.bulb(T0 + 100 + dt, True)     # A presses the dimmer (no ids, stays on)
    den.holds, den.prov = {L0: DIM}, [L0]
    den.row(T0 + 100 + dt + 0.01)     # den_lighting: provisional
    den.prov = []
    den.row(T0 + 100 + max(dt, 5) + 1.5)  # read-back finds DIM, not WARM: hold confirmed
    counted, skipped = den.run()
    counted_any |= bool(counted)
    print("RESULT press at +%d s, confirmed by read-back: counted=%s not_counted=%s" % (dt, counted, skipped))
print(("PASS" if counted_any else "FAIL") + " sweep: a non-flip press confirmed by read-back is %s counted"
      % ("sometimes" if counted_any else "never"))

out = {}
for name, held in (("late own report (hold WARM == command WARM)", WARM), ("person dims (hold DIM != WARM)", DIM)):
    den = Den()
    den.command(T0 + 200, WARM)
    den.bulb(T0 + 207, True)
    den.holds = {L0: held}
    den.row(T0 + 207.01)
    out[name] = den.run()
    print("RESULT %-45s counted=%s not_counted=%s" % (name, *out[name]))
same = len({repr(v) for v in out.values()}) == 1
print(("FAIL" if same else "PASS") + " the hold target that would tell them apart is %s" % ("ignored" if same else
                                                                                           "used"))
