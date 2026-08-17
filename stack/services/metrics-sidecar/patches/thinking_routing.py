"""thinking_routing.py — the routing rule from patch 0001, as testable code.

This is the exact function to paste into the LIVE metrics-sidecar's
`main.py` (see `0001-route-thinking-by-tool-presence.md`). It lives here as
a standalone module so the decision table can be unit-tested before anything
is built or deployed — a proxy rule that silently mis-routes would either
reinstate a 15% reasoning leak or blow the ambient latency budget, and
neither shows up until it is live.

No imports from the sidecar, so `tools/test-thinking-routing.py` can import
it directly.
"""
from __future__ import annotations

import os

# Rollback switch: set THINKING_ROUTING=off and recreate the container to
# pass every request through untouched. No rebuild required.
THINKING_ROUTING = os.environ.get("THINKING_ROUTING", "on").strip().lower()


def route_thinking(body: dict, enabled: str | None = None) -> str | None:
    """Suppress thinking on tool-free requests. Mutates `body` in place.

    Returns a short decision string for logging, or None when nothing was
    changed.

    The rule, and why each branch exists:

      * `chat_template_kwargs` already present -> LEAVE ALONE. Caller intent
        wins, so a probe or a future per-surface override can opt in or out
        without fighting the proxy. Overriding it would also silently break
        the leak reproducer, which sets its own kwargs.
      * `tools` present and non-empty -> LEAVE ALONE. The server default is
        thinking ON plus `--reasoning-parser qwen3`, which measured 0/40 on
        exactly this shape. Suppressing thinking here is what leaks.
      * otherwise -> set `enable_thinking: False`. Tool-free requests are
        captions and grounded looks; they never leaked with thinking off
        (0/20) and they are the ~1,400/day that must stay fast.

    `enabled` is injectable purely so tests can exercise the off path
    without mutating process environment.
    """
    mode = THINKING_ROUTING if enabled is None else enabled
    if mode != "on":
        return None
    if not isinstance(body, dict):
        return None
    if "chat_template_kwargs" in body:
        return None
    if body.get("tools"):
        return "on (tools present)"
    body["chat_template_kwargs"] = {"enable_thinking": False}
    return "off (no tools)"
