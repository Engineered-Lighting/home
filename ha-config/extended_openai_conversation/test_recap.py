#!/usr/bin/env python3
"""Tests for functions/recap.py — daily recap composition tool
(Addendum 27 M2).

Covers:
  - Empty data path (no clips, no recorder) → "pretty quiet" phrasing
  - Single-clip path → room + person aggregation
  - Multi-clip + multi-person → counts, top_clips selection
  - Top_clips ordering (face matches FIRST, then chronological)
  - Window-hours clamping (0 → 1, 9999 → 168, "abc" → 6 default)
  - Recorder failure (sync_scope unavailable) → minutes_occupied null
  - Person-last aggregation picks the most recent ts
  - Summary hints capped + ordered
  - Suggested phrasing variants (quiet / one person / multiple)
  - Dispatcher unknown tool

Standalone runner — mocks Frigate + recorder so it runs offline.

Run: PYTHONIOENCODING=utf-8 py -3 test_recap.py
"""

from __future__ import annotations

import asyncio
import sys
import types
import time
from pathlib import Path


# ── Mock voluptuous + HA modules ────────────────────────────────────
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

ha = _mk("homeassistant")
ha_core = _mk("homeassistant.core")


class HomeAssistant:
    pass


ha_core.HomeAssistant = HomeAssistant
sys.modules.setdefault("homeassistant", ha)
sys.modules.setdefault("homeassistant.core", ha_core)

ha_helpers = _mk("homeassistant.helpers")
ha_helpers_llm = _mk("homeassistant.helpers.llm")


class _LLMCtx:
    device_id = None
    conversation_id = None


ha_helpers_llm.LLMContext = _LLMCtx
sys.modules.setdefault("homeassistant.helpers", ha_helpers)
sys.modules.setdefault("homeassistant.helpers.llm", ha_helpers_llm)

# httpx mock (find_clips path inside recap calls it; record nothing)
class _FakeResponse:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json


class _FakeAsyncClient:
    next_response: _FakeResponse | None = None

    def __init__(self, timeout=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None):
        return _FakeAsyncClient.next_response or _FakeResponse(200, [])


class _httpx:
    AsyncClient = _FakeAsyncClient

    class TimeoutException(Exception):
        pass


sys.modules.setdefault("httpx", _httpx)

# Package skeleton — recap imports from .frigate, which imports from
# ..frigate_sync. Provide both.
_pkg = _mk("extended_openai_conversation")
_pkg.__path__ = [str(Path(__file__).parent)]
_frigate_sync = _mk("extended_openai_conversation.frigate_sync")
_frigate_sync.base_url = lambda: "http://192.168.0.125:5000"
_exceptions = _mk("extended_openai_conversation.exceptions")


class _FunctionNotFound(Exception):
    pass


_exceptions.FunctionNotFound = _FunctionNotFound
_exceptions.NativeNotFound = _FunctionNotFound

_funcs = _mk("extended_openai_conversation.functions")
_base_mod = _mk("extended_openai_conversation.functions.base")


class Function:
    def __init__(self, *a, **k):
        pass


_base_mod.Function = Function

sys.modules.setdefault("extended_openai_conversation", _pkg)
sys.modules.setdefault("extended_openai_conversation.exceptions", _exceptions)
sys.modules.setdefault("extended_openai_conversation.frigate_sync", _frigate_sync)
sys.modules.setdefault("extended_openai_conversation.functions", _funcs)
sys.modules.setdefault("extended_openai_conversation.functions.base", _base_mod)

# Load functions/frigate.py as the .frigate submodule (recap imports
# `from .frigate import FrigateFunction, _iso_from_unix`)
import importlib.util

frigate_path = Path(__file__).parent / "functions" / "frigate.py"
frigate_spec = importlib.util.spec_from_file_location(
    "extended_openai_conversation.functions.frigate", frigate_path
)
frigate_mod = importlib.util.module_from_spec(frigate_spec)
sys.modules["extended_openai_conversation.functions.frigate"] = frigate_mod
frigate_spec.loader.exec_module(frigate_mod)

# Load functions/recap.py — the unit under test
recap_path = Path(__file__).parent / "functions" / "recap.py"
recap_spec = importlib.util.spec_from_file_location(
    "extended_openai_conversation.functions.recap", recap_path
)
recap_mod = importlib.util.module_from_spec(recap_spec)
sys.modules["extended_openai_conversation.functions.recap"] = recap_mod
recap_spec.loader.exec_module(recap_mod)

RecapFunction = recap_mod.RecapFunction

# ── Tiny test harness ────────────────────────────────────────────────
passes = 0
fails = 0
fail_msgs: list[str] = []


def assert_eq(name, actual, expected):
    global passes, fails
    if actual == expected:
        passes += 1
        print(f"  PASS  {name}")
    else:
        fails += 1
        fail_msgs.append(f"FAIL {name}: expected {expected!r}, got {actual!r}")
        print(f"  FAIL  {name}\n        expected: {expected!r}\n        actual:   {actual!r}")


def assert_true(name, cond, hint=""):
    global passes, fails
    if cond:
        passes += 1
        print(f"  PASS  {name}")
    else:
        fails += 1
        fail_msgs.append(f"FAIL {name}: {hint}")
        print(f"  FAIL  {name} — {hint}")


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _ev(camera="kitchen", sub_label=None, age_s=30, label="person", has_clip=True):
    """Build a Frigate event dict the way find_clips returns one."""
    now = time.time()
    return {
        "id": f"e-{camera}-{int(age_s)}-{hash((camera, sub_label or '')) & 0xFFFF}",
        "start_time": now - age_s,
        "end_time": now - age_s + 3,
        "camera": camera,
        "label": label,
        "sub_label": [sub_label, 0.9] if sub_label else None,
        "has_clip": has_clip,
        "has_snapshot": True,
    }


async def call_recap(args=None, fake_events=None):
    """Run recap with a stubbed find_clips return value. fake_events
    is a list of Frigate event dicts (raw API shape). Recorder is also
    stubbed to return {} so minutes_occupied = null throughout."""
    fn = RecapFunction()
    _FakeAsyncClient.next_response = _FakeResponse(200, fake_events or [])
    # Patch _occupancy_minutes to return {} (recorder not available
    # in this test harness — HA's recorder module is not present)
    fn._occupancy_minutes = (lambda *a, **k: _noop())
    return await fn.execute(
        hass=None,
        function_config={"name": "recap"},
        arguments=args or {},
        llm_context=None,
        exposed_entities=[],
    )


async def _noop():
    return {}


async def call_recap_with_minutes(args, fake_events, minutes_by_room):
    fn = RecapFunction()
    _FakeAsyncClient.next_response = _FakeResponse(200, fake_events or [])
    async def _stub(*a, **k):
        return dict(minutes_by_room)
    fn._occupancy_minutes = _stub
    return await fn.execute(
        hass=None,
        function_config={"name": "recap"},
        arguments=args,
        llm_context=None,
        exposed_entities=[],
    )


# ── Suite 1: empty + clamping ────────────────────────────────────────
print("\nempty_and_clamping_test")

r = run(call_recap({"window_hours": 6}, []))
assert_eq("empty result window_hours = 6", r["data"]["window_hours"], 6)
assert_eq("empty result rooms list empty", r["data"]["rooms"], [])
assert_eq("empty result people list empty", r["data"]["people"], [])
assert_eq("empty result top_clips empty", r["data"]["top_clips"], [])
assert_eq("empty freshness = none", r["freshness"], "none")
assert_true("empty hints mention 'nothing notable'",
            "nothing notable" in (r["data"]["summary_hints"] or [""])[0])
assert_true("empty suggested_phrasing mentions quiet",
            "quiet" in r["suggested_phrasing"].lower())

# Clamping: 0 → 1
r = run(call_recap({"window_hours": 0}, []))
assert_eq("window_hours=0 clamps to 1", r["data"]["window_hours"], 1)

# Clamping: 999 → 168
r = run(call_recap({"window_hours": 9999}, []))
assert_eq("window_hours=9999 clamps to 168", r["data"]["window_hours"], 168)

# Non-numeric → default 6
r = run(call_recap({"window_hours": "garbage"}, []))
assert_eq("window_hours non-numeric → default 6", r["data"]["window_hours"], 6)

# Missing arg → default 6
r = run(call_recap({}, []))
assert_eq("missing window_hours → default 6", r["data"]["window_hours"], 6)

# ── Suite 2: single-room single-person ──────────────────────────────
print("\nsingle_person_test")

events = [
    _ev(camera="kitchen", sub_label="marcelo", age_s=30),
    _ev(camera="kitchen", sub_label="marcelo", age_s=120),
    _ev(camera="kitchen", sub_label="marcelo", age_s=300),
]
r = run(call_recap({"window_hours": 1}, events))
rooms = r["data"]["rooms"]
people = r["data"]["people"]
assert_eq("single room appears", len(rooms), 1)
assert_eq("kitchen has 3 clips", rooms[0]["clips"], 3)
assert_eq("marcelo identified 3x in kitchen",
          rooms[0]["identified"], [{"name": "marcelo", "seen": 3}])
assert_eq("single person aggregated", len(people), 1)
assert_eq("marcelo total_seen = 3", people[0]["total_seen"], 3)
assert_eq("marcelo last_room = kitchen", people[0]["last_room"], "kitchen")
assert_true("marcelo last_seen_ago_s is roughly 30s",
            people[0]["last_seen_ago_s"] is not None and abs(people[0]["last_seen_ago_s"] - 30) < 5,
            f"got {people[0]['last_seen_ago_s']}")
assert_eq("freshness = fresh (30s ago)", r["freshness"], "fresh")
assert_true("phrasing mentions marcelo + kitchen",
            "marcelo" in r["suggested_phrasing"].lower() and "kitchen" in r["suggested_phrasing"].lower())

# ── Suite 3: multi-room multi-person + top_clips ─────────────────────
print("\nmulti_room_multi_person_test")

events = [
    _ev(camera="kitchen", sub_label="marcelo", age_s=30),
    _ev(camera="kitchen", sub_label="amelia", age_s=60),
    _ev(camera="living_room", sub_label="amelia", age_s=90),
    _ev(camera="driveway", sub_label=None, age_s=200, label="car"),
    _ev(camera="kitchen", sub_label=None, age_s=400),       # generic person event
]
r = run(call_recap({"window_hours": 6}, events))
rooms = r["data"]["rooms"]
people = r["data"]["people"]

# Rooms sorted by activity
assert_eq("3 rooms appear", len(rooms), 3)
room_names = [r["room"] for r in rooms]
assert_eq("kitchen first (most clips=3)", room_names[0], "kitchen")

# Kitchen has marcelo + amelia
kitchen = next(r for r in rooms if r["room"] == "kitchen")
assert_eq("kitchen identified count = 2 (marcelo + amelia)",
          len(kitchen["identified"]), 2)

# Amelia total_seen = 2 (kitchen + living_room)
amelia = next(p for p in people if p["name"] == "amelia")
assert_eq("amelia total_seen = 2", amelia["total_seen"], 2)

# Top clips: face-matched ones come BEFORE unmatched
top_clips = r["data"]["top_clips"]
assert_true("top_clips count <= 4 (TOP_CLIPS_OUTPUT cap)",
            len(top_clips) <= 4)
face_count = sum(1 for c in top_clips if c.get("sub_label"))
unmatched_count = sum(1 for c in top_clips if not c.get("sub_label"))
# All face clips should appear before any unmatched in the result
first_unmatched = next((i for i, c in enumerate(top_clips) if not c.get("sub_label")), len(top_clips))
last_face = next((i for i in range(len(top_clips) - 1, -1, -1) if top_clips[i].get("sub_label")), -1)
assert_true("face clips ordered before unmatched in top_clips",
            last_face < first_unmatched,
            f"last_face={last_face} first_unmatched={first_unmatched}")

# ── Suite 4: minutes_occupied via recorder stub ─────────────────────
print("\nrecorder_minutes_test")

events = [
    _ev(camera="kitchen", sub_label="marcelo", age_s=30),
    _ev(camera="living_room", sub_label=None, age_s=300),
]
minutes = {"kitchen": 42, "office": 15}    # office has no clips but had occupancy
r = run(call_recap_with_minutes({"window_hours": 6}, events, minutes))
rooms = r["data"]["rooms"]
room_by_name = {r["room"]: r for r in rooms}
assert_eq("kitchen minutes_occupied = 42", room_by_name["kitchen"]["minutes_occupied"], 42)
assert_eq("office appears (no clips, just occupancy)",
          room_by_name.get("office", {}).get("minutes_occupied"), 15)
assert_eq("office clips = 0", room_by_name["office"]["clips"], 0)
assert_eq("living_room minutes_occupied null (not in stub)",
          room_by_name["living_room"]["minutes_occupied"], None)

# Phrasing should incorporate the minutes
assert_true("phrasing mentions kitchen + minutes",
            "kitchen" in r["suggested_phrasing"].lower() and "42" in r["suggested_phrasing"])

# ── Suite 5: clip source error path ─────────────────────────────────
print("\nclip_source_error_test")

# Simulate find_clips internal error (httpx unreachable)
_FakeAsyncClient.next_response = None
class _ErrorAsyncClient(_FakeAsyncClient):
    async def get(self, url, params=None):
        raise ConnectionError("frigate down")


orig_client = frigate_mod.httpx.AsyncClient
frigate_mod.httpx.AsyncClient = _ErrorAsyncClient

try:
    fn = RecapFunction()
    fn._occupancy_minutes = (lambda *a, **k: _noop())
    r = run(fn.execute(
        hass=None, function_config={"name": "recap"},
        arguments={"window_hours": 1}, llm_context=None, exposed_entities=[],
    ))
    assert_true("clip_source_error surfaced in data",
                "clip_source_error" in r["data"])
    assert_true("phrasing flags unavailable",
                "unavailable" in r["suggested_phrasing"].lower())
finally:
    frigate_mod.httpx.AsyncClient = orig_client

# ── Suite 6: summary hints structure ────────────────────────────────
print("\nsummary_hints_test")

# 4+ rooms — hints should cap at 6 entries
events = []
for i, room in enumerate(["kitchen", "living_room", "office", "driveway", "workshop"]):
    events.append(_ev(camera=room, sub_label="marcelo" if i < 2 else None, age_s=30 + i * 60))

r = run(call_recap({"window_hours": 6}, events))
hints = r["data"]["summary_hints"]
assert_true("hints capped at 6", len(hints) <= 6)
assert_true("hints non-empty when there's activity",
            len(hints) >= 1 and "nothing notable" not in hints[0])

# ── Suite 7: dispatcher ─────────────────────────────────────────────
print("\ndispatcher_test")

fn = RecapFunction()
r = run(fn.execute(
    hass=None, function_config={"name": "unknown_tool"},
    arguments={}, llm_context=None, exposed_entities=[],
))
assert_true("unknown tool returns error", "error" in r and "unknown" in r["error"])

# ── Suite 8: _seconds_since helper ──────────────────────────────────
print("\nseconds_since_helper_test")

now = time.time()
assert_eq("None ts returns None", recap_mod._seconds_since("", now), None)
# Generate an ISO ts that's 90s old
from datetime import datetime, timezone
iso_90s = (datetime.fromtimestamp(now - 90, tz=timezone.utc)
           .strftime("%Y-%m-%dT%H:%M:%SZ"))
diff = recap_mod._seconds_since(iso_90s, now)
assert_true(f"90s-old ts returns ~90 (got {diff})",
            diff is not None and abs(diff - 90) < 5)

# Future ts clamps to 0
iso_future = (datetime.fromtimestamp(now + 100, tz=timezone.utc)
              .strftime("%Y-%m-%dT%H:%M:%SZ"))
assert_eq("future ts clamps to 0",
          recap_mod._seconds_since(iso_future, now), 0)

assert_eq("malformed ts returns None",
          recap_mod._seconds_since("not-a-date", now), None)

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
