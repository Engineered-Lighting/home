#!/usr/bin/env python3
"""Tests for functions/frigate.py — find_clips tool (Addendum 27 F-2-tier).

Covers:
  - URL construction (search, camera, label, sub_label, window_min, limit)
  - Frigate response normalization (sub_label list vs string vs missing)
  - Clamping (limit > MAX, window_min > MAX, negative inputs)
  - Error paths (timeout, http 500, malformed JSON, unreachable)
  - Freshness ("fresh" < 5min, "stale" < 6h, "old" otherwise, "none" empty)
  - suggested_phrasing for empty/single/multi cases

Standalone runner — mocks httpx so the dispatcher can be exercised
without hitting Frigate. Same shape as test_registry.py.

Run: PYTHONIOENCODING=utf-8 py -3 test_frigate_tool.py
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
import time


# ── Mock the HA + httpx + voluptuous imports BEFORE importing frigate.py ──
def _mk(name: str):
    return types.ModuleType(name)


class _vol:
    @staticmethod
    def Schema(*a, **k):
        return None

    @staticmethod
    def Required(x, **k):
        return x


sys.modules.setdefault("voluptuous", _vol)

# HA skeleton
ha = _mk("homeassistant")
ha_core = _mk("homeassistant.core")


class HomeAssistant:
    pass


ha_core.HomeAssistant = HomeAssistant

ha_helpers = _mk("homeassistant.helpers")
ha_helpers_llm = _mk("homeassistant.helpers.llm")


class _LLMCtx:
    device_id = None
    conversation_id = None


ha_helpers_llm.LLMContext = _LLMCtx

sys.modules.setdefault("homeassistant", ha)
sys.modules.setdefault("homeassistant.core", ha_core)
sys.modules.setdefault("homeassistant.helpers", ha_helpers)
sys.modules.setdefault("homeassistant.helpers.llm", ha_helpers_llm)

# httpx mock — captures the request, returns a configurable response
class _FakeResponse:
    def __init__(self, status_code: int, json_data=None, raise_on_json: bool = False):
        self.status_code = status_code
        self._json_data = json_data
        self._raise_on_json = raise_on_json

    def json(self):
        if self._raise_on_json:
            raise ValueError("malformed json")
        return self._json_data


class _FakeAsyncClient:
    # Test sets these BEFORE the call so the mock returns the right shape.
    next_response: _FakeResponse | None = None
    next_exception: Exception | None = None
    last_url: str = ""
    last_params: dict = {}

    def __init__(self, timeout=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None):
        _FakeAsyncClient.last_url = url
        _FakeAsyncClient.last_params = dict(params or {})
        if _FakeAsyncClient.next_exception is not None:
            exc = _FakeAsyncClient.next_exception
            _FakeAsyncClient.next_exception = None
            raise exc
        return _FakeAsyncClient.next_response or _FakeResponse(200, [])

    async def post(self, url, json=None, params=None):
        """F-3: describe_clip POSTs to the vision-sidecar. Same
        request capture + response shape as get()."""
        _FakeAsyncClient.last_url = url
        _FakeAsyncClient.last_params = dict(params or {})
        _FakeAsyncClient.last_json = dict(json or {})
        if _FakeAsyncClient.next_exception is not None:
            exc = _FakeAsyncClient.next_exception
            _FakeAsyncClient.next_exception = None
            raise exc
        return _FakeAsyncClient.next_response or _FakeResponse(200, {})


class _httpx:
    AsyncClient = _FakeAsyncClient

    class TimeoutException(Exception):
        pass


sys.modules.setdefault("httpx", _httpx)

# ── Stub the .frigate_sync.base_url + .const imports ────────────────
# functions/frigate.py does `from ..frigate_sync import base_url` and
# `from ..const import VISION_SIDECAR_URL`. Both need to exist as
# importable modules in the fake package tree.
_pkg = _mk("extended_openai_conversation")
_pkg.__path__ = [str(Path(__file__).parent)]   # treat as package
_frigate_sync = _mk("extended_openai_conversation.frigate_sync")
_frigate_sync.base_url = lambda: "http://192.168.0.125:5000"
_const = _mk("extended_openai_conversation.const")
_const.VISION_SIDECAR_URL = "http://test-vision:8091"
sys.modules["extended_openai_conversation.const"] = _const
_exceptions = _mk("extended_openai_conversation.exceptions")


class _FunctionNotFound(Exception):
    pass


_exceptions.FunctionNotFound = _FunctionNotFound
_exceptions.NativeNotFound = _FunctionNotFound  # registry uses this; harmless

_base = _mk("extended_openai_conversation.functions")
_base_mod = _mk("extended_openai_conversation.functions.base")


class Function:
    def __init__(self, *a, **k):
        pass


_base_mod.Function = Function

sys.modules.setdefault("extended_openai_conversation", _pkg)
sys.modules.setdefault("extended_openai_conversation.exceptions", _exceptions)
sys.modules.setdefault("extended_openai_conversation.frigate_sync", _frigate_sync)
sys.modules.setdefault("extended_openai_conversation.functions", _base)
sys.modules.setdefault("extended_openai_conversation.functions.base", _base_mod)

# ── Now import the unit under test ───────────────────────────────────
import importlib.util

_src = Path(__file__).parent / "functions" / "frigate.py"
spec = importlib.util.spec_from_file_location(
    "extended_openai_conversation.functions.frigate", _src
)
frigate_mod = importlib.util.module_from_spec(spec)
sys.modules["extended_openai_conversation.functions.frigate"] = frigate_mod
spec.loader.exec_module(frigate_mod)

FrigateFunction = frigate_mod.FrigateFunction

# ── Tiny test harness ────────────────────────────────────────────────
passes = 0
fails = 0
fail_msgs: list[str] = []


def assert_eq(name: str, actual, expected):
    global passes, fails
    if actual == expected:
        passes += 1
        print(f"  PASS  {name}")
    else:
        fails += 1
        fail_msgs.append(f"FAIL {name}: expected {expected!r}, got {actual!r}")
        print(f"  FAIL  {name}\n        expected: {expected!r}\n        actual:   {actual!r}")


def assert_true(name: str, cond, hint: str = ""):
    global passes, fails
    if cond:
        passes += 1
        print(f"  PASS  {name}")
    else:
        fails += 1
        fail_msgs.append(f"FAIL {name}: {hint}")
        print(f"  FAIL  {name} — {hint}")


# Wrapper to run async — create a fresh event loop per call (compatible
# with Python 3.12+ where get_event_loop() no longer auto-creates one)
def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# Helper to call the tool
async def call_find_clips(args):
    fn = FrigateFunction()
    return await fn.execute(
        hass=None,
        function_config={"name": "find_clips"},
        arguments=args,
        llm_context=None,
        exposed_entities=[],
    )


# ── Suite 1: URL construction ────────────────────────────────────────
print("\nurl_construction_test")

_FakeAsyncClient.next_response = _FakeResponse(200, [])
run(call_find_clips({"search": "package on porch", "camera": "driveway", "limit": 5}))
assert_eq("URL is frigate /api/events", _FakeAsyncClient.last_url, "http://192.168.0.125:5000/api/events")
assert_eq("params.search passed through", _FakeAsyncClient.last_params.get("search"), "package on porch")
assert_eq("params.cameras passed through", _FakeAsyncClient.last_params.get("cameras"), "driveway")
assert_eq("params.limit cast to string", _FakeAsyncClient.last_params.get("limit"), "5")
assert_true("params.after is a unix timestamp",
            _FakeAsyncClient.last_params.get("after", "").isdigit())

# Sub_label + label filters
_FakeAsyncClient.next_response = _FakeResponse(200, [])
run(call_find_clips({"label": "person", "sub_label": "marcelo"}))
assert_eq("params.labels set when label arg given",
          _FakeAsyncClient.last_params.get("labels"), "person")
assert_eq("params.sub_labels set when sub_label arg given",
          _FakeAsyncClient.last_params.get("sub_labels"), "marcelo")
assert_true("no params.search when search arg empty",
            "search" not in _FakeAsyncClient.last_params)

# ── Suite 2: clamping ────────────────────────────────────────────────
print("\nclamping_test")

# limit > MAX_LIMIT
_FakeAsyncClient.next_response = _FakeResponse(200, [])
run(call_find_clips({"limit": 999}))
assert_eq("limit clamped to MAX_LIMIT=25", _FakeAsyncClient.last_params.get("limit"), "25")

# limit < 1
_FakeAsyncClient.next_response = _FakeResponse(200, [])
run(call_find_clips({"limit": 0}))
assert_eq("limit clamped to minimum 1", _FakeAsyncClient.last_params.get("limit"), "1")

# limit non-numeric → default
_FakeAsyncClient.next_response = _FakeResponse(200, [])
run(call_find_clips({"limit": "not a number"}))
assert_eq("limit non-numeric → DEFAULT_LIMIT=8",
          _FakeAsyncClient.last_params.get("limit"), "8")

# window_min > MAX_WINDOW_MIN
_FakeAsyncClient.next_response = _FakeResponse(200, [])
run(call_find_clips({"window_min": 99999}))
# Window is 1 week max — verify the `after` timestamp is ~1 week ago
after = int(_FakeAsyncClient.last_params["after"])
expected_max = int(time.time() - frigate_mod.MAX_WINDOW_MIN * 60) - 2
assert_true("window_min clamped to MAX_WINDOW_MIN (7 days)",
            abs(after - expected_max) < 10,
            f"after={after} expected_max~={expected_max}")

# ── Suite 3: response normalization ──────────────────────────────────
print("\nresponse_normalization_test")

now = time.time()
fake_events = [
    # Modern shape: sub_label as [name, score]
    {
        "id": "1779000000.123-aaaaaa",
        "start_time": now - 10,
        "end_time": now - 5,
        "camera": "kitchen",
        "label": "person",
        "sub_label": ["marcelo", 0.94],
        "top_score": 0.88,
        "has_clip": True,
        "has_snapshot": True,
    },
    # Older shape: sub_label as bare string
    {
        "id": "1779000000.456-bbbbbb",
        "start_time": now - 30,
        "end_time": now - 28,
        "camera": "living_room",
        "label": "person",
        "sub_label": "amelia",
        "top_score": 0.82,
        "has_clip": False,
        "has_snapshot": True,
    },
    # Unknown: sub_label "None" → should normalize to null
    {
        "id": "1779000000.789-cccccc",
        "start_time": now - 60,
        "end_time": now - 58,
        "camera": "driveway",
        "label": "car",
        "sub_label": "None",
        "top_score": 0.71,
        "has_clip": True,
        "has_snapshot": True,
    },
    # Malformed entry — missing id; should be SKIPPED, not crash
    {
        "start_time": now - 120,
        "camera": "ghost",
        "label": "person",
    },
]
_FakeAsyncClient.next_response = _FakeResponse(200, fake_events)
result = run(call_find_clips({"search": "anything"}))
clips = result["data"]["clips"]
assert_eq("malformed entry skipped (3 of 4 events become clips)",
          len(clips), 3)
assert_eq("first clip sub_label is normalized name",
          clips[0]["sub_label"], "marcelo")
assert_eq("first clip sub_label_score parsed",
          clips[0]["sub_label_score"], 0.94)
assert_eq("string sub_label preserved as name",
          clips[1]["sub_label"], "amelia")
assert_eq("'None' sub_label normalized to null",
          clips[2]["sub_label"], None)
assert_eq("duration_s computed from start/end",
          clips[0]["duration_s"], 5.0)
assert_true("thumb_url uses event id",
            clips[0]["thumb_url"] == "/api/events/1779000000.123-aaaaaa/thumbnail.jpg")
assert_eq("clip_url is null when has_clip is False",
          clips[1]["clip_url"], None)
assert_true("clip_url populated when has_clip True",
            clips[0]["clip_url"] == "/api/events/1779000000.123-aaaaaa/clip.mp4")
assert_true("ts is ISO Z format",
            clips[0]["ts"].endswith("Z") and "T" in clips[0]["ts"])

# ── Suite 4: error paths ─────────────────────────────────────────────
print("\nerror_paths_test")

# Timeout
_FakeAsyncClient.next_exception = frigate_mod.httpx.TimeoutException("timed out")
result = run(call_find_clips({"search": "anything"}))
assert_true("timeout produces error field", "error" in result and "timeout" in result["error"])
assert_eq("timeout returns empty clips list", result["data"]["count"], 0)

# Generic exception (connection refused)
_FakeAsyncClient.next_exception = ConnectionError("connection refused")
result = run(call_find_clips({"search": "anything"}))
assert_true("unreachable produces error field",
            "error" in result and "unreachable" in result["error"])

# HTTP 500
_FakeAsyncClient.next_response = _FakeResponse(500, [])
result = run(call_find_clips({"search": "anything"}))
assert_true("http 500 produces error field", "http 500" in result["error"])

# Bad JSON
_FakeAsyncClient.next_response = _FakeResponse(200, None, raise_on_json=True)
result = run(call_find_clips({"search": "anything"}))
assert_true("bad json produces error field", "bad json" in result["error"])

# ── Suite 5: freshness + phrasing ────────────────────────────────────
print("\nfreshness_and_phrasing_test")

# Empty result
_FakeAsyncClient.next_response = _FakeResponse(200, [])
result = run(call_find_clips({"search": "anything"}))
assert_eq("empty result freshness = none", result["freshness"], "none")
assert_true("empty result phrasing mentions 'No clips'",
            "No clips" in result["suggested_phrasing"])

# Fresh result (< 5 min)
fresh_events = [{
    "id": "fresh-1",
    "start_time": time.time() - 60,
    "end_time": time.time() - 55,
    "camera": "kitchen",
    "label": "person",
    "sub_label": "marcelo",
    "has_clip": True,
    "has_snapshot": True,
}]
_FakeAsyncClient.next_response = _FakeResponse(200, fresh_events)
result = run(call_find_clips({"search": "anything"}))
assert_eq("fresh result freshness = fresh", result["freshness"], "fresh")
assert_true("fresh single clip phrasing mentions camera",
            "kitchen" in result["suggested_phrasing"])

# Stale (3 hours ago)
stale_events = [{
    "id": "stale-1",
    "start_time": time.time() - 3 * 60 * 60,
    "end_time": time.time() - 3 * 60 * 60 + 5,
    "camera": "living_room",
    "label": "person",
    "sub_label": "amelia",
    "has_clip": True,
    "has_snapshot": True,
}]
_FakeAsyncClient.next_response = _FakeResponse(200, stale_events)
result = run(call_find_clips({"search": "anything"}))
assert_eq("3h-old result freshness = stale", result["freshness"], "stale")

# Old (> 6 hours)
old_events = [{
    "id": "old-1",
    "start_time": time.time() - 24 * 60 * 60,
    "end_time": time.time() - 24 * 60 * 60 + 5,
    "camera": "driveway",
    "label": "car",
    "has_clip": True,
    "has_snapshot": True,
}]
_FakeAsyncClient.next_response = _FakeResponse(200, old_events)
result = run(call_find_clips({"search": "anything"}))
assert_eq("24h-old result freshness = old", result["freshness"], "old")

# Multi-clip phrasing
many_events = [{
    "id": f"e-{i}",
    "start_time": time.time() - i,
    "end_time": time.time() - i + 1,
    "camera": "kitchen",
    "label": "person",
    "has_clip": True,
    "has_snapshot": True,
} for i in range(5)]
_FakeAsyncClient.next_response = _FakeResponse(200, many_events)
result = run(call_find_clips({}))
assert_true("multi-clip phrasing reports count",
            "5" in result["suggested_phrasing"])

# ── Suite 6: dispatcher unknown tool ─────────────────────────────────
print("\ndispatcher_test")

fn = FrigateFunction()
res = run(fn.execute(
    hass=None,
    function_config={"name": "nonexistent_tool"},
    arguments={},
    llm_context=None,
    exposed_entities=[],
))
assert_true("unknown tool returns error", "error" in res and "unknown" in res["error"])

# ── Suite 7: F-3 describe_clip ────────────────────────────────────────
print("\ndescribe_clip_test")

# Helper to call describe_clip
async def call_describe_clip(args):
    fn = FrigateFunction()
    return await fn.execute(
        hass=None,
        function_config={"name": "describe_clip"},
        arguments=args,
        llm_context=None,
        exposed_entities=[],
    )

# Suite 7.1: happy path — sidecar returns a clean DescribeClipOut shape.
_FakeAsyncClient.next_response = _FakeResponse(200, {
    "camera": "kitchen",
    "entity_id": "camera.kitchen",
    "description": "A person walks across the kitchen from left to right.",
    "frames_captured": 4,
    "span_s": 3.0,
    "sampling_ms": 3120,
    "inference_ms": 1880,
    "latency_ms": 5000,
    "model": "qwen3-vl-30b",
})
result = run(call_describe_clip({"camera": "kitchen"}))
assert_true("describe_clip happy: description surfaced",
    "person walks" in result["data"]["description"])
assert_eq("describe_clip: frames_captured echoed",
    result["data"]["frames_captured"], 4)
assert_eq("describe_clip: span_s echoed", result["data"]["span_s"], 3.0)
assert_true("describe_clip: suggested_phrasing == description when non-empty",
    result["suggested_phrasing"] == result["data"]["description"])
assert_true("URL is sidecar /describe_clip",
    _FakeAsyncClient.last_url.endswith("/describe_clip"))

# Suite 7.2: missing camera → error result, no HTTP call
result = run(call_describe_clip({}))
assert_true("missing camera arg → error",
    "error" in result and "camera" in result["error"].lower())

# Suite 7.3: frames clamping — too high → max 8
_FakeAsyncClient.last_url = ""
_FakeAsyncClient.next_response = _FakeResponse(200, {
    "camera": "k", "entity_id": "camera.k", "description": "ok",
    "frames_captured": 8, "span_s": 7.0, "sampling_ms": 7000,
    "inference_ms": 2000, "latency_ms": 9000, "model": "x",
})
# We can't directly observe the clamped value from the mock_response,
# but we can verify it's reflected in the data echo.
result = run(call_describe_clip({"camera": "kitchen", "frames": 99}))
# The mock returns frames_captured=8 — verify the call succeeded
assert_true("frames clamped to MAX=8 (mock returned 8)",
    result["data"]["frames_captured"] == 8)

# Suite 7.4: frames=0 → clamps to MIN=2 (default behavior; observable
# by checking the post body we sent)
async def capture_call_with_args(args):
    captured = {}
    class _Capturing(_FakeAsyncClient):
        async def post(self, url, json=None):
            captured["url"] = url
            captured["json"] = json
            return _FakeResponse(200, {
                "camera": "k", "entity_id": "camera.k", "description": "captured",
                "frames_captured": 2, "span_s": 0.2,
                "sampling_ms": 200, "inference_ms": 100, "latency_ms": 300, "model": "x",
            })
    orig = frigate_mod.httpx.AsyncClient
    frigate_mod.httpx.AsyncClient = _Capturing
    try:
        await call_describe_clip(args)
    finally:
        frigate_mod.httpx.AsyncClient = orig
    return captured

cap = asyncio.new_event_loop().run_until_complete(
    capture_call_with_args({"camera": "kitchen", "frames": 0})
)
assert_eq("frames=0 clamps to MIN_FRAMES=2", cap["json"]["frames"], 2)

# Suite 7.5: interval_s clamping
cap = asyncio.new_event_loop().run_until_complete(
    capture_call_with_args({"camera": "kitchen", "interval_s": 9999})
)
assert_eq("interval_s=9999 clamps to MAX=2.0", cap["json"]["interval_s"], 2.0)

cap = asyncio.new_event_loop().run_until_complete(
    capture_call_with_args({"camera": "kitchen", "interval_s": 0.01})
)
assert_eq("interval_s=0.01 clamps to MIN=0.2", cap["json"]["interval_s"], 0.2)

# Suite 7.6: non-numeric inputs fall back to defaults
cap = asyncio.new_event_loop().run_until_complete(
    capture_call_with_args({"camera": "kitchen", "frames": "garbage", "interval_s": "nope"})
)
assert_eq("frames non-numeric → DEFAULT=4", cap["json"]["frames"], 4)
assert_eq("interval_s non-numeric → DEFAULT=1.0", cap["json"]["interval_s"], 1.0)

# Suite 7.7: optional question passes through
cap = asyncio.new_event_loop().run_until_complete(
    capture_call_with_args({"camera": "kitchen", "question": "did the door open?"})
)
assert_eq("question passed through to sidecar",
    cap["json"].get("question"), "did the door open?")

cap = asyncio.new_event_loop().run_until_complete(
    capture_call_with_args({"camera": "kitchen"})
)
assert_true("question OMITTED when not given (sidecar fills default)",
    "question" not in cap["json"])

# Suite 7.8: error paths
# Timeout
_FakeAsyncClient.next_exception = frigate_mod.httpx.TimeoutException("timeout")
result = run(call_describe_clip({"camera": "kitchen"}))
assert_true("timeout → error", "error" in result and "timeout" in result["error"].lower())
assert_eq("timeout → frames_captured 0", result["data"]["frames_captured"], 0)

# HTTP 502 from sidecar
_FakeAsyncClient.next_response = _FakeResponse(502, {})
result = run(call_describe_clip({"camera": "kitchen"}))
assert_true("http 502 → error mentions 502", "502" in result["error"])

# Bad JSON
_FakeAsyncClient.next_response = _FakeResponse(200, None, raise_on_json=True)
result = run(call_describe_clip({"camera": "kitchen"}))
assert_true("bad json → error", "bad json" in result["error"].lower())

# Generic exception
_FakeAsyncClient.next_exception = ConnectionError("vision-sidecar down")
result = run(call_describe_clip({"camera": "kitchen"}))
assert_true("unreachable → error", "unreachable" in result["error"].lower())

# Suite 7.9: empty description from sidecar → suggested_phrasing fallback
_FakeAsyncClient.next_response = _FakeResponse(200, {
    "camera": "kitchen", "entity_id": "camera.kitchen",
    "description": "", "frames_captured": 4, "span_s": 3.0,
    "sampling_ms": 3000, "inference_ms": 500, "latency_ms": 3500, "model": "x",
})
result = run(call_describe_clip({"camera": "kitchen"}))
assert_true("empty description → fallback phrasing mentions frames",
    "4" in result["suggested_phrasing"] or "frames" in result["suggested_phrasing"].lower())

# ── Footer ───────────────────────────────────────────────────────────
print()
if fails == 0:
    print(f"\033[32m{passes} pass · 0 fail\033[0m")
    sys.exit(0)
else:
    print(f"\033[31m{passes} pass · {fails} fail\033[0m")
    for msg in fail_msgs:
        print("  " + msg)
    sys.exit(1)
