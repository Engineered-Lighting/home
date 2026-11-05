#!/usr/bin/env python3
"""Verify (independent of the reporter's probe): the file score.py writes for a held-out run, read back from
disk, against the canon EvalRecord in the contracts (schemas.json, "Field names here are canon"), including
the canon's own N2 example ("only the blocks that differ are shown": run_id, gold_set.n1, verdict).

Synthetic only: the branch's test World (8 sessions, 4 held out, fake check.py) in a temp dir.

Run:  python3 -B verify_evalrecord-not-canon.py <root of the claude/den-n2 checkout> [<contracts dir>]
  The contracts dir defaults to the claude/live-records worktree next to the checkout
  (<checkout>/../live-records/docs/architecture/live-v1).
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
SPEC = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT.parent / "live-records" / "docs" / "architecture" / "live-v1"
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
import test_den_n2 as T  # noqa: E402

schemas = json.loads((SPEC / "schemas.json").read_text(encoding="utf-8"))
ev = next(r for r in schemas["records"] if r["name"] == "EvalRecord")
canon = []
for f in ev["fields"]:
    for part in f["name"].split("/"):
        part = part.strip()
        if "[" not in part and "." not in part:
            canon.append(part)
n2_example = next(x for x in ev["example"] if "verdict" in x)
print(f"RESULT canon top-level EvalRecord fields: {canon}")
print(f"RESULT canon N2 example keys: {sorted(k for k in n2_example if k != 'note')}")

tmp = Path(tempfile.mkdtemp(prefix="n2-verify-er-"))
try:
    world = T.World(tmp, T.FOUR_DAYS)
    report, path = world.run("held_out")
    disk = json.loads(Path(path).read_bytes())
    print(f"RESULT written file {Path(path).name}: schema={disk.get('schema')} verdict={disk.get('verdict')}")
    print(f"RESULT written top-level keys: {sorted(disk)}")
    missing = [k for k in canon if k not in disk]
    print(f"RESULT canon top-level fields absent from the written record: {missing}")
    print(f"RESULT canon N2-example keys absent: {[k for k in n2_example if k != 'note' and k not in disk]}")
    flat = json.dumps(disk)
    for name in ("run_id", "per_link", "visual_demand", "coverage_per_band", "episodes_per_band",
                 "false_fine_rate", "early_off_s_per_h", "band_cut_points", "scored_by", "interval_level",
                 "sample_gate\"", "determinism", "holds_pinned"):
        print(f"RESULT   '{name.strip(chr(34))}' appears anywhere in the record: {name in flat}")
    eo = disk["metrics"]["early_off"]
    print(f"RESULT metrics.early_off unit={eo['unit']!r} counted_per={eo['counted_per']!r} value={eo['value']}")
    print(f"RESULT replay is a {type(disk['replay']).__name__} of {len(disk['replay'])}; first keys: "
          f"{sorted(disk['replay'][0]) if disk['replay'] else None}")
    gs = disk["gold_set"]
    print(f"RESULT gold_set keys {sorted(gs)}; regions={gs.get('regions')!r} regions_status={gs.get('regions_status')!r}")
    print(f"RESULT sample_gates is a list of {len(disk['sample_gates'])} items, first: {disk['sample_gates'][0]}")
    bad = bool(missing)
    print(("FAIL" if bad else "PASS") + ": the written N2 record carries the canon EvalRecord field names")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
