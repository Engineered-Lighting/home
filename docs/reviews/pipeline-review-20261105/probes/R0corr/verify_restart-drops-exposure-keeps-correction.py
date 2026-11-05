"""Verifier probe for restart-drops-exposure-keeps-correction (R0corr, correction-capture).

What it checks
  Monte Carlo of correction-capture's people rate, run through capture.capture() on synthetic exports. Each
  replicate is 10 occupied evenings of 3 h (no game). Switch corrections arrive as a Poisson process at LAMBDA per
  occupied hour; den_lighting's status sensor writes a row at occupancy on/off and at each hold change only. In the
  'restart' arm every evening also has an HA restart at a uniform time between 0.5 h and 2.5 h ('unavailable', then a
  row with a lower revision 30 s later); the den stays occupied. walk() drops the time from the last status row
  before the restart to the first row after it, but capture() keeps a correction made at that last row.
  The probe compares the pooled rate (sum of people / sum of occupied_h) in the two arms with the true LAMBDA, and
  an adjusted estimator that also drops the correction at the row that opens each dropped span.

How to run (synthetic data, temp dirs only, no host contacted; about 30-60 s):
    python3 -B verify_restart-drops-exposure-keeps-correction.py /path/to/correction-capture/checkout [replicates]
"""
import json
import random
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
REPS = int(sys.argv[2]) if len(sys.argv) > 2 else 150
sys.path.insert(0, str(ROOT / "tools" / "correction-capture"))
import capture  # noqa: E402

LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
OFF = {"on": False, "brightness": None, "kelvin": None}
WARM = {"on": True, "brightness": 100, "kelvin": 2700}
LAMBDA = 0.5  # corrections per occupied hour
EVENING_H = 3.0
T0 = datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc).timestamp()


def replicate(rng, restart, folder):
    status, bulbs = [], []
    st = {"rev": 100, "occ": "off", "holds": {}, "n": 0}
    opened_by_correction = 0  # dropped spans whose opening row is a correction row

    def row(t, revision=None):
        st["rev"] = st["rev"] + 1 if revision is None else revision
        status.append({"last_updated_ts": t, "entity_id": "sensor.den_lighting_control", "state": "occupancy",
                       "attributes": {"automatic": True, "gaming_session": False, "occupancy": st["occ"],
                                      "revision": st["rev"], "occupancy_source": "raw_person_count",
                                      "holds": {e: {"until": "room_vacant_2_minutes", "target": s}
                                                for e, s in st["holds"].items()},
                                      "provisional_fixtures": [], "pending_fixtures": []}})

    def bulb(t, e, on):
        st["n"] += 1
        bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off",
                      "context_id": "%032X" % (0x0198000000000000000000000000 + st["n"]),
                      "context_user_id": "", "context_parent_id": ""})

    for day in range(10):
        start = T0 + day * 86400
        st["occ"] = "off"
        row(start - 600)
        st["occ"] = "on"
        row(start)
        last_kind = "occupancy"
        times, t = [], 0.0
        while True:
            t += rng.expovariate(LAMBDA)
            if t >= EVENING_H:
                break
            times.append(t)
        r_h = rng.uniform(0.5, 2.5) if restart else None
        events = [(x, "c") for x in times] + ([(r_h, "r")] if restart else [])
        for x, kind in sorted(events):
            when = start + x * 3600
            if kind == "c":
                e = LIGHTS[rng.randrange(4)]
                new = OFF if st["holds"].get(e) == WARM else WARM
                bulb(when, e, new["on"])
                st["holds"][e] = new
                row(when + 0.01)
                last_kind = "c"
            else:
                opened_by_correction += last_kind == "c"
                status.append({"last_updated_ts": when, "entity_id": "sensor.den_lighting_control",
                               "state": "unavailable", "attributes": {"restored": True}})
                row(when + 30, revision=1)
                last_kind = "restart"
        st["occ"] = "off"
        st["holds"] = {}
        row(start + EVENING_H * 3600)
    row(T0 + 10 * 86400)
    paths = []
    for name, rows in (("status", status), ("lights", bulbs), ("commands", [])):
        p = Path(folder) / (name + ".jsonl")
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
        paths.append([p])
    record, _ = capture.capture(*paths)
    ng = record["planning"]["strata"]["no_game"]
    return ng["people"]["count"], ng["occupied_h"], opened_by_correction, record["planning"]["restart_gap_h"]


def main():
    rng = random.Random(20261105)
    out = {}
    for restart in (False, True):
        n = h = adj = gap = 0
        for _ in range(REPS):
            with tempfile.TemporaryDirectory(prefix="vfy-restart-") as tmp:
                c, oh, o, g = replicate(rng, restart, tmp)
            n, h, adj, gap = n + c, h + oh, adj + o, gap + (g or 0)
        out[restart] = (n, h, adj, gap)
        label = "restart" if restart else "no restart"
        print("RESULT: %-10s: %d people over %.1f h -> %.4f per h (true %.2f); restart_gap %.1f h; "
              "corrections at a dropped span's opening row %d; adjusted (drop them) %.4f per h"
              % (label, n, h, n / h, LAMBDA, gap, adj, (n - adj) / h))
    base = out[False][0] / out[False][1]
    rst = out[True][0] / out[True][1]
    adjr = (out[True][0] - out[True][2]) / out[True][1]
    print("%s: tool rate with one restart per evening is %.1f%% above truth (no-restart arm %.1f%%); adjusted "
          "estimator %.1f%%" % ("FAIL" if (rst / LAMBDA - 1) > 0.03 else "PASS", 100 * (rst / LAMBDA - 1),
                                100 * (base / LAMBDA - 1), 100 * (adjr / LAMBDA - 1)))


if __name__ == "__main__":
    main()
