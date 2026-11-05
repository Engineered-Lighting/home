"""probe_pre_1_0_4_window_counted: the incumbent is frozen at den_lighting 1.0.4 (ladder rung 11). den_lighting was
installed on 2026-10-27 and 1.0.4 went live on 2026-10-28 (notes R14, README "Export by 2026-11-07"); the recorder
keeps 10 days, so an export made before about 2026-11-07 also holds the install day: commissioning, an earlier
version, the owner testing holds by hand. capture.py has no option to start the count at the 1.0.4 restart (or any
time), and its status rows carry no version, so every hold of the install day counts as a correction of the frozen
incumbent.

Synthetic data only, temp dirs only, never contacts a host.
Run:  python3 probe_pre_1_0_4_window_counted.py <root of the claude/correction-capture checkout>
"""
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "correction-capture"))
import capture  # noqa: E402
import events  # noqa: E402

STATUS = "sensor.den_lighting_control"
OFF = {"on": False, "brightness": None, "kelvin": None}
INSTALL = datetime(2026, 10, 28, 2, 0, tzinfo=timezone.utc).timestamp()   # evening of the install day (local)
LIVE_104 = datetime(2026, 10, 28, 20, 30, tzinfo=timezone.utc).timestamp()  # HA restarted with 1.0.4
status, bulbs, n = [], [], 0


def srow(t, rev, occ, holds):
    attrs = {"automatic": True, "gaming_enabled": True, "gaming_session": False, "gaming_signal": "idle",
             "occupancy": occ, "revision": rev, "error": None, "gaming_entity": "sensor.pc_gaming",
             "holds": {e: {"until": "room_vacant_2_minutes", "target": OFF} for e in holds},
             "occupancy_source": "raw_person_count", "pending_fixtures": [], "provisional_fixtures": [],
             "friendly_name": "Den lighting control"}
    status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs})


def touch(t, e, rev):
    global n
    n += 1
    bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "off",
                  "context_id": "%032X" % (0x0190000000000000000000000000 + n), "context_user_id": "",
                  "context_parent_id": ""})
    srow(t + 0.01, rev, "on", [e])
    srow(t + 30, rev + 1, "on", [])


# install day: the owner tests den_lighting by switching bulbs, 12 touches in 2 hours
srow(INSTALL, 1, "on", [])
for k in range(12):
    touch(INSTALL + 540 * (k + 1), events.LIGHTS[k % 4], 2 + 2 * k)
srow(INSTALL + 7300, 40, "off", [])
status.append({"last_updated_ts": LIVE_104 - 60, "entity_id": STATUS, "state": "unavailable",
               "attributes": {"restored": True, "friendly_name": "Den lighting control"}})
srow(LIVE_104, 0, "unknown", [])            # 1.0.4 starts
# 8 ordinary evenings with 1.0.4: 3 occupied hours, 1 touch each
for day in range(1, 9):
    start = INSTALL + day * 86400
    srow(start, 100 * day, "on", [])
    touch(start + 3600, events.LIGHTS[day % 4], 100 * day + 1)
    srow(start + 3 * 3600, 100 * day + 3, "off", [])
with tempfile.TemporaryDirectory() as tmp:
    paths = []
    for name, rows in (("s", status), ("b", bulbs), ("c", [])):
        p = Path(tmp) / (name + ".jsonl")
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
        paths.append([p])
    record, _ = capture.capture(*paths)
p = record["planning"]["strata"]["no_game"]
before = sum(1 for c in record["corrections"] if c["ts"] < LIVE_104)
print("RESULT span %s to %s, restarts=%s" % (record["span"]["from"], record["span"]["to"], record["span"]["restarts"]))
print("RESULT people=%s (of which %d before the 1.0.4 restart) occupied_h=%s per_h=%s" % (
    p["people"]["count"], before, p["occupied_h"], p["people"]["per_occupied_h"]))
flags = subprocess.run([sys.executable, str(ROOT / "tools" / "correction-capture" / "capture.py"), "--help"],
                       capture_output=True, text=True).stdout
has_window = any(w in flags for w in ("--since", "--from", "--start", "--after"))
print("RESULT capture.py has a window option: %s" % has_window)
if before and not has_window:
    print("FAIL %d install-day holds from before den_lighting 1.0.4 count as corrections of the frozen incumbent "
          "(rate %s/h instead of %.3f/h) and nothing lets the owner start at the 1.0.4 restart"
          % (before, p["people"]["per_occupied_h"], 8 / 24.0))
else:
    print("PASS pre-1.0.4 rows are excluded or excludable")
