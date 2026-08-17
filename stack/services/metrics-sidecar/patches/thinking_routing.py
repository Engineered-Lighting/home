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


# ── Downstream leak recovery (patch 0002) ──────────────────────────────
# The checkpoint sometimes emits a stray `</think>` into `content` and then
# re-answers, producing "answer</think>answer</think>answer". A reasoning
# parser would route that out upstream, but it cannot: with thinking
# suppressed the template pre-fills an already-CLOSED think block, so the
# generated text has no opening tag for the parser to match and it never
# engages. Recovering downstream works precisely because it does not care
# how the block was opened.
#
# Deliberately conservative: it only acts when a delimiter is actually
# present, so non-leaking output is returned byte-identical.

_THINK_OPEN = None  # compiled lazily to keep import cost at zero


def recover_leaked_content(text):
    """Return the final answer from a possibly-leaked completion.

    "answer A</think>answer B" -> "answer B". Text with no delimiter is
    returned unchanged, so this is a no-op on the ~85% that never leak and
    on every incumbent response.
    """
    global _THINK_OPEN
    if not isinstance(text, str) or "</think>" not in text:
        return text
    if _THINK_OPEN is None:
        import re as _re
        _THINK_OPEN = _re.compile(r"<think\b[^>]*>")
    tail = text.rsplit("</think>", 1)[-1]
    tail = _THINK_OPEN.sub("", tail)
    stripped = tail.strip()
    # If the trailing segment is empty the model closed and said nothing —
    # fall back to what came BEFORE the delimiter rather than returning "".
    # Silence is worse than slightly-odd text: the user hears nothing and
    # the turn looks broken.
    if not stripped:
        head = text.rsplit("</think>", 1)[0]
        head = _THINK_OPEN.sub("", head)
        return head.strip()
    return stripped
