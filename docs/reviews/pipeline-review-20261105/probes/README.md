# Probes for the 2026-11-05 pipeline review

Every probe here was written for this review. Each one uses synthetic data only, writes only to temporary folders, and contacts nothing beyond 127.0.0.1. None of them contains real frames, takes, labels or logs.

## Running a probe

Most probes take the root of a clean checkout of the branch they test as their first argument. Make that checkout from this repository, for example:

```bash
git worktree add --detach ../wt/rung-10-verify origin/claude/rung-10-verify
```

```bash
python3 -B probes/R10/probe_held-exclusion-hides-free-misses.py ../wt/rung-10-verify
```

Each probe prints `RESULT` lines and ends with PASS, FAIL or REPRODUCED; its docstring says what it checks. Probes named `verify_*`, `tiebreak_*` or `*_intent*` were written by the independent verifiers. `mutate_*.py` files are mutation runners: each applies every mutant to a temporary copy, runs the tool's suite and prints a caught/survived table. Logs and outputs (`*.out`, `*.txt`) are kept where they were small. Absolute scratch paths in them read `$SCRATCH`. The camera's LAN address reads `<camera-lan-address>`.

## Folders

| Folder | What it covers | Branch(es) and commit(s) |
|---|---|---|
| `R/` | rung R, den-take and the staged scripts | rung-r 8b1c945c, den-take-script 23cdde50 |
| `N1/` | N1 plan and den-label | plan-n1 b1f1a9d2, den-label a28130a4 |
| `N2plan/`, `N2/` | N2 plan and den-n2 | plan-n2 764b213e, den-n2 a39f02f5 |
| `R0core/`, `R0corr/` | rung 0: den-core, correction-capture | den-core 022aa9d5, correction-capture 37e47b20 |
| `R9/` | rungs 0 and 9: den-core-live | den-core-live 64bd23ca |
| `R1/`, `R2/`, `R3/`, `R5/` | rungs 1, 2, 3, 5 | ce55b0a9, ab491a88, 49ff481d, cf659ba8 |
| `R7/`, `R7d/` | rung 7, and the delta to the final head | rung-7-photometry 1931c831 and 9a5023e3 |
| `R8s0/`, `R8s1/` | rung 8 plan, Stage 0, Stage 1 | plan-rung-8 7949678a, rung-8-stage0 58aa1742, rung-8-stage1 028981bc |
| `R10/` | rung 10, den-verify | rung-10-verify b8ac320e |
| `pipeline/` | rates, latency and pacing against perception-lab on main | main bd57ecc6 |
| `seam/` | the end-to-end harness and the schema validator | all of the above |
| `merge/` | merge rehearsal, hybrid-file check, all suites in one tree | all of the above |

## The seam harness

```bash
python3 probes/seam/run_seam.py --wt-root <folder with one checkout per branch> --out <empty temp folder>
```

The checkouts must be named `rung-r`, `rung-1-register`, `rung-2-observe`, `rung-3-track`, `rung-5-bundle`, `den-core`, `den-core-live`, `rung-8-stage0`, `rung-8-stage1`, `rung-7-photometry`, `rung-10-verify`, `den-label` and `den-n2`. The harness prints one row per link: OK, REFUSED, MISREAD, SCHEMA or MISSING-TOOL. `seam/SEAM.md` holds the run this review cites.

The schema validator checks JSON Lines records against the canon field names, and passes on all 17 of the spec's own examples:

```bash
python3 probes/seam/schema_validator.py docs/architecture/live-v1/schemas.json observation observations.jsonl
```

## The merge rehearsal

```bash
bash probes/merge/rehearse_merge.sh <repo> <empty scratch dir> origin/claude/live-records origin/claude/rung-r origin/claude/den-take-script
```

The command above shows only the first branches: list every branch in the order given in `RESULTS.md`. The script then merges them all into a detached scratch worktree, records conflicts, and never pushes. `run_all_suites.sh <tree> [python]` then runs every tool's suite in that tree. `hybrid_check.sh` lists any merged file that matches no branch's version.

`RESULTS.md` records what was run, the commits, and the counts.
