#!/usr/bin/env python3
"""Tests for the metrics-sidecar thinking-routing rule (patch 0001).

A proxy rule that mis-routes fails in two expensive directions and neither
is visible until it is live: route a tool-bearing request to thinking-off
and the ~15% `</think>`-to-TTS leak comes back; route a caption to
thinking-on and ambient latency goes from 0.18 s to 4.22 s against a 1.5 s
budget. So the decision table is pinned here before anything is built.

Run: python3 tools/test-thinking-routing.py
"""
from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "stack" / "services" / "metrics-sidecar" / "patches"))
from thinking_routing import route_thinking  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


def kw(body):
    return body.get("chat_template_kwargs")


print("tool_free_requests_get_thinking_off")
# The ~1,400/day that must stay fast. Proven not to leak with thinking off.
b = {"model": "m", "messages": [{"role": "user", "content": "describe"}]}
d = route_thinking(b, enabled="on")
check("no tools -> thinking suppressed", kw(b) == {"enable_thinking": False}, str(b))
check("decision reported", d == "off (no tools)", str(d))

b = {"model": "m", "messages": [], "tools": []}
route_thinking(b, enabled="on")
check("EMPTY tools list counts as tool-free", kw(b) == {"enable_thinking": False})

print("\ntool_bearing_requests_are_left_alone")
# This is the shape that leaks when thinking is suppressed (3/20).
b = {"model": "m", "messages": [], "tools": [{"type": "function",
                                              "function": {"name": "areas_in_home"}}]}
d = route_thinking(b, enabled="on")
check("tools present -> body untouched", kw(b) is None, str(b))
check("decision reported", d == "on (tools present)", str(d))

b = {"tools": [{"type": "function", "function": {"name": "a"}},
               {"type": "function", "function": {"name": "b"}}]}
route_thinking(b, enabled="on")
check("multiple tools -> untouched", kw(b) is None)

print("\ncaller_intent_always_wins")
# The leak reproducer sets its own kwargs; overriding them would silently
# invalidate the very harness used to verify this patch.
b = {"chat_template_kwargs": {"enable_thinking": True}}
d = route_thinking(b, enabled="on")
check("explicit kwargs preserved verbatim",
      kw(b) == {"enable_thinking": True}, str(b))
check("no decision claimed", d is None)

b = {"chat_template_kwargs": {"reasoning_effort": "low"}, "tools": []}
route_thinking(b, enabled="on")
check("explicit kwargs win over the tool-free rule",
      kw(b) == {"reasoning_effort": "low"})

b = {"chat_template_kwargs": {}}
route_thinking(b, enabled="on")
check("even an EMPTY kwargs dict is caller intent", kw(b) == {})

print("\nrollback_switch")
b = {"messages": []}
d = route_thinking(b, enabled="off")
check("THINKING_ROUTING=off passes through untouched", kw(b) is None and d is None)
b = {"messages": []}
route_thinking(b, enabled="anything-else")
check("any non-'on' value disables routing", kw(b) is None)

print("\nmalformed_input_is_survivable")
# The proxy must never 500 on a weird body; it forwards whatever it got.
check("None body is ignored", route_thinking(None, enabled="on") is None)
check("list body is ignored", route_thinking([], enabled="on") is None)
b = {"tools": None}
route_thinking(b, enabled="on")
check("tools=None is tool-free", kw(b) == {"enable_thinking": False})

print("\nno_other_fields_are_disturbed")
b = {"model": "qwen3-vl-30b", "messages": [{"role": "user", "content": "hi"}],
     "stream": True, "max_tokens": 200, "temperature": 0.2}
before = {k: v for k, v in b.items()}
route_thinking(b, enabled="on")
check("only chat_template_kwargs is added",
      set(b) - set(before) == {"chat_template_kwargs"}, str(set(b) - set(before)))
check("existing values unchanged",
      all(b[k] == before[k] for k in before))

print()
if FAILURES:
    print(f"thinking-routing tests: {len(FAILURES)} FAILED -> {FAILURES}")
    sys.exit(1)
print("thinking-routing tests: all passed")
