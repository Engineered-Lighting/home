"""R0corr probe: exposure.json's `corrections` against schemas.json's Correction record (field names are canon).

What it checks
  Runs capture.capture() on a synthetic evening with an app touch, a switch touch, an automation hold and a hold
  with no bulb row, then compares each written correction with the Correction fields that rung 0 names ("each human
  action that opened or replaced a hold as one Correction (its changes[] and the hold it opened)") and rung 11 adds
  ("Corrections record changes[] and whose_light"):
    top level  correction_id, ts, source (enum wall switch | app | voice | scene button), ha_context{id, user_id,
               parent_id}, action{grouping, settle_s, reports_level_pct}, changes[], hold{entity_id, channels,
               pinned, since, lifetime, hold_set_revision, ended}, whose_light, attribution
    changes[]  {entity, channels, before{level_pct, cct_k, aim}, after{...}}
  and whether every written Correction is a human action, and EvalRecord.exposure's mode_stratum value against the
  schema example ('non-gaming').
  Field lists are copied from docs/architecture/live-v1/schemas.json; pass that file as an optional second argument
  to read them from it instead.

How to run (synthetic data, temp dirs only, no host contacted):
    python3 -B probe_correction_record_schema.py /path/to/branch/checkout [/path/to/schemas.json]
"""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "correction-capture"))
import capture  # noqa: E402

TOP = ["correction_id", "ts", "source", "ha_context", "action", "changes", "hold", "whose_light", "attribution"]
SOURCE_ENUM = {"wall switch", "app", "voice", "scene button"}
ACTION = ["grouping", "settle_s", "reports_level_pct"]
CHANGE = ["entity", "channels", "before", "after"]
LEVEL = ["level_pct", "cct_k", "aim"]
HOLD = ["entity_id", "channels", "pinned", "since", "lifetime", "hold_set_revision", "ended"]

if len(sys.argv) > 2:
    spec = {r["name"]: r for r in json.loads(Path(sys.argv[2]).read_text("utf-8"))["records"]}
    names = [f["name"] for f in spec["Correction"]["fields"]]
    print("RESULT: schemas.json Correction fields: %s" % names)
    assert "changes" in names and "hold" in names and "whose_light" in names

L0, L1, L2, L3 = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
WARM = {"on": True, "brightness": 100, "kelvin": 2700}
OFF = {"on": False, "brightness": None, "kelvin": None}
T0 = datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc).timestamp()


def export(folder):
    status, bulbs, rev, holds = [], [], [10], {}

    def row(t):
        rev[0] += 1
        attrs = {"automatic": True, "gaming_session": False, "occupancy": "on", "revision": rev[0],
                 "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in holds.items()},
                 "provisional_fixtures": [], "occupancy_source": "raw_person_count", "pending_fixtures": []}
        status.append({"last_updated_ts": t, "entity_id": "sensor.den_lighting_control", "state": "occupancy",
                       "attributes": attrs})

    def bulb(t, e, on, n, user=False, parent=False):
        bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off",
                      "context_id": "%032X" % (0x0196 << 100 | n), "context_user_id": "0C" * 16 if user else "",
                      "context_parent_id": "AD" * 16 if parent else ""})

    row(T0)
    holds[L1] = WARM; row(T0 + 200); bulb(T0 + 200.3, L1, True, 1, user=True)          # app
    bulb(T0 + 300, L2, False, 2); holds[L2] = OFF; row(T0 + 300.01)                     # switch
    holds[L0] = OFF; row(T0 + 500); bulb(T0 + 500.2, L0, False, 3, parent=True)         # automation
    holds[L3] = WARM; row(T0 + 600)                                                     # no bulb row
    row(T0 + 3600)
    paths = []
    for name, rows in (("status", status), ("lights", bulbs), ("commands", [])):
        p = Path(folder) / (name + ".jsonl")
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
        paths.append([p])
    return paths


def main():
    with tempfile.TemporaryDirectory(prefix="r0corr-") as tmp:
        record, _ = capture.capture(*export(tmp))
    problems = []
    for c in record["corrections"]:
        missing = [k for k in TOP if k not in c]
        if missing:
            problems.append("%s lacks %s" % (c["correction_id"], missing))
        if c.get("source") not in SOURCE_ENUM:
            problems.append("%s source %r not in %s" % (c["correction_id"], c.get("source"), sorted(SOURCE_ENUM)))
        a_missing = [k for k in ACTION if k not in c.get("action", {})]
        if a_missing:
            problems.append("%s action lacks %s" % (c["correction_id"], a_missing))
        for ch in c.get("changes", []):
            ch_missing = [k for k in CHANGE if k not in ch]
            lv_missing = [k for k in LEVEL if not isinstance(ch.get("after"), dict) or k not in ch["after"]]
            if ch_missing or lv_missing:
                problems.append("%s changes[] lacks %s, after lacks %s (has %s / %s)" % (
                    c["correction_id"], ch_missing, lv_missing, sorted(ch), sorted(ch.get("after") or {})))
    humans = sum(c["source"] in capture.PEOPLE for c in record["corrections"])
    print("RESULT: %d corrections written, %d of them human actions; exposure corrections_total %d" % (
        len(record["corrections"]), humans, record["exposure"]["corrections_total"]))
    for p in problems:
        print("FAIL: %s" % p)
    print("%s: every written Correction is a human action" % ("PASS" if humans == len(record["corrections"]) else "FAIL"))
    strata = sorted({s["mode_stratum"] for s in record["exposure"]["per_session"]})
    print("%s: mode_stratum values %s vs schema example 'non-gaming'" % (
        "PASS" if "non-gaming" in strata else "FAIL", strata))
    print("RESULT: %d schema problems" % len(problems))


if __name__ == "__main__":
    main()
