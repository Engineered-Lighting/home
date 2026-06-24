#!/usr/bin/env python3
"""Crash-recovery for the production QA pass (AR31-9).

The orchestrator writes a checkpoint before+after each stage. If
the orchestrator dies mid-cycle (OOM, KeyboardInterrupt, computer-
use timeout, OS signal, power loss), the checkpoint marks the cycle
as "stages_in_progress: [...]" with cleanup_done: false. This
script:

    1. Scans tools/reports/ for orphaned cycles
    2. For each: invokes qa-state-guard.py to restore mute/sim/etc
    3. Writes a partial REPORT.md noting "crashed during stage N"
    4. Marks the checkpoint as cleanup_done

Usage:
    py -3 tools/qa-recovery.py             # auto-detect + clean all orphans
    py -3 tools/qa-recovery.py --list      # list orphans, don't touch
    py -3 tools/qa-recovery.py --report-dir <dir>  # clean specific cycle
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_common as qc


def recover_one(report_dir: Path, dry_run: bool = False) -> bool:
    """Recover a single orphaned cycle. Returns True if recovery
    succeeded (or was a no-op)."""
    cp = qc.read_checkpoint(report_dir)
    if cp is None:
        print(qc.dim(f"  no checkpoint in {report_dir.name} — skipping"))
        return True
    if cp.cleanup_done:
        print(qc.dim(f"  {report_dir.name} already cleaned up"))
        return True

    age_min = int((time.time() - cp.started_at) / 60) if cp.started_at else -1
    print(qc.yellow(f"\n  Orphaned cycle: {report_dir.name}"))
    print(f"    age:       {age_min} min")
    print(f"    in-progress stages: {cp.stages_in_progress}")
    print(f"    completed stages:   {cp.stages_completed}")

    # Invoke qa-state-guard.py
    cmd = ["py", "-3", str(qc.REPO_ROOT / "tools" / "qa-state-guard.py"),
           "--report-dir", str(report_dir)]
    if dry_run:
        cmd.append("--dry-run")

    print(qc.dim(f"    → running: {' '.join(cmd)}"))
    res = subprocess.run(cmd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=30)
    print(qc.dim(f"    state-guard exit: {res.returncode}"))
    if res.stdout:
        for line in res.stdout.splitlines():
            print(f"      {line}")
    ok = (res.returncode == 0)

    # Mark cleanup as done
    if not dry_run and ok:
        cp.cleanup_done = True
        qc.write_checkpoint(report_dir, cp)

    # Write a partial-report marker
    if not dry_run:
        partial_path = report_dir / "RECOVERY-NOTE.md"
        crashed_in = cp.stages_in_progress[-1] if cp.stages_in_progress else "(unknown)"
        partial_path.write_text(
            f"# Cycle aborted mid-run\n\n"
            f"This cycle was killed during **{crashed_in}**.\n"
            f"Completed stages: {cp.stages_completed}\n\n"
            f"Recovered by `qa-recovery.py` at "
            f"{time.strftime('%Y-%m-%d %H:%M:%S')}.\n"
            f"State-guard exit code: {res.returncode}\n",
            encoding="utf-8",
        )

    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true",
                    help="List orphan cycles without recovering")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be recovered, don't call HA")
    ap.add_argument("--report-dir", default=None,
                    help="Recover a specific cycle dir (skip auto-detect)")
    args = ap.parse_args()

    print(qc.bold("\n=== QA Cycle Recovery ==="))

    if args.report_dir:
        rd = Path(args.report_dir)
        if not rd.exists():
            print(qc.red(f"{rd} does not exist"), file=sys.stderr)
            return 2
        orphans = [rd]
    else:
        orphans = qc.find_orphan_cycles()

    if not orphans:
        print(qc.green("  No orphaned cycles found"))
        return 0

    if args.list:
        print(f"\n  Found {len(orphans)} orphan{'s' if len(orphans) != 1 else ''}:")
        for o in orphans:
            cp = qc.read_checkpoint(o)
            age = "?"
            crashed = "?"
            if cp:
                age_min = int((time.time() - cp.started_at) / 60) if cp.started_at else -1
                age = f"{age_min}m"
                crashed = cp.stages_in_progress[-1] if cp.stages_in_progress else "?"
            print(f"    {o.name}  (age {age}, crashed during {crashed})")
        return 0

    ok_count = 0
    fail_count = 0
    for o in orphans:
        if recover_one(o, dry_run=args.dry_run):
            ok_count += 1
        else:
            fail_count += 1

    print("")
    print(f"  Recovered: {ok_count}, failed: {fail_count}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
