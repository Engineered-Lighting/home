"""R0corr probe: an under-dispersed phi_hat (0 here) makes exposure.json's planning value 0 occupied hours per arm.

What it checks
  Three no-game evenings whose people-correction counts are exactly proportional to their occupied hours (2 h / 1,
  4 h / 2, 6 h / 3). The Pearson dispersion is then 0, and capture.py multiplies the people hours per arm by it
  (planning.strata.<s>.hours_per_arm_at_phi_hat = hours_per_arm * phi_hat) with no floor, so the record says a 30%
  improvement needs 0 occupied hours per arm. Quasi-Poisson planning keeps phi >= 1 (a Poisson process cannot be
  less variable for planning; the ladder plans at phi 1 and phi 2).

How to run (synthetic data, temp dirs only, no host contacted):
    python3 -B probe_phi_hat_zero_gives_zero_hours.py /path/to/branch/checkout
"""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "correction-capture"))
import capture  # noqa: E402

LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
OFF = {"on": False, "brightness": None, "kelvin": None}


def export(folder, evenings):
    status, bulbs, rev, holds, n = [], [], [60], {}, [0]

    def row(t, occ):
        rev[0] += 1
        attrs = {"automatic": True, "gaming_session": False, "occupancy": occ, "revision": rev[0],
                 "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in holds.items()},
                 "provisional_fixtures": [], "occupancy_source": "raw_person_count", "pending_fixtures": []}
        status.append({"last_updated_ts": t, "entity_id": "sensor.den_lighting_control", "state": "occupancy",
                       "attributes": attrs})

    for day, (hours, k) in enumerate(evenings):
        t = datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc).timestamp() + day * 86400
        row(t - 600, "off")
        row(t, "on")
        for i in range(k):
            tt = t + 600 + i * 1800
            n[0] += 1
            bulbs.append({"last_updated_ts": tt, "entity_id": LIGHTS[i], "state": "off",
                          "context_id": "%032X" % (0x0199 << 100 | n[0]), "context_user_id": "",
                          "context_parent_id": ""})
            holds[LIGHTS[i]] = OFF
            row(tt + 0.01, "on")
        holds.clear()
        row(t + hours * 3600, "off")
    row(t + hours * 3600 + 600, "off")
    paths = []
    for name, rows in (("status", status), ("lights", bulbs), ("commands", [])):
        p = Path(folder) / (name + ".jsonl")
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
        paths.append([p])
    return paths


def main():
    with tempfile.TemporaryDirectory(prefix="r0corr-") as tmp:
        record, _ = capture.capture(*export(tmp, [(2, 1), (4, 2), (6, 3)]))
    ng = record["planning"]["strata"]["no_game"]
    print("RESULT: per_session %s" % [(s["session_id"], s["occupied_h"], s["corrections"])
                                      for s in record["exposure"]["per_session"]])
    print("RESULT: phi_hat %s; people hours_per_arm %s at phi 1; hours_per_arm_at_phi_hat %s" % (
        record["exposure"]["phi_hat"], ng["people"]["hours_per_arm"], ng["hours_per_arm_at_phi_hat"]))
    bad = ng["hours_per_arm_at_phi_hat"] is not None and ng["hours_per_arm_at_phi_hat"] < ng["people"]["hours_per_arm"]
    print("%s: planning hours at phi_hat (%s) fall below the Poisson (phi 1) hours (%s)" % (
        "FAIL" if bad else "PASS", ng["hours_per_arm_at_phi_hat"], ng["people"]["hours_per_arm"]))


if __name__ == "__main__":
    main()
