#!/usr/bin/env python3
"""Coverage orchestrator — runs every test suite + prints a summary.

Used as the gate command per Addendum 11's self-gate contract. Exit
code is 0 iff every suite passed (including its own assertions).

Per-suite invocation is a subprocess so a single failing suite doesn't
poison the rest. Each suite's stdout is captured and tail-parsed for
the standard "N pass · M fail" footer that our test runners emit.

Usage:
    py -3 tools/run-test-coverage.py
    py -3 tools/run-test-coverage.py --quick      # skip slow suites
    py -3 tools/run-test-coverage.py --json       # machine-readable
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

# ── Suite registry ──────────────────────────────────────────────────
# Each entry: (id, command-as-list, slow_flag, description)

@dataclass
class Suite:
    id: str
    cmd: list[str]
    slow: bool
    description: str


def _node_test(rel_path: str, slow: bool = False, desc: str = "") -> Suite:
    return Suite(
        id=Path(rel_path).stem,
        cmd=["node", str(REPO_ROOT / rel_path)],
        slow=slow,
        description=desc,
    )


def _py_test(rel_path: str, slow: bool = False, desc: str = "") -> Suite:
    return Suite(
        id=Path(rel_path).stem,
        cmd=["py", "-3", str(REPO_ROOT / rel_path)],
        slow=slow,
        description=desc,
    )


def _cmd_test(id: str, cmd: list[str], slow: bool = False, desc: str = "") -> Suite:
    return Suite(
        id=id,
        cmd=cmd,
        slow=slow,
        description=desc,
    )


SUITES: list[Suite] = [
    # ── Test helpers' own smoke tests (Phase 1 — must pass first) ──
    _py_test("tools/test_helpers/mock_hass.py", desc="mock_hass self-test"),
    _py_test("tools/test_helpers/mock_frigate.py", desc="mock_frigate self-test"),
    _node_test("tools/test_helpers/fake-timers.js", desc="fake-timers self-test"),

    # ── Pure-function Node tests ──
    _node_test("tools/run-lab-tests.js", desc="lab helpers (anchors, palette, persistence)"),
    _node_test("tools/run-bootstrap-tests.js", desc="Home app bootstrap loader and mount-order contracts"),
    _node_test("tools/run-home-services-tests.js", desc="Home service resolver profiles, fallbacks, and probes"),
    _node_test("tools/qa-browser-smoke.js", desc="Stage 4 autonomous UI shell smoke (self-hosted app shell + Atlas entry points)"),
    _node_test("tools/run-people-tests.js", desc="people helpers (radial layout, edges)"),
    _node_test("tools/run-events-tests.js", desc="chat feed event helpers (speaker grouping, exports, action undo inverse map)"),
    _node_test("tools/run-metrics-tests.js", desc="metrics tray helpers (variance gating, pipeline normalization, exports)"),
    _node_test("tools/run-ai-stack-card-tests.js", desc="AI stack card recovery UI (status tones, start/restart/free-GPU affordances)"),
    _node_test("tools/run-explain-tests.js", desc="explain drawer helpers (partition, latency, eligibility)"),
    _node_test("tools/run-worldstate-tests.js", desc="world-state drawer helpers (age, fmt, band, sort)"),
    _node_test("tools/run-apartment-data-tests.js", desc="/apartment data layer (model load/save, registry palette, state binding, tracker WS)"),
    _node_test("tools/run-capabilities-tests.js", desc="A-1 capability registry + F-18 header chip derivation"),
    _node_test("tools/run-proactive-tests.js", desc="proactive coordinator policy and flow (arrival confirmation, room prompts, suppression, sim inertness)"),
    _node_test("tools/run-external-tests.js", desc="external reasoning frontend helpers (classifier, privacy payload, sim guard, provider errors, suite runner)"),
    _node_test("tools/run-app-frigate-label-tests.js", desc="HomeApp Frigate occupancy-label reducers (hydration, state_changed updates, aggregate skips)"),
    _node_test("tools/run-intelligence-tests.js", desc="Home Intelligence helpers (sidecar base, fetch wrapper, lighting evidence summaries, frame states)"),
    _node_test("tools/run-look-tests.js", desc="/look visual-reasoning helpers (camera parsing, entity-id prefixes, sidecar URL, command wiring)"),
    _node_test("tools/run-video-labeler-data-tests.js", desc="video-labeler data layer (sim guard, API wrappers, drafts, URLs, segment math)"),
    _node_test("tools/run-ha-client-tests.js", desc="HA websocket client helpers (call_service payloads, callService convenience, offline rejection)"),
    _node_test("tools/run-lighting-events-tests.js", desc="Living Lights HA event subscriber (manual override + articulation feed entries)"),
    _node_test("tools/run-lights-drawer-tests.js", desc="/lights drawer service-call wrapper and sleep/night-safe control wiring"),
    _node_test("tools/run-control-card-tests.js", desc="inline control-card helpers (targets, lifecycles, dispatch, light/media capabilities)"),
    _node_test("tools/run-simulation-camera-tests.js", desc="Simulation Mode camera fixtures (SVG placeholders, JPEG selection, privacy contract)"),
    _node_test("tools/run-simulation-scenario-tests.js", desc="Simulation Mode scenario registry and fixtures (baseline merging, outage labels, sim-only fixtures)"),
    _node_test("tools/run-simulation-command-tests.js", desc="Simulation Mode command/session behavior (URL boot, Tauri guard, /sim commands, session-only contracts)"),
    _node_test("tools/run-simulation-control-tests.js", desc="Simulation Mode control store (mock light/media state, subscribers, media groups, brightness fidelity)"),
    _node_test("tools/run-slash-command-tests.js", desc="slash-command catalog/handler coherence (visible commands, aliases, completion, lab-dump-watch regression)"),
    _node_test("tools/run-vision-tests.js", desc="Vision card helpers and source contracts (camera roster, stream signing, sim bypass, label occupancy wiring)"),
    _node_test("tools/run-s2s-tests.js", desc="S2S voice bridge helpers (URL mapping, Simulation Mode guard, mic/WS lifecycle, callbacks)"),
    _node_test("tools/run-recovery-scenarios-tests.js", desc="DOC-S105-S110 outage/recovery source contracts (HA, vision, Frigate, Sonos, metrics, bridge)"),
    _node_test("tools/run-stack-actions-tests.js", desc="AI stack supervisor action client (URLs, auth headers, confirm headers, errors)"),
    _node_test("tools/run-sse-fetch-tests.js", desc="SSE-via-fetch helper (auth headers, chunked frames, done events, abort/error closeout)"),
    _node_test("tools/run-tauri-glue-tests.js", desc="Tauri runtime glue (Simulation Mode fetch guard, prefs/events persistence, window controls)"),
    _cmd_test("cargo-test-tauri",
              ["cargo", "test", "--manifest-path", str(REPO_ROOT / "app/src-tauri/Cargo.toml")],
              desc="Tauri Rust bootstrap/manifest contract tests"),
    _node_test("tools/check-jsx.js", desc="frontend JSX/JS Babel parse check — catches syntax errors cargo build can't see"),

    # ── HA-side pytest standalone runners ──
    _py_test("ha-config/extended_openai_conversation/test_identity_store.py",
             desc="identity store CRUD + concurrency"),
    _py_test("ha-config/extended_openai_conversation/test_frigate_sync.py",
             desc="frigate sync drainer + PUT rename"),
    _py_test("ha-config/extended_openai_conversation/test_world_state.py",
             desc="world state aggregator + identity context"),
    _py_test("ha-config/extended_openai_conversation/test_external_routing.py",
             desc="external routing classifier + privacy"),
    _py_test("ha-config/extended_openai_conversation/test_native.py",
             desc="native.py media_player area->entity resolver"),
    _py_test("ha-config/extended_openai_conversation/test_registry.py",
             desc="M3 registry tools (areas/entities_in_area/entities_with_label/find_entity)"),
    _py_test("ha-config/extended_openai_conversation/test_frigate_tool.py",
             desc="find_clips tool (Frigate semantic search + normalization + freshness)"),
    _py_test("ha-config/extended_openai_conversation/test_recap.py",
             desc="M2 recap tool (composition of find_clips + recorder + aggregation)"),
    _py_test("ha-config/extended_openai_conversation/test_template_helpers.py",
             desc="A-5 current_room_context template helper (bound-room world_state preamble)"),
    _py_test("ha-config/extended_openai_conversation/test_entity_strict.py",
             desc="M4 strict-mode tool wrapper (_adjust_schema on tool specs)"),

    # ── Phase 2 P0 regression tests (Addendum 18) ──
    _py_test("ha-config/extended_openai_conversation/test_lifecycle.py",
             desc="drainer cleanup + cross-thread identity_mutation (F-3, F-5a)"),
    # ── Addendum 28 follow-up: TTS-safe error speech ──
    _py_test("ha-config/extended_openai_conversation/test_friendly_error_speech.py",
             desc="_friendly_error_speech caps raw error JSON before TTS reads it"),

    # ── Addendum 31: production-QA orchestrator meta-tests + helpers ──
    _py_test("tools/qa_common.py",
             desc="qa_common.py shared helpers self-test"),
    _py_test("tools/test-run-production-qa.py",
             desc="production-QA orchestrator meta-test (AR31-20)"),
    _py_test("tools/test-supervisor-service-actions.py",
             desc="stack supervisor allowlisted service actions (logs, stop, restart, free-gpu)"),
    _py_test("tools/qa-static-checks.py",
             desc="static QA drift checks (missing suites, duplicate routes/views/commands)"),
    _py_test("tools/qa-registry-audit.py",
             desc="canonical QA registry audit (docs, probes, suites, inventory)"),
    _py_test("tools/test-qa-feature-recipes.py",
             desc="live/manual QA feature recipe contracts (write gates, screenshots, privacy, audit mirror)"),
    _py_test("tools/test-scenario-evidence-map.py",
             desc="scenario evidence map covers every documented scenario without overstating live/manual coverage"),
    _py_test("tools/test-jarvis-mute-contract.py",
             desc="Jarvis mute lifecycle source contract (HA package, conversation gate, Home app UI, voice probe)"),
    _py_test("tools/test-workflow-scenarios.py",
             desc="workflow scenario model contracts (safe targets, invariants, relax/cozy/refusal predicates)"),
    _py_test("tools/test-qa-readiness-log.py",
             desc="RC readiness log stays synchronized with generated QA audit and local suite registry"),
    _py_test("tools/test-rc-blocker-ledger.py",
             desc="RC blocker ledger stays synchronized with readiness residual risks and evidence files"),

    # ── Addendum 33: Living Lights — presence override layer (Phase 4.A) ──
    _py_test("ha-config/extended_openai_conversation/test_living_lights.py",
             desc="living_lights presence-override tool (zone resolve, clamping, source validation, remote/pinned flags, clear)"),
    _py_test("ha-config/extended_openai_conversation/test_override_sessions.py",
             desc="Living Lights override-session collapse and automation-tail intent classifier"),
    _py_test("tools/test_living_lights_cooldown_gate.py",
             desc="Living Lights manual cooldown gate — manual holds stick while asleep and external changes arm fast"),
    _py_test("tools/test_living_lights_frigate_occupancy_path.py",
             desc="Living Lights Frigate zone occupancy path — raw/stable sensors feed classifiers and direct actuator wakeups"),

    # ── Addendum 35: harness inference-logic self-test (Phase 5 / AR35-11) ──
    _py_test("tools/test-probe-lighting-latency.py",
             desc="probe-lighting-latency harness — 8 synthetic inference scenarios (movie_mode, suppression, guard, cooldown, frigate latency, mirror divergence, predicted delta, dwell freeze)"),
    _py_test("tools/test-watch-frigate-occupancy.py",
             desc="Frigate/HA occupancy watcher report classification (camera health, zone geometry, HA mirror, classifier, cascade)"),
    _py_test("tools/test-watch-kitchen-lighting.py",
             desc="Kitchen lighting watcher report classification (manual override loss, asleep cap, Frigate/occupancy gaps)"),
    _py_test("tools/test-watch-ai-stack-status.py",
             desc="AI stack read-only status watcher report classification (token, supervisor, vLLM/model recovery)"),

    # ── Addendum 38 Phase 1: active spatial model + /spatial drawer ──
    _py_test("tools/test-init-spatial-model.py",
             desc="spatial model skeleton helpers"),
    _node_test("tools/run-spatial-tests.js",
               desc="Addendum 38 Phase 1 — /spatial drawer helpers (footprint->SVG projection, region map, per-light status, model roll-up)"),
]

# Regex to parse the standard "N pass · M fail" footer.
# Middle-dot may come through as "·" (utf-8), "Â·" (cp1252-misread), or "."
# depending on the Windows console's active codepage at capture time.
FOOTER_RE = re.compile(
    r"(\d+)\s+pass\s*(?:·|Â·|·|\.)\s*(\d+)\s+fail"
)
# test_world_state.py shape: "N/N passed" + a "✓ all N tests passed" banner.
# test_external_routing.py shape: "✓ all N suites passed".
ALT_FOOTER_RE = re.compile(
    r"(?:all\s+(\d+)\s+(?:tests?|suites?)\s+passed)|"
    r"(?:(\d+)/\d+\s+passed)"
)
CARGO_FOOTER_RE = re.compile(
    r"test result:\s+ok\.\s+(\d+)\s+passed;\s+(\d+)\s+failed"
)
# Strip ANSI color codes from output before scanning.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def run_suite(suite: Suite, timeout_s: int = 300) -> dict:
    """Run one suite as a subprocess, parse the result, return a row."""
    start = time.monotonic()
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            suite.cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            env=env,
            cwd=str(REPO_ROOT),
        )
    except subprocess.TimeoutExpired:
        return {
            "id": suite.id,
            "passed": 0, "failed": -1,
            "status": "TIMEOUT",
            "duration_s": timeout_s,
            "exit_code": -1,
            "stdout_tail": "",
            "stderr_tail": "TIMEOUT",
        }
    except FileNotFoundError as exc:
        return {
            "id": suite.id,
            "passed": 0, "failed": -1,
            "status": "MISSING",
            "duration_s": 0,
            "exit_code": -1,
            "stdout_tail": "",
            "stderr_tail": f"command not found: {exc}",
        }

    duration_s = round(time.monotonic() - start, 2)
    out = ANSI_RE.sub("", proc.stdout or "")
    err = ANSI_RE.sub("", proc.stderr or "")

    # Parse pass/fail counts.
    passed = failed = 0
    m = FOOTER_RE.search(out)
    if m:
        passed, failed = int(m.group(1)), int(m.group(2))
    else:
        # Try alt format (test_world_state, test_external_routing)
        m2 = ALT_FOOTER_RE.search(out)
        if m2:
            n = int(m2.group(1) or m2.group(2) or 0)
            passed = n
            failed = 0 if proc.returncode == 0 else 1
        else:
            # Last-ditch: count PASS / FAIL lines
            for line in out.splitlines():
                stripped = line.strip()
                if re.match(r"^(PASS|✓|✔)\b", stripped) or "PASS  " in stripped[:8]:
                    passed += 1
                elif re.match(r"^(FAIL|✗|✘)\b", stripped) or "FAIL  " in stripped[:8]:
                    failed += 1

    mc = CARGO_FOOTER_RE.search(out + "\n" + err)
    if mc:
        passed, failed = int(mc.group(1)), int(mc.group(2))

    if failed > 0 or proc.returncode != 0:
        status = "FAIL"
    elif passed == 0:
        status = "EMPTY"
    else:
        status = "PASS"

    return {
        "id": suite.id,
        "passed": passed,
        "failed": failed,
        "status": status,
        "duration_s": duration_s,
        "exit_code": proc.returncode,
        "stdout_tail": "\n".join(out.splitlines()[-15:]),
        "stderr_tail": "\n".join(err.splitlines()[-5:]) if err else "",
        "description": suite.description,
    }


def print_table(rows: list[dict]) -> None:
    print("")
    print(f"{'SUITE':<40} {'PASS':>6} {'FAIL':>6} {'TIME':>8}   STATUS")
    print("-" * 75)
    for r in rows:
        status_disp = r["status"]
        # Plain text — no color codes (Windows console pain).
        print(f"{r['id']:<40} {r['passed']:>6} {r['failed']:>6} {r['duration_s']:>7}s   {status_disp}")
    print("-" * 75)
    total_p = sum(r["passed"] for r in rows if r["passed"] > 0)
    total_f = sum(r["failed"] for r in rows if r["failed"] > 0)
    total_t = sum(r["duration_s"] for r in rows)
    fail_suites = sum(1 for r in rows if r["status"] != "PASS")
    print(f"{'TOTAL':<40} {total_p:>6} {total_f:>6} {total_t:>7.1f}s   "
          f"{('PASS' if fail_suites == 0 else f'{fail_suites} suite(s) FAILED')}")
    print("")
    # Show failures' stderr tail
    for r in rows:
        if r["status"] != "PASS":
            print(f"--- {r['id']} ({r['status']}) ---")
            if r["stdout_tail"]:
                print(r["stdout_tail"])
            if r["stderr_tail"]:
                print("STDERR:")
                print(r["stderr_tail"])
            print("")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true",
                        help="Skip slow suites")
    parser.add_argument("--json", action="store_true",
                        help="Emit machine-readable JSON instead of a table")
    parser.add_argument("--only", action="append", default=[],
                        help="Run only these suite IDs (repeat for multiple)")
    args = parser.parse_args()

    suites = SUITES
    if args.quick:
        suites = [s for s in suites if not s.slow]
    if args.only:
        suites = [s for s in suites if s.id in args.only]
        if not suites:
            print(f"No matching suites for --only {args.only}", file=sys.stderr)
            return 2

    rows = [run_suite(s) for s in suites]

    if args.json:
        print(json.dumps({"suites": rows}, indent=2))
    else:
        print_table(rows)

    fail_suites = sum(1 for r in rows if r["status"] != "PASS")
    return 0 if fail_suites == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
