"""probe_schema_decision_correction: den-core's Decision and Correction records against schemas.json (canon).

What it checks
- Runs the synthetic evening (need, incumbent, decide) in memory and collects every Decision record.
- Compares their field names and enum values with the canon Decision in
  docs/architecture/live-v1/schemas.json (read from the spec checkout given as the second argument):
  top-level fields, candidates[] keys (canon {scene, cost, why}), status enum, trigger enum,
  inputs.hold_set.held[] keys, inputs.actuator_state keys, applied_scene presence.
- Builds a canon-shaped Correction (one human action, `changes[]`, as in the schemas.json example, aimed at a den
  bulb) and hands it to den-core's events.validate as a `correction` input.

How to run (synthetic data only; no network):
    python3 -B probe_schema_decision_correction.py /path/to/den-core-checkout /path/to/live-records-checkout
"""
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
SPEC = Path(sys.argv[2]).resolve() / "docs" / "architecture" / "live-v1" / "schemas.json"
sys.path.insert(0, str(ROOT / "tools" / "den-core"))
import events  # noqa: E402
import runner  # noqa: E402
import synthetic  # noqa: E402

records = {r["name"]: r for r in json.loads(SPEC.read_text("utf-8"))["records"]}
canon = records["Decision"]
fields = {f["name"]: f for f in canon["fields"]}
canon_top = set()
for name in fields:
    if "." not in name:
        canon_top |= {part.strip() for part in name.split("/")}
trigger_enum = {s.split("(")[0].strip() for s in fields["trigger"]["meaning"].split("|")}
status_enum = {s.strip() for s in fields["status"]["type"].split("|")}
cand_keys = {"scene", "cost", "why"}  # candidates: [{scene, cost, why}]
held_keys = {"entity_id", "channels", "pinned", "since", "cause", "lifetime"}
actuator_keys = {"on", "level", "v_hat", "cct_k", "aim", "motion_status", "source", "as_of"}

items, _, _ = synthetic.evening(touch=True, game=True)
header, log = runner.run(runner.tagged(items, "synthetic"), runner.load_policies(["need", "incumbent", "decide"]),
                         synthetic=True)
decisions = [e for e in log if e["kind"] == "decision"]
problems = []


def note(text):
    problems.append(text)
    print("FAIL: " + text)


seen = {"top": set(), "cand": set(), "status": set(), "trigger": set(), "held": set(), "actuator": set()}
for e in decisions:
    d = e["data"]
    seen["top"] |= set(d)
    seen["status"].add(d["status"])
    seen["trigger"].add(d["trigger"])
    for c in d["candidates"]:
        seen["cand"] |= set(c)
    for h in d["inputs"]["hold_set"]["held"]:
        seen["held"] |= set(h)
    for v in d["inputs"]["actuator_state"].values():
        if v:
            seen["actuator"] |= set(v)
print("RESULT: %d decisions (incumbent %d, decide %d)" % (
    len(decisions), sum(e["source"] == "incumbent" for e in decisions), sum(e["source"] == "decide" for e in decisions)))
print("RESULT: canon top-level fields missing from every decision: %s" % sorted(
    {"applied_scene", "candidates", "status", "skipped", "requested_scene"} - seen["top"]))
if "applied_scene" not in seen["top"]:
    note("no Decision carries applied_scene (canon: per channel {status, readback, aim_status, aim_source, steps[]})")
if seen["cand"] and not cand_keys <= seen["cand"]:
    note("candidates[] keys %s; canon is {scene, cost, why} (missing %s)" % (sorted(seen["cand"]),
                                                                           sorted(cand_keys - seen["cand"])))
if not seen["status"] <= status_enum:
    note("status values %s are outside canon %s" % (sorted(seen["status"]), sorted(status_enum)))
if not seen["trigger"] <= trigger_enum:
    note("trigger values %s are outside canon %s" % (sorted(seen["trigger"] - trigger_enum), sorted(trigger_enum)))
if seen["held"] and not held_keys <= seen["held"]:
    note("inputs.hold_set.held[] keys %s; canon also has %s" % (sorted(seen["held"]), sorted(held_keys - seen["held"])))
if seen["actuator"] and not {"as_of", "source"} <= seen["actuator"]:
    note("inputs.actuator_state keys %s; canon per channel has %s" % (sorted(seen["actuator"]), sorted(actuator_keys)))

# A canon Correction: one human action, changes[] (here a scene button that set two den bulbs).
ex = records["Correction"]["example"]
canon_correction = {
    "correction_id": "c-probe-1", "ts": 1793765045.0, "source": "scene button",
    "ha_context": {"id": "01EXAMPLE", "user_id": None, "parent_id": None},
    "action": {"grouping": "one physical control within the settle window", "settle_s": 3, "reports_level_pct": [50]},
    "changes": [{"entity": "light.den_rear_left", "channels": ["bulb"], "before": {"level_pct": 70},
                 "after": {"level_pct": 50}},
                {"entity": "light.den_rear_right", "channels": ["bulb"], "before": {"level_pct": 70},
                 "after": {"level_pct": 50}}],
    "hold": {**ex["hold"], "entity_id": "light.den_rear_left"},
}
event = {"schema": events.SCHEMA, "t": 1793765045.0, "seq": 0, "kind": "correction", "source": "synthetic",
         "data": canon_correction}
try:
    events.validate(event)
    print("PASS: a canon Correction is accepted as a den-core correction")
except ValueError as error:
    note("a canon Correction (changes[], one per human action) is refused by events.validate: %s" % error)
den_corr = [e["data"] for e in log if e["kind"] == "correction"]
print("RESULT: den-core correction fields: %s; canon Correction fields: %s" % (
    sorted(den_corr[0]) if den_corr else None, [f["name"] for f in records["Correction"]["fields"]]))
print("RESULT: %s" % ("FAIL (%d)" % len(problems) if problems else "PASS"))
sys.exit(1 if problems else 0)
