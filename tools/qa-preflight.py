#!/usr/bin/env python3
"""Stage 0 — production-QA pre-flight (Addendum 31).

Catches the silent killers before the cycle wastes time on stages
that will all fail for the same root cause. This session's lesson:
Jarvis was muted; every conversation API call returned empty
speech; would have flagged 17 probes RED when the real fix was a
single mute toggle.

Pre-flight checks (each returns OK / WARN / FAIL with concrete
remediation):

    1.  HA token valid                          (FAIL = abort)
    2.  HA core healthy                         (FAIL = abort)
    3.  HA conversation agent ready (AR31-16)   (FAIL = abort)
    4.  home.exe running                        (WARN = stage 5 will skip)
    5.  home.exe binary fresh vs JSX            (WARN only)
    6.  Jarvis mute state                       (FAIL unless --clear-mute-if-blocking-only)
    7.  metrics-sidecar 200                     (FAIL = abort)
    8.  vision-sidecar 200                      (WARN = vision probes skip)
    9.  Frigate 200                             (WARN = frigate probes skip)
    10. Subentry tool list current              (WARN = some probes skip)

Records original state for Stage 6 cleanup (AR31-3 / AR31-17):
    input_boolean.jarvis_muted_explicit
    input_boolean.movie_mode
    input_datetime.jarvis_mute_until
    Sim-mode flag (best-effort via localStorage probe)

Output:
    tools/reports/qa-<ts>/preflight.json  (machine-readable)
    tools/reports/qa-<ts>/preflight.md    (human-readable)

Exit codes (mirror orchestrator contract):
    0 = all OK or only WARN
    2 = at least one FAIL → orchestrator should abort

Usage:
    py -3 tools/qa-preflight.py --cycle-ts <ts>
    py -3 tools/qa-preflight.py --cycle-ts <ts> --clear-mute-if-blocking-only
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

# Allow `import qa_common` when invoked from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_common as qc


@dataclass
class CheckResult:
    name: str
    status: str  # OK | WARN | FAIL
    summary: str
    remediation: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: int = 0


def _timed(fn):
    """Decorator: wrap a check, measure elapsed ms."""
    def wrapper(*args, **kwargs):
        t0 = time.monotonic()
        res = fn(*args, **kwargs)
        res.elapsed_ms = int((time.monotonic() - t0) * 1000)
        return res
    return wrapper


# ── Individual checks ───────────────────────────────────────────────

@_timed
def check_ha_token_valid(token: str) -> CheckResult:
    if not token:
        return CheckResult(
            name="ha_token_valid", status="FAIL",
            summary="No HA bearer token resolved",
            remediation="Set env HA_TOKEN, or write ~/.ha-token, or "
                        "ensure 'ssh hav-ubuntu' works to read /opt/home-ai-voice/.env",
        )
    code, _ = qc.ha_get("/api/", token, timeout=5)
    if code == 200:
        return CheckResult(
            name="ha_token_valid", status="OK",
            summary="Token authenticates against HA REST",
        )
    if code == 401:
        return CheckResult(
            name="ha_token_valid", status="FAIL",
            summary="HA returned 401 (token rejected)",
            remediation="Rotate HA long-lived access token; "
                        "update env HA_TOKEN or ~/.ha-token",
        )
    return CheckResult(
        name="ha_token_valid", status="FAIL",
        summary=f"HA returned {code} (unexpected)",
        remediation="Verify HA_BASE points at running HA core",
        detail={"status_code": code},
    )


@_timed
def check_ha_core_healthy(token: str) -> CheckResult:
    code, raw = qc.ha_get("/api/states", token, timeout=10)
    if code != 200:
        return CheckResult(
            name="ha_core_healthy", status="FAIL",
            summary=f"GET /api/states returned {code}",
            remediation="HA core may be restarting; wait + retry",
        )
    try:
        states = json.loads(raw)
        n = len(states)
    except Exception:
        return CheckResult(
            name="ha_core_healthy", status="FAIL",
            summary="GET /api/states returned non-JSON",
            remediation="HA may be mid-startup",
        )
    if n < 100:
        return CheckResult(
            name="ha_core_healthy", status="WARN",
            summary=f"Only {n} entities — HA may still be loading integrations",
            remediation="Wait 30s + re-run pre-flight",
            detail={"entity_count": n},
        )
    return CheckResult(
        name="ha_core_healthy", status="OK",
        summary=f"{n} entities present",
        detail={"entity_count": n},
    )


@_timed
def check_ha_conversation_agent_ready(token: str, agent_id: str) -> CheckResult:
    """AR31-16: HA can be 200 on /api/ while the conversation agent is
    still loading. Probe the agent specifically before scenarios run."""
    if not agent_id:
        return CheckResult(
            name="ha_conversation_agent_ready", status="WARN",
            summary="No agent_id auto-resolved",
            remediation="Probes will fall back to HA's default agent (no custom prompt)",
        )
    # Try a no-op-ish list of states for an agent-related entity.
    # The reliable probe: does the agent_id entity exist in HA states?
    state = qc.ha_state(agent_id, token)
    if state is None:
        return CheckResult(
            name="ha_conversation_agent_ready", status="FAIL",
            summary=f"agent_id {agent_id} not found in /api/states",
            remediation="Integration not yet loaded. Wait 10s + re-run. "
                        "If persists, run 'ha core check' on HAOS to find load errors.",
        )
    return CheckResult(
        name="ha_conversation_agent_ready", status="OK",
        summary=f"agent_id {agent_id} present + state={state.get('state', 'unknown')}",
        detail={"agent_id": agent_id, "state": state.get("state", "")},
    )


@_timed
def check_home_exe_running() -> CheckResult:
    try:
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "(Get-Process home -ErrorAction SilentlyContinue | Measure-Object).Count"],
            capture_output=True, text=True, timeout=5,
        )
        n = (res.stdout or "0").strip()
        if n == "0":
            return CheckResult(
                name="home_exe_running", status="WARN",
                summary="home.exe not running",
                remediation="Launch C:/Claude/home/app/src-tauri/target/release/home.exe; "
                            "Stage 4 (screenshots) will SKIP without it.",
                detail={"process_count": 0},
            )
        return CheckResult(
            name="home_exe_running", status="OK",
            summary=f"home.exe running ({n} instance{'s' if n != '1' else ''})",
            detail={"process_count": int(n)},
        )
    except Exception as e:
        return CheckResult(
            name="home_exe_running", status="WARN",
            summary=f"Could not check home.exe ({e})",
            remediation="PowerShell may be unavailable; Stage 4 will SKIP",
        )


@_timed
def check_home_exe_binary_fresh() -> CheckResult:
    """Compare home.exe mtime against newest JSX file. WARN if stale."""
    exe = qc.REPO_ROOT / "app" / "src-tauri" / "target" / "release" / "home.exe"
    src = qc.REPO_ROOT / "app" / "src"
    if not exe.exists():
        return CheckResult(
            name="home_exe_binary_fresh", status="WARN",
            summary="home.exe binary not found at expected path",
            remediation="cargo build --release in app/src-tauri",
            detail={"expected_path": str(exe)},
        )
    if not src.exists():
        return CheckResult(
            name="home_exe_binary_fresh", status="OK",
            summary="JSX src dir absent — skipping freshness check",
        )
    exe_mtime = exe.stat().st_mtime
    newest_jsx = 0.0
    newest_jsx_path = ""
    for p in src.glob("**/*.jsx"):
        if p.stat().st_mtime > newest_jsx:
            newest_jsx = p.stat().st_mtime
            newest_jsx_path = str(p)
    for p in src.glob("**/*.js"):
        if p.stat().st_mtime > newest_jsx:
            newest_jsx = p.stat().st_mtime
            newest_jsx_path = str(p)
    if newest_jsx > exe_mtime:
        gap = int(newest_jsx - exe_mtime)
        return CheckResult(
            name="home_exe_binary_fresh", status="WARN",
            summary=f"home.exe is {gap}s older than latest JSX",
            remediation=f"cargo build --release — newest source: {newest_jsx_path}",
            detail={"exe_mtime": exe_mtime, "newest_jsx_mtime": newest_jsx,
                    "newest_jsx_path": newest_jsx_path, "gap_seconds": gap},
        )
    return CheckResult(
        name="home_exe_binary_fresh", status="OK",
        summary="home.exe newer than all JSX",
    )


@_timed
def check_jarvis_mute_state(token: str, clear_if_blocking_only: bool,
                            allow_movie_override: bool) -> CheckResult:
    """AR31-17: explicit muted+movie_mode is hostile to override.

    Reads mute signals + the composite sensor:
        binary_sensor.jarvis_muted_effective_2  (the canonical aggregate)
        input_boolean.jarvis_muted_explicit
        input_boolean.homeai_movie
        input_boolean.jarvis_auto_mute_tv
        media_player.lg_tv
        input_datetime.jarvis_mute_until
    """
    composite = qc.ha_state("binary_sensor.jarvis_muted_effective_2", token)
    explicit = qc.ha_state("input_boolean.jarvis_muted_explicit", token)
    movie = qc.ha_state("input_boolean.homeai_movie", token)
    tv_auto = qc.ha_state("input_boolean.jarvis_auto_mute_tv", token)
    tv = qc.ha_state("media_player.lg_tv", token)
    until = qc.ha_state("input_datetime.jarvis_mute_until", token)

    snapshot = {
        "binary_sensor.jarvis_muted_effective_2": composite.get("state") if composite else None,
        "input_boolean.jarvis_muted_explicit": explicit.get("state") if explicit else None,
        "input_boolean.homeai_movie": movie.get("state") if movie else None,
        "input_boolean.jarvis_auto_mute_tv": tv_auto.get("state") if tv_auto else None,
        "media_player.lg_tv": tv.get("state") if tv else None,
        "input_datetime.jarvis_mute_until": until.get("state") if until else None,
    }

    composite_state = (composite or {}).get("state", "unknown")
    if composite_state != "on":
        return CheckResult(
            name="jarvis_mute_state", status="OK",
            summary="Jarvis is NOT muted",
            detail={"snapshot": snapshot},
        )

    # Muted. What's the cause?
    explicit_on = (explicit or {}).get("state") == "on"
    movie_on = (movie or {}).get("state") == "on"
    tv_auto_active = (
        (tv_auto or {}).get("state") == "on"
        and (tv or {}).get("state") in {"on", "playing", "paused", "buffering"}
    )
    reason = (composite or {}).get("attributes", {}).get("reason", "")

    if movie_on and not allow_movie_override:
        return CheckResult(
            name="jarvis_mute_state", status="FAIL",
            summary=f"Jarvis is muted via movie_mode (reason: {reason!r})",
            remediation="Movie mode reflects user intent. Wait for movie to end, "
                        "OR pass --allow-movie-override to override (rude during a movie!).",
            detail={"snapshot": snapshot, "auto_clearable": False},
        )

    if tv_auto_active:
        return CheckResult(
            name="jarvis_mute_state", status="FAIL",
            summary=f"Jarvis is muted via TV-active auto-mute (LG TV is {snapshot['media_player.lg_tv']})",
            remediation="TV-active auto-mute reflects live household context. Turn TV off, "
                        "wait for the grace window to clear, or manually disable "
                        "input_boolean.jarvis_auto_mute_tv if that is intentional.",
            detail={"snapshot": snapshot, "auto_clearable": False},
        )

    if explicit_on and clear_if_blocking_only:
        # Auto-clear ONLY this signal; never touch movie_mode here.
        code, _ = qc.ha_service_call(
            "input_boolean", "turn_off",
            {"entity_id": "input_boolean.jarvis_muted_explicit"},
            token, timeout=5,
        )
        if code in (200, 201):
            return CheckResult(
                name="jarvis_mute_state", status="OK",
                summary="Jarvis was muted (manual); cleared per --clear-mute-if-blocking-only",
                detail={"snapshot": snapshot, "auto_cleared": True},
            )
        return CheckResult(
            name="jarvis_mute_state", status="FAIL",
            summary=f"Tried to clear mute, HA returned {code}",
            remediation="Manually clear input_boolean.jarvis_muted_explicit via HA UI",
            detail={"snapshot": snapshot, "clear_status": code},
        )

    return CheckResult(
        name="jarvis_mute_state", status="FAIL",
        summary=f"Jarvis is muted ({reason or 'reason unknown'})",
        remediation="Clear Jarvis mute manually, OR re-run with "
                    "--clear-mute-if-blocking-only (only clears explicit toggle, "
                    "never touches movie_mode or TV-active grace)",
        detail={"snapshot": snapshot, "auto_clearable": explicit_on},
    )


@_timed
def check_metrics_sidecar() -> CheckResult:
    code, _ = qc.http_get(f"{qc.SIDECAR_BASE}/healthz")
    if code != 200:
        return CheckResult(
            name="metrics_sidecar", status="FAIL",
            summary=f"metrics-sidecar healthz returned {code}",
            remediation="Restart hav-metrics-sidecar container on Ubuntu AI box",
            detail={"status": code},
        )
    code, raw = qc.http_get(f"{qc.SIDECAR_BASE}/conversations/recent?n=1")
    if code != 200:
        return CheckResult(
            name="metrics_sidecar", status="WARN",
            summary=f"healthz OK but /conversations/recent returned {code}",
            remediation="Routing-log assertions in Stage 3 will fail",
            detail={"recent_status": code},
        )
    # Verify the new metric shape from Addendum 27 (cpu_pct + cpu_avg_pct)
    code2, raw2 = qc.http_get(f"{qc.SIDECAR_BASE}/metrics")
    has_temp = False
    has_cpu_max = False
    if code2 == 200:
        try:
            j = json.loads(raw2)
            has_temp = "gpu_temp_c" in j
            has_cpu_max = "cpu_pct" in j and "cpu_avg_pct" in j
        except Exception:
            pass
    summary = "metrics-sidecar healthy"
    if not has_temp:
        summary += " (no gpu_temp_c — F-19 temp pill will be empty)"
    if not has_cpu_max:
        summary += " (no cpu_pct/cpu_avg_pct split — old sidecar)"
    return CheckResult(
        name="metrics_sidecar", status="OK",
        summary=summary,
        detail={"has_temp": has_temp, "has_cpu_max_per_core": has_cpu_max},
    )


@_timed
def check_vision_sidecar() -> CheckResult:
    code, _ = qc.http_get(f"{qc.VISION_BASE}/healthz", timeout=5)
    if code != 200:
        return CheckResult(
            name="vision_sidecar", status="WARN",
            summary=f"vision-sidecar returned {code}",
            remediation="Restart hav-vision-sidecar container; vision probes will SKIP",
            detail={"status": code},
        )
    return CheckResult(
        name="vision_sidecar", status="OK",
        summary="vision-sidecar healthy",
    )


@_timed
def check_frigate() -> CheckResult:
    code, raw = qc.http_get(f"{qc.FRIGATE_BASE}/api/version", timeout=5)
    if code != 200:
        return CheckResult(
            name="frigate", status="WARN",
            summary=f"Frigate returned {code}",
            remediation="Check Frigate addon in HA; find_clips/face_rec probes will SKIP",
            detail={"status": code},
        )
    version = (raw or "").strip().strip('"')[:40]
    return CheckResult(
        name="frigate", status="OK",
        summary=f"Frigate {version} reachable",
        detail={"version": version},
    )


@_timed
def check_subentry_tools_current() -> CheckResult:
    """AR31-24: detect drift but don't auto-fix. Probes that need
    specific tools will SKIP if the tool is missing from the subentry."""
    import re
    import tempfile

    try:
        # Mirror the patch-subentry-function-tools.py approach: SCP
        # the storage file to a local temp, parse with regex (no yaml
        # dep needed — we only care about tool *names*).
        with tempfile.NamedTemporaryFile(
            suffix=".json", delete=False
        ) as tmp:
            tmp_path = tmp.name
        try:
            scp = subprocess.run(
                ["scp", "-P", "22222",
                 "root@homeassistant.local:/config/.storage/core.config_entries",
                 tmp_path],
                capture_output=True, text=True, timeout=15,
            )
            if scp.returncode != 0:
                return CheckResult(
                    name="subentry_tools_current", status="WARN",
                    summary="Could not SCP core.config_entries",
                    remediation="Check SSH key auth: ssh -p 22222 root@homeassistant.local",
                    detail={"stderr": (scp.stderr or "")[:200]},
                )
            with open(tmp_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        finally:
            try:
                Path(tmp_path).unlink()
            except Exception:
                pass

        # Find the integration subentries + extract tool names via regex.
        names = set()
        for entry in data.get("data", {}).get("entries", []):
            if entry.get("domain") != "extended_openai_conversation":
                continue
            for sub in entry.get("subentries", []):
                funcs_yaml = sub.get("data", {}).get("functions", "") or ""
                # Match `name: foo_bar` lines (ignore quoted strings, comments).
                # The function_tools YAML uses `- spec:\n    name: tool_name`.
                names.update(
                    re.findall(
                        r"^\s*name:\s*([a-z_][a-z0-9_]*)\s*$",
                        funcs_yaml,
                        flags=re.MULTILINE,
                    )
                )
        expected = {"recap", "find_clips", "get_history", "invoke_script",
                    "describe_clip", "areas_in_home", "entities_in_area",
                    "find_entity"}
        missing = expected - names
        if missing:
            return CheckResult(
                name="subentry_tools_current", status="WARN",
                summary=f"Subentry missing {len(missing)} expected tools: "
                        + ", ".join(sorted(missing)),
                remediation="py -3 tools/patch-subentry-function-tools.py --apply "
                            "(then full HA restart, not reload_config_entry)",
                detail={"missing": sorted(missing), "present_count": len(names)},
            )
        return CheckResult(
            name="subentry_tools_current", status="OK",
            summary=f"All {len(expected)} flagship tools present in subentry "
                    f"({len(names)} total tools)",
            detail={"tool_count": len(names)},
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="subentry_tools_current", status="WARN",
            summary="SSH timeout reading subentry",
            remediation="Check HAOS SSH connectivity",
        )
    except Exception as e:
        return CheckResult(
            name="subentry_tools_current", status="WARN",
            summary=f"Subentry check failed: {e!r}",
        )


# ── Orchestration + output ──────────────────────────────────────────

def run_all_checks(token: str, agent_id: str,
                   clear_mute_if_blocking_only: bool,
                   allow_movie_override: bool) -> list[CheckResult]:
    return [
        check_ha_token_valid(token),
        check_ha_core_healthy(token),
        check_ha_conversation_agent_ready(token, agent_id),
        check_home_exe_running(),
        check_home_exe_binary_fresh(),
        check_jarvis_mute_state(token, clear_mute_if_blocking_only,
                                allow_movie_override),
        check_metrics_sidecar(),
        check_vision_sidecar(),
        check_frigate(),
        check_subentry_tools_current(),
    ]


def write_outputs(report_dir: Path, results: list[CheckResult],
                  token: str, agent_id: str) -> None:
    # JSON
    json_path = report_dir / "preflight.json"
    json_path.write_text(
        json.dumps({
            "checks": [asdict(r) for r in results],
            "agent_id": agent_id,
            "ha_token_resolved": bool(token),
        }, indent=2),
        encoding="utf-8",
    )
    # Markdown
    md = ["# Stage 0 — Pre-flight", ""]
    fails = [r for r in results if r.status == "FAIL"]
    warns = [r for r in results if r.status == "WARN"]
    oks = [r for r in results if r.status == "OK"]
    md.append(f"**Summary**: {len(oks)} OK · {len(warns)} WARN · {len(fails)} FAIL")
    md.append("")
    md.append("| Check | Status | Summary | Elapsed |")
    md.append("|---|---|---|---|")
    for r in results:
        md.append(f"| {r.name} | {r.status} | {r.summary} | {r.elapsed_ms}ms |")
    md.append("")
    if fails:
        md.append("## Remediation (FAIL)")
        for r in fails:
            md.append(f"- **{r.name}**: {r.remediation}")
        md.append("")
    if warns:
        md.append("## Remediation (WARN)")
        for r in warns:
            md.append(f"- **{r.name}**: {r.remediation}")
        md.append("")
    md.append(f"_agent_id: `{agent_id or '(none — using HA default agent)'}`_")
    (report_dir / "preflight.md").write_text("\n".join(md), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cycle-ts", required=False, default=None,
                    help="Cycle timestamp (e.g. 2026-05-18-143245); auto-generates if absent")
    ap.add_argument("--report-dir", required=False, default=None,
                    help="Override report dir (default: tools/reports/qa-<ts>/)")
    ap.add_argument("--clear-mute-if-blocking-only", action="store_true",
                    help="AR31-17: clear input_boolean.jarvis_muted_explicit if muted "
                         "(NEVER touches movie_mode or TV-active grace)")
    ap.add_argument("--allow-movie-override", action="store_true",
                    help="Allow cycle to run even when movie_mode is on (hostile during movies)")
    ap.add_argument("--json", action="store_true",
                    help="Emit machine-readable JSON to stdout")
    args = ap.parse_args()

    cycle_ts = args.cycle_ts or qc.make_cycle_ts()
    report_dir = Path(args.report_dir) if args.report_dir else qc.make_report_dir(cycle_ts)
    report_dir.mkdir(parents=True, exist_ok=True)

    token = qc.load_ha_token()
    agent_id = qc.load_agent_id(token) if token else ""

    print(qc.bold(f"\n=== Stage 0 — Pre-flight (cycle {cycle_ts}) ==="))

    results = run_all_checks(
        token, agent_id,
        args.clear_mute_if_blocking_only,
        args.allow_movie_override,
    )

    # Pretty-print to stdout
    for r in results:
        if r.status == "OK":
            badge = qc.green(" OK ")
        elif r.status == "WARN":
            badge = qc.yellow("WARN")
        else:
            badge = qc.red("FAIL")
        print(f"  [{badge}] {r.name:<32} {r.summary}")
        if r.status == "FAIL" and r.remediation:
            print(f"          {qc.dim('→ ' + r.remediation)}")

    write_outputs(report_dir, results, token, agent_id)

    fails = sum(1 for r in results if r.status == "FAIL")
    warns = sum(1 for r in results if r.status == "WARN")
    print("")
    if fails:
        print(qc.red(f"  ABORT: {fails} pre-flight FAIL"))
    elif warns:
        print(qc.yellow(f"  OK with {warns} WARN — proceed with degraded coverage"))
    else:
        print(qc.green("  All pre-flight checks GREEN"))
    print(f"  Report: {report_dir / 'preflight.md'}")

    if args.json:
        print(json.dumps({
            "fails": fails, "warns": warns,
            "oks": sum(1 for r in results if r.status == "OK"),
            "cycle_ts": cycle_ts,
            "report_dir": str(report_dir),
            "agent_id": agent_id,
        }))

    return 2 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
