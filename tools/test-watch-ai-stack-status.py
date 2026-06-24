#!/usr/bin/env python3
"""Pure local tests for watch-ai-stack-status.py."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "watch_ai_stack_status", HERE / "watch-ai-stack-status.py"
)
wass = importlib.util.module_from_spec(SPEC)
sys.modules["watch_ai_stack_status"] = wass
SPEC.loader.exec_module(wass)


PASSES = 0
FAILS = 0


def assert_(condition: bool, label: str) -> None:
    global PASSES, FAILS
    if condition:
        PASSES += 1
        print(f"[OK] {label}")
    else:
        FAILS += 1
        print(f"[FAIL] {label}", file=sys.stderr)
        raise AssertionError(label)


def result(status: int, body=None, ok: bool | None = None, error: str | None = None) -> dict:
    return {
        "ok": (200 <= status < 300) if ok is None else ok,
        "status": status,
        "body": body,
        **({"error": error} if error else {}),
    }


def status_body(overall="ready", services=None, loaded="qwen3-vl-30b", expected="qwen3-vl-30b", inflight=None):
    return {
        "overall": overall,
        "services": services or [
            {"name": "vllm", "container": "running", "health": "healthy", "probe": "ok", "probe_kind": "http"},
            {"name": "metrics-sidecar", "container": "running", "health": "healthy", "probe": "ok", "probe_kind": "http"},
        ],
        "in_flight": inflight,
        "vllm_model_loaded": loaded,
        "expected_vllm_model": expected,
    }


def sample(token=True, health=None, status=None):
    return wass.build_sample(
        "http://127.0.0.1:8093",
        "token-123" if token else "",
        health or result(200, {"ok": True}),
        status or result(200, status_body()),
    )


def hints_text(samples) -> str:
    return "\n".join(wass.diagnosis_hints(samples))


def test_helpers_and_controls() -> None:
    assert_(wass.normalize_base("http://x:8093///") == "http://x:8093", "normalize_base trims trailing slashes")
    assert_(wass.service_by_name(status_body(), "vllm")["name"] == "vllm", "service_by_name finds vllm")
    assert_(wass.service_by_name(status_body(), "missing") is None, "service_by_name returns None")
    assert_("container=running" in wass.compact_service(status_body()["services"][0]), "compact_service formats container")
    assert_("missing from status" in wass.compact_service(None), "compact_service handles missing service")

    controls = wass.recovery_controls(status_body(overall="down"))
    assert_("start ai stack" in controls and "service restarts" in controls, "down state exposes start and service restart controls")
    controls = wass.recovery_controls(status_body(overall="ready"))
    assert_(controls == ["restart", "stop ai stack", "free gpu", "service restarts"], "ready state exposes restart/stop/free-gpu/service controls")
    controls = wass.recovery_controls(status_body(overall="starting", inflight={"verb": "start"}))
    assert_(controls == ["watch in-flight task"], "in-flight state suppresses mutating controls")
    assert_(wass.recovery_controls(None) == [], "missing status has no recovery controls")


def test_diagnosis_hints() -> None:
    text = hints_text([sample(token=False, status=result(0, None, ok=False, error="STACK_TOKEN not configured"))])
    assert_("STACK_TOKEN is not configured" in text, "missing token is diagnosed")

    text = hints_text([sample(health=result(0, None, ok=False, error="connection refused"))])
    assert_("healthz is unreachable" in text, "unreachable supervisor is diagnosed")

    text = hints_text([sample(status=result(401, {"detail": "bad"}, ok=False))])
    assert_("token was rejected" in text, "auth failure is diagnosed")

    text = hints_text([sample(status=result(429, {"detail": {"retry_after_s": 7}}, ok=False))])
    assert_("rate-limited" in text, "rate limiting is diagnosed")

    text = hints_text([sample(status=result(503, {"detail": "down"}, ok=False, error="http 503"))])
    assert_("status failed" in text and "503" in text, "status failure is diagnosed")

    vllm_missing = status_body(
        overall="down",
        services=[{"name": "vllm", "container": "missing", "health": "none", "probe": "fail", "detail": "missing"}],
        loaded=None,
    )
    text = hints_text([sample(status=result(200, vllm_missing))])
    assert_("vLLM container is missing" in text, "vLLM missing container is diagnosed")
    assert_("Expected model qwen3-vl-30b is not loaded yet" in text, "missing model is diagnosed")
    assert_("start ai stack" in text, "expected recovery controls are named")

    wrong_model = status_body(overall="ready", loaded="other-model", expected="qwen3-vl-30b")
    text = hints_text([sample(status=result(200, wrong_model))])
    assert_("loaded other-model" in text, "model mismatch is diagnosed")

    ready = status_body(overall="ready", loaded="qwen3-vl-30b", expected="qwen3-vl-30b")
    text = hints_text([sample(status=result(200, ready))])
    assert_("status is ready" in text, "ready stack is diagnosed positively")


def test_report_output_is_read_only_and_token_safe() -> None:
    ready_sample = sample(status=result(200, status_body(overall="ready")))
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        md = tmpdir / "ai.md"
        jsonl = tmpdir / "ai.jsonl"
        wass.write_summary(md, jsonl, 0, [ready_sample])
        text = md.read_text(encoding="utf-8")
    assert_("Mutating supervisor endpoints called: none" in text, "summary states read-only behavior")
    assert_("token-123" not in text, "summary never writes token value")
    assert_("Recovery controls expected" in text, "summary lists recovery controls")
    assert_("vllm:" in text, "summary lists services")


def main() -> int:
    for test in [
        test_helpers_and_controls,
        test_diagnosis_hints,
        test_report_output_is_read_only_and_token_safe,
    ]:
        test()
    print(f"{PASSES} pass . {FAILS} fail")
    return 0 if FAILS == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
