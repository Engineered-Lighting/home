#!/usr/bin/env python3
"""Filter qa-cycle-* entries from the HA routing log (AR31-10).

Production-side `external_routing.log` collects per-turn JSONL entries.
The QA cycle pollutes it with up to ~150 entries per run, each
tagged with conversation_id starting `qa-cycle-`. This script strips
those entries from a dump for clean production analysis.

Two modes:

    # Filter a local file (assumes you already SCP'd the log):
    py -3 tools/qa-log-filter.py --input routing.log --output prod-only.log

    # Pipe-friendly:
    cat routing.log | py -3 tools/qa-log-filter.py > prod-only.log

    # Tail the live log on HAOS, filter, and print:
    py -3 tools/qa-log-filter.py --ssh --tail 200

    # Show ONLY qa-cycle entries (inverse — for cycle-specific debugging):
    py -3 tools/qa-log-filter.py --input routing.log --keep-only-qa

Idempotent: input order preserved, JSONL malformed lines passed through.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_common as qc


def is_qa_entry(line: str) -> bool:
    """True if the JSONL line is from a qa-cycle conversation."""
    try:
        obj = json.loads(line)
    except Exception:
        return False
    cid = obj.get("conv_id", "") or obj.get("conversation_id", "")
    return qc.is_qa_conv_id(cid)


def filter_lines(lines, keep_only_qa: bool = False):
    """Generator: yields lines that should be kept."""
    for line in lines:
        line = line.rstrip("\n")
        if not line:
            continue
        is_qa = is_qa_entry(line)
        if keep_only_qa:
            if is_qa:
                yield line
        else:
            if not is_qa:
                yield line


def fetch_ssh_tail(n: int) -> list[str]:
    """SSH to HAOS and tail the routing log."""
    res = subprocess.run(
        qc.HAOS_SSH + ["tail", "-n", str(n), "/config/external_routing.log"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=15,
    )
    if res.returncode != 0:
        print(qc.red(f"SSH tail failed: {res.stderr[:200]}"), file=sys.stderr)
        return []
    return res.stdout.splitlines()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", "-i", default=None,
                    help="Input log file (default: stdin)")
    ap.add_argument("--output", "-o", default=None,
                    help="Output file (default: stdout)")
    ap.add_argument("--ssh", action="store_true",
                    help="Fetch via SSH from HAOS instead of file/stdin")
    ap.add_argument("--tail", type=int, default=200,
                    help="With --ssh, fetch this many lines")
    ap.add_argument("--keep-only-qa", action="store_true",
                    help="Inverse: keep ONLY qa-cycle entries")
    ap.add_argument("--count", action="store_true",
                    help="Print just counts (qa-cycle / non-qa) and exit")
    args = ap.parse_args()

    # Source
    if args.ssh:
        lines = fetch_ssh_tail(args.tail)
    elif args.input:
        lines = Path(args.input).read_text(encoding="utf-8",
                                           errors="replace").splitlines()
    else:
        lines = sys.stdin.read().splitlines()

    if args.count:
        # Only count lines that are valid JSONL (skip blank + malformed).
        # qa-cycle vs production is a JSONL-only distinction.
        qa = 0
        prod = 0
        malformed = 0
        for ln in lines:
            if not ln.strip():
                continue
            try:
                obj = json.loads(ln)
            except Exception:
                malformed += 1
                continue
            cid = obj.get("conv_id", "") or obj.get("conversation_id", "")
            if qc.is_qa_conv_id(cid):
                qa += 1
            else:
                prod += 1
        print(f"qa-cycle: {qa}")
        print(f"production: {prod}")
        print(f"malformed: {malformed}")
        print(f"total: {qa + prod + malformed}")
        return 0

    out_lines = list(filter_lines(lines, keep_only_qa=args.keep_only_qa))

    # Sink
    if args.output:
        Path(args.output).write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        kept = len(out_lines)
        dropped = sum(1 for ln in lines if ln.strip()) - kept
        print(qc.dim(f"wrote {kept} lines to {args.output} (dropped {dropped})"),
              file=sys.stderr)
    else:
        for ln in out_lines:
            print(ln)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
