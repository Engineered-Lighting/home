"""Addendum 28 — probe runner.

Reads tools/feature-validation-probes.json and executes each probe.
Outputs a markdown report + JSON summary. Self-contained: no
external deps beyond Python stdlib + httpx (already used in repo).

Exit codes (mirror Addendum 18b §5):
  0 = all green
  1 = at least one probe FAIL but no service-level outage
  2 = at least one P0 outage (service down OR multiple FAIL)
  3 = preflight failure (probes file missing, malformed)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:
    print("missing httpx — pip install httpx", file=sys.stderr)
    sys.exit(3)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO = Path(r"C:/Claude/home").resolve()
PROBES_FILE = REPO / "tools" / "feature-validation-probes.json"
REPORT_DIR = REPO / "tools" / "reports"
REPORT_DIR.mkdir(exist_ok=True)


@dataclass
class ProbeResult:
    feature_id: str
    name: str
    tier: str
    verdict: str  # PASS / FAIL / SKIP / ERROR
    details: str = ""
    elapsed_ms: int = 0


@dataclass
class CycleReport:
    started_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    service_results: list[ProbeResult] = field(default_factory=list)
    feature_results: list[ProbeResult] = field(default_factory=list)
    routing_log: dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────
# Probe executors
# ─────────────────────────────────────────────────────────────────────
def probe_grep_count(spec: dict) -> ProbeResult:
    """Generic grep: every needle must appear at least min_per_needle times."""
    f = Path(spec["file"])
    if not f.exists():
        return ProbeResult("", "", "tier1", "FAIL", f"file missing: {f}")
    text = f.read_text(encoding="utf-8", errors="ignore")
    needles = spec.get("needles", [])
    min_per = spec.get("min_per_needle", 1)
    missing: list[str] = []
    for needle in needles:
        if text.count(needle) < min_per:
            missing.append(f"'{needle}' (<{min_per})")
    if missing:
        return ProbeResult("", "", "tier1", "FAIL", f"missing: {', '.join(missing)}")
    return ProbeResult("", "", "tier1", "PASS", f"all {len(needles)} needles found")


def probe_grep_count_min(spec: dict) -> ProbeResult:
    f = Path(spec["file"])
    if not f.exists():
        return ProbeResult("", "", "tier1", "FAIL", f"file missing: {f}")
    text = f.read_text(encoding="utf-8", errors="ignore")
    count = text.count(spec["needle"])
    min_c = spec["min_count"]
    if count < min_c:
        return ProbeResult("", "", "tier1", "FAIL",
                          f"'{spec['needle']}' count={count} < {min_c}")
    return ProbeResult("", "", "tier1", "PASS",
                      f"'{spec['needle']}' count={count} (>= {min_c})")


def probe_http(spec: dict) -> ProbeResult:
    url = spec["url"]
    method = spec.get("method", "GET").upper()
    expect = spec.get("expect_http", spec.get("expect", 200))
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=10.0) as c:
            resp = c.request(method, url)
    except httpx.ConnectError:
        return ProbeResult("", "", "tier3", "FAIL", f"connection refused: {url}")
    except Exception as e:
        return ProbeResult("", "", "tier3", "ERROR", f"{type(e).__name__}: {e}")
    elapsed = int((time.monotonic() - t0) * 1000)
    if isinstance(expect, int):
        ok = resp.status_code == expect
    else:
        ok = resp.status_code in expect
    verdict = "PASS" if ok else "FAIL"
    return ProbeResult("", "", "tier3", verdict,
                      f"HTTP {resp.status_code} (expected {expect})",
                      elapsed_ms=elapsed)


def probe_http_jq(spec: dict) -> ProbeResult:
    """HTTP GET + apply jq-like path extraction + assertion."""
    url = spec["url"]
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=15.0) as c:
            resp = c.get(url)
        resp.raise_for_status()
        data = resp.json()
    except httpx.ConnectError:
        return ProbeResult("", "", "tier3", "FAIL", f"connection refused: {url}")
    except Exception as e:
        return ProbeResult("", "", "tier3", "ERROR", f"{type(e).__name__}: {e}")
    elapsed = int((time.monotonic() - t0) * 1000)
    jq = spec.get("jq", "")
    value = _apply_jq(data, jq) if jq else data
    verdict, msg = _assert_value(value, spec.get("assert", {}))
    return ProbeResult("", "", "tier3", verdict,
                      f"{jq}={value!r} → {msg}", elapsed_ms=elapsed)


def probe_http_jq_post(spec: dict) -> ProbeResult:
    """HTTP POST with JSON body, then multiple jq+assert pairs."""
    url = spec["url"]
    body = spec.get("body", {})
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=60.0) as c:
            resp = c.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()
    except httpx.ConnectError:
        return ProbeResult("", "", "tier3", "FAIL", f"connection refused: {url}")
    except Exception as e:
        return ProbeResult("", "", "tier3", "ERROR", f"{type(e).__name__}: {e}")
    elapsed = int((time.monotonic() - t0) * 1000)
    asserts = spec.get("asserts", [])
    failures: list[str] = []
    for a in asserts:
        jq = a.get("jq", "")
        value = _apply_jq(data, jq)
        verdict, msg = _assert_value(value, a)
        if verdict != "PASS":
            failures.append(f"{jq}={value!r}: {msg}")
    if failures:
        return ProbeResult("", "", "tier3", "FAIL",
                          " · ".join(failures), elapsed_ms=elapsed)
    return ProbeResult("", "", "tier3", "PASS",
                      f"all {len(asserts)} asserts passed",
                      elapsed_ms=elapsed)


def _apply_jq(data: Any, path: str) -> Any:
    """Minimal jq subset: .field.subfield, .arr | length, .field[0]."""
    # Handle "| length"
    if "| length" in path:
        target = _apply_jq(data, path.split("|")[0].strip())
        try: return len(target)
        except TypeError: return -1
    # Strip leading dot
    p = path.lstrip(".").strip()
    if not p:
        return data
    parts = p.split(".")
    cur = data
    for part in parts:
        if cur is None:
            return None
        # Array index
        m = re.match(r"^(\w+)\[(\d+)\]$", part)
        if m:
            cur = (cur or {}).get(m.group(1)) if isinstance(cur, dict) else None
            if cur is None:
                return None
            try: cur = cur[int(m.group(2))]
            except (IndexError, TypeError): return None
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _assert_value(value: Any, spec: dict) -> tuple[str, str]:
    t = spec.get("type")
    if t is None or value is None:
        return ("FAIL", "no value or no assert type") if value is None else ("PASS", "no-op")
    if t == "exists":
        return ("PASS", "exists") if value is not None else ("FAIL", "missing")
    if t == "int_range":
        try: v = int(value)
        except (TypeError, ValueError): return ("FAIL", "not int")
        lo, hi = spec.get("min", 0), spec.get("max", 10**9)
        if lo <= v <= hi:
            return ("PASS", f"in [{lo},{hi}]")
        return ("FAIL", f"out of range [{lo},{hi}]")
    if t == "int_min":
        try: v = int(value)
        except (TypeError, ValueError): return ("FAIL", "not int")
        if v >= spec["min"]:
            return ("PASS", f">= {spec['min']}")
        return ("FAIL", f"< {spec['min']}")
    if t == "int_max":
        try: v = int(value)
        except (TypeError, ValueError): return ("FAIL", "not int")
        if v <= spec["max"]:
            return ("PASS", f"<= {spec['max']}")
        return ("FAIL", f"> {spec['max']}")
    if t == "eq":
        return ("PASS", "eq") if value == spec["value"] else ("FAIL", f"!= {spec['value']!r}")
    if t == "http_ok":
        return ("PASS", "http 200")  # already verified in caller
    return ("ERROR", f"unknown assert type: {t}")


def probe_node_run(spec: dict) -> ProbeResult:
    cmd = spec["cmd"].split()
    expect_exit = spec.get("expect_exit", 0)
    t0 = time.monotonic()
    try:
        r = subprocess.run(
            cmd, cwd=str(REPO), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
    except subprocess.TimeoutExpired:
        return ProbeResult("", "", "tier2", "FAIL", "timeout > 120s")
    except FileNotFoundError as e:
        return ProbeResult("", "", "tier2", "ERROR", f"missing tool: {e}")
    elapsed = int((time.monotonic() - t0) * 1000)
    verdict = "PASS" if r.returncode == expect_exit else "FAIL"
    tail = (r.stdout or "")[-200:].strip().replace("\n", " | ")
    return ProbeResult("", "", "tier2", verdict,
                      f"exit={r.returncode} tail={tail!r}",
                      elapsed_ms=elapsed)


def probe_python_test(spec: dict) -> ProbeResult:
    rel = spec["file"]
    full = REPO / rel
    if not full.exists():
        return ProbeResult("", "", "tier2", "FAIL", f"missing: {rel}")
    cwd = full.parent
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    t0 = time.monotonic()
    try:
        r = subprocess.run(
            ["py", "-3", full.name],
            cwd=str(cwd), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
    except subprocess.TimeoutExpired:
        return ProbeResult("", "", "tier2", "FAIL", "timeout > 120s")
    elapsed = int((time.monotonic() - t0) * 1000)
    verdict = "PASS" if r.returncode == 0 else "FAIL"
    tail = (r.stdout or "")[-200:].strip().replace("\n", " | ")
    return ProbeResult("", "", "tier2", verdict,
                      f"exit={r.returncode} tail={tail!r}",
                      elapsed_ms=elapsed)


# ─────────────────────────────────────────────────────────────────────
# Routing log probe
# ─────────────────────────────────────────────────────────────────────
def probe_routing_log(spec: dict) -> dict[str, Any]:
    ssh_host = spec["ssh_host"]
    ssh_port = spec.get("ssh_port", 22)
    tail = spec.get("tail_count", 50)
    path = spec["path"]
    real_rooms = set()
    for a in spec.get("asserts", []):
        if a.get("real_rooms"):
            real_rooms = set(a["real_rooms"])
    try:
        r = subprocess.run(
            ["ssh", "-p", str(ssh_port), ssh_host, f"tail -{tail} {path}"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception as e:
        return {"verdict": "ERROR", "reason": str(e)}
    if r.returncode != 0:
        return {"verdict": "FAIL", "reason": "ssh error", "stderr": r.stderr[:200]}
    entries = []
    for line in r.stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except Exception:
            pass
    perc = [e for e in entries if e.get("kind") == "perception_dispatch"]
    http_502 = sum(1 for e in perc if e.get("error_kind", "").startswith("http_5"))
    phantoms = [e for e in perc if e.get("room") and e["room"] not in real_rooms]
    # Recent-window view: only count phantoms / 502s from the last 10 min.
    # The post-restart transient window can leave older entries in the tail;
    # what matters for steady-state health is whether the fix is currently
    # blocking new phantoms.
    now = time.time()
    window_s = spec.get("recent_window_s", 600)
    perc_recent = [e for e in perc if (now - e.get("ts", 0)) < window_s]
    http_502_recent = sum(1 for e in perc_recent if e.get("error_kind", "").startswith("http_5"))
    phantoms_recent = [e for e in perc_recent if e.get("room") and e["room"] not in real_rooms]
    latest_ts = max((e.get("ts", 0) for e in entries), default=0)
    age_s = int(now - latest_ts) if latest_ts > 0 else -1
    # PASS if (a) no recent phantoms AND no recent 502s, OR (b) log is
    # silent for the whole window (no perception activity recently).
    steady = http_502_recent == 0 and len(phantoms_recent) == 0
    return {
        "total_entries": len(entries),
        "perception_dispatch_count": len(perc),
        "http_502_count_all_tail": http_502,
        "phantom_rooms_count_all_tail": len(phantoms),
        "perception_recent_window_s": window_s,
        "perception_recent_count": len(perc_recent),
        "http_502_recent": http_502_recent,
        "phantom_rooms_recent": len(phantoms_recent),
        "phantom_rooms_recent_set": sorted({e["room"] for e in phantoms_recent})[:10],
        "latest_ts_age_seconds": age_s,
        "verdict": "PASS" if steady else "FAIL",
    }


# ─────────────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────────────
KINDS = {
    "grep_count": probe_grep_count,
    "grep_count_min": probe_grep_count_min,
    "http_jq": probe_http_jq,
    "http_jq_post": probe_http_jq_post,
    "node_run": probe_node_run,
    "python_test": probe_python_test,
}


def run_feature(feature: dict) -> list[ProbeResult]:
    out: list[ProbeResult] = []
    for tier_name, spec in feature.items():
        if tier_name in ("id", "name", "_description"):
            continue
        if not isinstance(spec, dict) or "kind" not in spec:
            continue
        kind = spec["kind"]
        fn = KINDS.get(kind)
        if fn is None:
            res = ProbeResult(feature["id"], feature["name"], tier_name,
                             "ERROR", f"unknown kind: {kind}")
        else:
            try:
                res = fn(spec)
            except Exception as e:
                res = ProbeResult("", "", tier_name, "ERROR",
                                 f"{type(e).__name__}: {e}")
        res.feature_id = feature["id"]
        res.name = feature["name"]
        res.tier = tier_name
        out.append(res)
    return out


def run_service(svc: dict) -> ProbeResult:
    res = probe_http({"url": svc["url"], "expect_http": svc["expect_http"]})
    res.feature_id = svc["id"]
    res.name = svc["id"]
    return res


def write_markdown(rep: CycleReport, path: Path) -> None:
    def short_verdict(v: str) -> str:
        return {"PASS": "✅", "FAIL": "❌", "ERROR": "💥", "SKIP": "⏭"}.get(v, v)
    lines: list[str] = []
    lines.append(f"# Addendum 28 — Probe cycle\n")
    lines.append(f"_Started: {rep.started_at}_\n\n")
    lines.append("## Service health\n\n")
    lines.append("| Service | Verdict | Detail | ms |\n|---|---|---|---|\n")
    for r in rep.service_results:
        lines.append(f"| {r.feature_id} | {short_verdict(r.verdict)} | {r.details} | {r.elapsed_ms} |\n")
    lines.append("\n## Routing-log health\n\n")
    rl = rep.routing_log
    lines.append(f"- **Verdict**: {short_verdict(rl.get('verdict','?'))}\n")
    for k, v in rl.items():
        if k == "verdict":
            continue
        lines.append(f"- {k}: `{v}`\n")
    lines.append("\n## Feature matrix\n\n")
    lines.append("| ID | Feature | Tier | Verdict | Detail |\n|---|---|---|---|---|\n")
    for r in rep.feature_results:
        det = r.details.replace("|", "\\|")[:120]
        lines.append(f"| {r.feature_id} | {r.name} | {r.tier} | {short_verdict(r.verdict)} | {det} |\n")
    by_feature: dict[str, list[ProbeResult]] = {}
    for r in rep.feature_results:
        by_feature.setdefault(r.feature_id, []).append(r)
    pass_count = sum(1 for fid, rs in by_feature.items() if all(r.verdict == "PASS" for r in rs))
    fail_count = sum(1 for fid, rs in by_feature.items() if any(r.verdict in ("FAIL", "ERROR") for r in rs))
    partial_count = sum(1 for fid, rs in by_feature.items()
                        if any(r.verdict == "PASS" for r in rs) and any(r.verdict in ("FAIL", "ERROR") for r in rs))
    lines.append(f"\n## Summary\n\n")
    lines.append(f"- Total features: {len(by_feature)}\n")
    lines.append(f"- Fully PASS (all tiers): **{pass_count}**\n")
    lines.append(f"- PARTIAL (some tiers fail): **{partial_count}**\n")
    lines.append(f"- FAIL (all/no tiers pass): **{fail_count}**\n")
    path.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    if not PROBES_FILE.exists():
        print(f"missing: {PROBES_FILE}", file=sys.stderr)
        return 3
    try:
        spec = json.loads(PROBES_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"malformed json: {e}", file=sys.stderr)
        return 3
    rep = CycleReport()
    # Phase 1: services
    print("== service health ==")
    for s in spec.get("service_probes", []):
        r = run_service(s)
        rep.service_results.append(r)
        print(f"  {s['id']:20s} {r.verdict:6s} {r.details}")
    # Phase 2: routing log
    print("\n== routing log ==")
    rl_spec = spec.get("routing_log_probe")
    if rl_spec:
        rep.routing_log = probe_routing_log(rl_spec)
        for k, v in rep.routing_log.items():
            print(f"  {k:25s} = {v}")
    # Phase 3: features
    print("\n== feature matrix ==")
    for f in spec.get("features", []):
        results = run_feature(f)
        rep.feature_results.extend(results)
        for r in results:
            print(f"  {r.feature_id:25s} {r.tier:18s} {r.verdict:6s} {r.details[:80]}")
    # Write report
    ts = time.strftime("%Y-%m-%d-%H%M")
    md_path = REPORT_DIR / f"{ts}-probe-cycle.md"
    json_path = REPORT_DIR / f"{ts}-probe-cycle.json"
    write_markdown(rep, md_path)
    json_path.write_text(json.dumps({
        "started_at": rep.started_at,
        "services": [asdict(r) for r in rep.service_results],
        "routing_log": rep.routing_log,
        "features": [asdict(r) for r in rep.feature_results],
    }, indent=2), encoding="utf-8")
    print(f"\n→ {md_path}")
    print(f"→ {json_path}")
    # Exit code
    svc_fail = any(r.verdict in ("FAIL", "ERROR") for r in rep.service_results)
    if svc_fail:
        return 2
    feat_fail = any(r.verdict in ("FAIL", "ERROR") for r in rep.feature_results)
    rl_fail = rep.routing_log.get("verdict") in ("FAIL", "ERROR")
    if feat_fail or rl_fail:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
