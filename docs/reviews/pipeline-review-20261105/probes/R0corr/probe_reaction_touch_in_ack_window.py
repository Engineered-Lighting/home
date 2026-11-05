"""probe_reaction_touch_in_ack_window: a person who reacts to den_lighting's own command with a dimmer within 8 s
(lights come on at arrival, A dims one at once) is not counted. capture.py sets aside every id-less bulb row within
[command - 2 s, command + 8 s] unless it flips on/off against the command, and never compares the hold's target with
the command's target. So:
  case A: den_lighting itself marks the touch provisional, read-back finds DIM not WARM and keeps the hold
          (a confirmed hold, which the README says is a candidate) -> capture says den_lighting / own_fade_report.
  case B: the touch lands 6 s after a 2 s-transition command, after den_lighting's own window (transition + 3 s)
          and read-back closed, so den_lighting opens an ordinary (non-provisional) manual hold at DIM
          -> capture again says den_lighting / own_fade_report.
  control: the same touch 30 s after the command is counted as a switch_or_dimmer correction.

Synthetic data only, temp dirs only, never contacts a host.
Run:  python3 probe_reaction_touch_in_ack_window.py <root of the claude/correction-capture checkout>
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
        attrs = {"automatic": True, "gaming_enabled": True, "gaming_session": False, "gaming_signal": "idle",
                 "occupancy": "on", "revision": self.rev, "error": None, "gaming_entity": "sensor.pc_gaming",
                 "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in self.holds.items()},
                 "occupancy_source": "raw_person_count", "pending_fixtures": [],
                 "provisional_fixtures": list(self.prov), "friendly_name": "Den lighting control"}
        self.status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs})

    def bulb(self, t, e, on, context=None):
        context = context or self.ctx()
        self.bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off",
                           "context_id": context, "context_user_id": "", "context_parent_id": ""})
        return context

    def command(self, t, e, target):
        context = self.bulb(t - 0.3, e, target["on"])  # den_lighting's own row, its context
        self.commands.append({"time_fired_ts": t, "event_data": {"entity_id": e, "revision": self.rev,
                              "target": target, "context_id": sources.ulid(context)}})

    def write(self, folder):
        out = []
        for name, rows in (("s", self.status), ("b", self.bulbs), ("c", self.commands)):
            p = Path(folder) / (name + ".jsonl")
            p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
            out.append([p])
        return out


def run(build):
    den = Den()
    build(den)
    den.row(T0 + 3600)
    with tempfile.TemporaryDirectory() as tmp:
        record, _ = capture.capture(*den.write(tmp))
    counted = [(c["source"], c["changes"][0]["hold"]) for c in record["corrections"]]
    skipped = [(c["reason"], c["source"], c["hold"]) for c in record["not_counted"]]
    return counted, skipped


def case_a(den):
    den.command(T0 + 100, L0, WARM)        # arrival: den_lighting fades L0 to WARM (2 s)
    den.bulb(T0 + 101, L0, True)           # its own fade report
    den.bulb(T0 + 104, L0, True)           # A presses the dimmer: id-less report at DIM, inside the phase
    den.holds, den.prov = {L0: DIM}, [L0]
    den.row(T0 + 104.01)                   # den_lighting: provisional hold
    den.prov = []
    den.row(T0 + 107)                      # read-back finds DIM, not WARM: den_lighting keeps the hold


def case_b(den):
    den.command(T0 + 100, L0, WARM)        # 2 s transition: den_lighting's own window ends at +5 s
    den.bulb(T0 + 101.5, L0, True)         # its exact final report (matches WARM, no hold)
    den.bulb(T0 + 106, L0, True)           # A dims 6 s after the command, read-back already done
    den.holds = {L0: DIM}
    den.row(T0 + 106.01)                   # den_lighting: an ordinary manual hold (not provisional)


def control(den):
    den.command(T0 + 100, L0, WARM)
    den.bulb(T0 + 101.5, L0, True)
    den.bulb(T0 + 130, L0, True)           # the same touch, 30 s later
    den.holds = {L0: DIM}
    den.row(T0 + 130.01)


bad = 0
for name, build in (("A confirmed provisional", case_a), ("B manual hold at +6 s", case_b), ("control +30 s", control)):
    counted, skipped = run(build)
    print("RESULT %-24s counted=%s not_counted=%s" % (name, counted, skipped))
    if not counted and name != "control +30 s":
        bad += 1
        print("FAIL %s: den_lighting kept a hold at DIM after commanding WARM, but capture counts no correction"
              % name)
    elif counted and name == "control +30 s":
        print("PASS control: the same touch outside the window is a person's correction")
print("RESULT %d of 2 reaction touches dropped" % bad)
