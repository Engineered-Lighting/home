"""probe_within_budget_despite_phi_hat: cph-budget-v0 keeps phi = 1 and Garwood (pure Poisson) intervals, so with
overdispersed evenings (some quiet, some with many touches, which the ladder expects: about 300 h per arm at phi = 2)
capture.py prints `within_budget` while its own fitted phi_hat puts the hours per arm far over the 300 h budget.

Synthetic data only, temp dirs only, never contacts a host.
Run:  python3 probe_within_budget_despite_phi_hat.py <root of the claude/correction-capture checkout>
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

STATUS = "sensor.den_lighting_control"
OFF = {"on": False, "brightness": None, "kelvin": None}
T0 = datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc).timestamp()
status, bulbs, rev, n = [], [], 100, 0


def srow(t, occ, holds):
    global rev
    rev += 1
    attrs = {"automatic": True, "gaming_enabled": True, "gaming_session": False, "gaming_signal": "idle",
             "occupancy": occ, "revision": rev, "error": None, "gaming_entity": "sensor.pc_gaming",
             "holds": {e: {"until": "room_vacant_2_minutes", "target": OFF} for e in holds},
             "occupancy_source": "raw_person_count", "pending_fixtures": [], "provisional_fixtures": [],
             "friendly_name": "Den lighting control"}
    status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs})


counts = [0, 6] * 10  # 20 evenings of 3 occupied hours: quiet ones and busy ones, 60 switch touches in 60 h
for day, count in enumerate(counts):
    start = T0 + day * 86400
    srow(start, "on", [])
    for k in range(count):
        n += 1
        t = start + (k + 1) * 3 * 3600 / (count + 1)
        e = events.LIGHTS[k % 4]
        bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "off",
                      "context_id": "%032X" % (0x0190000000000000000000000000 + n), "context_user_id": "",
                      "context_parent_id": ""})
        srow(t + 0.01, "on", [e])
        srow(t + 60, "on", [])
    srow(start + 3 * 3600, "off", [])
with tempfile.TemporaryDirectory() as tmp:
    paths = []
    for name, rows in (("s", status), ("b", bulbs), ("c", [])):
        p = Path(tmp) / (name + ".jsonl")
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
        paths.append([p])
    record, _ = capture.capture(*paths)
p = record["planning"]["strata"]["no_game"]
phi = record["exposure"]["phi_hat"]
print("RESULT occupied_h=%s people=%s per_h=%s interval=%s hours_per_arm=%s" % (
    p["occupied_h"], p["people"]["count"], p["people"]["per_occupied_h"], p["people"]["interval"],
    p["people"]["hours_per_arm"]))
print("RESULT phi_hat=%s hours_per_arm_at_phi_hat=%s verdict=%s" % (phi, p["hours_per_arm_at_phi_hat"],
                                                                      p["verdict"]))
low_h_at_phi = 148 * phi / p["people"]["interval"][0]
print("RESULT hours per arm at people's lower end and phi_hat: %.0f (budget 300)" % low_h_at_phi)
if p["verdict"] == "within_budget" and p["hours_per_arm_at_phi_hat"] > 300:
    print("FAIL verdict within_budget although the tool's own phi_hat %.2f needs %s h per arm at the point rate "
          "(and %.0f h at the lower end), over the 300 h budget" % (phi, p["hours_per_arm_at_phi_hat"], low_h_at_phi))
else:
    print("PASS verdict agrees with phi_hat")
