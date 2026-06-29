"""Shared helpers for the production QA pass (Addendum 31).

All qa-*.py scripts import from here so token resolution, conv_id
generation, sidecar/HA endpoints, and SSH conventions are consistent
across stages.

Conventions:
    QA conv_id format: ``qa-cycle-<ts>-<stage>-<probe_id>``
    Report directory:  ``tools/reports/qa-<ts>/``
    Checkpoint file:   ``tools/reports/qa-<ts>/.checkpoint.json``
    State snapshot:    ``tools/reports/qa-<ts>/state-snapshot.json``

Per AR31-10: the qa-cycle prefix lets tools/qa-log-filter.py strip
QA-generated entries from /config/external_routing.log for clean
production analysis.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


# Windows cp1252 codec chokes on Unicode arrows/box-drawing. Reconfigure
# stdout/stderr to UTF-8 + replace errors so qa tooling runs cleanly in
# PowerShell, cmd, and CI. Idempotent — safe to import multiple times.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


REPO_ROOT = Path(r"C:/Claude/home").resolve()
REPORTS_DIR = REPO_ROOT / "tools" / "reports"

# ── Service endpoints (mirror Addendum 27 §V3 verified values) ──────
SIDECAR_BASE = "http://192.168.0.100:8092"
VISION_BASE = "http://192.168.0.100:8091"
HA_BASE = "http://192.168.0.125:8123"
FRIGATE_BASE = "http://192.168.0.125:5000"

# SSH targets per memory/home_lan_topology.md
HAOS_SSH = ["ssh", "-p", "22222", "root@homeassistant.local"]
UBUNTU_SSH = ["ssh", "hav-ubuntu"]


# ── Token + agent_id resolution (mirrors diagnose-identity.py) ──────
def load_ha_token() -> str:
    """Resolve HA bearer token from (1) env, (2) ~/.ha-token, (3) Ubuntu bridge .env."""
    env = os.environ.get("HA_TOKEN", "")
    if env:
        return env
    ha_token_file = Path.home() / ".ha-token"
    if ha_token_file.exists():
        return ha_token_file.read_text(encoding="utf-8").strip()
    try:
        res = subprocess.run(
            UBUNTU_SSH + ["grep", "-E", "^HA_TOKEN=", "/opt/home-ai-voice/.env"],
            capture_output=True, text=True, timeout=10,
        )
        for line in res.stdout.splitlines():
            if line.startswith("HA_TOKEN="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def load_agent_id(ha_token: str) -> str:
    """Resolve the extended_openai_conversation agent_id via HAOS ssh+jq.

    Returns "" if the integration isn't found; callers fall back to HA's
    default agent (which doesn't have our custom prompt — degraded).
    """
    env = os.environ.get("AGENT_ID", "")
    if env:
        return env
    try:
        r = subprocess.run(
            HAOS_SSH + [
                "jq -r '.data.entities[] | "
                "select(.platform == \"extended_openai_conversation\" "
                "and (.entity_id | startswith(\"conversation.\"))) | "
                ".entity_id' /config/.storage/core.entity_registry"
            ],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
        )
        for line in r.stdout.splitlines():
            line = line.strip()
            if line:
                return line
    except Exception:
        pass
    return ""


# ── conversation_id factory (AR31-10 routing-log tagging) ───────────
def qa_conv_id(cycle_ts: str, stage: str, probe_id: str) -> str:
    """Build a qa-cycle conversation_id that:

    (a) routing-log filter can identify (qa-cycle prefix)
    (b) is unique per probe per cycle (so dedup doesn't merge)
    (c) is human-scannable (cycle_ts + stage + probe in the id itself)

    Example: qa-cycle-2026-05-18-1432-stage3-U3
    """
    safe = f"{stage}-{probe_id}".replace("/", "_").replace(" ", "_")
    return f"qa-cycle-{cycle_ts}-{safe}"


def is_qa_conv_id(conv_id: str) -> bool:
    return bool(conv_id) and conv_id.startswith("qa-cycle-")


# ── HTTP helpers (consistent with probe-trace-pane.py shape) ────────
def http_get(url: str, headers: Optional[dict[str, str]] = None,
             timeout: int = 5) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def http_post(url: str, body: dict, headers: Optional[dict[str, str]] = None,
              timeout: int = 30) -> tuple[int, str]:
    data = json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def ha_get(path: str, token: str, timeout: int = 5) -> tuple[int, str]:
    """GET against HA REST with bearer token."""
    url = f"{HA_BASE}{path}"
    return http_get(url, headers={"Authorization": f"Bearer {token}"}, timeout=timeout)


def ha_post(path: str, body: dict, token: str, timeout: int = 30) -> tuple[int, str]:
    """POST against HA REST with bearer token."""
    url = f"{HA_BASE}{path}"
    return http_post(url, body, headers={"Authorization": f"Bearer {token}"}, timeout=timeout)


def ha_state(entity_id: str, token: str) -> Optional[dict]:
    """Return the parsed state dict for an entity, or None on miss/error."""
    code, raw = ha_get(f"/api/states/{entity_id}", token, timeout=5)
    if code != 200:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def ha_service_call(domain: str, service: str, data: dict,
                    token: str, return_response: bool = False,
                    timeout: int = 10) -> tuple[int, str]:
    """Call HA service via REST. Returns (status, raw body)."""
    path = f"/api/services/{domain}/{service}"
    if return_response:
        path += "?return_response"
    return ha_post(path, data, token, timeout=timeout)


# ── Report directory + checkpoint helpers (AR31-9 crash recovery) ───
def make_cycle_ts() -> str:
    """HHMMSS-resolution timestamp avoids AR31-29 minute-collision risk."""
    return time.strftime("%Y-%m-%d-%H%M%S")


def make_report_dir(cycle_ts: str) -> Path:
    d = REPORTS_DIR / f"qa-{cycle_ts}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "screenshots").mkdir(exist_ok=True)
    return d


@dataclass
class Checkpoint:
    """Written before+after every stage; qa-recovery.py uses this to
    detect orphaned cycles + run cleanup using the snapshot."""

    cycle_ts: str
    report_dir: str
    started_at: float
    stages_in_progress: list[str] = field(default_factory=list)
    stages_completed: list[str] = field(default_factory=list)
    state_snapshot: dict[str, Any] = field(default_factory=dict)
    cleanup_done: bool = False


def write_checkpoint(report_dir: Path, cp: Checkpoint) -> None:
    """Atomic write: write to .tmp + rename."""
    path = report_dir / ".checkpoint.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(cp), indent=2), encoding="utf-8")
    tmp.replace(path)


def read_checkpoint(report_dir: Path) -> Optional[Checkpoint]:
    path = report_dir / ".checkpoint.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Checkpoint(
            cycle_ts=data.get("cycle_ts", ""),
            report_dir=data.get("report_dir", ""),
            started_at=data.get("started_at", 0.0),
            stages_in_progress=data.get("stages_in_progress", []),
            stages_completed=data.get("stages_completed", []),
            state_snapshot=data.get("state_snapshot", {}),
            cleanup_done=data.get("cleanup_done", False),
        )
    except Exception:
        return None


def find_orphan_cycles() -> list[Path]:
    """Return report dirs whose checkpoint indicates an incomplete cycle."""
    orphans = []
    if not REPORTS_DIR.exists():
        return orphans
    for d in REPORTS_DIR.glob("qa-*"):
        if not d.is_dir():
            continue
        cp = read_checkpoint(d)
        if cp is None:
            continue
        if cp.cleanup_done:
            continue
        if cp.stages_in_progress:
            orphans.append(d)
        elif "stage6" not in cp.stages_completed and cp.stages_completed:
            orphans.append(d)
    return orphans


# ── Stage result envelope (consumed by report writer) ───────────────
@dataclass
class StageResult:
    name: str
    status: str  # PASS | DRIFT | FAIL | SKIP | ERROR | ABORT
    started_at: float
    elapsed_s: float = 0.0
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)  # relative paths


def write_stage_result(report_dir: Path, stage_id: str,
                       result: StageResult) -> Path:
    """Write the structured result + a human-readable .md for the stage."""
    json_path = report_dir / f"{stage_id}.json"
    json_path.write_text(json.dumps(asdict(result), indent=2, default=str),
                         encoding="utf-8")
    return json_path


def read_stage_result(report_dir: Path, stage_id: str) -> Optional[StageResult]:
    path = report_dir / f"{stage_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return StageResult(
            name=data.get("name", stage_id),
            status=data.get("status", "UNKNOWN"),
            started_at=data.get("started_at", 0.0),
            elapsed_s=data.get("elapsed_s", 0.0),
            summary=data.get("summary", ""),
            details=data.get("details", {}),
            artifacts=data.get("artifacts", []),
        )
    except Exception:
        return None


# ── Lightweight ANSI (degrade gracefully) ───────────────────────────
def _has_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("TERM") != "dumb"


def green(s: str) -> str:
    return f"\033[32m{s}\033[0m" if _has_color() else s


def red(s: str) -> str:
    return f"\033[31m{s}\033[0m" if _has_color() else s


def yellow(s: str) -> str:
    return f"\033[33m{s}\033[0m" if _has_color() else s


def dim(s: str) -> str:
    return f"\033[2m{s}\033[0m" if _has_color() else s


def bold(s: str) -> str:
    return f"\033[1m{s}\033[0m" if _has_color() else s


# ── Self-test (so this module can be smoke-tested) ──────────────────
def _self_test() -> int:
    """Tiny smoke test runnable as `py -3 tools/qa_common.py`."""
    fails = 0

    # conv_id factory
    cid = qa_conv_id("2026-05-18-1432-00", "stage3", "U3")
    assert cid == "qa-cycle-2026-05-18-1432-00-stage3-U3", cid
    assert is_qa_conv_id(cid)
    assert not is_qa_conv_id("not-a-qa-id")
    assert not is_qa_conv_id("")

    # Timestamp shape
    ts = make_cycle_ts()
    assert len(ts) == 17, ts  # 2026-05-18-143245 + dashes = 17
    assert ts.count("-") == 3, ts

    # Checkpoint roundtrip
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        rd = Path(td) / "qa-test"
        rd.mkdir()
        cp = Checkpoint(
            cycle_ts=ts, report_dir=str(rd),
            started_at=time.time(),
            state_snapshot={"input_boolean.jarvis_muted_explicit": "off"},
        )
        write_checkpoint(rd, cp)
        loaded = read_checkpoint(rd)
        assert loaded is not None
        assert loaded.cycle_ts == ts
        assert loaded.state_snapshot == {"input_boolean.jarvis_muted_explicit": "off"}
        # Stage result roundtrip
        sr = StageResult(name="stage0", status="PASS", started_at=time.time(),
                         elapsed_s=1.2, summary="ok")
        write_stage_result(rd, "stage0", sr)
        loaded_sr = read_stage_result(rd, "stage0")
        assert loaded_sr is not None
        assert loaded_sr.status == "PASS"

    print("1 pass · 0 fail")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(_self_test())
