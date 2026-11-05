"""Verifier probe for dimmer-within-ack-window-not-counted (R0corr, correction-capture).

What it checks
  den_lighting (2 s transition) turns L0 on to WARM. Its read-back phase ends at transition + 3 s plus at most one
  2 s tick plus the read, so by about 7.5 s an id-less report is, to den_lighting, a person's touch: it opens a plain
  (non-provisional) manual hold at once. A person then dims L0 with a Hue dimmer (id-less 'on' report, no on/off
  flip). The hold's target (DIM) differs from the command's target (WARM), so it is not den_lighting's late
  acknowledgement of its own command (the case test_a_fade_alone_counts_nothing covers, where the hold target
  equals the command target). The probe moves the touch across the tool's fixed 8 s window: 6.0, 7.5, 7.9, 8.1, 9 s.
  If the cut-off follows ACK_AFTER_S (8 s) rather than den_lighting's own judgement or the hold/command target
  mismatch, touches at 7.5 and 7.9 s are not counted although den_lighting itself opened a plain manual hold.

How to run (synthetic data, temp dirs only, no host contacted):
    python3 -B verify_dimmer-within-ack-window-not-counted.py /path/to/correction-capture/checkout
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
T0 = datetime(2026, 10, 30, 3, 0, tzinfo=timezone.utc).timestamp()


def build(delay, folder):
    status, bulbs, commands = [], [], []
    rev = [10]
    n = [0]

    def ctx():
        n[0] += 1
        return "%032X" % (0x0197000000000000000000000000 + n[0])

    def row(t, holds):
        rev[0] += 1
        status.append({"last_updated_ts": t, "entity_id": "sensor.den_lighting_control", "state": "occupancy",
                       "attributes": {"automatic": True, "gaming_session": False, "occupancy": "on",
                                      "revision": rev[0], "occupancy_source": "raw_person_count",
                                      "holds": {e: {"until": "room_vacant_2_minutes", "target": s}
                                                for e, s in holds.items()},
                                      "provisional_fixtures": [], "pending_fixtures": []}})

    def bulb(t, on, context=None):
        context = context or ctx()
        bulbs.append({"last_updated_ts": t, "entity_id": L0, "state": "on" if on else "off", "context_id": context,
                      "context_user_id": "", "context_parent_id": ""})
        return context

    row(T0, {})
    bulb(T0 + 10, False)
    c = T0 + 100
    own = bulb(c - 0.3, True)  # den_lighting's own state row (its context) while the call runs
    commands.append({"time_fired_ts": c, "event_data": {"entity_id": L0, "revision": rev[0], "target": WARM,
                                                        "context_id": sources.ulid(own)}})
    bulb(c + delay, True)       # the person's dimmer: id-less, no flip
    row(c + delay + 0.05, {L0: DIM})  # den_lighting: plain manual hold (no provisional_fixtures)
    row(T0 + 3600, {L0: DIM})
    paths = []
    for name, rows in (("status", status), ("lights", bulbs), ("commands", commands)):
        p = Path(folder) / (name + ".jsonl")
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
        paths.append([p])
    return paths


def main():
    print("RESULT: ACK_BEFORE_S, ACK_AFTER_S = %s, %s" % (capture.ACK_BEFORE_S, capture.ACK_AFTER_S))
    missed = []
    for delay in (6.0, 7.5, 7.9, 8.1, 9.0):
        with tempfile.TemporaryDirectory(prefix="vfy-dimmer-") as tmp:
            record, _ = capture.capture(*build(delay, tmp))
        people = record["planning"]["strata"]["no_game"]["people"]["count"]
        nc = [(x["hold"], x["reason"], x["source"]) for x in record["not_counted"]]
        if people == 0:
            missed.append(delay)
        print("%s: touch %.1f s after a 2 s-transition command, plain manual hold DIM (command target WARM) -- "
              "people %d; not_counted %s" % ("FAIL" if people == 0 else "PASS", delay, people, nc))
    print("RESULT: touches not counted at %s s; den_lighting's own phase for a 2 s transition ends by ~5-7.5 s"
          % missed)


if __name__ == "__main__":
    main()
