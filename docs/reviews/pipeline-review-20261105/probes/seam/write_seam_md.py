#!/usr/bin/env python3
"""Assemble SEAM.md from a harness run folder, the narrative, the shas and moved.sh's output."""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SP = HERE.parents[1]
out = Path(sys.argv[1]).resolve()          # round 2 run folder
out1 = Path(sys.argv[2]).resolve()         # round 1 run folder
moved = subprocess.run(["sh", str(HERE / "moved.sh")], capture_output=True, text=True).stdout.strip()
moved2 = subprocess.run(["sh", str(HERE / "moved.sh"), str(SP / "seamwt2" / "SHAS_ROUND2.txt")],
                        capture_output=True, text=True).stdout.strip()
shas = (SP / "seamwt" / "SHAS_START.txt").read_text().strip()
shas2 = (SP / "seamwt2" / "SHAS_ROUND2.txt").read_text().strip()
table = (out / "seam-table.md").read_text(encoding="utf-8").strip()
table1 = (out1 / "seam-table.md").read_text(encoding="utf-8").strip()
narrative = (HERE / "SEAM-narrative.md").read_text(encoding="utf-8").strip()
findings = json.loads((SP.parent / "findings" / "seam.json").read_text(encoding="utf-8"))
counts = {}
for f in findings:
    counts[f["severity"]] = counts.get(f["severity"], 0) + 1
parts = [
    "# SEAM: one synthetic evening through the real live-v1 tools",
    "",
    f"Run folders: round 2 `{out}`, round 1 `{out1}` (synthetic data only, temp dirs only, no network beyond "
    "127.0.0.1).",
    f"Harness: `{HERE / 'run_seam.py'}` (`python3 run_seam.py --wt-root <checkouts> [--out <dir>]`), validator "
    f"`{HERE / 'schema_validator.py'}` (improved: keys a field's meaning names in backticks, such as `n_zones`, and "
    "sub-keys the spec's own example uses are accepted; all 17 records[].example now pass, two failed before), "
    f"findings `{SP.parent / 'findings' / 'seam.json'}` "
    f"({len(findings)} findings: " + ", ".join(f"{k} {counts.get(k, 0)}" for k in ("critical", "high", "medium", "low")) + ").",
    "",
    "## Summary",
    "",
    "Round 2 statuses: " + ", ".join(f"{k} {v}" for k, v in sorted(
        __import__("collections").Counter(l.split(" | ")[2] for l in table.splitlines()[2:]).items())) + ".",
    "The real chain now runs from the fake camera through take, check, registration, detector rows, observe "
    "(only with den-observe's own camera entry: den-register's is refused), den-track, den-bundle, the fake "
    "interpreter and need/decide. It breaks at: register -> observe (SEAM-01, SEAM-02); bundle -> need, where "
    "two people collapse into one room-level PersonState and B's reading goes unserved while A works (SEAM-04, "
    "critical); Stage 1's light-model.json -> den-core decide and -> den-verify (SEAM-17, SEAM-18); den-core's "
    "log.jsonl -> den-verify (SEAM-13). Labels -> N2 refuses correctly (no staged take, so N1 froze nothing).",
    "",
    "## Seam table, round 2 (every branch at its newest head; seamwt2)",
    "",
    "Status: OK = consumer read the producer's real output; REFUSED = consumer refused it; MISREAD = read but "
    "meaning lost or changed; SCHEMA = record differs from schemas.json; MISSING-TOOL = no tool to run. "
    "FALLBACK / FIXTURE-FED rows are where an upstream refused or was missing and the harness kept going from the "
    "closest real output, as labelled.",
    "",
    table,
    "",
    "## Seam table, round 1 (the heads at 05:27 UTC; seamwt)",
    "",
    table1,
    "",
    "## Commits used",
    "",
    "Round 1 (detached worktrees under scratchpad/seamwt, made at 05:27 UTC):",
    "",
    "```",
    shas,
    "```",
    "",
    "Round 2 (scratchpad/seamwt2; NEW = a new worktree at the moved head, same = round 1's checkout):",
    "",
    "```",
    shas2,
    "```",
    "",
    "## Branches that moved",
    "",
    "Since round 1's worktrees (fetched again at the end):",
    "",
    "```",
    moved,
    "```",
    "",
    "Since round 2's worktrees (fetched again at the end):",
    "",
    "```",
    moved2,
    "```",
    "",
    narrative,
    "",
    "## Problems, with the exact command and the observed output",
    "",
]
for f in findings:
    parts += [f"### {f['id']} ({f['severity']}, {f['category']}): {f['title']}",
              "",
              f"- Where: `{f['branch']}` @ `{f['commit'][:8]}`, `{f['file']}:{f['line']}`",
              f"- What breaks: {f['failure_scenario']}",
              f"- Reproduce: `{f['reproduction']}`",
              f"- Status: {f['claimed_status']}. Fix: {f['suggested_fix']}",
              ""]
(HERE / "SEAM.md").write_text("\n".join(parts) + "\n", encoding="utf-8")
print("wrote", HERE / "SEAM.md")
