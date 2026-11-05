#!/usr/bin/env python3
"""Verify finding evalrecord-fields-not-canon (unit N2, claude/den-n2).

What it checks, on the branch's own synthetic world (temp dirs only, the tests' fake check.py, no host contacted):
  1. Which canon EvalRecord field names (docs/architecture/live-v1/schemas.json, record eval_record) the
     held-out report written by tools/den-n2/score.py carries, and which it lacks.
  2. Whether `decision` should be there at all for N2 (canon: "N2 never promotes: it writes verdict instead").
  3. Whether the report's `variant` names the served model, and whether any field says where a prompt-2 was
     written (plan section 4.8 / 1(b) / 11 of docs/plans/n2-need-test/PLAN.md on claude/plan-n2).
  4. The shape of the report's demand metrics (coverage pooled or per band; interval level per metric).

Run:  python3 -B verify_evalrecord-fields-not-canon.py <root of the claude/den-n2 checkout> [schemas.json]
Default schemas.json: <checkout>/../live-records/docs/architecture/live-v1/schemas.json
Prints RESULT lines and one PASS/FAIL line per sub-claim.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
SCHEMAS = Path(sys.argv[2]) if len(sys.argv) > 2 else (
    ROOT.parent / "live-records" / "docs" / "architecture" / "live-v1" / "schemas.json")
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
import test_den_n2 as T  # noqa: E402

canon = next(r for r in json.loads(SCHEMAS.read_text(encoding="utf-8"))["records"] if r["id"] == "eval_record")
decision_meaning = next(f["meaning"] for f in canon["fields"] if f["name"] == "decision")

tmp = Path(tempfile.mkdtemp(prefix="n2-verify-er-"))
try:
    world = T.World(tmp, T.FOUR_DAYS)
    report, path = world.run("held_out")
    on_disk = json.loads(Path(path).read_text(encoding="utf-8"))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

assert on_disk == report or set(on_disk) == set(report)
print(f"RESULT schema tag: {report.get('schema')!r}; verdict={report.get('verdict')!r}")
print(f"RESULT top-level keys: {sorted(report)}")

def has(d, dotted):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True

canon_names = ["run_id", "versions", "gold_set", "gold_set.n1", "gold_set.n1.fixture", "gold_set.n1.labels",
               "gold_set.n_intervals", "gold_set.label_version", "gold_set.hash", "gold_set.refreshed",
               "gold_set.regions", "gold_set.R_eval", "gold_set.tau_R", "gold_set.tau_R_status",
               "per_link", "per_link.visual_demand", "per_link.visual_demand.ece",
               "per_link.visual_demand.fine_recall", "per_link.visual_demand.false_fine_rate",
               "per_link.visual_demand.coverage_per_band", "per_link.visual_demand.episodes_per_band",
               "per_link.engagement", "replay", "coverage", "intervals", "sample_gate", "scored_by",
               "decision", "verdict"]
absent = []
for name in canon_names:
    ok = has(report, name)
    if not ok:
        absent.append(name)
    print(f"RESULT canon {name:45s} {'present' if ok else 'ABSENT'}")
print(f"RESULT replay type: {type(report['replay']).__name__} (canon: object {{determinism, holds_pinned, pacing}})")
print(f"RESULT canon says of decision: {decision_meaning!r}")

# 2. decision should be absent for N2
print(("PASS" if "decision" not in report else "FAIL") +
      " (refutes part of the finding) decision is absent, and canon says N2 writes verdict instead of decision")

# 1. canon names the finding lists as absent
listed = ["run_id", "versions", "per_link", "intervals", "sample_gate", "scored_by"]
missing_listed = [n for n in listed if n not in report]
print(("FAIL" if missing_listed == listed else "PASS") +
      f" canon names listed by the finding are absent from the report: {missing_listed}")

# 3. variant: served model, and where prompt-2 was written
variant = report["variant"]
print(f"RESULT variant keys: {sorted(variant)}")
model_keys = [k for k in variant if "model" in k.lower()]
flat = json.dumps(report)
print(("FAIL" if not model_keys else "PASS") + f" variant names the served model (keys with 'model': {model_keys})")
origin = [k for k in ("prompt_origin", "prompt_written", "written_on", "prompt2_written") if k in flat]
print(f"RESULT variant.prompt = {variant['prompt']!r}; rows keys = {sorted(report['rows'])}")
print(("FAIL" if not origin else "PASS") + " some field says where a prompt-2 was written")

# 4. demand metric shapes
metrics = report["metrics"]
print(f"RESULT metrics keys: {sorted(metrics)}")
print(f"RESULT metrics['coverage'] = {json.dumps(metrics.get('coverage'), sort_keys=True)}")
print(f"RESULT keys of one metric: {sorted(metrics['fine_recall'])}")
print(f"RESULT sample_gates names: {[g['name'] for g in report['sample_gates']]}")
level_recorded = any("level" in k for m in metrics.values() for k in m) or "interval.level" in flat \
    or "interval_level" in flat
print(("FAIL" if not level_recorded else "PASS") + " the report records the interval level next to the intervals")
