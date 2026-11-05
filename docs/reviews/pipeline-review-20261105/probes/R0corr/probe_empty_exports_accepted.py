"""probe_empty_exports_accepted: capture.py runs a scored count with an empty command export or an empty bulb
export and says nothing. With no commands it cannot recognise den_lighting's own rows, so den_lighting's late report
of its own write (its own test case, test_a_fade_alone_counts_nothing) becomes a person's switch correction; with no
bulb rows every hold becomes `unknown`, so people's count is 0. Either export coming back empty (wrong db path, the
recorder excluding the event type, a typo in the SQL) still yields a verdict.

Synthetic data only, temp dirs only, never contacts a host.
Run:  python3 probe_empty_exports_accepted.py <root of the claude/correction-capture checkout>
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
OFF = {"on": False, "brightness": None, "kelvin": None}
status, bulbs, commands, rev, n = [], [], [], 100, 0


def ctx():
    global n
    n += 1
    return "%032X" % (0x0190000000000000000000000000 + n)


def srow(t, holds):
    global rev
    rev += 1
    attrs = {"automatic": True, "gaming_enabled": True, "gaming_session": False, "gaming_signal": "idle",
             "occupancy": "on", "revision": rev, "error": None, "gaming_entity": "sensor.pc_gaming",
             "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in holds.items()},
             "occupancy_source": "raw_person_count", "pending_fixtures": [], "provisional_fixtures": [],
             "friendly_name": "Den lighting control"}
    status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs})


def bulb(t, e, on, c=None, user=False):
    bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off", "context_id": c or ctx(),
                  "context_user_id": "0F" * 16 if user else "", "context_parent_id": ""})


srow(T0, {})
own = ctx()
bulb(T0 + 199.7, L1, True, own)  # den_lighting's own row (its context)
commands.append({"time_fired_ts": T0 + 200, "event_data": {"entity_id": L1, "revision": rev, "target": WARM,
                                                           "context_id": sources.ulid(own)}})
bulb(T0 + 207, L1, True)         # its late id-less report, which den_lighting took for a touch
srow(T0 + 207.01, {L1: WARM})
srow(T0 + 400, {})
bulb(T0 + 1000, L2, False)       # a real switch touch
srow(T0 + 1000.01, {L2: OFF})
srow(T0 + 1100, {})
srow(T0 + 1200, {L3: WARM})      # a real app touch
bulb(T0 + 1200.3, L3, True, user=True)
srow(T0 + 3600, {})


def run(tmp, cmd_rows, bulb_rows):
    paths = []
    for name, rows in (("s", status), ("b", bulb_rows), ("c", cmd_rows)):
        p = Path(tmp) / (name + ".jsonl")
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
        paths.append([p])
    record, _ = capture.capture(*paths)
    s = record["planning"]["strata"]["no_game"]
    return s["people"]["count"], s["every_hold"]["count"], s["actions_by_source"], record["span"]


with tempfile.TemporaryDirectory() as tmp:
    full = run(tmp, commands, bulbs)
    no_cmd = run(tmp, [], bulbs)
    no_bulb = run(tmp, commands, [])
print("RESULT all three exports: people=%s every=%s by_source=%s" % full[:3])
print("RESULT empty command export: people=%s every=%s by_source=%s span.commands=%s (no error)" % (
    no_cmd[0], no_cmd[1], no_cmd[2], no_cmd[3]["commands"]))
print("RESULT empty bulb export: people=%s every=%s by_source=%s span.bulb_rows=%s (no error)" % (
    no_bulb[0], no_bulb[1], no_bulb[2], no_bulb[3]["bulb_rows"]))
if no_cmd[0] > full[0]:
    print("FAIL with the command export empty, den_lighting's own late report counts as a person (%d -> %d) and the "
          "run is not refused" % (full[0], no_cmd[0]))
if no_bulb[0] == 0 and full[0] > 0:
    print("FAIL with the bulb export empty, people's count drops to 0 and the run is not refused")
