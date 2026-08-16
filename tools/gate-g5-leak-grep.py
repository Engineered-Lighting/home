#!/usr/bin/env python3
"""gate-g5-leak-grep.py — acceptance gate G5 (docs/QWEN38-MIGRATION.md).

G5 is ZERO-tolerance: no reasoning may reach a transcript, in `<think>`
markup inside `content` OR in either reasoning response field. The app
renders leaked thinking verbatim (there is no client-side guard) and the
voice path speaks it, so one leak is a user-visible failure.

Two traps this tool exists to avoid:

  * vLLM 0.20.2 renamed `reasoning_content` -> `reasoning` (R5). A scanner
    that checks the old name only will pass a model that is leaking
    through the new one.
  * A NON-EMPTY reasoning field is a leak even with no `<think>` markup
    anywhere: it means `enable_thinking:false` was not honoured, which is
    the condition G5 exists to detect.

Inputs it understands, auto-detected per line/file:
  * raw OpenAI chat-completion responses (buffered or streamed chunks) —
    this is the intended source: the Phase-3 matrix driver's own saved
    completions;
  * a bare JSON message object;
  * `/trace` NDJSON from the metrics-sidecar.

⚠ `/trace` IS NOT A TRANSCRIPT SOURCE on this stack, verified 2026-08-16.
Its records are voice-latency telemetry with no message bodies (the
conversation tee is containment-locked), and its only producer is the
s2s personaplex-bridge, which by policy never runs — the newest record is
from 2026-05-17. Scanning it yields exit 2 (INCONCLUSIVE), never PASS.
That distinction is the whole point of the exit-2 path below: a scanner
that reported "0 leaks" over a file nobody writes would hand the cutover a
green G5 backed by no evidence at all.

Usage:
  tools/gate-g5-leak-grep.py TRACE.ndjson [MORE...]
  docker exec hav-metrics-sidecar cat /app/data/trace.ndjson | tools/gate-g5-leak-grep.py -
  tools/gate-g5-leak-grep.py --json TRACE.ndjson

Exit status: 0 = zero leaks (G5 PASS), 1 = leaks found (G5 FAIL),
             2 = nothing scannable was found (NOT a pass — say so loudly).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import qwen38_gates as gates  # noqa: E402


def iter_json_objects(stream):
    """Yield (line_no, object) for NDJSON, tolerating blank and junk lines.

    A whole-file JSON array is also accepted, because archived traces get
    pretty-printed by hand often enough to be worth handling.
    """
    text = stream.read()
    stripped = text.lstrip()
    if stripped.startswith("["):
        try:
            for i, obj in enumerate(json.loads(text), 1):
                yield i, obj
            return
        except json.JSONDecodeError:
            pass  # fall through to line mode
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            yield i, json.loads(line)
        except json.JSONDecodeError:
            continue


def candidate_messages(obj):
    """Pull every message-shaped dict out of one trace record.

    The sidecar tee wraps the upstream response under assorted keys
    depending on the code path, so look in all of them rather than assume
    one shape — a missed shape is a silent pass.
    """
    found = []
    if not isinstance(obj, dict):
        return found

    # A full chat-completion response, at the top level or nested.
    for key in (None, "response", "body", "result", "completion", "upstream"):
        node = obj if key is None else obj.get(key)
        if isinstance(node, dict) and "choices" in node:
            for choice in node.get("choices") or []:
                if not isinstance(choice, dict):
                    continue
                for mk in ("message", "delta"):
                    if isinstance(choice.get(mk), dict):
                        found.append(choice[mk])

    # A bare message object.
    if any(k in obj for k in ("content", *gates.REASONING_FIELDS)):
        found.append(obj)

    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="NDJSON trace files, or - for stdin")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--max-report", type=int, default=25,
                    help="how many leaks to print in full (default 25)")
    args = ap.parse_args()

    leaks, n_records, n_messages = [], 0, 0
    for path in args.paths:
        stream = sys.stdin if path == "-" else open(path, "r", encoding="utf-8",
                                                    errors="replace")
        try:
            for line_no, obj in iter_json_objects(stream):
                n_records += 1
                for msg in candidate_messages(obj):
                    n_messages += 1
                    for leak in gates.find_reasoning_leaks(msg):
                        leaks.append({
                            "file": path, "line": line_no,
                            "where": leak.where, "pattern": leak.pattern,
                            "excerpt": leak.excerpt,
                        })
        finally:
            if stream is not sys.stdin:
                stream.close()

    if args.json:
        print(json.dumps({
            "records": n_records, "messages": n_messages,
            "leaks": len(leaks), "passed": not leaks and n_messages > 0,
            "detail": leaks[:args.max_report],
        }, indent=2))
    else:
        print("=== G5 reasoning-leakage scan ===")
        print(f"records scanned : {n_records}")
        print(f"messages scanned: {n_messages}")
        print(f"leaks found     : {len(leaks)}")
        print(f"fields checked  : content <think> markup + "
              f"{' + '.join(gates.REASONING_FIELDS)}")
        for leak in leaks[:args.max_report]:
            print(f"\n  {leak['file']}:{leak['line']}  [{leak['where']}]"
                  f"  pattern={leak['pattern']}")
            print(f"    {leak['excerpt']!r}")
        if len(leaks) > args.max_report:
            print(f"\n  ... {len(leaks) - args.max_report} more")

    if n_messages == 0:
        print("\nG5 INCONCLUSIVE: nothing scannable was found. An empty scan "
              "is NOT a pass — check the trace path and that the sidecar's "
              "/trace tee is live.", file=sys.stderr)
        return 2
    if leaks:
        print(f"\nG5 FAIL — {len(leaks)} leak(s). G5 is zero-tolerance.")
        return 1
    print(f"\nG5 PASS — zero leaks across {n_messages} messages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
