"""probe_photometry_settings_counted_as_people: the ladder (rung 7) says probes "must not look like people: den_lighting
paused, every probe logged as a non-human change and excluded from corrections". The built rung-7 tool instead leaves
den_lighting running and has the owner set the light by hand for each capture ("Leave on whatever light you are
measuring ... dim a little and run the same line again", tools/den-photometry/README.md step 5). capture.py has no
input or option to exclude such a session, so every level the owner sets from the HA app during an occupied
photometry session is counted as a person's correction, and the planning verdict flips to within_budget.

Synthetic data only, temp dirs only, never contacts a host.
Run:  python3 probe_photometry_settings_counted_as_people.py <root of the claude/correction-capture checkout>
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
T0 = datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc).timestamp()
status, bulbs, rev, n = [], [], 100, 0


def srow(t, holds, occ="on"):
    global rev
    rev += 1
    attrs = {"automatic": True, "gaming_enabled": True, "gaming_session": False, "gaming_signal": "idle",
             "occupancy": occ, "revision": rev, "error": None, "gaming_entity": "sensor.pc_gaming",
             "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in holds.items()},
             "occupancy_source": "raw_person_count", "pending_fixtures": [], "provisional_fixtures": [],
             "friendly_name": "Den lighting control"}
    status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs})


def app_touch(t, e, level):
    global n
    n += 1
    srow(t, {e: {"on": True, "brightness": level, "kelvin": 2700}})
    bulbs.append({"last_updated_ts": t + 0.3, "entity_id": e, "state": "on",
                  "context_id": "%032X" % (0x0190000000000000000000000000 + n), "context_user_id": "0F" * 16,
                  "context_parent_id": ""})


# 10 ordinary evenings of 3 occupied hours, 4 real touches in all (0.13 per hour)
for day in range(10):
    start = T0 + day * 86400
    srow(start, {})
    if day in (1, 4, 6, 8):
        app_touch(start + 3600, events.LIGHTS[day % 4], 60)
        srow(start + 3700, {})
    srow(start + 3 * 3600, {}, "off")  # A leaves: occupancy off until the next evening
before = len(status)
# two photometry evenings (E8 runs on at least two days): the owner sets 4 levels on each of 4 bulbs from the HA
# app, one per capture
for day in (10, 11):
    start = T0 + day * 86400
    srow(start, {})
    k = 0
    for level in (255, 180, 100, 40):
        for e in events.LIGHTS:
            k += 1
            app_touch(start + 120 * k, e, level)
    srow(start + 3 * 3600, {}, "off")
with tempfile.TemporaryDirectory() as tmp:
    def run(status_rows):
        paths = []
        for name, rows in (("s", status_rows), ("b", bulbs), ("c", [])):
            p = Path(tmp) / (name + ".jsonl")
            p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
            paths.append([p])
        record, _ = capture.capture(*paths)
        return record["planning"]["strata"]["no_game"]
    ordinary = run(status[:before])
    with_photometry = run(status)
for name, p in (("ordinary evenings", ordinary), ("plus two photometry evenings", with_photometry)):
    print("RESULT %-28s occupied_h=%s people=%s per_h=%s verdict=%s" % (
        name, p["occupied_h"], p["people"]["count"], p["people"]["per_occupied_h"], p["verdict"]))
flags = subprocess.run([sys.executable, str(ROOT / "tools" / "correction-capture" / "capture.py"), "--help"],
                       capture_output=True, text=True).stdout
print("RESULT capture.py options: %s" % sorted({w for w in flags.split() if w.startswith("--")}))
if with_photometry["verdict"] != ordinary["verdict"] and with_photometry["people"]["count"] > ordinary["people"]["count"]:
    print("FAIL 32 owner-set photometry levels count as people's corrections (%d -> %d) and flip the verdict %r -> "
          "%r; there is no option or input to exclude them" % (ordinary["people"]["count"],
                                                             with_photometry["people"]["count"],
                                                             ordinary["verdict"], with_photometry["verdict"]))
else:
    print("PASS photometry settings did not change the verdict")
