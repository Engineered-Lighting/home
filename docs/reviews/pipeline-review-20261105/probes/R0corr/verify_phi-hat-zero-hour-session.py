"""verify_phi-hat-zero-hour-session: checks whether a 24-hour block with a counted correction but 0.0 occupied hours
can come out of capture.capture end to end (not only from a direct phi_hat call), and what phi_hat then reports
against a Poisson GLM refit on the sessions phi_hat actually uses (occupied_h > 0).

Route tried: two ordinary evenings with switch corrections, then a third block whose den_lighting status row shows
occupancy turning on in the same row as a switch hold, and that row is the last one in the export (the export was
taken right after). Second route: the hold row lands 0.1 s after the 20:00 UTC block boundary and is the last row.

Synthetic data only, temp dirs only, never contacts a host.
Run:  python3 -B verify_phi-hat-zero-hour-session.py <root of the claude/correction-capture checkout>
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
OFF = {"on": False, "brightness": None, "kelvin": None}
T0 = datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc).timestamp()


def build(route):
    status, bulbs, state = [], [], {"rev": 100, "n": 0}

    def srow(t, occ, holds):
        state["rev"] += 1
        attrs = {"automatic": True, "gaming_enabled": True, "gaming_session": False, "gaming_signal": "idle",
                 "occupancy": occ, "revision": state["rev"], "error": None, "gaming_entity": "sensor.pc_gaming",
                 "holds": {e: {"until": "room_vacant_2_minutes", "target": OFF} for e in holds},
                 "occupancy_source": "raw_person_count", "pending_fixtures": [], "provisional_fixtures": [],
                 "friendly_name": "Den lighting control"}
        status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs})

    def switch(t, e):
        state["n"] += 1
        bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "off",
                      "context_id": "%032X" % (0x0190000000000000000000000000 + state["n"]),
                      "context_user_id": "", "context_parent_id": ""})

    for day, (hours, count) in enumerate([(2.0, 3), (2.0, 1)]):
        start = T0 + day * 86400
        srow(start, "on", [])
        for k in range(count):
            t = start + (k + 1) * hours * 3600 / (count + 1)
            switch(t, events.LIGHTS[k % 4])
            srow(t + 0.01, "on", [events.LIGHTS[k % 4]])
            srow(t + 60, "on", [])
        srow(start + hours * 3600, "off", [])
    if route == "occupancy on with the hold, last row":
        start = T0 + 2 * 86400
        srow(start, "off", [])
        switch(start + 600, L0)
        srow(start + 600.01, "on", [L0])
    else:
        boundary = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc).timestamp()
        srow(boundary - 600, "on", [])
        switch(boundary + 0.05, L0)
        srow(boundary + 0.1, "on", [L0])
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for name, rows in (("s", status), ("b", bulbs), ("c", [])):
            p = Path(tmp) / (name + ".jsonl")
            p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
            paths.append([p])
        record, _ = capture.capture(*paths)
    return record


def refit(per):
    rows = [s for s in per if s["occupied_h"] > 0]
    h, c = sum(s["occupied_h"] for s in rows), sum(s["corrections"] for s in rows)
    if len(rows) < 2 or not c:
        return None
    return round(sum((s["corrections"] - s["occupied_h"] * c / h) ** 2 / (s["occupied_h"] * c / h)
                     for s in rows) / (len(rows) - 1), 4)


for route in ("occupancy on with the hold, last row", "hold 0.1 s after the block boundary, last row"):
    record = build(route)
    per = record["exposure"]["per_session"]
    zero = [s for s in per if s["occupied_h"] == 0 and s["corrections"] > 0]
    print("RESULT route '%s': per_session=%s" % (route, [(s["session_id"], s["occupied_h"], s["corrections"])
                                                          for s in per]))
    print("RESULT phi_hat tool=%s refit_on_used_sessions=%s" % (record["exposure"]["phi_hat"], refit(per)))
    print("%s zero-hour session with a counted correction reached end to end: %s" % (
        "FAIL" if zero and record["exposure"]["phi_hat"] != refit(per) else "PASS", bool(zero)))
