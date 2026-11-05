"""probe_evalrecord-canon: the EvalRecord N2's plan writes (section 4.8, table 4.4) against the canon
EvalRecord in schemas.json, whose field names are canon (live-v1 README).

What it checks
  - canon fields the N2 plan's metrics map to: per_link.engagement {onset_delay_s_median,
    early_off_s_per_h, f1}, per_link.visual_demand {ece, fine_recall, false_fine_rate,
    coverage_per_band, episodes_per_band}, sample_gate (passed | below gate), replay {determinism,
    holds_pinned, pacing}, decision, versions.band_cut_points;
  - the names and units the plan uses instead: `metrics`, `sample_gates`, `promoted`, early_off as
    a fraction of engaged seconds, one pooled coverage, `replay` holding log hashes, pacing in `variant`.
Run
  python3 probe_evalrecord-canon.py <root of claude/plan-n2> [<root of live-records checkout>]
"""
import json
import re
import sys
from pathlib import Path


def main(root, records_root):
    plan = re.sub(r"\s+", " ", (Path(root) / "docs/plans/n2-need-test/PLAN.md").read_text(encoding="utf-8"))
    schema = json.loads((Path(records_root) / "docs/architecture/live-v1/schemas.json").read_text(encoding="utf-8"))
    ev = next(r for r in schema["records"] if r["name"] == "EvalRecord")
    fields = {f["name"]: f["type"] for f in ev["fields"]}
    ex = ev["example"][0]
    print(f"RESULT canon engagement keys {sorted(ex['per_link']['engagement'])}; demand keys "
          f"{sorted(ex['per_link']['visual_demand'])}")
    print(f"RESULT canon sample_gate: {fields['sample_gate']!r}; replay: {fields['replay']!r}; "
          f"decision: {fields['decision']!r}")
    print(f"RESULT canon intervals example unit for early-off: {ex['intervals'][0]['metric']} in {ex['intervals'][0]['unit']}")
    problems = []
    if "`metrics` (unit, counted-per" in plan and "per_link" not in plan:
        problems.append("plan writes `metrics`; canon per_link / intervals are never named")
    if "`sample_gates`" in plan and "sample_gates" not in fields:
        problems.append("plan writes `sample_gates` (met/unmet); canon is `sample_gate` (passed | below gate)")
    if "`promoted: false`" in plan and "promoted" not in fields:
        problems.append("plan writes `promoted: false`; canon is `decision` (promote | hold | roll back)")
    if "| early_off | fraction of scored gold-engaged seconds" in plan and "early_off_s_per_h" in ex["per_link"]["engagement"]:
        problems.append("plan's early_off is a fraction of engaged seconds; canon early_off_s_per_h is s/h")
    if "averaged over interval units" in plan and "coverage_per_band" in ex["per_link"]["visual_demand"]:
        problems.append("plan has one pooled coverage over interval units; canon coverage_per_band has fine/casual/none")
    if "`replay` (each log's sha256 and `check.py` result)" in plan and "pacing" in fields["replay"]:
        problems.append("plan's `replay` holds log hashes; canon replay is {determinism, holds_pinned, pacing}, "
                        "and the plan puts pacing_s in `variant`")
    if "band_cut_points" not in plan:
        problems.append("plan never writes versions.band_cut_points although the owner set them")
    for p in problems:
        print("FAIL: " + p)
    if not problems:
        print("PASS: the plan's EvalRecord uses canon names and units")


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    rec = sys.argv[2] if len(sys.argv) > 2 else str(Path(root).resolve().parent / "live-records")
    main(root, rec)
