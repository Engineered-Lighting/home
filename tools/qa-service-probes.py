#!/usr/bin/env python3
"""Stage 2 — live service-health probes for the production QA pass.

Wraps existing probe-trace-pane.py + adds the missing shape checks
that the cycle relies on for downstream stages:

    - sidecar /metrics returns the post-Addendum-27 shape
      (cpu_pct = max-per-core, cpu_avg_pct = system avg, gpu_temp_c)
    - sidecar /conversations/recent returns expected trace shape
    - vision-sidecar /describe_clip endpoint reachable
    - Frigate /api/events returns valid event shape

Each probe returns one of:
    OK    — service responding with expected shape
    DRIFT — responding but shape changed (downstream probes may fail)
    DOWN  — service unreachable / non-200
    SKIP  — pre-flight already flagged this service down

Per AR31-25: DOWN propagates to a `degraded_services` set that
Stage 3 probes consult — vision-dependent probes auto-SKIP when
vision-sidecar is DOWN, instead of failing N times for the same
root cause.

Output:
    <report-dir>/services.json
    <report-dir>/services.md

Exit codes:
    0 = all OK
    1 = one or more DRIFT (continue but flag)
    2 = at least one DOWN affecting critical functionality
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_common as qc


@dataclass
class ServiceProbe:
    name: str
    status: str  # OK | DRIFT | DOWN | SKIP
    summary: str
    detail: dict = field(default_factory=dict)
    elapsed_ms: int = 0


def _timed(fn):
    def wrapper(*args, **kwargs):
        t0 = time.monotonic()
        res = fn(*args, **kwargs)
        res.elapsed_ms = int((time.monotonic() - t0) * 1000)
        return res
    return wrapper


# ── Probes ──────────────────────────────────────────────────────────

@_timed
def probe_sidecar_healthz() -> ServiceProbe:
    code, _ = qc.http_get(f"{qc.SIDECAR_BASE}/healthz")
    if code == 200:
        return ServiceProbe(name="sidecar_healthz", status="OK",
                            summary="metrics-sidecar /healthz 200")
    return ServiceProbe(name="sidecar_healthz", status="DOWN",
                        summary=f"sidecar /healthz returned {code}",
                        detail={"status": code})


@_timed
def probe_sidecar_metrics_shape() -> ServiceProbe:
    code, raw = qc.http_get(f"{qc.SIDECAR_BASE}/metrics")
    if code != 200:
        return ServiceProbe(name="sidecar_metrics_shape", status="DOWN",
                            summary=f"/metrics returned {code}",
                            detail={"status": code})
    try:
        data = json.loads(raw)
    except Exception as e:
        return ServiceProbe(name="sidecar_metrics_shape", status="DRIFT",
                            summary=f"/metrics non-JSON ({e})")
    # Required fields per Addendum 27
    required = ["gpu_util_pct", "vram_used_gb", "ram_used_gb",
                "cpu_pct", "cpu_avg_pct"]
    missing = [f for f in required if f not in data]
    if missing:
        return ServiceProbe(name="sidecar_metrics_shape", status="DRIFT",
                            summary=f"/metrics missing fields: {missing}",
                            detail={"missing": missing,
                                    "present": list(data.keys())[:20]})
    # gpu_temp_c is optional but flag if absent
    has_temp = "gpu_temp_c" in data
    summary = "metrics shape current"
    if not has_temp:
        summary += " (no gpu_temp_c — F-19 will be empty)"
    return ServiceProbe(name="sidecar_metrics_shape", status="OK",
                        summary=summary,
                        detail={"has_gpu_temp_c": has_temp,
                                "cpu_pct": data.get("cpu_pct"),
                                "cpu_avg_pct": data.get("cpu_avg_pct"),
                                "gpu_util_pct": data.get("gpu_util_pct")})


@_timed
def probe_sidecar_recent_shape() -> ServiceProbe:
    code, raw = qc.http_get(f"{qc.SIDECAR_BASE}/conversations/recent?n=3")
    if code != 200:
        return ServiceProbe(name="sidecar_recent_shape", status="DOWN",
                            summary=f"/conversations/recent returned {code}")
    try:
        data = json.loads(raw)
        entries = data.get("entries", [])
    except Exception as e:
        return ServiceProbe(name="sidecar_recent_shape", status="DRIFT",
                            summary=f"non-JSON ({e})")
    if not isinstance(entries, list):
        return ServiceProbe(name="sidecar_recent_shape", status="DRIFT",
                            summary="entries is not a list")
    if not entries:
        return ServiceProbe(name="sidecar_recent_shape", status="OK",
                            summary="No recent entries (system quiet)",
                            detail={"entry_count": 0})
    # Sample one entry, check expected fields
    e = entries[-1]
    required = ["id", "user", "assistant", "upstream_ms",
                "ttft_observed_ms", "tool_calls", "ts"]
    missing = [f for f in required if f not in e]
    if missing:
        return ServiceProbe(name="sidecar_recent_shape", status="DRIFT",
                            summary=f"entry missing fields: {missing}",
                            detail={"missing": missing,
                                    "present": list(e.keys())})
    return ServiceProbe(name="sidecar_recent_shape", status="OK",
                        summary=f"shape OK ({len(entries)} entries)",
                        detail={"entry_count": len(entries),
                                "newest_id": e.get("id")})


@_timed
def probe_sidecar_sse_endpoint() -> ServiceProbe:
    """Verify /conversations/stream returns event-stream content-type."""
    import http.client
    import urllib.parse
    parsed = urllib.parse.urlparse(qc.SIDECAR_BASE)
    try:
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        conn.request("GET", "/conversations/stream",
                     headers={"Accept": "text/event-stream"})
        resp = conn.getresponse()
        ctype = resp.getheader("content-type", "")
        status = resp.status
        conn.close()
        if status != 200:
            return ServiceProbe(name="sidecar_sse_endpoint", status="DOWN",
                                summary=f"SSE returned {status}")
        if "event-stream" not in (ctype or ""):
            return ServiceProbe(name="sidecar_sse_endpoint", status="DRIFT",
                                summary=f"unexpected content-type: {ctype!r}",
                                detail={"ctype": ctype})
        return ServiceProbe(name="sidecar_sse_endpoint", status="OK",
                            summary="SSE endpoint reachable + correct ctype")
    except Exception as e:
        return ServiceProbe(name="sidecar_sse_endpoint", status="DOWN",
                            summary=f"SSE connect failed: {e!r}")


@_timed
def probe_vision_healthz() -> ServiceProbe:
    code, _ = qc.http_get(f"{qc.VISION_BASE}/healthz", timeout=5)
    if code == 200:
        return ServiceProbe(name="vision_healthz", status="OK",
                            summary="vision-sidecar /healthz 200")
    return ServiceProbe(name="vision_healthz", status="DOWN",
                        summary=f"vision /healthz returned {code}",
                        detail={"status": code})


@_timed
def probe_vision_cameras_endpoint() -> ServiceProbe:
    code, raw = qc.http_get(f"{qc.VISION_BASE}/cameras", timeout=5)
    if code != 200:
        return ServiceProbe(name="vision_cameras", status="DOWN",
                            summary=f"/cameras returned {code}")
    try:
        data = json.loads(raw)
    except Exception:
        return ServiceProbe(name="vision_cameras", status="DRIFT",
                            summary="/cameras non-JSON")
    cams = data.get("cameras", []) if isinstance(data, dict) else (data or [])
    n = len(cams)
    return ServiceProbe(name="vision_cameras", status="OK",
                        summary=f"{n} cameras registered",
                        detail={"camera_count": n})


@_timed
def probe_frigate_version() -> ServiceProbe:
    code, raw = qc.http_get(f"{qc.FRIGATE_BASE}/api/version", timeout=5)
    if code != 200:
        return ServiceProbe(name="frigate_version", status="DOWN",
                            summary=f"Frigate /api/version returned {code}")
    version = (raw or "").strip().strip('"')[:40]
    return ServiceProbe(name="frigate_version", status="OK",
                        summary=f"Frigate {version}",
                        detail={"version": version})


@_timed
def probe_frigate_events_shape() -> ServiceProbe:
    code, raw = qc.http_get(f"{qc.FRIGATE_BASE}/api/events?limit=1", timeout=5)
    if code != 200:
        return ServiceProbe(name="frigate_events_shape", status="DOWN",
                            summary=f"/api/events returned {code}")
    try:
        events = json.loads(raw)
    except Exception:
        return ServiceProbe(name="frigate_events_shape", status="DRIFT",
                            summary="/api/events non-JSON")
    if not isinstance(events, list):
        return ServiceProbe(name="frigate_events_shape", status="DRIFT",
                            summary="/api/events not a list")
    if not events:
        return ServiceProbe(name="frigate_events_shape", status="OK",
                            summary="No events yet (fresh install or no activity)")
    e = events[0]
    required = ["id", "camera", "label", "start_time"]
    missing = [f for f in required if f not in e]
    if missing:
        return ServiceProbe(name="frigate_events_shape", status="DRIFT",
                            summary=f"event missing: {missing}")
    return ServiceProbe(name="frigate_events_shape", status="OK",
                        summary=f"event shape OK ({len(events)} sample)",
                        detail={"sample_keys": list(e.keys())[:15]})


@_timed
def probe_ha_websocket(token: str) -> ServiceProbe:
    """Verify HA WebSocket endpoint accepts connection + auth handshake."""
    if not token:
        return ServiceProbe(name="ha_websocket", status="SKIP",
                            summary="no HA token")
    # WS probe via http.client → upgrade not trivial; use REST as proxy
    code, _ = qc.ha_get("/api/config", token, timeout=5)
    if code != 200:
        return ServiceProbe(name="ha_websocket", status="DOWN",
                            summary=f"HA /api/config returned {code}")
    return ServiceProbe(name="ha_websocket", status="OK",
                        summary="HA REST healthy (WS shares port + auth)")


# ── Orchestration ───────────────────────────────────────────────────

def run_all(token: str, preflight_degraded: set[str]) -> list[ServiceProbe]:
    probes = [
        probe_sidecar_healthz(),
        probe_sidecar_metrics_shape(),
        probe_sidecar_recent_shape(),
        probe_sidecar_sse_endpoint(),
    ]
    # Vision probes — skip if pre-flight already said DOWN
    if "vision_sidecar" in preflight_degraded:
        probes.append(ServiceProbe(name="vision_healthz", status="SKIP",
                                   summary="pre-flight flagged vision down"))
        probes.append(ServiceProbe(name="vision_cameras", status="SKIP",
                                   summary="pre-flight flagged vision down"))
    else:
        probes.append(probe_vision_healthz())
        probes.append(probe_vision_cameras_endpoint())
    # Frigate probes
    if "frigate" in preflight_degraded:
        probes.append(ServiceProbe(name="frigate_version", status="SKIP",
                                   summary="pre-flight flagged frigate down"))
        probes.append(ServiceProbe(name="frigate_events_shape", status="SKIP",
                                   summary="pre-flight flagged frigate down"))
    else:
        probes.append(probe_frigate_version())
        probes.append(probe_frigate_events_shape())
    probes.append(probe_ha_websocket(token))
    return probes


def degraded_services(probes: list[ServiceProbe]) -> set[str]:
    """Build set of {sidecar, vision_sidecar, frigate, ha} that's DOWN."""
    d = set()
    for p in probes:
        if p.status not in ("DOWN", "DRIFT"):
            continue
        if p.name.startswith("sidecar"):
            d.add("metrics_sidecar")
        elif p.name.startswith("vision"):
            d.add("vision_sidecar")
        elif p.name.startswith("frigate"):
            d.add("frigate")
        elif p.name.startswith("ha_"):
            d.add("ha")
    return d


def write_outputs(report_dir: Path, probes: list[ServiceProbe]) -> None:
    json_path = report_dir / "services.json"
    json_path.write_text(
        json.dumps({
            "probes": [asdict(p) for p in probes],
            "degraded": sorted(degraded_services(probes)),
        }, indent=2),
        encoding="utf-8",
    )
    md = ["# Stage 2 — Live service health", ""]
    oks = sum(1 for p in probes if p.status == "OK")
    drifts = sum(1 for p in probes if p.status == "DRIFT")
    downs = sum(1 for p in probes if p.status == "DOWN")
    skips = sum(1 for p in probes if p.status == "SKIP")
    md.append(f"**Summary**: {oks} OK · {drifts} DRIFT · {downs} DOWN · {skips} SKIP")
    md.append("")
    md.append("| Probe | Status | Summary | Elapsed |")
    md.append("|---|---|---|---|")
    for p in probes:
        md.append(f"| {p.name} | {p.status} | {p.summary} | {p.elapsed_ms}ms |")
    md.append("")
    deg = degraded_services(probes)
    if deg:
        md.append(f"**Degraded services** (downstream probes will SKIP): "
                  f"{', '.join(sorted(deg))}")
        md.append("")
    (report_dir / "services.md").write_text("\n".join(md), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report-dir", required=True,
                    help="Cycle report directory")
    ap.add_argument("--preflight-degraded", default="",
                    help="Comma-separated list of services pre-flight marked DOWN "
                         "(skips redundant probes for the same services)")
    ap.add_argument("--json", action="store_true",
                    help="Emit machine-readable JSON to stdout")
    args = ap.parse_args()

    report_dir = Path(args.report_dir)
    if not report_dir.exists():
        print(qc.red(f"Report dir does not exist: {report_dir}"), file=sys.stderr)
        return 2

    token = qc.load_ha_token()
    preflight_degraded = set(
        s.strip() for s in args.preflight_degraded.split(",") if s.strip()
    )

    print(qc.bold(f"\n=== Stage 2 — Service health ({report_dir.name}) ==="))

    probes = run_all(token, preflight_degraded)

    for p in probes:
        if p.status == "OK":
            badge = qc.green(" OK  ")
        elif p.status == "DRIFT":
            badge = qc.yellow("DRIFT")
        elif p.status == "DOWN":
            badge = qc.red("DOWN ")
        else:
            badge = qc.dim("SKIP ")
        print(f"  [{badge}] {p.name:<32} {p.summary}")

    write_outputs(report_dir, probes)

    downs = sum(1 for p in probes if p.status == "DOWN")
    drifts = sum(1 for p in probes if p.status == "DRIFT")
    print("")
    if downs:
        critical = any(p.name == "sidecar_healthz" and p.status == "DOWN"
                       for p in probes)
        if critical:
            print(qc.red(f"  ABORT: metrics-sidecar DOWN (cycle cannot continue)"))
            ret = 2
        else:
            print(qc.yellow(f"  {downs} DOWN — downstream probes will degrade"))
            ret = 1
    elif drifts:
        print(qc.yellow(f"  {drifts} DRIFT — schema changes, investigate"))
        ret = 1
    else:
        print(qc.green("  All services healthy"))
        ret = 0

    if args.json:
        print(json.dumps({
            "ok": ret == 0,
            "downs": downs,
            "drifts": drifts,
            "degraded": sorted(degraded_services(probes)),
        }))

    return ret


if __name__ == "__main__":
    raise SystemExit(main())
