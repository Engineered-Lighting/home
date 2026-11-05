"""R0corr verifier probe (intent lens) for finding dimmer-within-ack-window-not-counted.

What it checks
  correction-capture's own test (test_capture.py, test_a_fade_alone_counts_nothing) deliberately sets aside "a late
  report of its own after read-back, which den_lighting took for a touch": an id-less 'on' row 7 s after a command,
  followed by a NON-provisional hold whose target equals the command's target. The finding's case (b) is a person's
  dimmer 6.5 s after a command: also id-less, also non-provisional, but the hold's target (what den_lighting read
  from the bulb) differs from the command's target. This probe runs both through capture() and prints:
    - how each is attributed (expected by the tool's design: both 'den_lighting' / own_fade_report);
    - whether the hold target equals the command target in each (the discriminator already in the inputs);
    - what the finding's suggested fix (exempt only provisional holds) would do to the late-own-report case.
  Synthetic data only, temp dirs only, no host contacted.

How to run
    python3 -B verify_dimmer-within-ack-window-not-counted_intent.py /path/to/branch/checkout
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
DIM = {"on": True, "brightness": 60, "kelvin": 2700}
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
        attrs = {"automatic": True, "gaming_session": False, "occupancy": "on", "revision": self.rev,
                 "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in self.now["holds"].items()},
                 "occupancy_source": "raw_person_count", "provisional_fixtures": list(self.now["provisional"])}
        self.status.append({"last_updated_ts": t, "entity_id": "sensor.den_lighting_control", "state": "occupancy",
                            "attributes": attrs})

    def bulb(self, t, on, context=None):
        context = context or self.ctx()
        self.bulbs.append({"last_updated_ts": t, "entity_id": L0, "state": "on" if on else "off",
                           "context_id": context, "context_user_id": "", "context_parent_id": ""})
        return context

    def command(self, t, target):
        context = self.bulb(t - 0.3, target["on"])
        self.commands.append({"time_fired_ts": t, "event_data": {"entity_id": L0, "revision": self.rev,
                                                                 "target": target, "context_id": sources.ulid(context)}})

    def write(self, folder):
        out = []
        for name, rows in (("status", self.status), ("lights", self.bulbs), ("commands", self.commands)):
            p = Path(folder) / (name + ".jsonl")
            p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
            out.append([p])
        return out


def case(delay, held):
    ex = Ex()
    ex.bulb(T0 + 50, False)
    c = T0 + 100
    ex.command(c, WARM)
    ex.bulb(c + delay, True)                       # id-less 'on' report, no flip
    ex.row(c + delay + 0.01, holds={L0: held})     # non-provisional hold, as den_lighting does after its phase
    ex.row(T0 + 3600)
    return ex


def main():
    rows = []
    for label, delay, held in (("late own report (tool's own test shape)", 7.0, WARM),
                               ("person's dimmer (finding case b)", 6.5, DIM)):
        with tempfile.TemporaryDirectory(prefix="r0corr-v-") as tmp:
            record, _ = capture.capture(*case(delay, held).write(tmp))
        people = record["planning"]["strata"]["no_game"]["people"]["count"]
        nc = [(x["hold"], x["reason"], x["source"], x["after"]) for x in record["not_counted"]]
        same = held == WARM
        rows.append((label, people, nc, same))
        print("RESULT: %s -- people %d; not_counted %s; hold target == command target: %s" % (label, people, nc, same))
    both_set_aside = all(p == 0 for _, p, _, _ in rows)
    print("%s: the tool treats both identically (both set aside as den_lighting's own)"
          % ("PASS" if both_set_aside else "FAIL"))
    print("RESULT: discriminator present in the inputs but unused: hold target == command target is %s for the late "
          "own report and %s for the dimmer" % (rows[0][3], rows[1][3]))
    print("RESULT: the finding's suggested fix (exempt only provisional holds) would count the late own report "
          "(non-provisional) as a person, reversing the tool's tested intent")


if __name__ == "__main__":
    main()
