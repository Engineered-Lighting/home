#!/usr/bin/env python3
"""Read-only AI stack status watcher.

Polls the stack supervisor's read-only endpoints and writes a small JSONL plus
Markdown report. It never calls mutating supervisor endpoints such as start,
restart, stop, free-gpu, or service restart.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parent.parent
REPORT_DIR = REPO / "tools" / "reports"
DEFAULT_SUPERVISOR_URL = os.environ.get("STACK_SUPERVISOR_URL", "http://127.0.0.1:8093")
STARTABLE = {"offline", "down", "partial", "error", "unknown"}
RESTARTABLE = {"ready", "warming"}
STOPPABLE = {"ready", "warming"}
SERVICE_RESTARTABLE = {"ready", "warming", "partial", "error", "down"}


def utc_stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_base(url: str | None) -> str:
    raw = (url or DEFAULT_SUPERVISOR_URL).strip()
    return raw.rstrip("/")


def load_stack_token() -> str:
    return (
        os.environ.get("STACK_TOKEN", "")
        or os.environ.get("HOME_STACK_TOKEN", "")
        or ""
    ).strip()


def rest_get_json(url: str, token: str | None = None, timeout_s: float = 5.0) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
            body: Any = None
            if raw.strip():
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    body = {"raw": raw[:500], "json_error": True}
            return {"ok": 200 <= response.status < 300, "status": response.status, "body": body}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        body = None
        if raw.strip():
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                body = {"raw": raw[:500], "json_error": True}
        return {"ok": False, "status": exc.code, "body": body, "error": f"http {exc.code}"}
    except Exception as exc:
        return {"ok": False, "status": 0, "body": None, "error": str(exc)}


def status_body(sample: dict[str, Any]) -> dict[str, Any] | None:
    body = (sample.get("status") or {}).get("body")
    return body if isinstance(body, dict) else None


def service_by_name(body: dict[str, Any] | None, name: str) -> dict[str, Any] | None:
    for svc in (body or {}).get("services") or []:
        if isinstance(svc, dict) and svc.get("name") == name:
            return svc
    return None


def recovery_controls(body: dict[str, Any] | None) -> list[str]:
    if not body:
        return []
    overall = body.get("overall") or "unknown"
    busy = bool(body.get("in_flight"))
    if busy:
        return ["watch in-flight task"]
    controls: list[str] = []
    if overall in STARTABLE:
        controls.append("start ai stack")
    if overall in RESTARTABLE:
        controls.append("restart")
    if overall in STOPPABLE:
        controls.extend(["stop ai stack", "free gpu"])
    if overall in SERVICE_RESTARTABLE:
        controls.append("service restarts")
    return controls


def build_sample(base_url: str, token: str, healthz: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    body = status.get("body") if isinstance(status.get("body"), dict) else None
    return {
        "kind": "sample",
        "utc": utc_stamp(),
        "supervisor_url": base_url,
        "token_configured": bool(token),
        "healthz": healthz,
        "status": status,
        "overall": body.get("overall") if body else None,
        "vllm_model_loaded": body.get("vllm_model_loaded") if body else None,
        "expected_vllm_model": body.get("expected_vllm_model") if body else None,
        "recovery_controls": recovery_controls(body),
    }


def compact_service(svc: dict[str, Any] | None) -> str:
    if not svc:
        return "missing from status payload"
    bits = [
        f"container={svc.get('container', '?')}",
        f"health={svc.get('health', '?')}",
        f"probe={svc.get('probe', '?')}",
    ]
    if svc.get("detail"):
        bits.append(f"detail={svc.get('detail')}")
    return ", ".join(bits)


def diagnosis_hints(samples: list[dict[str, Any]]) -> list[str]:
    if not samples:
        return ["- No samples captured; supervisor reachability is unknown."]
    sample = samples[-1]
    hints: list[str] = []
    healthz = sample.get("healthz") or {}
    status = sample.get("status") or {}
    body = status_body(sample)

    if not sample.get("token_configured"):
        hints.append("- STACK_TOKEN is not configured for this watcher; the app would show the /stack-token setup path instead of live stack status.")
    if not healthz.get("ok"):
        hints.append("- Stack supervisor /healthz is unreachable; check hav-stack-supervisor on the AI box before trying model recovery.")
        return hints
    if status.get("status") == 401:
        hints.append("- Stack supervisor is reachable, but the token was rejected; update the Home app with /stack-token before using recovery controls.")
        return hints
    if status.get("status") == 429:
        hints.append("- Stack supervisor status is rate-limited; wait briefly before retrying.")
        return hints
    if not status.get("ok"):
        hints.append(f"- Stack supervisor is reachable, but /api/stack/status failed ({status.get('error') or status.get('status')}).")
        return hints
    if not body:
        hints.append("- Stack supervisor returned no JSON status body; the Home app cannot decide which recovery controls to show.")
        return hints

    overall = body.get("overall") or "unknown"
    inflight = body.get("in_flight")
    expected = body.get("expected_vllm_model")
    loaded = body.get("vllm_model_loaded")
    vllm = service_by_name(body, "vllm")

    if inflight:
        hints.append(f"- Stack action already in flight ({inflight.get('verb', 'unknown')}); watch the task logs instead of repeatedly starting the stack.")
    if vllm and vllm.get("container") in {"missing", "exited"}:
        hints.append(f"- vLLM container is {vllm.get('container')}; the model runtime is offline and the UI should expose start/restart recovery.")
    elif vllm and vllm.get("probe") == "fail":
        hints.append(f"- vLLM probe is failing ({vllm.get('detail') or 'no detail'}); model commands may time out until the service recovers.")

    if expected and loaded != expected:
        if loaded:
            hints.append(f"- vLLM loaded {loaded}, but the expected model is {expected}; verify model selection before trusting recovery status.")
        else:
            hints.append(f"- Expected model {expected} is not loaded yet; chat may be unavailable until vLLM warms.")

    controls = recovery_controls(body)
    if controls:
        hints.append("- UI recovery controls expected for this state: " + ", ".join(controls) + ".")

    if overall == "ready" and loaded == expected:
        hints.append("- AI stack status is ready and the expected model is loaded.")
    elif overall in {"offline", "down", "error"}:
        hints.append(f"- AI stack overall state is {overall}; typed/voice model commands should be treated as degraded until recovery succeeds.")
    elif overall in {"partial", "warming", "starting", "stopping"}:
        hints.append(f"- AI stack overall state is {overall}; recovery is not complete yet.")

    return hints


def write_jsonl(handle: Any, row: dict[str, Any]) -> None:
    handle.write(json.dumps(row, sort_keys=True) + "\n")
    handle.flush()


def write_summary(md_path: Path, jsonl_path: Path, duration_s: int, samples: list[dict[str, Any]]) -> None:
    latest = samples[-1] if samples else {}
    body = status_body(latest)
    services = body.get("services") if body else []
    lines = [
        "# AI Stack Status Watch",
        "",
        f"Run duration: {duration_s}s",
        f"Raw JSONL: `{jsonl_path.name}`",
        "Mutating supervisor endpoints called: none",
        "",
        "## Final State",
        "",
        f"- Supervisor URL: {latest.get('supervisor_url', 'unknown')}",
        f"- Token configured: {'yes' if latest.get('token_configured') else 'no'}",
        f"- Healthz: {(latest.get('healthz') or {}).get('status', 'unknown')} ({'ok' if (latest.get('healthz') or {}).get('ok') else 'not ok'})",
        f"- Status: {(latest.get('status') or {}).get('status', 'unknown')} ({'ok' if (latest.get('status') or {}).get('ok') else 'not ok'})",
        f"- Overall: {latest.get('overall') or 'unknown'}",
        f"- Model: {latest.get('vllm_model_loaded') or 'not loaded'} / expected {latest.get('expected_vllm_model') or 'unknown'}",
        f"- Recovery controls expected: {', '.join(latest.get('recovery_controls') or []) or 'none'}",
        "",
        "## Services",
        "",
    ]
    if services:
        for svc in services:
            if isinstance(svc, dict):
                lines.append(f"- {svc.get('name', '?')}: {compact_service(svc)}")
    else:
        lines.append("- No service status payload available.")

    lines.extend(["", "## Diagnosis Hints", ""])
    lines.extend(diagnosis_hints(samples))
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def collect_sample(base_url: str, token: str) -> dict[str, Any]:
    healthz = rest_get_json(f"{base_url}/healthz")
    status = (
        rest_get_json(f"{base_url}/api/stack/status", token=token)
        if token
        else {"ok": False, "status": None, "body": None, "error": "STACK_TOKEN not configured"}
    )
    return build_sample(base_url, token, healthz, status)


def watch(base_url: str, token: str, duration_s: int, interval_s: int, jsonl_path: Path, md_path: Path) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, Any]] = []
    start = time.time()
    with jsonl_path.open("w", encoding="utf-8") as handle:
        write_jsonl(handle, {
            "kind": "run_start",
            "utc": utc_stamp(),
            "duration_s": duration_s,
            "interval_s": interval_s,
            "supervisor_url": base_url,
            "token_configured": bool(token),
            "mutating_endpoints_called": 0,
        })
        while True:
            sample = collect_sample(base_url, token)
            samples.append(sample)
            write_jsonl(handle, sample)
            print(
                f"{sample['utc']} overall={sample.get('overall') or 'unknown'} "
                f"healthz={(sample.get('healthz') or {}).get('status')} "
                f"status={(sample.get('status') or {}).get('status')}",
                flush=True,
            )
            if time.time() - start >= duration_s:
                break
            time.sleep(max(1, interval_s))
    write_summary(md_path, jsonl_path, duration_s, samples)
    print(f"Summary: {md_path}", flush=True)


def default_paths() -> tuple[Path, Path]:
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    return (
        REPORT_DIR / f"ai-stack-status-watch-{stamp}.jsonl",
        REPORT_DIR / f"ai-stack-status-watch-{stamp}.md",
    )


def main() -> int:
    jsonl_default, md_default = default_paths()
    parser = argparse.ArgumentParser(description="Read-only AI stack status watcher.")
    parser.add_argument("--supervisor-url", default=DEFAULT_SUPERVISOR_URL)
    parser.add_argument("--token", default=load_stack_token())
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--interval", type=int, default=10)
    parser.add_argument("--once", action="store_true", help="Capture one sample and exit.")
    parser.add_argument("--jsonl", type=Path, default=jsonl_default)
    parser.add_argument("--md", type=Path, default=md_default)
    args = parser.parse_args()
    duration = 0 if args.once else max(1, args.duration)
    watch(
        base_url=normalize_base(args.supervisor_url),
        token=(args.token or "").strip(),
        duration_s=duration,
        interval_s=max(1, args.interval),
        jsonl_path=args.jsonl,
        md_path=args.md,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
