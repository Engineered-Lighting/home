#!/usr/bin/env python3
"""Probe: the record score.py writes (schema "den-n2-eval-1") against the canon EvalRecord in
docs/architecture/live-v1/schemas.json ("field names here are canon"; the ladder says N2's verdict is
"written to an EvalRecord").

What it checks: runs a held-out scoring on the branch's own synthetic world (temp dirs, fake check.py),
then lists canon EvalRecord fields that a consumer reading by canon names would not find, and the fields
the report renames or reshapes (sample_gate vs sample_gates, per_link.visual_demand / engagement vs
metrics, replay's canon shape, versions.band_cut_points, interval_level).

Run:  python3 -B probe_evalrecord-not-canon.py <root of the claude/den-n2 checkout> [path to schemas.json]
The default schemas.json is the live-records worktree next to the checkout (../live-records/...).
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
names = []
for f in canon["fields"]:
    for part in f["name"].split(" / "):
        if "[" not in part:
            names.append(part.strip())
tmp = Path(tempfile.mkdtemp(prefix="n2-probe-er-"))
try:
    world = T.World(tmp, T.FOUR_DAYS)
    report, _ = world.run("held_out")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
relevant = ["run_id", "versions", "gold_set", "per_link", "replay", "coverage", "intervals", "sample_gate",
            "scored_by", "verdict"]
missing = [n for n in relevant if n not in report]
print(f"RESULT canon EvalRecord fields: {names}")
print(f"RESULT report top-level keys: {sorted(report)}")
print(f"RESULT canon fields N2 should carry but the report lacks: {missing}")
print(f"RESULT report 'replay' is a {type(report['replay']).__name__} of per-log entries; canon replay is "
      f"{{determinism, holds_pinned, pacing}}")
print(f"RESULT early_off: report unit={report['metrics']['early_off']['unit']!r}; canon per_link.engagement "
      f"reports early-off seconds per hour (example key early_off_s_per_h)")
print(f"RESULT gold_set.regions={report['gold_set']['regions']!r} (canon: {{marked, uncertain, missing}})")
print(("FAIL" if missing else "PASS") + " the N2 EvalRecord carries the canon field names")
