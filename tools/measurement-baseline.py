#!/usr/bin/env python3
"""measurement-baseline.py — capture a daily snapshot of the metrics
that the Addendum 27 / Section 13 milestones promise to improve.

Reads:
  • /config/external_routing.log on HAOS (over SSH; same pattern as
    analyze-routing.py) — routing decisions, per-tool latencies (M0),
    perception dispatches (when M1 ships), confirmations (when M6 ships)
  • metrics-sidecar /conversations/recent (HTTP; LAN) — per-completion
    token counts, upstream latencies (M0), tool emergence timings
  • metrics-sidecar /metrics (HTTP) — current resource snapshot

Writes:
  • tools/measurement-history.csv — append-only, one row per day
    The columns are stable (new metrics append; never reorder existing).
  • prints a one-paragraph delta vs the prior row (when there is one)

Run modes:
  • python3 measurement-baseline.py             # 24h window ending now
  • python3 measurement-baseline.py --hours N   # last N hours
  • python3 measurement-baseline.py --check     # read history.csv, print last 7 rows + delta

Cadence: intended to run nightly via Windows Task Scheduler at ~03:00.
First run establishes the baseline; subsequent runs let any milestone's
acceptance criteria be evaluated as a CSV diff.

Per Addendum 27 / Section 13's measurement contract: every milestone
captures baseline → ships → captures post-ship → diffs against a
target. This script provides the capture mechanism. Targets live in
tools/measurement-baselines/M<N>-baseline.md per Section 13.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


HERE = Path(__file__).resolve().parent
HISTORY_CSV = HERE / "measurement-history.csv"

DEFAULT_SSH = "root@homeassistant.local"
DEFAULT_PORT = "22222"
DEFAULT_REMOTE_LOG = "/config/external_routing.log"

DEFAULT_SIDECAR = "http://192.168.0.100:8092"

# Stable column order — append-only. Adding a new metric = append a new
# column header in this list; do NOT reorder. Older rows get an empty
# cell for new columns (rolled forward by `--check`).
COLUMNS = [
    "date",                          # YYYY-MM-DD (UTC)
    "window_hours",                  # int — capture window covering this row
    # Routing decisions
    "turns_total",
    "turns_local",
    "turns_external",
    "turns_ambiguous",
    "turns_cache",                   # M1 only — 0 until F-1 ships
    "turns_intercepted",             # M6 only — 0 until M6 ships
    # Latency (ms, sourced from metrics-sidecar /conversations/recent + routing log)
    "upstream_ms_p50",
    "upstream_ms_p95",
    "ttft_observed_ms_p50",
    "ttft_observed_ms_p95",
    # Token counts (sourced from sidecar; null = vLLM didn't emit usage)
    "prompt_tokens_p50",
    "prompt_tokens_p95",
    "completion_tokens_p50",
    "completion_tokens_p95",
    # Tool dispatch (M0.3 / M0.4 — populated as logs grow)
    "tool_calls_total",
    "tool_calls_ok",
    "tool_calls_failed",
    "tool_latency_ms_p50",
    "tool_latency_ms_p95",
    # Resource snapshot at run time (single sample, not p50/p95)
    "vram_used_gb",
    "vram_total_gb",
    "gpu_util_pct",
    "gpu_temp_c",
    "model",
]


def _http_get_json(url: str, timeout: float = 10.0) -> Any:
    """Tiny urllib wrapper — no httpx/requests dependency so this script
    works on a fresh workstation. Returns parsed JSON or None on error."""
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=timeout) as r:
            if 200 <= r.status < 300:
                return json.loads(r.read().decode("utf-8", "replace"))
    except (URLError, TimeoutError, OSError) as e:
        print(f"warn: GET {url} failed: {e}", file=sys.stderr)
    except json.JSONDecodeError as e:
        print(f"warn: GET {url} returned non-JSON: {e}", file=sys.stderr)
    return None


def _fetch_routing_log_via_ssh(ssh_target: str, port: str, remote_path: str,
                               hours: float) -> list[dict]:
    """SSH-cat the routing log, return parsed JSONL entries from the
    last `hours` window. Errors return []."""
    cutoff = time.time() - (hours * 3600)
    try:
        result = subprocess.run(
            ["ssh", "-p", port, "-o", "ConnectTimeout=10",
             "-o", "StrictHostKeyChecking=no",
             ssh_target, f"tail -n 5000 {remote_path} 2>/dev/null"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"warn: ssh routing-log tail failed: {result.stderr.strip()}",
                  file=sys.stderr)
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"warn: ssh failed: {e}", file=sys.stderr)
        return []

    out: list[dict] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = entry.get("ts")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
        if isinstance(ts, (int, float)) and ts >= cutoff:
            out.append(entry)
    return out


def _fetch_sidecar_completions(base: str, hours: float) -> list[dict]:
    """Pull recent completions from metrics-sidecar (bounded ring of 50;
    we accept that as the window if hours captures fewer than 50)."""
    since = time.time() - (hours * 3600)
    payload = _http_get_json(f"{base.rstrip('/')}/conversations/recent?since={since}")
    if not payload:
        return []
    return payload.get("entries", []) or []


def _fetch_sidecar_snapshot(base: str) -> dict:
    return _http_get_json(f"{base.rstrip('/')}/metrics") or {}


def _percentile(values: list[float], pct: float) -> float | None:
    """statistics.quantiles needs n>=2; for sparse data fall back to
    nearest-rank to avoid silent zeros."""
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    sorted_v = sorted(values)
    k = max(0, min(len(sorted_v) - 1, int(round(pct / 100.0 * (len(sorted_v) - 1)))))
    return float(sorted_v[k])


def _summarize_routing(entries: list[dict]) -> dict[str, Any]:
    # Routing-decision entries are the canonical per-turn counter. Tool
    # entries (M0.4) reach this list once tool_call kind starts emitting.
    decisions = [e for e in entries if e.get("kind", "routing_decision") == "routing_decision"]
    tool_calls = [e for e in entries if e.get("kind") == "tool_call"]
    perception = [e for e in entries if e.get("kind") == "perception_dispatch"]
    confirms = [e for e in entries if e.get("kind") == "confirmation"]

    by_dispatched: dict[str, int] = {}
    for d in decisions:
        by_dispatched[d.get("dispatched", "?")] = 1 + by_dispatched.get(d.get("dispatched", "?"), 0)

    tool_lats = [t["latency_ms"] for t in tool_calls
                 if isinstance(t.get("latency_ms"), (int, float))]
    tool_ok = sum(1 for t in tool_calls if t.get("ok") is True)
    tool_failed = sum(1 for t in tool_calls if t.get("ok") is False)

    return {
        "turns_total": len(decisions),
        "turns_local": by_dispatched.get("local", 0),
        "turns_external": by_dispatched.get("external", 0)
                          + by_dispatched.get("external→fallback", 0),
        "turns_ambiguous": by_dispatched.get("ambiguous", 0),
        "turns_cache": len([p for p in perception if p.get("verdict") == "cache_hit"]),
        "turns_intercepted": sum(1 for c in confirms if c.get("intercepted")),
        "tool_calls_total": len(tool_calls),
        "tool_calls_ok": tool_ok,
        "tool_calls_failed": tool_failed,
        "tool_latency_ms_p50": _percentile(tool_lats, 50),
        "tool_latency_ms_p95": _percentile(tool_lats, 95),
    }


def _summarize_completions(entries: list[dict]) -> dict[str, Any]:
    """M0.1 + M0.2 fields: upstream_ms, ttft_observed_ms, *_tokens."""
    upstream = [e["upstream_ms"] for e in entries
                if isinstance(e.get("upstream_ms"), (int, float))]
    ttft = [e["ttft_observed_ms"] for e in entries
            if isinstance(e.get("ttft_observed_ms"), (int, float))]
    prompt_t = [e["prompt_tokens"] for e in entries
                if isinstance(e.get("prompt_tokens"), (int, float))]
    completion_t = [e["completion_tokens"] for e in entries
                    if isinstance(e.get("completion_tokens"), (int, float))]
    return {
        "upstream_ms_p50": _percentile(upstream, 50),
        "upstream_ms_p95": _percentile(upstream, 95),
        "ttft_observed_ms_p50": _percentile(ttft, 50),
        "ttft_observed_ms_p95": _percentile(ttft, 95),
        "prompt_tokens_p50": _percentile(prompt_t, 50),
        "prompt_tokens_p95": _percentile(prompt_t, 95),
        "completion_tokens_p50": _percentile(completion_t, 50),
        "completion_tokens_p95": _percentile(completion_t, 95),
    }


def _append_row(row: dict[str, Any]) -> None:
    """Append the row to history.csv. Creates the file with headers if
    missing. Never rewrites existing rows — append-only by contract so a
    diff against an old row is always a real diff."""
    is_new = not HISTORY_CSV.exists()
    with HISTORY_CSV.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        if is_new:
            writer.writeheader()
        writer.writerow({c: row.get(c, "") for c in COLUMNS})


def _read_history() -> list[dict[str, str]]:
    if not HISTORY_CSV.exists():
        return []
    with HISTORY_CSV.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _print_delta(curr: dict[str, Any], prev: dict[str, str]) -> None:
    """One-paragraph human-readable delta. Just the columns where the
    value changed meaningfully (>5% relative, or any change for counters)."""
    notable: list[str] = []
    for col in COLUMNS:
        c_raw, p_raw = curr.get(col, ""), prev.get(col, "")
        if c_raw == "" or p_raw == "":
            continue
        if col in ("date", "model"):
            continue
        try:
            c, p = float(c_raw), float(p_raw)
        except (TypeError, ValueError):
            continue
        if p == 0 and c == 0:
            continue
        if p == 0:
            notable.append(f"{col}: 0 → {c:g}")
            continue
        delta_pct = (c - p) / p * 100
        if abs(delta_pct) >= 5:
            notable.append(f"{col}: {p:g} → {c:g} ({delta_pct:+.1f}%)")
    if not notable:
        print("delta vs previous row: no metrics moved >=5%")
    else:
        print(f"delta vs previous ({prev.get('date')}):")
        for n in notable[:15]:
            print(f"  • {n}")
        if len(notable) > 15:
            print(f"  • (+ {len(notable) - 15} more)")


def cmd_capture(args: argparse.Namespace) -> int:
    print(f"capturing baseline for last {args.hours}h …", file=sys.stderr)
    routing_entries = _fetch_routing_log_via_ssh(
        args.ssh, args.port, args.remote_log, args.hours,
    )
    completions = _fetch_sidecar_completions(args.sidecar, args.hours)
    snapshot = _fetch_sidecar_snapshot(args.sidecar)

    print(f"  routing log entries: {len(routing_entries)}", file=sys.stderr)
    print(f"  sidecar completions: {len(completions)}", file=sys.stderr)
    print(f"  metrics snapshot: {'ok' if snapshot else 'MISSING'}", file=sys.stderr)

    row: dict[str, Any] = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "window_hours": args.hours,
        "vram_used_gb": snapshot.get("vram_used_gb"),
        "vram_total_gb": snapshot.get("vram_total_gb"),
        "gpu_util_pct": snapshot.get("gpu_util_pct"),
        "gpu_temp_c": snapshot.get("gpu_temp_c"),
        "model": snapshot.get("model"),
    }
    row.update(_summarize_routing(routing_entries))
    row.update(_summarize_completions(completions))

    history = _read_history()
    _append_row(row)
    print(f"\nwrote {HISTORY_CSV}", file=sys.stderr)
    print(json.dumps(row, indent=2, default=str))

    if history:
        _print_delta(row, history[-1])
    else:
        print("(first row — no delta)")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    history = _read_history()
    if not history:
        print(f"no history at {HISTORY_CSV}", file=sys.stderr)
        return 1
    print(f"last {min(len(history), 7)} rows of {HISTORY_CSV}:")
    print()
    # Compact tabular view of a few headline columns
    cols = ["date", "turns_total", "upstream_ms_p50", "upstream_ms_p95",
            "prompt_tokens_p50", "tool_calls_total", "vram_used_gb"]
    widths = {c: max(len(c), max((len(str(r.get(c, ""))) for r in history[-7:]), default=0))
              for c in cols}
    print("  " + "  ".join(c.ljust(widths[c]) for c in cols))
    for r in history[-7:]:
        print("  " + "  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))
    print()
    if len(history) >= 2:
        _print_delta({c: history[-1].get(c) for c in COLUMNS}, history[-2])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hours", type=float, default=24.0,
                    help="capture window in hours (default 24)")
    ap.add_argument("--ssh", default=DEFAULT_SSH,
                    help=f"SSH target for routing log (default {DEFAULT_SSH})")
    ap.add_argument("--port", default=DEFAULT_PORT,
                    help=f"SSH port (default {DEFAULT_PORT})")
    ap.add_argument("--remote-log", default=DEFAULT_REMOTE_LOG,
                    help=f"remote routing log path (default {DEFAULT_REMOTE_LOG})")
    ap.add_argument("--sidecar", default=DEFAULT_SIDECAR,
                    help=f"metrics-sidecar base URL (default {DEFAULT_SIDECAR})")
    ap.add_argument("--check", action="store_true",
                    help="don't capture — just print last 7 rows + delta")
    args = ap.parse_args()
    return cmd_check(args) if args.check else cmd_capture(args)


if __name__ == "__main__":
    sys.exit(main())
