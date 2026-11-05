"""probe_export_gap_counted_as_exposure_verify: verifier's realistic variant of probe_export_gap_counted_as_exposure.
With HA's default 10-day retention, an export made 12 days after the first one starts about 2 days after the first
export ended, so the unrecorded gap is about 2 days (not 12). This checks whether that 2-day gap is still counted as
occupied hours when export 1's last status row shows den_lighting in charge, and, as a control, that it is not
counted when that last row shows occupancy off. It also checks that a hold already present in export 2's first row
is counted instead of being held_at_export_start.

Synthetic data only, temp dirs only, never contacts a host.
Run:  python3 -B probe_export_gap_counted_as_exposure_verify.py <root of the claude/correction-capture checkout>
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

L0, L1, L2, L3 = events.LIGHTS
STATUS = "sensor.den_lighting_control"
T0 = datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc).timestamp()
OFF = {"on": False, "brightness": None, "kelvin": None}


def status_row(t, revision, holds, occupancy="on"):
    attrs = {"automatic": True, "gaming_enabled": True, "gaming_session": False, "gaming_signal": "idle",
             "occupancy": occupancy, "revision": revision, "error": None, "gaming_entity": "sensor.pc_gaming",
             "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in holds.items()},
             "occupancy_source": "raw_person_count", "pending_fixtures": [], "provisional_fixtures": [],
             "friendly_name": "Den lighting control"}
    return {"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs}


def export(days, start, rev, last_occupancy, first_holds=None):
    """One export: `days` evenings of 1.5 occupied hours each (on at 19:00, off at 20:30 local), then a last row at
    export time with occupancy `last_occupancy`."""
    rows = []
    if first_holds:
        rows.append(status_row(start - 60, rev, first_holds, occupancy="on"))
        rev += 1
    for d in range(days):
        t = start + d * 86400
        rows.append(status_row(t, rev, {}, "on"))
        rows.append(status_row(t + 5400, rev + 1, {}, "off"))
        rev += 2
    rows.append(status_row(start + days * 86400 - 3600, rev, {}, last_occupancy))
    return rows, rev + 1


def write(folder, name, rows):
    path = Path(folder) / name
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
    return path


for last_occ in ("on", "off"):
    with tempfile.TemporaryDirectory() as tmp:
        s1, rev = export(10, T0, 100, last_occ)  # 10-28 .. 11-07, last row = the export moment
        gap_start = s1[-1]["last_updated_ts"]
        start2 = T0 + 12 * 86400  # export 2 made 12 days later keeps only the last 10 days
        s2, _ = export(10, start2, rev + 50, "off", first_holds={L2: OFF})
        gap_h = (s2[0]["last_updated_ts"] - gap_start) / 3600
        f1, f2 = write(tmp, "s1.jsonl", s1), write(tmp, "s2.jsonl", s2)
        b, c = write(tmp, "b.jsonl", []), write(tmp, "c.jsonl", [])
        one, _ = capture.capture([f1], [b], [c])
        both, _ = capture.capture([f1, f2], [b], [c])
        h1 = one["planning"]["strata"]["no_game"]["occupied_h"]
        h2 = both["planning"]["strata"]["no_game"]["occupied_h"]
        true_h = 20 * 1.5 + (1 if last_occ == "on" else 0) * 0  # 20 evenings x 1.5 h
        reasons = both["planning"]["not_counted_by_reason"]
        every = both["planning"]["strata"]["no_game"]["every_hold"]["count"]
        print("RESULT last row occupancy=%s: export1 occupied_h=%s; both=%s (recorded evenings %.1f h; "
              "unrecorded gap %.1f h); restarts=%s; every_hold=%s; not_counted=%s; verdict=%s"
              % (last_occ, h1, h2, true_h, gap_h, both["span"]["restarts"], every, reasons,
                 both["planning"]["strata"]["no_game"]["verdict"]))
        if h2 > true_h + 1:
            print("FAIL last row occupancy=%s: %.1f h of the unrecorded gap counted as occupied" % (last_occ,
                                                                                                 h2 - true_h))
        else:
            print("PASS last row occupancy=%s: the gap is not counted" % last_occ)
        if every and "held_at_export_start" not in reasons:
            print("FAIL hold present in export 2's first row counted in every_hold (%d) instead of "
                  "held_at_export_start" % every)
