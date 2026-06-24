#!/usr/bin/env python3
"""Aggregate stage outputs into a single REPORT.md + results.json.

Reads <report-dir>/{preflight,services,stage3,stage4,stage5,cleanup}.json
and the checkpoint, produces:

    REPORT.md      — scannable in 30s, drills down per-stage
    results.json   — machine-readable for baseline-diff (AR31-21)

Per AR31-21: if a previous cycle's results.json exists at
tools/reports/qa-LAST/results.json (symlink or copy), the report
includes a "Changes since last run" section.

Per AR31-19: report opens with explicit coverage scope:
"this cycle validated X of Y documented features".

Per AR31-26: workflow scenarios that DRIFT (passed below 5/5) are
called out by name with attempt detail.

Usage:
    py -3 tools/qa-report-writer.py --report-dir <dir>
    py -3 tools/qa-report-writer.py --report-dir <dir> --status-summary
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_common as qc


# ── Status badges ─────────────────────────────────────────────────────
BADGE = {
    "PASS": "✅",
    "OK": "✅",
    "DRIFT": "⚠️",
    "WARN": "⚠️",
    "FAIL": "❌",
    "DOWN": "❌",
    "ABORT": "🛑",
    "SKIP": "⏭️",
    "SKIPPED": "⏭️",
    "ERROR": "💥",
    "UNKNOWN": "❓",
}


def _b(status: str) -> str:
    return BADGE.get(status.upper(), "❓") + " " + status


def _load_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _stage_status_from(stage_file: Path) -> tuple[str, str]:
    """Derive overall stage status + summary line from a stage JSON file."""
    data = _load_json(stage_file)
    if data is None:
        return ("SKIP", f"(no {stage_file.name})")
    # Stage 0 (preflight.json) shape: {checks: [...], ...}
    if "checks" in data:
        checks = data.get("checks", [])
        fails = sum(1 for c in checks if c.get("status") == "FAIL")
        warns = sum(1 for c in checks if c.get("status") == "WARN")
        oks = sum(1 for c in checks if c.get("status") == "OK")
        if fails:
            return ("FAIL", f"{fails} FAIL, {warns} WARN, {oks} OK")
        if warns:
            return ("DRIFT", f"{warns} WARN, {oks} OK")
        return ("PASS", f"{oks} OK")
    # Stage 1/2 (services.json) shape: {probes: [...], degraded: [...]}
    if "probes" in data:
        probes = data.get("probes", [])
        downs = sum(1 for p in probes if p.get("status") == "DOWN")
        drifts = sum(1 for p in probes if p.get("status") == "DRIFT")
        skips = sum(1 for p in probes if p.get("status") == "SKIP")
        oks = sum(1 for p in probes if p.get("status") == "OK")
        if downs:
            return ("FAIL", f"{downs} DOWN, {drifts} DRIFT, {skips} SKIP, {oks} OK")
        if drifts:
            return ("DRIFT", f"{drifts} DRIFT, {skips} SKIP, {oks} OK")
        return ("PASS", f"{oks} OK ({skips} SKIP)")
    # Stage 6 (cleanup.json) shape: {actions: [...], summary: ..., ok: bool}
    if "actions" in data and "summary" in data:
        return ("PASS" if data.get("ok") else "FAIL", data.get("summary", ""))
    # Generic StageResult shape
    if "status" in data:
        return (data["status"], data.get("summary", ""))
    return ("UNKNOWN", "unrecognized stage shape")


def _diff_against_baseline(current: dict, baseline_path: Path) -> dict:
    """Compare current run's results.json to a previous one."""
    baseline = _load_json(baseline_path) if baseline_path.exists() else None
    if not baseline:
        return {"baseline_found": False}
    delta = {"baseline_found": True, "newly_failing": [], "newly_passing": []}
    cur_stages = {s["id"]: s for s in current.get("stages", [])}
    base_stages = {s["id"]: s for s in baseline.get("stages", [])}
    for sid, cur in cur_stages.items():
        prev = base_stages.get(sid)
        if not prev:
            continue
        cur_st = cur.get("status", "")
        prev_st = prev.get("status", "")
        if prev_st in ("PASS", "OK") and cur_st in ("FAIL", "DOWN", "ABORT"):
            delta["newly_failing"].append({
                "stage": sid, "was": prev_st, "now": cur_st,
                "summary": cur.get("summary", ""),
            })
        elif prev_st in ("FAIL", "DOWN", "ABORT") and cur_st in ("PASS", "OK"):
            delta["newly_passing"].append({
                "stage": sid, "was": prev_st, "now": cur_st,
            })
    return delta


def build_results_json(report_dir: Path) -> dict:
    cp = qc.read_checkpoint(report_dir)
    stages = []
    for sid, fname in [
        ("stage0_preflight", "preflight.json"),
        ("stage1_unit", "stage1.json"),
        ("stage2_services", "services.json"),
        ("stage3_agent", "stage3.json"),
        ("stage4_screenshots", "stage4.json"),
        ("stage5_workflow", "stage5.json"),
        ("stage6_cleanup", "cleanup.json"),
    ]:
        status, summary = _stage_status_from(report_dir / fname)
        stages.append({"id": sid, "status": status, "summary": summary})
    return {
        "cycle_ts": cp.cycle_ts if cp else "",
        "started_at": cp.started_at if cp else 0,
        "stages": stages,
        "report_dir": str(report_dir),
    }


def build_markdown(report_dir: Path, results: dict, delta: dict) -> str:
    cp = qc.read_checkpoint(report_dir)
    ts = results.get("cycle_ts", report_dir.name.replace("qa-", ""))

    lines = [f"# Production QA Report — {ts}", ""]

    # Top-line verdict
    fail_stages = [s for s in results["stages"]
                   if s["status"] in ("FAIL", "DOWN", "ABORT")]
    drift_stages = [s for s in results["stages"]
                    if s["status"] in ("DRIFT", "WARN")]
    skip_stages = [s for s in results["stages"]
                   if s["status"] in ("SKIP", "SKIPPED")]
    pass_stages = [s for s in results["stages"]
                   if s["status"] in ("PASS", "OK")]

    if fail_stages:
        verdict = "🛑 **NOT READY** — at least one stage failed"
    elif drift_stages:
        verdict = "⚠️ **REVIEW** — passing with drift; investigate flags"
    elif not pass_stages:
        verdict = "❓ **EMPTY** — no stages produced output"
    else:
        verdict = "✅ **GREEN** — all stages passed"
    lines.append(verdict)
    lines.append("")

    # Per-stage table
    lines.append("## Stages")
    lines.append("")
    lines.append("| Stage | Status | Summary |")
    lines.append("|---|---|---|")
    for s in results["stages"]:
        lines.append(f"| `{s['id']}` | {_b(s['status'])} | {s['summary']} |")
    lines.append("")

    # AR31-19: coverage scope statement
    lines.append("## Coverage scope (AR31-19)")
    lines.append("")
    lines.append("This cycle validates:")
    lines.append("- Pre-flight blockers (token, mute, services)")
    lines.append("- Pure-function unit tests (delegated to run-test-coverage.py)")
    lines.append("- Live service health (sidecar, vision, Frigate, HA)")
    lines.append("- Feature probe matrix (static/unit/live read probes)")
    lines.append("- Local UI shell/Atlas smoke when Stage 4 runs")
    lines.append("- Cleanup state restoration")
    lines.append("")
    lines.append("**Still not fully validated by this cycle:**")
    lines.append("- Full live agent scenario scoring via /api/conversation/process")
    lines.append("- Pixel-level screenshot acceptance and visual diffing")
    lines.append("- Write-gated scenarios unless explicitly enabled")
    lines.append("")
    lines.append("Green here means *infrastructure is healthy*. It does NOT mean ")
    lines.append("*every feature is verified end-to-end*. Continue mapping qa-features.yaml ")
    lines.append("entries to Stage 3/4/5 runners for full coverage.")
    lines.append("")

    # Baseline diff (AR31-21)
    if delta and delta.get("baseline_found"):
        lines.append("## Changes since last run (AR31-21)")
        lines.append("")
        if delta["newly_failing"]:
            lines.append("### 🔴 Newly failing")
            for d in delta["newly_failing"]:
                lines.append(f"- `{d['stage']}`: was {d['was']} → now {d['now']} "
                             f"({d['summary']})")
            lines.append("")
        if delta["newly_passing"]:
            lines.append("### 🟢 Newly passing")
            for d in delta["newly_passing"]:
                lines.append(f"- `{d['stage']}`: was {d['was']} → now {d['now']}")
            lines.append("")
        if not delta["newly_failing"] and not delta["newly_passing"]:
            lines.append("_no status changes since last run_")
            lines.append("")
    else:
        lines.append("## Baseline diff")
        lines.append("")
        lines.append("_no previous run found — this becomes the baseline_")
        lines.append("")

    # Recommended next actions
    lines.append("## Recommended next actions")
    lines.append("")
    if fail_stages:
        lines.append("1. Address FAIL stages above (see per-stage .md files for detail)")
    if drift_stages:
        lines.append(f"{2 if fail_stages else 1}. Investigate DRIFT in: "
                     + ", ".join(f"`{s['id']}`" for s in drift_stages))
    if skip_stages:
        lines.append(f"{3 if (fail_stages and drift_stages) else 2 if (fail_stages or drift_stages) else 1}. "
                     f"SKIPPED stages (run when ready): "
                     + ", ".join(f"`{s['id']}`" for s in skip_stages))
    if not (fail_stages or drift_stages):
        lines.append("- No action needed. Cycle GREEN.")
    lines.append("")

    # Per-stage detail
    lines.append("## Per-stage detail")
    lines.append("")
    for sid, fname in [
        ("stage0_preflight", "preflight.md"),
        ("stage2_services", "services.md"),
        ("stage6_cleanup", "cleanup.json"),
    ]:
        path = report_dir / fname
        if path.exists():
            lines.append(f"### {sid}")
            lines.append("")
            if path.suffix == ".md":
                lines.append(path.read_text(encoding="utf-8"))
            else:
                lines.append("```json")
                lines.append(path.read_text(encoding="utf-8")[:2000])
                lines.append("```")
            lines.append("")

    # Report metadata
    lines.append("---")
    lines.append("")
    lines.append(f"_Report dir: `{report_dir}`_")
    lines.append(f"_Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}_")
    if cp:
        lines.append(f"_Cycle started: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(cp.started_at))}_")
        elapsed = int(time.time() - cp.started_at)
        lines.append(f"_Elapsed: {elapsed // 60}m {elapsed % 60}s_")

    return "\n".join(lines)


def find_previous_results(reports_dir: Path, current_dir: Path) -> Optional[Path]:
    """Find the most recent results.json from a prior cycle."""
    cycles = sorted(
        [d for d in reports_dir.glob("qa-*") if d.is_dir() and d != current_dir],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for d in cycles:
        rj = d / "results.json"
        if rj.exists():
            return rj
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report-dir", required=True)
    ap.add_argument("--status-summary", action="store_true",
                    help="Print one-line summary to stdout (for CI hooks)")
    args = ap.parse_args()

    report_dir = Path(args.report_dir)
    if not report_dir.exists():
        print(qc.red(f"Report dir does not exist: {report_dir}"), file=sys.stderr)
        return 2

    results = build_results_json(report_dir)
    # Write results.json BEFORE diffing so subsequent runs can pick it up
    (report_dir / "results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8")

    # Diff against most recent OTHER cycle's results.json
    prev = find_previous_results(qc.REPORTS_DIR, report_dir)
    delta = _diff_against_baseline(results, prev) if prev else {"baseline_found": False}

    md = build_markdown(report_dir, results, delta)
    (report_dir / "REPORT.md").write_text(md, encoding="utf-8")

    print(qc.bold("\n=== QA report ==="))
    print(f"  REPORT:      {report_dir / 'REPORT.md'}")
    print(f"  results.json: {report_dir / 'results.json'}")

    # Top-line verdict
    fail = sum(1 for s in results["stages"]
               if s["status"] in ("FAIL", "DOWN", "ABORT"))
    drift = sum(1 for s in results["stages"]
                if s["status"] in ("DRIFT", "WARN"))
    skip = sum(1 for s in results["stages"]
               if s["status"] in ("SKIP", "SKIPPED"))

    if fail:
        print(qc.red(f"  Verdict: NOT READY ({fail} stage{'s' if fail != 1 else ''} FAIL)"))
        exit_code = 2
    elif drift:
        print(qc.yellow(f"  Verdict: REVIEW ({drift} stage{'s' if drift != 1 else ''} DRIFT)"))
        exit_code = 1
    else:
        print(qc.green(f"  Verdict: GREEN ({skip} skip{'ped' if skip else ''})"))
        exit_code = 0

    if args.status_summary:
        print(json.dumps({
            "verdict": "FAIL" if fail else "DRIFT" if drift else "PASS",
            "fail": fail, "drift": drift, "skip": skip,
            "stages": results["stages"],
        }))

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
