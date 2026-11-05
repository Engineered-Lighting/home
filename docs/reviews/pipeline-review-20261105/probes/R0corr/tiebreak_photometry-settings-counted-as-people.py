"""tiebreak_photometry-settings-counted-as-people: tiebreak check for finding photometry-settings-counted-as-people.

Builds its own synthetic HA recorder exports (status sensor, bulbs, no commands) and runs capture.capture() on them.
It models the built rung-7 owner flow with modest, realistic numbers instead of the reporter's 32 settings:
  - photometry day 1 (bulbs): A sets front left + front right to full from the HA app in one HA context (one
    action), and later dims front left a little because the tool said "too bright" (one action);
  - photometry day 2 (the daylight day): five visits through the day; each time A comes back, den_lighting's arrival
    scene is on, so A turns all four bulbs off from the HA app in one HA context (one action per visit).
  That is 7 hand actions over the two days.
Three variants per baseline:
  baseline   - ten ordinary 3-hour evenings only;
  running    - plus the photometry days with den_lighting running (rung-7 README step 5 as built);
  paused     - plus the same photometry days with den_lighting's automatic switch off for each session and
               resume_automatic clearing the holds afterwards (ladder rung 7: 'den_lighting paused').
Prints RESULT lines and PASS/FAIL lines.

Synthetic data only, temp dirs only, never contacts a host.
Run:  python3 -B tiebreak_photometry-settings-counted-as-people.py <root of the claude/correction-capture checkout>
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

FL, FR, RL, RR = events.LIGHTS
STATUS = "sensor.den_lighting_control"
T0 = datetime(2026, 11, 10, 1, 0, tzinfo=timezone.utc).timestamp()  # evenings, inside one 20:00 UTC block each


class Export:
    def __init__(self):
        self.status, self.bulbs, self.rev, self.ctx = [], [], 100, 0

    def row(self, t, holds, occ="on", automatic=True):
        self.rev += 1
        attrs = {"automatic": automatic, "gaming_enabled": True, "gaming_session": False, "gaming_signal": "idle",
                 "occupancy": occ, "revision": self.rev, "error": None, "gaming_entity": "sensor.pc_gaming",
                 "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in holds.items()},
                 "occupancy_source": "raw_person_count", "pending_fixtures": [], "provisional_fixtures": [],
                 "friendly_name": "Den lighting control"}
        self.status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs})

    def app(self, t, targets, holds_now, automatic=True):
        """One HA-app action (one context, a user id) that sets each bulb in targets; den_lighting holds them."""
        self.ctx += 1
        context = "%032X" % (0x0190000000000000000000000000 + self.ctx)
        merged = dict(holds_now)
        merged.update(targets)
        self.row(t, merged, automatic=automatic)
        for i, (e, target) in enumerate(sorted(targets.items())):
            self.bulbs.append({"last_updated_ts": t + 0.2 + 0.01 * i + self.ctx * 1e-6, "entity_id": e,
                               "state": "on" if target["on"] else "off", "context_id": context,
                               "context_user_id": "0F" * 16, "context_parent_id": ""})
        return merged


def level(b, on=True):
    return {"on": on, "brightness": b if on else None, "kelvin": 2700 if on else None}


def ordinary(x, k):
    """Ten 3-hour evenings, k real HA-app corrections spread over them."""
    for day in range(10):
        start = T0 + day * 86400
        x.row(start, {})
        n = k // 10 + (1 if day < k % 10 else 0)
        for j in range(n):
            t = start + 600 + j * (2.5 * 3600 / max(n, 1))
            x.app(t, {events.LIGHTS[(day + j) % 4]: level(120 + j)}, {})
            x.row(t + 600, {})  # the hold ends (A's own later change, say), ending the hold
        x.row(start + 3 * 3600, {}, "off")


def photometry(x, paused):
    auto = not paused
    # day 1, bulbs: counter-1, desk, sofa
    start = T0 + 10 * 86400
    x.row(start, {})
    if paused:
        x.row(start + 30, {}, automatic=False)
    h = x.app(start + 120, {FL: level(254), FR: level(254)}, {}, automatic=auto)
    h = x.app(start + 900, {FL: level(200)}, h, automatic=auto)  # "too bright": dim a little
    x.row(start + 5400, {} if paused else h, automatic=auto)
    if paused:
        x.row(start + 5460, {})  # resume_automatic clears the holds
    x.row(start + 5520, {}, "off")
    # day 2, daylight day: five visits, bulbs off each time
    day2 = T0 + 11 * 86400 - 6 * 3600
    for v in range(5):
        t = day2 + v * 7200
        x.row(t, {})
        if paused:
            x.row(t + 20, {}, automatic=False)
        off = {e: level(None, on=False) for e in events.LIGHTS}
        h = x.app(t + 60, off, {}, automatic=auto)
        if paused:
            x.row(t + 840, {})  # resume_automatic
        x.row(t + 900, {}, "off")  # A leaves; den_lighting ends the holds 2 minutes later
        x.row(t + 1020, {}, "off")


def run(k, variant):
    x = Export()
    ordinary(x, k)
    if variant != "baseline":
        photometry(x, paused=(variant == "paused"))
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for name, rows in (("s", x.status), ("b", sorted(x.bulbs, key=lambda r: r["last_updated_ts"])), ("c", [])):
            p = Path(tmp) / (name + ".jsonl")
            p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
            paths.append([p])
        record, _ = capture.capture(*paths)
    p = record["planning"]["strata"]["no_game"]
    return p, record["planning"]["not_counted_by_reason"]


for k in (4, 18):
    res = {}
    for variant in ("baseline", "running", "paused"):
        p, nc = run(k, variant)
        res[variant] = p
        print("RESULT k=%-2d %-8s occupied_h=%-7s people=%-3s every=%-3s per_h=%-7s verdict=%-14s not_counted=%s" % (
            k, variant, p["occupied_h"], p["people"]["count"], p["every_hold"]["count"],
            p["people"]["per_occupied_h"], p["verdict"], nc))
    if res["running"]["people"]["count"] - res["baseline"]["people"]["count"] == 7:
        print("FAIL k=%d den_lighting running (rung-7 README as built): all 7 photometry hand actions count as people; "
              "verdict %s -> %s" % (k, res["baseline"]["verdict"], res["running"]["verdict"]))
    if (res["paused"]["people"]["count"] == res["baseline"]["people"]["count"]
            and res["paused"]["verdict"] == res["baseline"]["verdict"]):
        print("PASS k=%d den_lighting paused (ladder rung 7): capture.py already excludes every photometry action "
              "and adds no occupied time; verdict stays %s" % (k, res["paused"]["verdict"]))
    else:
        print("FAIL k=%d paused variant still changed the result" % k)
