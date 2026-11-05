"""R0corr probe: cph-budget-v0 says within_budget while the same exposure.json's own planning value needs more than
the 300 h budget per arm.

What it checks
  Eight 5-hour no-game evenings (40 occupied hours) with 40 switch corrections bunched into four evenings
  (0, 0, 10, 0, 12, 0, 8, 10): an over-dispersed incumbent. The ladder (rung 11) plans with phi, "about 300 h per arm
  at phi = 2", and says rung 0's log gives the planning value of phi. The rule's verdict uses phi = 1 and a Poisson
  (Garwood) interval, so it reports within_budget; the record's own hours_per_arm_at_phi_hat and phi_hat show the
  budget is exceeded. The printed summary shows the verdict and phi_hat but not the hours at phi_hat.

How to run (synthetic data, temp dirs only, no host contacted):
    python3 -B probe_within_budget_ignores_phi_hat.py /path/to/branch/checkout
"""
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "correction-capture"))
import capture  # noqa: E402

LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
OFF = {"on": False, "brightness": None, "kelvin": None}
ON = {"on": True, "brightness": 120, "kelvin": 2700}


def export(folder, counts):
    status, bulbs, rev, holds, n = [], [], [100], {}, [0]

    def row(t, occ):
        rev[0] += 1
        attrs = {"automatic": True, "gaming_session": False, "occupancy": occ, "revision": rev[0],
                 "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in holds.items()},
                 "provisional_fixtures": [], "occupancy_source": "raw_person_count", "pending_fixtures": []}
        status.append({"last_updated_ts": t, "entity_id": "sensor.den_lighting_control", "state": "occupancy",
                       "attributes": attrs})

    for day, k in enumerate(counts):
        t = datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc).timestamp() + day * 86400
        row(t - 600, "off")
        row(t, "on")
        for i in range(k):
            e = LIGHTS[i % 4]
            scene = OFF if (i // 4) % 2 == 0 else ON  # each touch changes that bulb's held target
            tt = t + 900 + i * 1200
            n[0] += 1
            bulbs.append({"last_updated_ts": tt, "entity_id": e, "state": "on" if scene["on"] else "off",
                          "context_id": "%032X" % (0x0197 << 100 | n[0]), "context_user_id": "",
                          "context_parent_id": ""})
            holds[e] = scene
            row(tt + 0.01, "on")
        holds.clear()
        row(t + 5 * 3600, "off")
    row(t + 5 * 3600 + 600, "off")
    paths = []
    for name, rows in (("status", status), ("lights", bulbs), ("commands", [])):
        p = Path(folder) / (name + ".jsonl")
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
        paths.append([p])
    return paths


def main():
    counts = [0, 0, 10, 0, 12, 0, 8, 10]
    with tempfile.TemporaryDirectory(prefix="r0corr-") as tmp:
        record, _ = capture.capture(*export(tmp, counts))
    ng = record["planning"]["strata"]["no_game"]
    phi = record["exposure"]["phi_hat"]
    print("RESULT: no_game %s h, %d people, rate %s/h, Garwood %s, hours/arm at phi 1 %s, verdict %s" % (
        ng["occupied_h"], ng["people"]["count"], ng["people"]["per_occupied_h"], ng["people"]["interval"],
        ng["people"]["hours_per_arm"], ng["verdict"]))
    print("RESULT: phi_hat %s; hours_per_arm_at_phi_hat %s; budget %s h per arm" % (
        phi, ng["hours_per_arm_at_phi_hat"], 300))
    low_rate = ng["people"]["interval"][0]
    print("RESULT: at phi_hat, hours per arm at people's low end = 148 * %s / %s = %.0f" % (phi, low_rate,
                                                                                           148 * phi / low_rate))
    bad = ng["verdict"] == "within_budget" and ng["people"]["count"] == sum(counts) and ng["hours_per_arm_at_phi_hat"] > 300
    print("%s: verdict within_budget while the record's own planning value needs %s h per arm" % (
        "FAIL" if bad else "PASS", ng["hours_per_arm_at_phi_hat"]))
    with redirect_stdout(io.StringIO()) as out:
        print(capture.summary(record))
    shown = "hours_per_arm_at_phi_hat" in out.getvalue() or str(ng["hours_per_arm_at_phi_hat"]) in out.getvalue()
    print("%s: the owner's printed summary shows the hours at phi_hat" % ("PASS" if shown else "FAIL"))


if __name__ == "__main__":
    main()
