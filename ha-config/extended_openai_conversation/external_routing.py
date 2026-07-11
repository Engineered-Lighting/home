"""External Reasoning Fallback for HA-side voice + chat conversation input.

Mirrors the Tauri-side router in `home/app/src/home-external.jsx`. Same
rules, same bare-prompt-no-home-context policy, same OpenAI provider —
just running inside HA so voice utterances (which route via the Voice
PE → Wyoming → assist_pipeline → ConversationEntity flow, NOT through
the Tauri app's `sendInput()`) get the same general-knowledge fallback.

ROUTING runs BEFORE the system prompt is assembled in conversation.py,
so home context (entity IDs, room state, perception summaries) never
reaches the external provider. The local home agent's constrained
prompt is unchanged for local-routed messages.

SAFETY: the external provider's response is wrapped as a plain
IntentResponse speech body. No tool calls are parsed from it, no HA
services are invoked. If the model suggests "set lights to 30%", that
becomes TTS audio describing a suggestion — never an action.

CONFIG: the OpenAI API key is read from a single line in
`/config/extended_openai_conversation_external_key` (mode 0600 owned
by the homeassistant user). If the file is absent or empty, external
routing is disabled and every message falls through to the local
agent — voice control of the home keeps working unchanged.

KEEP IN SYNC: the four regex constants below must stay character-for-
character identical with the JS version in
`home/app/src/home-external.jsx`. Use the test slash commands
`/test classifier` (Tauri side) and `pytest test_external_routing.py`
(here, if/when added) to validate after any rule edit.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

# NOTE: httpx is imported lazily inside ask_external() so this module
# can be loaded outside an HA context (the standalone test runner at
# test_external_routing.py imports classify_intent/get_api_key without
# needing httpx).

_LOGGER = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Classifier rules — MIRRORED FROM home-external.jsx. Keep in sync.
# ─────────────────────────────────────────────────────────────────────
_HOME_VERBS = re.compile(
    r"\b(turn (on|off)|dim|brighten|warmer|cooler|set the|pause|resume|"
    r"play|skip|next|previous|mute|unmute|volume|louder|quieter|lock|"
    r"unlock|open|close)\b"
)
_HOME_NOUNS = re.compile(
    r"\b(light|lights|lamp|kitchen|living room|office|dining|workshop|"
    r"bedroom|hallway|driveway|garage|sonos|spotify|stereo|speakers?|tv|"
    r"movie mode|frigate|camera|home assistant|home-?app|haos|metrics|"
    r"gpu|vram|vllm|stack|ai stack|presence|the room|in here|in the room)\b"
)
_HOME_QUERIES = re.compile(
    r"\b(what do you see|who('s| is) home|who('s| is) in the|"
    r"is anyone (home|in the|here)|am i home|are we home)\b"
)
_GENERAL = re.compile(
    r"\b(explain|what (is|are|does|year|day|century)|"
    r"how (do|does) .* work|difference between|compare|teach me|"
    r"tell me (about|a)|describe|define|"
    r"who (is|was|invented|wrote|made|created|discovered|founded|painted)|"
    r"when (is|was|did|were|will)|where (is|was|did|are)|"
    r"which (is|are|was|of)|"
    r"list (the|some|all)|name (the|a|some)|"
    r"(tldr|tl;?dr|summary|overview) (on|of|for|about)|"
    r"why (is|are|does) .* (faster|slower|better|worse)|"
    r"help me (write|understand|think|brainstorm)|give me ideas|code review)\b"
)


def classify_intent(text: str) -> str:
    """Returns "local" | "external" | "ambiguous". Same semantics as the
    JS classifyIntent: first-match wins, defaults to ambiguous (caller
    treats as local — safer fallback).

    Convenience shim — for gap analysis you want classify_intent_with_rule()
    which also tells you WHICH rule matched.
    """
    intent, _ = classify_intent_with_rule(text)
    return intent


def classify_intent_with_rule(text: str) -> tuple[str, str]:
    """Same as classify_intent but also returns the name of the rule
    that fired ("HOME_VERBS" / "HOME_NOUNS" / "HOME_QUERIES" /
    "GENERAL" / "-"). Used by the routing log so gap analysis can
    distinguish "GENERAL didn't catch this" from "HOME_NOUNS caught
    something that should have gone external."
    """
    s = (text or "").strip().lower()
    if not s:
        return "ambiguous", "-"
    if _HOME_VERBS.search(s):   return "local",    "HOME_VERBS"
    if _HOME_NOUNS.search(s):   return "local",    "HOME_NOUNS"
    if _HOME_QUERIES.search(s): return "local",    "HOME_QUERIES"
    if _GENERAL.search(s):      return "external", "GENERAL"
    return "ambiguous", "-"


# ─────────────────────────────────────────────────────────────────────
# Provider config — read API key from a file (not env var, not yaml,
# so the user controls it without restarting the HA supervisor).
# ─────────────────────────────────────────────────────────────────────
_KEY_PATH = Path("/config/extended_openai_conversation_external_key")


# ─────────────────────────────────────────────────────────────────────
# Routing log — JSONL, append-only. The analyzer (workstation-side)
# pulls this file over SSH and joins it with the chat-tee SSE corpus.
#
# Three modes via EXTERNAL_ROUTING_LOG_MODE env var:
#   - "full"     (default) — text + responses captured
#   - "redacted" — lengths + intent + matched rule only, no content
#   - "off"      — no log at all
#
# Each decision also fires an HA event so the Tauri app (or any other
# event-bus listener) can subscribe and render decisions live —
# /debug on in the Home app shows them as kind:"diag" chat events.
# The same redaction mode applies: "redacted" strips content, "off"
# skips the event entirely. Single source of truth for what content
# leaves log_decision().
# ─────────────────────────────────────────────────────────────────────
_LOG_PATH = Path("/config/external_routing.log")
_LOG_MODE_ENV = "EXTERNAL_ROUTING_LOG_MODE"
_VALID_MODES = ("full", "redacted", "off")
EVENT_ROUTING_DECISION = "extended_openai_conversation.routing_decision"


def _log_mode() -> str:
    # Locked containment default: historical environments often set this to
    # ``full``, which persisted utterances and response snippets under
    # /config.  The legacy external router is disabled for the MVP, so its
    # contentful audit channel is hard-off too.  A later reviewed design must
    # introduce a new content-minimized ledger rather than flipping an env var.
    return "off"


def _write_log_line_sync(line: str) -> None:
    """Append a single JSONL line to the routing log. Best-effort:
    silently swallows OSError so a full disk / bad perms can't break
    conversation routing. Caller is responsible for off-loop dispatch.
    """
    try:
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except (OSError, PermissionError) as e:
        _LOGGER.debug("external_routing: log write failed: %s", e)


# M0.4 — multi-kind routing-log schema. Today's entries are implicitly
# `kind: "routing_decision"`; new milestones (M1 cache hit, M6 confirm
# pause, per-tool latency from execute_service_single) emit their own
# kinds against the SAME conv_id so the analyzer can reconstruct the
# full per-turn timeline. Backcompat: missing `kind` defaults to
# routing_decision when read.
LOG_KIND_ROUTING_DECISION = "routing_decision"   # today's default
LOG_KIND_TOOL_CALL = "tool_call"                 # per-tool latency + result (M0)
LOG_KIND_PERCEPTION_DISPATCH = "perception_dispatch"  # M1 cache hit/miss
LOG_KIND_CONFIRMATION = "confirmation"           # M6 high-impact pause
_VALID_LOG_KINDS = frozenset({
    LOG_KIND_ROUTING_DECISION,
    LOG_KIND_TOOL_CALL,
    LOG_KIND_PERCEPTION_DISPATCH,
    LOG_KIND_CONFIRMATION,
})

# Fields kept under `redacted` mode. Per-kind: routing_decision keeps
# the original audit set; tool_call keeps tool name + latency + result
# status (no args, no response text); perception_dispatch keeps room +
# cache verdict (no caption text); confirmation keeps domain + service
# (no entity_ids if they're considered sensitive).
_REDACT_KEEP_BY_KIND: dict[str, set[str]] = {
    LOG_KIND_ROUTING_DECISION: {
        "ts", "kind", "conv_id", "src", "text_len",
        "intent", "matched", "key_present",
        "dispatched",
        "ext_status", "ext_latency_ms", "ext_reply_chars",
        "local_reply_chars",
    },
    LOG_KIND_TOOL_CALL: {
        "ts", "kind", "conv_id",
        "tool_name", "domain", "service",
        "latency_ms", "ok", "error_kind",
    },
    LOG_KIND_PERCEPTION_DISPATCH: {
        "ts", "kind", "conv_id",
        "room", "verdict",  # verdict in {cache_hit, cache_miss, debounced, rate_capped}
        "age_seconds", "latency_ms",
    },
    LOG_KIND_CONFIRMATION: {
        "ts", "kind", "conv_id",
        "domain", "service", "intercepted", "resolved",
    },
}


def _redact_entry(entry: dict) -> dict:
    """Return a copy of the entry with all user/response content removed,
    keeping only metadata appropriate for the entry's `kind`. Missing
    `kind` falls back to routing_decision for backcompat with pre-M0
    callers.
    """
    kind = entry.get("kind") or LOG_KIND_ROUTING_DECISION
    keep = _REDACT_KEEP_BY_KIND.get(kind, _REDACT_KEEP_BY_KIND[LOG_KIND_ROUTING_DECISION])
    return {k: v for k, v in entry.items() if k in keep}


async def log_decision(hass, entry: dict) -> None:
    """Persist a routing-log entry to /config/external_routing.log.

    `entry` is a dict matching one of the LOG_KIND_* schemas. The
    function fills in `kind: routing_decision` if absent (backcompat).
    The entry is serialized as one JSON line and appended via the HA
    executor pool so the asyncio event loop isn't blocked on disk I/O.

    Mode behaviour:
      - "full"     → entry written verbatim
      - "redacted" → content fields stripped, metadata preserved per
                     the per-kind allow-list above
      - "off"      → no-op

    Multi-entry-per-turn pattern (Addendum 27 / Section 13 / M0.4):
    callers MAY emit multiple log_decision() calls per user turn,
    sharing the same `conv_id`, with different `kind` values. The
    analyzer joins them by conv_id to reconstruct the full per-turn
    timeline (decision → tool calls → perception dispatches → final
    response).
    """
    mode = _log_mode()
    if mode == "off":
        return
    # Default kind = routing_decision so all existing call sites keep
    # working untouched.
    if "kind" not in entry:
        entry = {**entry, "kind": LOG_KIND_ROUTING_DECISION}
    if mode == "redacted":
        entry = _redact_entry(entry)
    try:
        # ensure_ascii=False keeps utf-8 inline; default=str handles
        # any datetime/Path/Exception that slipped into the dict.
        line = json.dumps(entry, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as e:
        _LOGGER.debug("external_routing: log serialize failed: %s", e)
        return
    await hass.async_add_executor_job(_write_log_line_sync, line)
    # Fire the HA event with the SAME (already-redacted) entry — the
    # event-bus payload mirrors the file write exactly, so there's one
    # source of truth for what content escapes log_decision(). Live
    # listeners (Tauri /debug on, or any other integration that wants
    # to subscribe) receive it within ~1s of the file write.
    try:
        hass.bus.async_fire(EVENT_ROUTING_DECISION, entry)
    except Exception as e:  # noqa: BLE001 — never break routing on event fire
        _LOGGER.debug("external_routing: event fire failed: %s", e)


def _read_api_key_sync() -> Optional[str]:
    """Sync file read — caller is responsible for off-loop dispatch."""
    try:
        if not _KEY_PATH.is_file():
            return None
        raw = _KEY_PATH.read_text(encoding="utf-8").strip()
        if not raw or len(raw) < 10:
            return None
        return raw
    except (OSError, PermissionError) as e:
        _LOGGER.debug("external_routing: key file unreadable: %s", e)
        return None


async def get_api_key(hass) -> Optional[str]:
    """Read the OpenAI API key from /config/extended_openai_conversation_external_key,
    via HA's executor so the asyncio loop isn't blocked on disk I/O.

    Returns None if the file is missing, unreadable, or empty. No caching
    so that key rotation (write the new key, no restart needed) takes
    effect on the next request — at the cost of one cheap stat+read per
    conversation turn.
    """
    return await hass.async_add_executor_job(_read_api_key_sync)


# ─────────────────────────────────────────────────────────────────────
# Bare system prompts. TTS-friendly version is the default here because
# voice is the primary HA-side path; chat callers can override.
# ─────────────────────────────────────────────────────────────────────
_SYSTEM_PROMPT_TTS = (
    "You are a helpful general-knowledge assistant answering through a "
    "voice interface. The user is talking through a smart-home assistant "
    "but you do NOT have access to their home, devices, cameras, or any "
    "private state. Answer in plain prose suitable for text-to-speech: "
    "no markdown, no bullet points, no numbered lists, no headings, no "
    "code fences. Aim for two to four sentences. If asked to perform a home action (turn on lights, "
    "play music, etc.) say you can answer questions but cannot control "
    "the user's home — they should ask their home assistant directly."
)


# ─────────────────────────────────────────────────────────────────────
# OpenAI call. Single-shot, no streaming (HA voice waits for full
# response before TTS-ing it; streaming chunks wouldn't help here).
# ─────────────────────────────────────────────────────────────────────
async def ask_external(
    hass: Any,
    text: str,
    api_key: str,
    model: str = "gpt-4o-mini",
    max_tokens: int = 300,
    timeout_s: float = 30.0,
) -> str:
    """Single-shot OpenAI Chat Completion. Returns the full response text.

    Privacy invariant: only `text` + the bare TTS-friendly system prompt
    are sent. There is no parameter for home context — by design.
    Raises on HTTP error or timeout; caller handles fallback.

    Uses HA's shared httpx client (cached, SSL-context pre-warmed at HA
    startup) instead of constructing a fresh httpx.AsyncClient per
    request — the latter hits a blocking SSL ctx init on the asyncio
    loop, which HA flags as a "blocking call" warning every conversation
    turn.
    """
    # Import lazily so the module can be loaded outside HA for the
    # standalone test runner (which only needs classify_intent +
    # get_api_key).
    import httpx  # noqa: F401  (used implicitly via the shared client)
    from homeassistant.helpers.httpx_client import get_async_client

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT_TTS},
            {"role": "user", "content": text},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.5,
    }
    client = get_async_client(hass)
    r = await client.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=timeout_s,
    )
    if r.status_code != 200:
        # Hygiene: never log headers or the request body (would leak the
        # bearer). Status code alone is enough for triage.
        _LOGGER.warning("external_routing: openai http %d", r.status_code)
        raise RuntimeError(f"openai http {r.status_code}")
    data = r.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        _LOGGER.warning("external_routing: malformed openai response: %s", e)
        raise RuntimeError("openai malformed response")
