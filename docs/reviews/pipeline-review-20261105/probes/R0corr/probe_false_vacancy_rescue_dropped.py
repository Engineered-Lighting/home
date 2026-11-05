"""probe_false_vacancy_rescue_dropped: A sits still, the camera loses A, den_lighting's occupancy goes off and after
its 120 s vacancy it turns the bulbs off on a present person; A turns them back on at the switch (a flip against
den_lighting's own OFF, 10 s after it). That rescue is a human action that opened a hold, the numerator rung 11 names,
and the most direct correction of den_lighting there is, but capture.py drops it as `outside_exposure` because
den_lighting's own occupancy was off at that moment. Each re-off 120 s later (the hold expires while still "vacant")
and each re-rescue is dropped the same way.

Synthetic data only, temp dirs only, never contacts a host.
Run:  python3 probe_false_vacancy_rescue_dropped.py <root of the claude/correction-capture checkout>
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
OFF = {"on": False, "brightness": None, "kelvin": None}
ON = {"on": True, "brightness": 180, "kelvin": 2700}
status, bulbs, commands, rev, n = [], [], [], 100, 0


def ctx():
    global n
    n += 1
    return "%032X" % (0x0190000000000000000000000000 + n)


def srow(t, occ, holds):
    global rev
    rev += 1
    attrs = {"automatic": True, "gaming_enabled": True, "gaming_session": False, "gaming_signal": "idle",
             "occupancy": occ, "revision": rev, "error": None, "gaming_entity": "sensor.pc_gaming",
             "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in holds.items()},
             "occupancy_source": "raw_person_count", "pending_fixtures": [], "provisional_fixtures": [],
             "friendly_name": "Den lighting control"}
    status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs})


def bulb(t, e, on, c=None):
    bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off", "context_id": c or ctx(),
                  "context_user_id": "", "context_parent_id": ""})


def command(t, e, target):
    c = ctx()
    bulb(t - 0.3, e, target["on"], c)
    commands.append({"time_fired_ts": t, "event_data": {"entity_id": e, "revision": rev, "target": target,
                                                        "context_id": sources.ulid(c)}})


srow(T0, "on", {})
srow(T0 + 1800, "off", {})                 # the camera loses A, who is still in the armchair
command(T0 + 1920, L0, OFF)                # 120 s later den_lighting turns L0 off on A
bulb(T0 + 1930, L0, True)                  # A switches it back on (against den_lighting's OFF)
srow(T0 + 1930.01, "off", {L0: ON})        # den_lighting: a manual hold, occupancy still off
srow(T0 + 2050, "off", {})                 # hold expires 120 s later, still "vacant"
command(T0 + 2050.5, L0, OFF)              # off again
bulb(T0 + 2062, L0, True)                  # A rescues it again
srow(T0 + 2062.01, "off", {L0: ON})
srow(T0 + 2400, "on", {L0: ON})            # A stands up, the camera sees A again
srow(T0 + 3600, "on", {})
with tempfile.TemporaryDirectory() as tmp:
    paths = []
    for name, rows in (("s", status), ("b", bulbs), ("c", commands)):
        p = Path(tmp) / (name + ".jsonl")
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
        paths.append([p])
    record, _ = capture.capture(*paths)
p = record["planning"]["strata"]["no_game"]
dropped = [(round(c["t"] - T0, 2), c["reason"], c["at"]["occupancy"]) for c in record["not_counted"]]
print("RESULT counted corrections=%d people=%d occupied_h=%s" % (len(record["corrections"]), p["people"]["count"],
                                                                  p["occupied_h"]))
print("RESULT not_counted=%s" % dropped)
if p["people"]["count"] == 0 and sum(1 for d in dropped if d[1] == "outside_exposure") == 2:
    print("FAIL both rescues of den_lighting's vacancy-off on a present person are dropped as outside_exposure; "
          "the incumbent's numerator never sees the corrections its occupancy errors cause")
else:
    print("PASS rescues are counted")
