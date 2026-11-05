"""probe_export_gap_counted_as_exposure: two non-overlapping status exports (the README's "give each kind of file
more than once" to extend the window) leave a gap the recorder no longer holds. If the first export's last status
row shows den_lighting in charge (automatic, occupancy on: the owner exporting from the den), capture.py counts the
whole gap as occupied hours, with no warning, and the planning verdict flips.

Synthetic data only, temp dirs only, never contacts a host.
Run:  python3 probe_export_gap_counted_as_exposure.py <root of the claude/correction-capture checkout>
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
WARM = {"on": True, "brightness": 100, "kelvin": 2700}


def status_row(t, revision, holds, occupancy="on", automatic=True):
    attrs = {"automatic": automatic, "gaming_enabled": True, "gaming_session": False, "gaming_signal": "idle",
             "occupancy": occupancy, "revision": revision, "error": None, "gaming_entity": "sensor.pc_gaming",
             "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in holds.items()},
             "occupancy_source": "raw_person_count", "pending_fixtures": [], "provisional_fixtures": [],
             "friendly_name": "Den lighting control"}
    return {"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs}


def bulb_row(t, e, on, n):
    return {"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off",
            "context_id": "%032X" % (0x0190000000000000000000000000 + n), "context_user_id": "",
            "context_parent_id": ""}


def evening(start, rev, n):
    """6 occupied hours with 2 switch corrections; the last row (export time) still shows den_lighting in charge."""
    status = [status_row(start, rev, {})]
    bulbs = [bulb_row(start + 3600, L0, False, n), bulb_row(start + 7200, L1, False, n + 1)]
    status.append(status_row(start + 3600.01, rev + 1, {L0: OFF}))
    status.append(status_row(start + 7200.01, rev + 2, {L0: OFF, L1: OFF}))
    status.append(status_row(start + 6 * 3600, rev + 3, {}))  # holds ended, still occupied, export taken now
    return status, bulbs


def write(folder, name, rows):
    path = Path(folder) / name
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
    return path


with tempfile.TemporaryDirectory() as tmp:
    s1, b1 = evening(T0, 100, 1)
    gap_days = 12  # the second export was made more than 10 days (purge_keep_days) after the first
    s2, b2 = evening(T0 + gap_days * 86400, 400, 10)  # revision went up: no restart in between
    status1, status2 = write(tmp, "den-status-1.jsonl", s1), write(tmp, "den-status-2.jsonl", s2)
    bulbs1, bulbs2 = write(tmp, "den-bulbs-1.jsonl", b1), write(tmp, "den-bulbs-2.jsonl", b2)
    cmds = write(tmp, "den-commands.jsonl", [])

    one, _ = capture.capture([status1], [bulbs1], [cmds])
    both, _ = capture.capture([status1, status2], [bulbs1, bulbs2], [cmds])
    p1, p2 = one["planning"]["strata"]["no_game"], both["planning"]["strata"]["no_game"]
    print("RESULT export 1 alone: occupied_h=%s people=%s verdict=%s" % (p1["occupied_h"], p1["people"]["count"],
                                                                         p1["verdict"]))
    print("RESULT exports 1+2 (gap %d days): occupied_h=%s people=%s per_h=%s verdict=%s restarts=%s"
          % (gap_days, p2["occupied_h"], p2["people"]["count"], p2["people"]["per_occupied_h"], p2["verdict"],
             both["span"]["restarts"]))
    true_h = 2 * 6.0
    print("RESULT hand count of occupied hours actually covered by the two exports: %.1f h" % true_h)
    if p2["occupied_h"] > true_h + 1:
        print("FAIL the %d-day gap between exports is counted as %.1f occupied hours; verdict %r from a rate of %s/h "
              "instead of %.3f/h" % (gap_days, p2["occupied_h"] - true_h, p2["verdict"],
                                     p2["people"]["per_occupied_h"], 4 / true_h))
    else:
        print("PASS the gap between exports is not counted as exposure")
