"""verify_pre-1-0-4-window-counted_reproduce (REPRODUCE lens): independent re-check of the finding that install-day
holds from before den_lighting 1.0.4 are counted as corrections of the frozen 1.0.4 incumbent.

Builds one export: an install-day evening (3 hand switch holds, before an HA restart that brings 1.0.4), then four
1.0.4 evenings (1 hold each). Runs capture.capture twice:
  (a) install-day status rows carry the same six attributes capture.py needs (what the finding assumes);
  (b) install-day status rows lack provisional_fixtures (an earlier version with a smaller attribute set).
Also reports whether capture() or the CLI accept any start time, and whether a status row carries a version.
Synthetic data only, temp dirs only, never contacts a host.
Run:  python3 -B verify_pre-1-0-4-window-counted_reproduce.py <root of the claude/correction-capture checkout>
"""
import inspect
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

L = events.LIGHTS
STATUS = "sensor.den_lighting_control"
OFF = {"on": False, "brightness": None, "kelvin": None}
INSTALL = datetime(2026, 10, 28, 2, 0, tzinfo=timezone.utc).timestamp()
LIVE = datetime(2026, 10, 28, 20, 30, tzinfo=timezone.utc).timestamp()


def build(old_has_provisional):
    status, bulbs, rev = [], [], [0]

    def srow(t, occ, holds, old=False, revision=None):
        rev[0] = rev[0] + 1 if revision is None else revision
        attrs = {"automatic": True, "gaming_enabled": True, "gaming_session": False, "gaming_signal": "idle",
                 "occupancy": occ, "revision": rev[0], "error": None, "gaming_entity": "sensor.pc_gaming",
                 "holds": {e: {"until": "room_vacant_2_minutes", "target": OFF} for e in holds},
                 "occupancy_source": "raw_person_count", "pending_fixtures": [], "provisional_fixtures": [],
                 "friendly_name": "Den lighting control"}
        if old and not old_has_provisional:
            del attrs["provisional_fixtures"]
        status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs})

    def touch(t, e, old=False):
        bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "off",
                      "context_id": "%032X" % (0x0190000000000000000000000000 + len(bulbs) + 1),
                      "context_user_id": "", "context_parent_id": ""})
        srow(t + 0.01, "on", [e], old)
        srow(t + 60, "on", [], old)

    srow(INSTALL, "on", [], True)
    for k in range(3):
        touch(INSTALL + 1200 * (k + 1), L[k], True)
    srow(INSTALL + 3 * 3600, "off", [], True)
    status.append({"last_updated_ts": LIVE - 60, "entity_id": STATUS, "state": "unavailable",
                   "attributes": {"restored": True, "friendly_name": "Den lighting control"}})
    srow(LIVE, "unknown", [], revision=0)
    for day in range(1, 5):
        start = INSTALL + day * 86400
        srow(start, "on", [])
        touch(start + 3600, L[day % 4])
        srow(start + 3 * 3600, "off", [])
    return status, bulbs


for label, has in (("a: old rows carry all six attributes", True), ("b: old rows lack provisional_fixtures", False)):
    status, bulbs = build(has)
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for name, rows in (("s", status), ("b", bulbs), ("c", [])):
            p = Path(tmp) / (name + ".jsonl")
            p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
            paths.append([p])
        record, _ = capture.capture(*paths)
    p = record["planning"]["strata"]["no_game"]
    early = sum(1 for c in record["corrections"] if c["ts"] < LIVE)
    print("RESULT %s: people=%d (before 1.0.4: %d) occupied_h=%s per_h=%s span_from=%s" % (
        label, p["people"]["count"], early, p["occupied_h"], p["people"]["per_occupied_h"], record["span"]["from"]))
params = list(inspect.signature(capture.capture).parameters)
helptext = subprocess.run([sys.executable, "-B", str(ROOT / "tools/correction-capture/capture.py"), "--help"],
                          capture_output=True, text=True).stdout
print("RESULT capture() parameters: %s" % params)
print("RESULT CLI start-time option present: %s" % any(w in helptext for w in ("--since", "--from", "--start",
                                                                               "--after", "--begin")))
print("RESULT status fields kept per row: %s" % sorted(capture._status(build(True)[0][0]["attributes"])))
