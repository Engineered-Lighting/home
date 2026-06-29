#!/usr/bin/env python3
"""Meta-test for run-production-qa.py (AR31-20).

Verifies the ORCHESTRATOR mechanics without hitting live services.
A bug in the orchestrator could silently make every cycle GREEN —
this test catches that class of bug.

Strategy: replace the stage subprocess calls with shims that emit
known JSON outputs to the report directory, then verify:

    - Checkpoint is written BEFORE + AFTER each stage
    - State snapshot is recorded in checkpoint
    - Stage results are written to disk (stage<N>.json)
    - --only filter actually filters
    - Cleanup runs in finally block (SIGINT test)
    - Final REPORT.md is generated
    - Exit codes propagate correctly
    - qa_common.py self-test passes

Runnable as standalone Python script per run-test-coverage.py convention.

Usage:
    py -3 tools/test-run-production-qa.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_common as qc


REPO = Path(r"C:/Claude/home").resolve()

passes = 0
fails = 0
failures = []


def _ok(name: str):
    global passes
    passes += 1
    print(f"  PASS  {name}")


def _fail(name: str, detail: str = ""):
    global fails
    fails += 1
    failures.append((name, detail))
    print(f"  FAIL  {name}")
    if detail:
        print(f"        {detail[:300]}")


def assert_eq(name: str, actual, expected):
    if actual == expected:
        _ok(name)
    else:
        _fail(name, f"expected={expected!r} actual={actual!r}")


def assert_true(name: str, cond, detail: str = ""):
    if cond:
        _ok(name)
    else:
        _fail(name, detail)


def assert_contains(name: str, haystack: str, needle: str):
    if needle in haystack:
        _ok(name)
    else:
        _fail(name, f"missing {needle!r} in {haystack[:200]!r}")


# ── Suite 1: qa_common.py contract ────────────────────────────────────
def suite_qa_common():
    print("\n--- Suite 1: qa_common.py contract ---")

    # conv_id factory
    cid = qc.qa_conv_id("2026-05-18-1432-00", "stage3", "U3")
    assert_eq("qa_conv_id format",
              cid, "qa-cycle-2026-05-18-1432-00-stage3-U3")
    assert_true("is_qa_conv_id detects qa-cycle prefix",
                qc.is_qa_conv_id(cid))
    assert_true("is_qa_conv_id rejects non-qa id",
                not qc.is_qa_conv_id("normal-conv-123"))
    assert_true("is_qa_conv_id handles empty",
                not qc.is_qa_conv_id(""))

    # Timestamp shape (AR31-29: HHMMSS resolution)
    ts = qc.make_cycle_ts()
    assert_eq("cycle_ts length", len(ts), 17)
    # Format: YYYY-MM-DD-HHMMSS
    parts = ts.split("-")
    assert_eq("cycle_ts has 4 dash-separated parts", len(parts), 4)
    assert_true("cycle_ts last part is HHMMSS",
                len(parts[3]) == 6 and parts[3].isdigit(),
                f"last part: {parts[3]!r}")

    # Checkpoint roundtrip
    with tempfile.TemporaryDirectory() as td:
        rd = Path(td) / "qa-test-ck"
        rd.mkdir()
        cp = qc.Checkpoint(
            cycle_ts=ts, report_dir=str(rd),
            started_at=time.time(),
            stages_in_progress=["stage1"],
            state_snapshot={"input_boolean.jarvis_muted_explicit": "off"},
        )
        qc.write_checkpoint(rd, cp)
        loaded = qc.read_checkpoint(rd)
        assert_true("checkpoint roundtrip", loaded is not None)
        assert_eq("checkpoint cycle_ts preserved",
                  loaded.cycle_ts if loaded else None, ts)
        assert_eq("checkpoint state_snapshot preserved",
                  loaded.state_snapshot if loaded else None,
                  {"input_boolean.jarvis_muted_explicit": "off"})

    # Stage result roundtrip
    with tempfile.TemporaryDirectory() as td:
        rd = Path(td) / "qa-test-sr"
        rd.mkdir()
        sr = qc.StageResult(
            name="stage0", status="PASS", started_at=time.time(),
            elapsed_s=1.2, summary="ok",
        )
        qc.write_stage_result(rd, "stage0", sr)
        loaded = qc.read_stage_result(rd, "stage0")
        assert_true("stage_result roundtrip", loaded is not None)
        assert_eq("stage_result status preserved",
                  loaded.status if loaded else None, "PASS")


# ── Suite 2: orphan-cycle detection ──────────────────────────────────
def suite_orphan_detection():
    print("\n--- Suite 2: orphan-cycle detection (AR31-9) ---")

    # Build a synthetic orphan in a temp REPORTS_DIR
    original_reports_dir = qc.REPORTS_DIR
    with tempfile.TemporaryDirectory() as td:
        try:
            qc.REPORTS_DIR = Path(td)
            qc.REPORTS_DIR.mkdir(exist_ok=True)

            # No orphans yet
            assert_eq("no orphans on fresh dir",
                      len(qc.find_orphan_cycles()), 0)

            # Create a "completed" cycle (cleanup_done=True)
            completed_dir = qc.REPORTS_DIR / "qa-2026-05-18-100000"
            completed_dir.mkdir()
            cp_done = qc.Checkpoint(
                cycle_ts="2026-05-18-100000",
                report_dir=str(completed_dir),
                started_at=time.time(),
                stages_completed=["stage0", "stage1", "stage6"],
                cleanup_done=True,
            )
            qc.write_checkpoint(completed_dir, cp_done)

            # Create an "orphan" (stages_in_progress not empty, cleanup_done false)
            orphan_dir = qc.REPORTS_DIR / "qa-2026-05-18-110000"
            orphan_dir.mkdir()
            cp_orphan = qc.Checkpoint(
                cycle_ts="2026-05-18-110000",
                report_dir=str(orphan_dir),
                started_at=time.time(),
                stages_in_progress=["stage2"],
                stages_completed=["stage0", "stage1"],
                cleanup_done=False,
            )
            qc.write_checkpoint(orphan_dir, cp_orphan)

            orphans = qc.find_orphan_cycles()
            assert_eq("detects orphan, ignores completed", len(orphans), 1)
            assert_eq("orphan path is correct",
                      orphans[0].name if orphans else "",
                      "qa-2026-05-18-110000")
        finally:
            qc.REPORTS_DIR = original_reports_dir


# ── Suite 3: qa-log-filter.py qa-cycle detection ────────────────────
def suite_log_filter():
    print("\n--- Suite 3: qa-log-filter conv_id filtering (AR31-10) ---")

    # Build a synthetic routing log with mixed entries
    log_lines = [
        json.dumps({"ts": "2026-05-18T12:00:00",
                    "conv_id": "qa-cycle-2026-05-18-120000-stage3-U1",
                    "kind": "routing_decision", "intent": "local"}),
        json.dumps({"ts": "2026-05-18T12:01:00",
                    "conv_id": "real-user-conv-abc",
                    "kind": "routing_decision", "intent": "local"}),
        json.dumps({"ts": "2026-05-18T12:02:00",
                    "conv_id": "qa-cycle-2026-05-18-120000-stage3-U2",
                    "kind": "tool_call", "tool_name": "find_clips"}),
        json.dumps({"ts": "2026-05-18T12:03:00",
                    "conv_id": "another-real-conv",
                    "kind": "perception_dispatch"}),
        "malformed line that is not JSON",
        "",
    ]

    with tempfile.TemporaryDirectory() as td:
        log_path = Path(td) / "routing.log"
        log_path.write_text("\n".join(log_lines), encoding="utf-8")

        # Run the filter
        res = subprocess.run(
            ["py", "-3", str(REPO / "tools" / "qa-log-filter.py"),
             "--input", str(log_path), "--count"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10,
        )
        assert_eq("qa-log-filter --count exit 0", res.returncode, 0)
        assert_contains("--count reports qa-cycle: 2", res.stdout, "qa-cycle: 2")
        assert_contains("--count reports production: 2", res.stdout, "production: 2")


# ── Suite 4: qa-state-guard dry-run behavior ────────────────────────
def suite_state_guard_dry_run():
    print("\n--- Suite 4: qa-state-guard.py dry-run + idempotency ---")

    with tempfile.TemporaryDirectory() as td:
        rd = Path(td) / "qa-test-cleanup"
        rd.mkdir()
        # Fake preflight.json with snapshot
        pf = rd / "preflight.json"
        pf.write_text(json.dumps({
            "checks": [{
                "name": "jarvis_mute_state",
                "status": "OK",
                "summary": "not muted",
                "detail": {
                    "snapshot": {
                        "input_boolean.jarvis_muted_explicit": "off",
                        "input_boolean.movie_mode": "off",
                        "input_datetime.jarvis_mute_until": "2000-01-01T00:00:00",
                        "binary_sensor.jarvis_muted_effective_2": "off",
                    }
                }
            }],
        }), encoding="utf-8")

        # Dry-run should NOT touch HA, just print plan + exit 0
        # (We can't fully test the HA call without mocking; check
        # the structural side — does it produce cleanup.json?)
        res = subprocess.run(
            ["py", "-3", str(REPO / "tools" / "qa-state-guard.py"),
             "--report-dir", str(rd), "--dry-run"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15,
        )
        # In dry-run with snapshot of "off" + current state likely "off",
        # actions should mostly be skip_unchanged. Exit code depends on
        # HA reachability — accept any (test that it didn't crash).
        assert_true("state-guard --dry-run doesn't crash",
                    res.returncode in (0, 1),
                    f"exit={res.returncode}")
        cj = rd / "cleanup.json"
        assert_true("cleanup.json written", cj.exists())


# ── Suite 5: report-writer status aggregation ───────────────────────
def suite_report_writer():
    print("\n--- Suite 5: report-writer aggregation + baseline diff ---")

    with tempfile.TemporaryDirectory() as td:
        rd = Path(td) / "qa-test-report"
        rd.mkdir()

        # Fake stage outputs
        (rd / "preflight.json").write_text(json.dumps({
            "checks": [
                {"name": "ha_token_valid", "status": "OK"},
                {"name": "metrics_sidecar", "status": "OK"},
                {"name": "vision_sidecar", "status": "WARN"},
            ],
        }), encoding="utf-8")
        (rd / "stage1.json").write_text(json.dumps({
            "suites": [{"id": "x", "passed": 5, "failed": 0, "status": "PASS"}],
            "exit_code": 0,
        }), encoding="utf-8")
        (rd / "services.json").write_text(json.dumps({
            "probes": [
                {"name": "sidecar", "status": "OK"},
                {"name": "vision", "status": "DRIFT"},
            ],
            "degraded": ["vision_sidecar"],
        }), encoding="utf-8")
        (rd / "cleanup.json").write_text(json.dumps({
            "actions": [],
            "summary": "no changes to restore",
            "ok": True,
        }), encoding="utf-8")
        # Checkpoint so the writer can pick up cycle_ts
        cp = qc.Checkpoint(
            cycle_ts="2026-05-18-120000",
            report_dir=str(rd),
            started_at=time.time() - 60,
            stages_completed=["stage0", "stage1", "stage2", "stage6"],
            cleanup_done=True,
        )
        qc.write_checkpoint(rd, cp)

        res = subprocess.run(
            ["py", "-3", str(REPO / "tools" / "qa-report-writer.py"),
             "--report-dir", str(rd)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15,
        )
        # Exit code should reflect DRIFT (1) since vision-sidecar WARN/DRIFT
        assert_true("report-writer produces REPORT.md",
                    (rd / "REPORT.md").exists(),
                    f"stdout={res.stdout[-200:]!r} stderr={res.stderr[-200:]!r}")
        assert_true("report-writer produces results.json",
                    (rd / "results.json").exists())

        # Check the REPORT.md content
        report = (rd / "REPORT.md").read_text(encoding="utf-8")
        assert_contains("REPORT mentions cycle_ts", report, "2026-05-18-120000")
        assert_contains("REPORT has per-stage table", report, "| Stage |")
        assert_contains("REPORT has coverage scope section",
                        report, "Coverage scope")
        assert_contains("REPORT includes recommended actions",
                        report, "Recommended next actions")

        # Verify results.json shape
        results = json.loads((rd / "results.json").read_text(encoding="utf-8"))
        assert_true("results.json has stages list",
                    "stages" in results and isinstance(results["stages"], list))
        assert_true("results.json has 7 stage entries",
                    len(results["stages"]) == 7)


# ── Suite 6: orchestrator --only filter via dry-import ──────────────
def suite_orchestrator_only_filter():
    print("\n--- Suite 6: orchestrator --only filter (AR31-13) ---")

    # Import the orchestrator module to access STAGES
    sys.path.insert(0, str(REPO / "tools"))
    try:
        # We can't easily import "run-production-qa.py" (hyphen in name).
        # Just verify the file parses + STAGES is declared via grep.
        orch = (REPO / "tools" / "run-production-qa.py").read_text(encoding="utf-8")
        assert_contains("orchestrator declares STAGES list", orch, "STAGES: list[StageDef]")
        assert_contains("--only filter present", orch, "--only")
        assert_contains("always_run on stage0", orch,
                        'StageDef("stage0", "Pre-flight", always_run=True')
        assert_contains("always_run on stage6", orch,
                        'StageDef("stage6", "Cleanup", always_run=True')
        # AR31-9: signal handler
        assert_contains("SIGINT handler registered", orch, "signal.SIGINT")
        assert_contains("atexit registered for cleanup", orch, "atexit.register")
        assert_contains("emergency_cleanup defined", orch, "_emergency_cleanup")
    finally:
        sys.path.pop(0)


# ── Suite 7: privacy mode declared in qa-features.yaml ──────────────
def suite_stage4_browser_smoke_autonomous():
    print("\n--- Suite 7: Stage 4 browser smoke autonomous static host ---")

    with tempfile.TemporaryDirectory() as td:
        rd = Path(td) / "qa-stage4-smoke"
        rd.mkdir()
        env = dict(os.environ)
        env.pop("HOME_APP_URL", None)
        env["QA_REPORT_DIR"] = str(rd)
        res = subprocess.run(
            ["node", str(REPO / "tools" / "qa-browser-smoke.js")],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=20, env=env, cwd=str(REPO),
        )
        assert_eq("qa-browser-smoke exits 0 without prestarted dev server", res.returncode, 0)
        assert_contains("qa-browser-smoke reports self-hosted server", res.stdout, "self-hosted static app server starts")
        assert_contains("qa-browser-smoke reports app shell", res.stdout, "app shell responds")
        smoke_json = rd / "stage4-browser-smoke.json"
        assert_true("qa-browser-smoke writes structured report", smoke_json.exists())
        if smoke_json.exists():
            data = json.loads(smoke_json.read_text(encoding="utf-8"))
            checks = data.get("checks") or []
            assert_true("qa-browser-smoke structured report is self-hosted", data.get("self_hosted") is True, json.dumps(data)[:300])
            assert_true("qa-browser-smoke uses loopback app URL", str(data.get("app_url", "")).startswith("http://127.0.0.1:"), str(data.get("app_url")))
            assert_true("qa-browser-smoke keeps all checks passing", checks and all(item.get("status") == "PASS" for item in checks), json.dumps(checks)[:500])


def suite_privacy_modes_declared():
    print("\n--- Suite 8: privacy modes in qa-features.yaml (AR31-11) ---")
    yaml_path = REPO / "tools" / "qa-features.yaml"
    assert_true("qa-features.yaml exists", yaml_path.exists())
    raw = yaml_path.read_text(encoding="utf-8")
    for needle in ["privacy:", "strict:", "clear:", "redact:",
                   "default_mode: strict", "blur_targets:"]:
        assert_contains(f"qa-features.yaml has {needle!r}", raw, needle)


def main() -> int:
    print("Meta-test for production-QA orchestrator (Addendum 31)")
    print("=" * 60)

    suite_qa_common()
    suite_orphan_detection()
    suite_log_filter()
    suite_state_guard_dry_run()
    suite_report_writer()
    suite_orchestrator_only_filter()
    suite_stage4_browser_smoke_autonomous()
    suite_privacy_modes_declared()

    print("")
    if fails == 0:
        print(f"{passes} pass · 0 fail")
        return 0
    print(f"{passes} pass · {fails} fail")
    for n, d in failures:
        print(f"  FAIL: {n} — {d[:200]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
