"""verify_export-gap-counted-as-exposure: independent check of the export-gap finding with a realistic window.

Two status/bulb exports as the README's 'give each kind of file more than once' allows: export 1 holds ten evenings
(3 occupied hours each) and was taken while A sat in the den (last row: automatic, occupancy on); export 2 was taken
12 days later, so the recorder (10-day purge) has dropped about two days in between. It prints the occupied hours
capture.py reports, the hand count, and the same run when export 1's last row shows occupancy off (control). A third
run puts a hold that began inside the gap in export 2's first row and shows how it is classed.

Synthetic data only, temp dirs only, never contacts a host.
Run:  python3 -B verify_export-gap-counted-as-exposure.py <root of the claude/correction-capture checkout>
"""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "correction-capture"))
import capture  # noqa: E402
import events  # noqa: E402

L0, L1, L2, L3 = events.LIGHTS
STATUS = "sensor.den_lighting_control"
OFF = {"on": False, "brightness": None, "kelvin": None}
DIM = {"on": True, "brightness": 40, "kelvin": 2700}
DAY = 86400.0


class Export:
    def __init__(self, rev):
        self.status, self.bulbs, self.rev, self.n = [], [], rev, 0

    def srow(self, t, occupancy, holds=None):
        self.rev += 1
        attrs = {"automatic": True, "gaming_enabled": True, "gaming_session": False, "gaming_signal": "idle",
                 "occupancy": occupancy, "revision": self.rev, "error": None, "gaming_entity": "sensor.pc_gaming",
                 "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in (holds or {}).items()},
                 "occupancy_source": "raw_person_count", "pending_fixtures": [], "provisional_fixtures": [],
                 "friendly_name": "Den lighting control"}
        self.status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs})

    def brow(self, t, e, on):
        self.n += 1
        self.bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off",
                           "context_id": "%032X" % ((self.rev << 40) + self.n), "context_user_id": "",
                           "context_parent_id": ""})

    def evening(self, start):
        """3 occupied hours, one wall-switch correction (L2 off) that ends at vacancy."""
        self.srow(start, "on")
        self.brow(start + 3600, L2, False)
        self.srow(start + 3600.01, "on", {L2: OFF})
        self.srow(start + 3 * 3600, "off")


def run(last_occupancy, hold_in_gap=False):
    d0 = datetime(2026, 10, 28, 2, 0, tzinfo=timezone.utc).timestamp()   # 19:00 local on the first evening
    e1 = Export(100)
    for k in range(10):
        e1.evening(d0 + k * DAY)
    e1.srow(d0 + 10 * DAY, last_occupancy)              # export 1 taken from the den PC on day 11
    e2 = Export(e1.rev + 5000)                          # no restart in between: the revision kept climbing
    first2 = d0 + 12 * DAY - 1 * 3600                   # first retained row of export 2, 12 days later
    if hold_in_gap:
        e2.srow(first2, "on", {L0: DIM})                # L0 was held by someone inside the gap
        e2.srow(first2 + 600, "off")
    else:
        e2.srow(first2, "off")
    for k in range(12, 22):
        e2.evening(d0 + k * DAY)
    e2.srow(d0 + 22 * DAY, "off")
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for name, rows in (("status-1", e1.status), ("status-2", e2.status), ("bulbs-1", e1.bulbs),
                           ("bulbs-2", e2.bulbs), ("cmds", [])):
            p = Path(tmp) / (name + ".jsonl")
            p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
            paths.append(p)
        rec, _ = capture.capture(paths[0:2], paths[2:4], [paths[4]])
    return rec


true_h = 20 * 3.0
for label, occ in (("export 1 ends in charge (A at the den PC)", "on"), ("control: export 1 ends vacant", "off")):
    rec = run(occ)
    p = rec["planning"]["strata"]["no_game"]
    print("RESULT %-42s occupied_h=%s (hand count %.1f) people=%s per_h=%s restarts=%s verdict=%s"
          % (label, p["occupied_h"], true_h, p["people"]["count"], p["people"]["per_occupied_h"],
             rec["span"]["restarts"], p["verdict"]))
    if occ == "on":
        extra = p["occupied_h"] - true_h
        print(("FAIL " if extra > 1 else "PASS ") + "gap time counted as occupied hours: %.1f h (no warning field: %s)"
              % (extra, sorted(k for k in rec["span"])))
rec = run("on", hold_in_gap=True)
first = [(c["changes"][0]["entity_id"] == L0, c["source"], c["changes"][0]["hold"]) for c in rec["corrections"]
         if c["changes"][0]["entity_id"] == L0]
skipped = [(c["reason"], c["hold"]) for c in rec["not_counted"] if c["entity_id"] == L0]
print("RESULT hold begun inside the gap: counted=%s not_counted=%s every_hold=%s"
      % (first, skipped, rec["planning"]["strata"]["no_game"]["every_hold"]["count"]))
print(("FAIL " if first else "PASS ") + "a hold already in export 2's first row is %s"
      % ("counted as a new correction (source %s)" % first[0][1] if first else "not counted"))
