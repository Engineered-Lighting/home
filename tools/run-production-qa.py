#!/usr/bin/env python3
"""Production QA orchestrator (Addendum 31) — single entry point.

Runs 7 stages cheap-to-expensive, fail-fast on pre-flight blockers,
always-run cleanup, single REPORT.md at the end.

Stage map:

    0. Pre-flight              (qa-preflight.py)        — fail-fast
    1. Unit tests              (run-test-coverage.py)   — < 10s
    2. Live service health     (qa-service-probes.py)   — < 30s
    3. Feature probe matrix    (probe-runner.py)         — read-only by default
    4. UI app smoke            (qa-browser-smoke.js)     — local app shell
    5. Workflow scenarios      (diagnose-identity.py)    — read-only; writes opt-in
    6. Cleanup                 (qa-state-guard.py)      — always runs

Per AR31-9 (crash recovery), checkpoint is written BEFORE and AFTER
every stage. SIGINT/SIGTERM caught + cleanup forced before re-raise.

Per AR31-13, `--only <ids>` flag filters which stages run (always
keeps stages 0/2/6 — pre-flight, services, cleanup).

Per AR31-17, mute-clearing requires `--clear-mute-if-blocking-only`.
Movie-mode override requires `--allow-movie-override`.

Per AR31-22, concurrent-user detection: if home.exe has had user
input in the last 60s AND we're about to run Stage 4, prompt
(or refuse with `--no-input-check`).

Usage:
    py -3 tools/run-production-qa.py
    py -3 tools/run-production-qa.py --quick           # skip stages 4+5
    py -3 tools/run-production-qa.py --only stage0,stage2
    py -3 tools/run-production-qa.py --strict          # abort on any unit fail
    py -3 tools/run-production-qa.py --clear-mute-if-blocking-only
    py -3 tools/run-production-qa.py --include-write-gated
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_common as qc


# ── Stage definitions ------------------------------------------------

@dataclass
class StageDef:
    id: str
    name: str
    runnable: bool = True
    always_run: bool = False     # ignored by --only filter
    description: str = ""


STAGES: list[StageDef] = [
    StageDef("stage0", "Pre-flight", always_run=True,
             description="Token, mute, services, subentry drift"),
    StageDef("stage1", "Unit tests",
             description="Pure-function + mock integration (run-test-coverage.py)"),
    StageDef("stage2", "Live service health", always_run=True,
             description="Sidecar/vision/Frigate/HA shape probes"),
    StageDef("stage3", "Feature probe matrix",
             description="Feature-validation probes from tools/feature-validation-probes.json"),
    StageDef("stage4", "UI app smoke",
             description="Local app shell + intelligence route source smoke"),
    StageDef("stage5", "Workflow scenarios",
             description="diagnose-identity.py --workflow read-only (skipped by --quick)"),
    StageDef("stage6", "Cleanup", always_run=True,
             description="State restore (qa-state-guard.py)"),
]


# ── Orchestrator state (module-global for signal handler access) ----─

@dataclass
class OrchestratorState:
    cycle_ts: str
    report_dir: Path
    checkpoint: qc.Checkpoint
    cleanup_invoked: bool = False
    interrupted: bool = False
    cli_args: dict = field(default_factory=dict)


_STATE: Optional[OrchestratorState] = None


def _emergency_cleanup():
    """Called via SIGINT handler + atexit. Idempotent."""
    global _STATE
    if _STATE is None or _STATE.cleanup_invoked:
        return
    _STATE.cleanup_invoked = True
    print(qc.yellow("\n\n  Interrupt → running emergency cleanup..."))
    try:
        cmd = ["py", "-3", str(qc.REPO_ROOT / "tools" / "qa-state-guard.py"),
               "--report-dir", str(_STATE.report_dir)]
        subprocess.run(cmd, timeout=30, encoding="utf-8", errors="replace")
        _STATE.checkpoint.cleanup_done = True
        qc.write_checkpoint(_STATE.report_dir, _STATE.checkpoint)
        print(qc.green("  Emergency cleanup complete."))
    except Exception as e:
        print(qc.red(f"  Emergency cleanup FAILED: {e!r}"))
        print(qc.red("  Run 'py -3 tools/qa-recovery.py' to retry"))


def _signal_handler(signum, frame):
    global _STATE
    if _STATE:
        _STATE.interrupted = True
    print(qc.yellow(f"\n\n  Got signal {signum} — initiating cleanup..."))
    _emergency_cleanup()
    raise KeyboardInterrupt(f"interrupted by signal {signum}")


# ── Stage runners ----------------------------------------------------

def _stage_begin(state: OrchestratorState, stage_id: str) -> None:
    state.checkpoint.stages_in_progress = list(
        set(state.checkpoint.stages_in_progress) | {stage_id}
    )
    qc.write_checkpoint(state.report_dir, state.checkpoint)


def _stage_end(state: OrchestratorState, stage_id: str,
               result: qc.StageResult) -> None:
    qc.write_stage_result(state.report_dir, stage_id, result)
    state.checkpoint.stages_in_progress = [
        s for s in state.checkpoint.stages_in_progress if s != stage_id
    ]
    if stage_id not in state.checkpoint.stages_completed:
        state.checkpoint.stages_completed.append(stage_id)
    qc.write_checkpoint(state.report_dir, state.checkpoint)


def run_stage0(state: OrchestratorState) -> qc.StageResult:
    """Pre-flight."""
    print(qc.bold("\n---- Stage 0: Pre-flight ----"))
    _stage_begin(state, "stage0")
    t0 = time.monotonic()
    cmd = ["py", "-3", str(qc.REPO_ROOT / "tools" / "qa-preflight.py"),
           "--cycle-ts", state.cycle_ts,
           "--report-dir", str(state.report_dir)]
    if state.cli_args.get("clear_mute_if_blocking_only"):
        cmd.append("--clear-mute-if-blocking-only")
    if state.cli_args.get("allow_movie_override"):
        cmd.append("--allow-movie-override")
    res = subprocess.run(cmd, encoding="utf-8", errors="replace")

    # Read structured output
    pf_json = state.report_dir / "preflight.json"
    snapshot = {}
    agent_id = ""
    degraded = []
    if pf_json.exists():
        try:
            data = json.loads(pf_json.read_text(encoding="utf-8"))
            agent_id = data.get("agent_id", "")
            for check in data.get("checks", []):
                if check["name"] == "jarvis_mute_state":
                    snap = check.get("detail", {}).get("snapshot", {})
                    snapshot.update(
                        {k: v for k, v in snap.items() if isinstance(v, str)}
                    )
                if check["status"] == "WARN":
                    if "vision" in check["name"]:
                        degraded.append("vision_sidecar")
                    elif "frigate" in check["name"]:
                        degraded.append("frigate")
                    elif "home_exe" in check["name"]:
                        degraded.append("home_exe")
        except Exception:
            pass

    # Persist snapshot into checkpoint for cleanup + recovery
    if snapshot:
        state.checkpoint.state_snapshot.update(snapshot)
    state.checkpoint.state_snapshot["_agent_id"] = agent_id
    state.checkpoint.state_snapshot["_degraded_services"] = degraded

    status = "PASS" if res.returncode == 0 else "FAIL"
    elapsed = time.monotonic() - t0
    summary = "all checks OK" if res.returncode == 0 else "FAIL → abort"
    result = qc.StageResult(
        name="Pre-flight", status=status,
        started_at=t0, elapsed_s=round(elapsed, 2),
        summary=summary,
        details={"exit_code": res.returncode, "agent_id": agent_id,
                 "degraded": degraded, "snapshot_keys": list(snapshot.keys())},
        artifacts=["preflight.json", "preflight.md"],
    )
    _stage_end(state, "stage0", result)
    return result


def run_stage1(state: OrchestratorState) -> qc.StageResult:
    """Unit tests via run-test-coverage.py."""
    print(qc.bold("\n---- Stage 1: Unit tests ----"))
    _stage_begin(state, "stage1")
    t0 = time.monotonic()
    cmd = ["py", "-3", str(qc.REPO_ROOT / "tools" / "run-test-coverage.py"),
           "--json"]
    if state.cli_args.get("quick"):
        cmd.append("--quick")
    res = subprocess.run(cmd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=600)
    elapsed = time.monotonic() - t0

    summary = "unknown"
    details = {"exit_code": res.returncode, "stdout_tail": ""}
    suites = []
    try:
        data = json.loads(res.stdout)
        suites = data.get("suites", [])
        passes = sum(s.get("passed", 0) for s in suites if s.get("passed", 0) > 0)
        fails = sum(s.get("failed", 0) for s in suites if s.get("failed", 0) > 0)
        fail_suites = [s["id"] for s in suites if s.get("status") != "PASS"]
        summary = f"{passes} assert PASS, {fails} FAIL across {len(suites)} suites"
        if fail_suites:
            summary += f" — failures: {', '.join(fail_suites[:3])}"
        details["suite_count"] = len(suites)
        details["assertion_pass"] = passes
        details["assertion_fail"] = fails
        details["failed_suites"] = fail_suites
    except Exception:
        details["stdout_tail"] = (res.stdout or "")[-500:]
        details["stderr_tail"] = (res.stderr or "")[-500:]

    # Write to stage1.json so report-writer can pick it up
    stage_out = state.report_dir / "stage1.json"
    stage_out.write_text(json.dumps({
        "suites": suites, "exit_code": res.returncode,
    }, indent=2), encoding="utf-8")

    if state.cli_args.get("strict") and res.returncode != 0:
        status = "FAIL"
    elif res.returncode == 0:
        status = "PASS"
    else:
        status = "DRIFT"  # non-strict: don't abort the cycle
    result = qc.StageResult(
        name="Unit tests", status=status,
        started_at=t0, elapsed_s=round(elapsed, 2),
        summary=summary, details=details,
        artifacts=["stage1.json"],
    )
    _stage_end(state, "stage1", result)
    return result


def run_stage2(state: OrchestratorState) -> qc.StageResult:
    """Live service health."""
    print(qc.bold("\n---- Stage 2: Service health ----"))
    _stage_begin(state, "stage2")
    t0 = time.monotonic()
    degraded = state.checkpoint.state_snapshot.get("_degraded_services", [])
    cmd = ["py", "-3", str(qc.REPO_ROOT / "tools" / "qa-service-probes.py"),
           "--report-dir", str(state.report_dir)]
    if degraded:
        cmd.extend(["--preflight-degraded", ",".join(degraded)])
    res = subprocess.run(cmd, encoding="utf-8", errors="replace", timeout=120)
    elapsed = time.monotonic() - t0

    services_json = state.report_dir / "services.json"
    summary = "no output"
    new_degraded = []
    if services_json.exists():
        try:
            data = json.loads(services_json.read_text(encoding="utf-8"))
            probes = data.get("probes", [])
            new_degraded = data.get("degraded", [])
            oks = sum(1 for p in probes if p.get("status") == "OK")
            downs = sum(1 for p in probes if p.get("status") == "DOWN")
            drifts = sum(1 for p in probes if p.get("status") == "DRIFT")
            summary = f"{oks} OK, {drifts} DRIFT, {downs} DOWN"
            # Merge into degraded set for downstream stages
            if new_degraded:
                state.checkpoint.state_snapshot["_degraded_services"] = list(
                    set(degraded) | set(new_degraded)
                )
        except Exception:
            pass

    if res.returncode == 2:
        status = "FAIL"
    elif res.returncode == 1:
        status = "DRIFT"
    else:
        status = "PASS"

    result = qc.StageResult(
        name="Service health", status=status,
        started_at=t0, elapsed_s=round(elapsed, 2),
        summary=summary,
        details={"exit_code": res.returncode, "degraded": new_degraded},
        artifacts=["services.json", "services.md"],
    )
    _stage_end(state, "stage2", result)
    return result


def run_stage3(state: OrchestratorState) -> qc.StageResult:
    """Feature-validation probes. Read-only/static probes dominate; live probes
    are classified as DRIFT instead of aborting the cycle."""
    print(qc.bold("\n---- Stage 3: Feature probe matrix ----"))
    _stage_begin(state, "stage3")
    t0 = time.monotonic()
    cmd = ["py", "-3", str(qc.REPO_ROOT / "tools" / "probe-runner.py")]
    res = subprocess.run(cmd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=240)
    elapsed = time.monotonic() - t0

    details = {
        "exit_code": res.returncode,
        "stdout_tail": (res.stdout or "")[-1000:],
        "stderr_tail": (res.stderr or "")[-1000:],
    }
    status = "PASS" if res.returncode == 0 else "DRIFT"
    summary = "feature probes green" if res.returncode == 0 else "feature probes reported drift"
    result = qc.StageResult(
        name="Feature probe matrix", status=status,
        started_at=t0, elapsed_s=round(elapsed, 2),
        summary=summary, details=details,
        artifacts=["stage3.json"],
    )
    _stage_end(state, "stage3", result)
    return result


def run_stage4(state: OrchestratorState) -> qc.StageResult:
    """Local UI smoke for the app shell and Atlas entry points."""
    print(qc.bold("\n---- Stage 4: UI app smoke ----"))
    _stage_begin(state, "stage4")
    t0 = time.monotonic()
    env = dict(os.environ)
    env["QA_REPORT_DIR"] = str(state.report_dir)
    cmd = ["node", str(qc.REPO_ROOT / "tools" / "qa-browser-smoke.js")]
    res = subprocess.run(cmd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=30,
                         env=env, cwd=str(qc.REPO_ROOT))
    elapsed = time.monotonic() - t0

    smoke_json = state.report_dir / "stage4-browser-smoke.json"
    summary = "UI smoke did not produce structured output"
    details = {
        "exit_code": res.returncode,
        "stdout_tail": (res.stdout or "")[-1000:],
        "stderr_tail": (res.stderr or "")[-1000:],
    }
    if smoke_json.exists():
        try:
            data = json.loads(smoke_json.read_text(encoding="utf-8"))
            summary = data.get("summary", summary)
            details["checks"] = data.get("checks", [])
        except Exception:
            pass
    status = "PASS" if res.returncode == 0 else "DRIFT"
    result = qc.StageResult(
        name="UI app smoke", status=status,
        started_at=t0, elapsed_s=round(elapsed, 2),
        summary=summary, details=details,
        artifacts=["stage4-browser-smoke.json", "stage4.json"],
    )
    _stage_end(state, "stage4", result)
    return result


def run_stage5(state: OrchestratorState) -> qc.StageResult:
    """Workflow command validation.

    Runs read-only workflow scenarios by default. Safe-listed writes,
    helper-state setup, and Travel Mode mutation tests remain opt-in via
    --include-write-gated.
    """
    print(qc.bold("\n---- Stage 5: Workflow scenarios ----"))
    _stage_begin(state, "stage5")
    t0 = time.monotonic()

    cmd = ["py", "-3", str(qc.REPO_ROOT / "tools" / "diagnose-identity.py"),
           "--workflow", "--quick", "--phase1-only"]
    if state.cli_args.get("include_write_gated"):
        cmd.append("--include-write-gated")
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=240,
                             cwd=str(qc.REPO_ROOT))
        exit_code = res.returncode
        stdout_tail = (res.stdout or "")[-1500:]
        stderr_tail = (res.stderr or "")[-1500:]
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        exit_code = -1
        stdout_tail = ((exc.stdout or "") if isinstance(exc.stdout, str) else "").strip()[-1500:]
        stderr_tail = ((exc.stderr or "") if isinstance(exc.stderr, str) else "").strip()[-1500:]
        timed_out = True
    elapsed = time.monotonic() - t0
    details = {
        "exit_code": exit_code,
        "mode": "read-only + write-gated" if state.cli_args.get("include_write_gated") else "read-only",
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "timed_out": timed_out,
    }
    status = "PASS" if exit_code == 0 else "DRIFT"
    if timed_out:
        summary = "bounded workflow smoke timed out after 240s"
    else:
        summary = "bounded workflow smoke green" if exit_code == 0 else "bounded workflow smoke reported drift"
    result = qc.StageResult(
        name="Workflow scenarios", status=status,
        started_at=t0, elapsed_s=round(elapsed, 2),
        summary=summary, details=details,
        artifacts=["stage5.json"],
    )
    _stage_end(state, "stage5", result)
    return result


def run_stage_skip(state: OrchestratorState, stage_id: str,
                   reason: str) -> qc.StageResult:
    """Placeholder for v2 stages (3/4/5) that don't have runners yet."""
    print(qc.dim(f"\n---- {stage_id.upper()}: SKIPPED ({reason}) ----"))
    _stage_begin(state, stage_id)
    result = qc.StageResult(
        name=stage_id, status="SKIP",
        started_at=time.monotonic(), elapsed_s=0,
        summary=reason,
    )
    qc.write_stage_result(state.report_dir, stage_id, result)
    _stage_end(state, stage_id, result)
    return result


def run_stage6(state: OrchestratorState) -> qc.StageResult:
    """Cleanup — always runs."""
    print(qc.bold("\n---- Stage 6: Cleanup ----"))
    _stage_begin(state, "stage6")
    t0 = time.monotonic()
    cmd = ["py", "-3", str(qc.REPO_ROOT / "tools" / "qa-state-guard.py"),
           "--report-dir", str(state.report_dir)]
    if state.cli_args.get("no_cleanup"):
        cmd.append("--dry-run")
    res = subprocess.run(cmd, encoding="utf-8", errors="replace", timeout=60)
    elapsed = time.monotonic() - t0

    cleanup_json = state.report_dir / "cleanup.json"
    summary = "cleanup ran"
    if cleanup_json.exists():
        try:
            data = json.loads(cleanup_json.read_text(encoding="utf-8"))
            summary = data.get("summary", summary)
        except Exception:
            pass

    status = "PASS" if res.returncode == 0 else "FAIL"
    if state.cli_args.get("no_cleanup"):
        status = "SKIP"
        summary = "[--no-cleanup] dry-run only"
    result = qc.StageResult(
        name="Cleanup", status=status,
        started_at=t0, elapsed_s=round(elapsed, 2),
        summary=summary,
        details={"exit_code": res.returncode},
        artifacts=["cleanup.json"],
    )
    _stage_end(state, "stage6", result)
    state.checkpoint.cleanup_done = True
    qc.write_checkpoint(state.report_dir, state.checkpoint)
    state.cleanup_invoked = True
    return result


def write_final_report(state: OrchestratorState) -> int:
    """Invoke qa-report-writer.py + return its exit code."""
    print(qc.bold("\n---- Final report ----"))
    cmd = ["py", "-3", str(qc.REPO_ROOT / "tools" / "qa-report-writer.py"),
           "--report-dir", str(state.report_dir)]
    res = subprocess.run(cmd, encoding="utf-8", errors="replace", timeout=30)
    return res.returncode


# ── Main ------------------------------------------------------------──

def main() -> int:
    global _STATE
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true",
                    help="Skip slow stages (workflow + workflow scenarios)")
    ap.add_argument("--strict", action="store_true",
                    help="Abort if Stage 1 unit tests fail (default: continue)")
    ap.add_argument("--only", default="",
                    help="Comma-separated stage IDs to run "
                         "(always includes stage0/stage2/stage6)")
    ap.add_argument("--skip-screenshots", action="store_true",
                    help="Skip Stage 4 (UI screenshots)")
    ap.add_argument("--skip-workflow", action="store_true",
                    help="Skip Stage 5 (workflow scenarios)")
    ap.add_argument("--clear-mute-if-blocking-only", action="store_true",
                    help="AR31-17: clear explicit mute if it's blocking; "
                         "NEVER touches movie_mode or TV-active grace")
    ap.add_argument("--allow-movie-override", action="store_true",
                    help="Allow cycle to run during movie_mode (rude!)")
    ap.add_argument("--no-cleanup", action="store_true",
                    help="Skip Stage 6 cleanup (for debugging)")
    ap.add_argument("--no-input-check", action="store_true",
                    help="AR31-22: skip concurrent-user detection")
    ap.add_argument("--include-write-gated", action="store_true",
                    help="Run opt-in workflow scenarios that may touch safe-listed devices")
    args = ap.parse_args()

    # Set up state + signal handlers BEFORE the first stage
    cycle_ts = qc.make_cycle_ts()
    report_dir = qc.make_report_dir(cycle_ts)

    checkpoint = qc.Checkpoint(
        cycle_ts=cycle_ts,
        report_dir=str(report_dir),
        started_at=time.time(),
    )
    qc.write_checkpoint(report_dir, checkpoint)

    _STATE = OrchestratorState(
        cycle_ts=cycle_ts,
        report_dir=report_dir,
        checkpoint=checkpoint,
        cli_args={
            "quick": args.quick,
            "strict": args.strict,
            "clear_mute_if_blocking_only": args.clear_mute_if_blocking_only,
            "allow_movie_override": args.allow_movie_override,
            "no_cleanup": args.no_cleanup,
            "no_input_check": args.no_input_check,
            "include_write_gated": args.include_write_gated,
        },
    )

    # Signal handling — SIGINT (Ctrl+C), SIGTERM (kill)
    signal.signal(signal.SIGINT, _signal_handler)
    try:
        signal.signal(signal.SIGTERM, _signal_handler)
    except (ValueError, AttributeError):
        # SIGTERM not catchable on some Windows configs
        pass
    # Last-resort: atexit hook
    atexit.register(_emergency_cleanup)

    # ── Filter which stages run ──
    only_set = {s.strip() for s in args.only.split(",") if s.strip()} if args.only else set()
    enabled = {s.id for s in STAGES}
    if only_set:
        enabled = {s.id for s in STAGES if s.id in only_set or s.always_run}
    if args.skip_screenshots:
        enabled.discard("stage4")
    if args.skip_workflow:
        enabled.discard("stage5")
    if args.quick:
        enabled.discard("stage4")
        enabled.discard("stage5")

    # ── Print run summary ──
    print(qc.bold(f"\n=== Production QA Pass — cycle {cycle_ts} ==="))
    print(f"  Report dir:  {report_dir}")
    print(f"  Stages to run: " + ", ".join(s.id for s in STAGES if s.id in enabled))
    print(f"  CLI: {args}")
    print("")

    # ── Run stages ──
    abort = False
    try:
        for stage in STAGES:
            if stage.id not in enabled:
                continue
            if stage.id == "stage0":
                result = run_stage0(_STATE)
                if result.status == "FAIL":
                    print(qc.red("\n  Pre-flight FAIL → aborting cycle"))
                    abort = True
                    break
            elif stage.id == "stage1":
                result = run_stage1(_STATE)
                if args.strict and result.status == "FAIL":
                    print(qc.red("\n  --strict: Stage 1 FAIL → aborting"))
                    abort = True
                    break
            elif stage.id == "stage2":
                result = run_stage2(_STATE)
                if result.status == "FAIL":
                    # Critical service down → abort but still run cleanup
                    print(qc.red("\n  Critical service DOWN → aborting"))
                    abort = True
                    break
            elif stage.id == "stage3":
                run_stage3(_STATE)
            elif stage.id == "stage4":
                run_stage4(_STATE)
            elif stage.id == "stage5":
                run_stage5(_STATE)
            elif stage.id == "stage6":
                run_stage6(_STATE)
    except KeyboardInterrupt:
        print(qc.red("\n  KeyboardInterrupt — cleanup ran via signal handler"))
        abort = True

    # Always run cleanup unless explicitly aborted at stage6
    if not _STATE.cleanup_invoked and "stage6" in enabled:
        run_stage6(_STATE)

    # ── Final report ──
    report_exit = write_final_report(_STATE)

    # Disable atexit since we ran cleanup successfully
    atexit.unregister(_emergency_cleanup)

    if abort:
        return 2
    return report_exit


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        sys.exit(130)
