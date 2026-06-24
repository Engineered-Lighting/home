"""Conversation / device  <->  room binding for proactive follow-ups.

Leaf module: imports nothing from this component, so both conversation.py
and functions/native.py can import it with no circular-dependency risk.

The Home app's proactive coordinator tags a room-entry prompt's
``extra_system_prompt`` with a machine-readable ``[[hg-room:<room>]]``
marker. This module parses that marker and caches the binding under a
short TTL, keyed by BOTH the conversation_id and the device_id, so the
conversation agent can feed the bound room to the LLM as the
authoritative current area on every turn of that conversation.
"""

from __future__ import annotations

import re
import time

# key (conversation_id or device_id) -> (created_ts, room_id)
_BINDING: dict[str, tuple[float, str]] = {}

# Short on purpose: a proactive opener and its follow-up reply(ies) happen
# back-to-back. Expiring a fixed ~90 s from creation (NO refresh-on-hit)
# bounds any bleed into a later, unrelated conversation that happens to
# share a conversation_id (via the component's DEVICE_CONV_MEMORY) or the
# device_id.
_TTL_SEC: float = 90.0

_MARKER_RE = re.compile(r"\[\[hg-room:([a-z0-9_]+)\]\]")


def parse_marker(extra_system_prompt: str | None) -> str | None:
    """Extract the room id from a ``[[hg-room:<room>]]`` marker, else None."""
    if not extra_system_prompt:
        return None
    match = _MARKER_RE.search(extra_system_prompt)
    return match.group(1) if match else None


def bind(room: str, *keys: str | None) -> None:
    """Store ``room`` under each non-empty key, timestamped now.

    Opportunistically prunes expired entries first -- conversation_id keys
    are otherwise unbounded (every conversation is a fresh id).
    """
    now = time.time()
    for stale in [k for k, (ts, _) in list(_BINDING.items()) if now - ts > _TTL_SEC]:
        _BINDING.pop(stale, None)
    for key in keys:
        if key:
            _BINDING[key] = (now, room)


def resolve(*keys: str | None) -> str | None:
    """Return the bound room for the first non-expired key, else None.

    No TTL refresh on hit -- the binding expires a fixed ~90 s from
    creation, which bounds bleed into a later unrelated conversation.
    """
    now = time.time()
    for key in keys:
        if not key:
            continue
        hit = _BINDING.get(key)
        if hit and (now - hit[0]) < _TTL_SEC:
            return hit[1]
    return None
