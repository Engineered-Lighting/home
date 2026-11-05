"""probe_correction_fields_vs_schema: the Correction records capture.py writes (exposure.json "corrections") against
the canon field names in schemas.json's Correction record (rung 0's `correction` output; rung 11 says "Corrections
record changes[] and whose_light"). Checks top-level fields, the source enum, action keys and changes[] keys.

Synthetic data only, temp dirs only, never contacts a host.
Run:  python3 probe_correction_fields_vs_schema.py <root of the claude/correction-capture checkout> [schemas.json]
(the default schemas.json is the pinned live-records worktree next to the branch checkout)
"""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
SCHEMAS = Path(sys.argv[2]) if len(sys.argv) > 2 else (
    ROOT.parent / "live-records" / "docs" / "architecture" / "live-v1" / "schemas.json")
sys.path.insert(0, str(ROOT / "tools" / "correction-capture"))
import capture  # noqa: E402
import events  # noqa: E402

L0, L1, L2, L3 = events.LIGHTS
STATUS = "sensor.den_lighting_control"
T0 = datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc).timestamp()
OFF = {"on": False, "brightness": None, "kelvin": None}
WARM = {"on": True, "brightness": 100, "kelvin": 2700}


def srow(t, rev, holds):
    attrs = {"automatic": True, "gaming_enabled": True, "gaming_session": False, "gaming_signal": "idle",
             "occupancy": "on", "revision": rev, "error": None, "gaming_entity": "sensor.pc_gaming",
             "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in holds.items()},
             "occupancy_source": "raw_person_count", "pending_fixtures": [], "provisional_fixtures": [],
             "friendly_name": "Den lighting control"}
    return {"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs}


status = [srow(T0, 1, {}), srow(T0 + 100.01, 2, {L0: OFF}), srow(T0 + 200, 3, {L0: OFF, L1: WARM}),
          srow(T0 + 3600, 4, {})]
bulbs = [{"last_updated_ts": T0 + 100, "entity_id": L0, "state": "off", "context_id": "01" * 16,
          "context_user_id": "", "context_parent_id": ""},
         {"last_updated_ts": T0 + 200.2, "entity_id": L1, "state": "on", "context_id": "02" * 16,
          "context_user_id": "0F" * 16, "context_parent_id": ""}]
with tempfile.TemporaryDirectory() as tmp:
    paths = []
    for name, rows in (("s", status), ("b", bulbs), ("c", [])):
        p = Path(tmp) / (name + ".jsonl")
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
        paths.append([p])
    record, _ = capture.capture(*paths)

schema = next(r for r in json.loads(SCHEMAS.read_text("utf-8"))["records"] if r["name"] == "Correction")
canon = set()
for f in schema["fields"]:
    canon.update(part.strip() for part in f["name"].split("/"))
optional = {"override_contested"}  # present only under S1/S2
enum = {"wall switch", "app", "voice", "scene button"}
example = schema["example"]
bad = 0
for c in record["corrections"]:
    keys = set(c)
    missing = sorted(canon - keys - optional)
    print("RESULT correction %s source=%r keys=%s" % (c["correction_id"], c["source"], sorted(keys)))
    print("RESULT   canon fields missing: %s" % missing)
    print("RESULT   fields not in canon: %s" % sorted(keys - canon))
    if c["source"] not in enum:
        bad += 1
        print("FAIL   source %r is not in the canon enum %s" % (c["source"], sorted(enum)))
    if "reports_level_pct" not in c["action"]:
        bad += 1
        print("FAIL   action has %s, canon {grouping, settle_s, reports_level_pct}" % sorted(c["action"]))
    ch = c["changes"][0]
    if "entity" not in ch or "level_pct" not in json.dumps(ch):
        bad += 1
        print("FAIL   changes[0] keys %s, before %s; canon {entity, channels, before {level_pct, cct_k, aim}, "
              "after}" % (sorted(ch), ch["before"]))
    for need in ("whose_light", "hold"):
        if need not in c:
            bad += 1
            print("FAIL   no top-level %r (canon: %s)" % (need, json.dumps(example.get(need))[:90]))
print("RESULT exposure.json is one indented JSON document (schema %s), not JSON Lines of versioned Correction "
      "records" % record["schema"])
print("RESULT %d mismatch(es)" % bad)
