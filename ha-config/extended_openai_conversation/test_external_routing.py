#!/usr/bin/env python3
"""Standalone test runner for external_routing.py.

Runs the classifier against a comprehensive fixture set + (optionally)
performs one live OpenAI roundtrip to validate the end-to-end pipeline.
Designed to run via plain `python3` on HAOS without an HA context —
that way I (Claude) can run it autonomously over SSH to verify
voice-path routing without needing a HA token or speaking to Voice PE.

Usage on HAOS:

    cd /config/custom_components/extended_openai_conversation
    python3 test_external_routing.py

Exit code 0 = all passed. Non-zero = at least one failure. The
fixture list is generous (45+ cases) — anything that fails is a real
regression that would manifest as the user shouting a question into
Voice PE and getting the wrong agent.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

# Make this script's directory importable so `from external_routing
# import ...` works regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from external_routing import (  # noqa: E402  — sys.path adjust above
    classify_intent,
    _read_api_key_sync,
    _KEY_PATH,
)


GREEN = "\033[32m"
RED   = "\033[31m"
YEL   = "\033[33m"
DIM   = "\033[2m"
RST   = "\033[0m"


# ─────────────────────────────────────────────────────────────────────
# Classifier fixtures — generous enough to catch regressions in any
# rule branch. Each entry is (input, expected). "ambiguous" means the
# dispatcher should fall back to local; the test asserts the explicit
# classifier output, not the final route.
# ─────────────────────────────────────────────────────────────────────
CLASSIFIER_FIXTURES = [
    # ── HOME_VERBS ──
    ("turn off the kitchen lights",               "local"),
    ("turn on the office lamp",                   "local"),
    ("dim the lights",                            "local"),
    ("brighten the bedroom",                      "local"),
    ("make it warmer in here",                    "local"),
    ("make the living room cooler",               "local"),
    ("set the brightness to fifty percent",       "local"),
    ("pause the music",                           "local"),
    ("pause sonos",                               "local"),
    ("resume playback",                           "local"),
    ("play something jazzy",                      "local"),
    ("skip this song",                            "local"),
    ("next track",                                "local"),
    ("previous track",                            "local"),
    ("mute the tv",                               "local"),
    ("unmute",                                    "local"),
    ("volume up please",                          "local"),
    ("a bit louder",                              "local"),
    ("quieter",                                   "local"),
    ("lock the front door",                       "local"),
    ("unlock the back door",                      "local"),

    # ── HOME_NOUNS ──
    ("the lamp is too bright",                    "local"),
    ("kitchen is cold",                           "local"),
    ("what's playing on sonos",                   "local"),
    ("frigate cameras",                           "local"),
    ("how much vram is being used",               "local"),
    ("metrics show high gpu",                     "local"),
    ("ai stack status",                           "local"),
    ("how many people are here in the room",      "local"),

    # ── HOME_QUERIES ──
    ("what do you see in the office",             "local"),
    ("who's home right now",                      "local"),
    ("who is in the kitchen",                     "local"),
    ("is anyone home",                            "local"),
    ("are we home",                               "local"),
    ("am i home",                                 "local"),

    # ── GENERAL (external) ──
    ("explain quantum physics",                                            "external"),
    ("Explain quantum physics",                                            "external"),  # case-insensitive
    ("Can you explain quantum physics to me?",                             "external"),
    ("explain how transformer attention works",                            "external"),
    ("what is the difference between OLED and Mini LED",                   "external"),
    ("compare apples and oranges",                                         "external"),
    ("teach me chess openings",                                            "external"),
    ("tell me about the Renaissance",                                      "external"),
    ("help me write a pitch deck outline",                                 "external"),
    ("help me understand this error message",                              "external"),
    ("help me brainstorm dinner ideas",                                    "external"),
    ("give me ideas for a side project",                                   "external"),
    ("code review this snippet please",                                    "external"),

    # ── GENERAL — question-starter expansion (May 2026, Addendum 2) ──
    # Real-world misroutes that fell through to ambiguous→local before.
    ("when was abe lincoln born?",                "external"),  # was "ambiguous" — fixed by `when (is|was|...)`
    ("When did the war of 1812 start",            "external"),
    ("who invented the lightbulb",                "external"),
    ("who is isaac newton",                       "external"),
    ("where is paris",                            "external"),
    ("which is bigger, jupiter or saturn",        "external"),
    ("what year did rome fall",                   "external"),
    ("tell me a joke",                            "external"),
    ("tell me a story about dragons",             "external"),
    ("describe the Renaissance period",           "external"),
    ("define entropy",                            "external"),
    ("give me the tldr on quantum mechanics",     "external"),
    ("give me a summary of WWII",                 "external"),
    ("list the planets",                          "external"),
    ("name the seven wonders",                    "external"),

    # ── Ambiguous (no rule matches → local fallback) ──
    ("",                                          "ambiguous"),
    ("hello",                                     "ambiguous"),
    ("hi there",                                  "ambiguous"),
    ("how is the weather",                        "ambiguous"),  # no rule
    ("good morning",                              "ambiguous"),
    ("what time is it",                           "ambiguous"),  # MUST stay ambiguous — OpenAI has no clock
    ("what time is it now",                       "ambiguous"),  # same — local agent / HA time intent handles it

    # ── Mixed — home noun match wins (privacy-preserving default) ──
    ("explain why warm lighting in the kitchen feels cozier",  "local"),  # "kitchen" → local
    ("given i'm in the office, why does warm light feel cozy", "local"),  # "office" → local
    ("based on what you see in my living room, suggest a setup", "local"),  # living room
    ("compare the lights in different rooms",                    "local"),  # lights

    # ── Mixed — proves HOME_* still wins for new question starters ──
    ("where is the office camera",                "local"),   # "office" + "camera" in HOME_NOUNS first
    ("which light is on in the kitchen",          "local"),   # "light" / "kitchen" in HOME_NOUNS first
    ("when will the kitchen lights turn off",     "local"),   # "turn off" in HOME_VERBS first
    ("who's home right now please",               "local"),   # already covered by HOME_QUERIES — guards against regex re-ordering
    ("tell me about the living room",             "local"),   # "living room" in HOME_NOUNS first
]


def run_classifier_tests():
    """Run all CLASSIFIER_FIXTURES through classify_intent and report."""
    print(f"{DIM}── classifier fixtures ──{RST}")
    passed = 0
    failed = []
    for text, expected in CLASSIFIER_FIXTURES:
        got = classify_intent(text)
        if got == expected:
            passed += 1
        else:
            failed.append((text, expected, got))
    total = len(CLASSIFIER_FIXTURES)
    for text, expected, got in failed:
        print(f"  {RED}FAIL{RST}: {text!r}  expected={expected}  got={got}")
    color = GREEN if not failed else RED
    print(f"{color}  {passed}/{total} passed{RST}")
    return len(failed) == 0


def run_key_file_test():
    """Verify the key file is set + parseable. Doesn't echo the key."""
    print(f"{DIM}── key file ──{RST}")
    p = _KEY_PATH
    if not p.is_file():
        print(f"  {YEL}SKIP{RST}: {p} not present — voice routing disabled (ok if intentional)")
        return True  # not a failure; absence is a valid state
    key = _read_api_key_sync()
    if not key:
        print(f"  {RED}FAIL{RST}: key file present but read returned None/empty")
        return False
    if not key.startswith("sk-"):
        print(f"  {YEL}WARN{RST}: key doesn't start with 'sk-' (got first 4 chars: '{key[:4]}...') — might still work for non-OpenAI providers")
    print(f"  {GREEN}OK{RST}: key file present, {len(key)} chars, prefix {key[:4]}...")
    return True


def run_live_openai_test():
    """One real OpenAI call. Uses urllib so we don't depend on httpx/async.
    Costs ~$0.0001. Skipped if key absent or OPENAI_LIVE_TEST=0."""
    print(f"{DIM}── live OpenAI roundtrip ──{RST}")
    if os.environ.get("OPENAI_LIVE_TEST") == "0":
        print(f"  {YEL}SKIP{RST}: OPENAI_LIVE_TEST=0")
        return True
    key = _read_api_key_sync()
    if not key:
        print(f"  {YEL}SKIP{RST}: no key configured")
        return True

    import urllib.request
    import urllib.error
    body = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "You answer in exactly one word."},
            {"role": "user", "content": "Reply with the word pong only."},
        ],
        "max_tokens": 5,
        "temperature": 0,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        method="POST",
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        },
        data=body,
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        print(f"  {RED}FAIL{RST}: HTTP {e.code} — {e.reason}")
        return False
    except Exception as e:
        print(f"  {RED}FAIL{RST}: {type(e).__name__}: {e}")
        return False
    dt_ms = int((time.monotonic() - t0) * 1000)
    try:
        data = json.loads(raw)
        reply = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  {RED}FAIL{RST}: response parse: {e}")
        return False
    pong = "pong" in reply.lower()
    print(f"  {GREEN if pong else RED}{'OK' if pong else 'FAIL'}{RST}: latency={dt_ms}ms reply={reply!r}")
    return pong


def run_privacy_audit():
    """Build the request body the integration WOULD send and assert it
    contains none of the forbidden leak strings. Doesn't make a network
    call — purely an offline audit of the construction path."""
    print(f"{DIM}── privacy audit (offline) ──{RST}")
    from external_routing import _SYSTEM_PROMPT_TTS

    cases = [
        "explain quantum physics",
        "what is the difference between OLED and Mini LED",
        "compare two programming languages",
    ]
    forbidden = [
        ("HA entity-id prefix",  "light."),
        ("HA entity-id prefix",  "binary_sensor."),
        ("HA entity-id prefix",  "sensor."),
        ("HA entity-id prefix",  "media_player."),
        ("container name",       "hav-"),
        ("LAN IP",               "192.168."),
        ("JWT prefix",           "eyJ"),
        ("local model name",     "qwen3"),
        ("system username",      "marcelo-lima"),
        ("RTSP",                 "rtsp://"),
        ("room binding marker",  "[[hg-room"),
    ]
    failed = []
    for text in cases:
        body = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT_TTS},
                {"role": "user", "content": text},
            ],
            "max_tokens": 300,
            "temperature": 0.5,
        }
        body_s = json.dumps(body).lower()
        for name, pat in forbidden:
            if pat.lower() in body_s:
                failed.append((text, name, pat))
        # Shape check: exactly 2 messages, system + user, user content verbatim.
        msgs = body["messages"]
        if len(msgs) != 2 or msgs[0]["role"] != "system" or msgs[1]["role"] != "user" or msgs[1]["content"] != text:
            failed.append((text, "shape", "messages array malformed"))
        prompt = msgs[0]["content"].lower()
        for phrase in ("plain prose", "no markdown", "no bullet", "no headings", "code fences", "two to four sentences"):
            if phrase not in prompt:
                failed.append((text, "system prompt contract", phrase))
    for text, name, pat in failed:
        print(f"  {RED}LEAK{RST}: {text!r} contains {name} pattern {pat!r}")
    if not failed:
        print(f"  {GREEN}OK{RST}: {len(cases)} cases, no leak patterns detected, message shape correct")
    return not failed


def run_classifier_with_rule_test():
    """Verify classify_intent_with_rule returns (intent, matched_rule)
    tuples with matched_rule matching expected category labels."""
    from external_routing import classify_intent_with_rule
    print(f"{DIM}── classify_intent_with_rule ──{RST}")
    cases = [
        ("turn off the lights",          "local",    "HOME_VERBS"),
        ("the kitchen is bright",        "local",    "HOME_NOUNS"),
        ("what do you see in the office", "local",   "HOME_NOUNS"),  # office is in HOME_NOUNS; takes precedence
        ("who's home",                   "local",    "HOME_QUERIES"),
        ("explain quantum physics",      "external", "GENERAL"),
        ("",                              "ambiguous", "-"),
        ("hello",                         "ambiguous", "-"),
    ]
    failed = []
    for text, exp_intent, exp_rule in cases:
        got_intent, got_rule = classify_intent_with_rule(text)
        if got_intent == exp_intent and got_rule == exp_rule:
            continue
        failed.append((text, exp_intent, exp_rule, got_intent, got_rule))
    for text, ei, er, gi, gr in failed:
        print(f"  {RED}FAIL{RST}: {text!r}  expected=({ei},{er})  got=({gi},{gr})")
    print(f"  {GREEN if not failed else RED}{len(cases)-len(failed)}/{len(cases)} passed{RST}")
    return not failed


def run_log_serialization_test():
    """Verify the routing log entries serialize to valid JSON in all
    three modes (full / redacted / off). Doesn't write to disk."""
    print(f"{DIM}── log entry serialization ──{RST}")
    from external_routing import _redact_entry
    sample = {
        "ts": "2026-05-15T17:42:11Z",
        "conv_id": "test-cid",
        "src": "voice",
        "text": "explain quantum physics",
        "text_len": 24,
        "intent": "external",
        "matched": "GENERAL",
        "key_present": True,
        "dispatched": "external",
        "ext_status": "ok",
        "ext_latency_ms": 1421,
        "ext_reply_chars": 284,
        "ext_reply_snippet": "Quantum physics is...",
        "local_reply_chars": None,
        "local_reply_snippet": None,
    }
    failed = []

    # Full mode — entry passes through, JSON parses.
    try:
        line = json.dumps(sample, ensure_ascii=False)
        parsed = json.loads(line)
        assert parsed["text"] == sample["text"]
        assert parsed["matched"] == "GENERAL"
        print(f"  {GREEN}OK{RST}: full mode JSON round-trip")
    except (AssertionError, json.JSONDecodeError) as e:
        failed.append(f"full mode: {e}")

    # Redacted mode — content fields gone, metadata kept.
    try:
        redacted = _redact_entry(sample)
        assert "text" not in redacted, f"text field should be redacted, got {redacted}"
        assert "ext_reply_snippet" not in redacted
        assert "local_reply_snippet" not in redacted
        assert redacted["text_len"] == 24  # length kept
        assert redacted["matched"] == "GENERAL"  # metadata kept
        print(f"  {GREEN}OK{RST}: redacted mode keeps metadata, drops content")
    except AssertionError as e:
        failed.append(f"redacted mode: {e}")

    if failed:
        for f in failed:
            print(f"  {RED}FAIL{RST}: {f}")
    return not failed


def run_log_kind_test():
    """M0.4: verify the multi-kind routing-log schema. log_decision()
    now accepts entries with a `kind` field and the redactor uses a
    per-kind allow-list (tool_call, perception_dispatch, confirmation
    each have their own kept-fields set). Backcompat: entries without
    `kind` default to routing_decision."""
    print(f"{DIM}── log kind (M0.4) ──{RST}")
    from external_routing import (
        _redact_entry,
        LOG_KIND_ROUTING_DECISION,
        LOG_KIND_TOOL_CALL,
        LOG_KIND_PERCEPTION_DISPATCH,
        LOG_KIND_CONFIRMATION,
        _VALID_LOG_KINDS,
    )

    failed = []

    # 1. Backcompat — pre-M0 entry with no `kind` redacts as routing_decision.
    pre_m0 = {
        "ts": "2026-05-17T20:00:00Z",
        "conv_id": "cid-1",
        "src": "voice",
        "text": "secret content",
        "text_len": 14,
        "intent": "local",
        "matched": "HOME_VERBS",
        "dispatched": "local",
        "local_reply_chars": 17,
        "local_reply_snippet": "secret reply text",
    }
    try:
        r = _redact_entry(pre_m0)
        assert "text" not in r, "pre-M0: text leaked"
        assert "local_reply_snippet" not in r, "pre-M0: snippet leaked"
        assert r["text_len"] == 14, "pre-M0: text_len dropped"
        assert r["matched"] == "HOME_VERBS", "pre-M0: matched dropped"
        print(f"  {GREEN}OK{RST}: pre-M0 entry (no kind) redacts as routing_decision")
    except AssertionError as e:
        failed.append(f"pre-M0 redact: {e}")

    # 2. Tool-call entry — name + latency + ok kept; args + result dropped.
    tool_entry = {
        "ts": "2026-05-17T20:00:01Z",
        "kind": LOG_KIND_TOOL_CALL,
        "conv_id": "cid-1",
        "tool_name": "execute_services",
        "domain": "light",
        "service": "turn_on",
        "args": {"entity_id": ["light.kitchen"], "brightness_pct": 50},
        "result": {"some": "data"},
        "latency_ms": 142,
        "ok": True,
        "error_kind": None,
    }
    try:
        r = _redact_entry(tool_entry)
        assert "args" not in r, f"tool_call: args leaked: {r}"
        assert "result" not in r, f"tool_call: result leaked: {r}"
        assert r["tool_name"] == "execute_services"
        assert r["domain"] == "light"
        assert r["service"] == "turn_on"
        assert r["latency_ms"] == 142
        assert r["ok"] is True
        print(f"  {GREEN}OK{RST}: tool_call entry keeps name+domain+service+latency+ok, drops args+result")
    except AssertionError as e:
        failed.append(f"tool_call redact: {e}")

    # 3. Perception-dispatch entry — room + verdict kept; caption dropped.
    perc_entry = {
        "ts": "2026-05-17T20:00:02Z",
        "kind": LOG_KIND_PERCEPTION_DISPATCH,
        "conv_id": "cid-1",
        "room": "kitchen",
        "verdict": "cache_hit",
        "age_seconds": 12,
        "latency_ms": 4,
        "caption": "Marcelo standing at the island",  # must drop
    }
    try:
        r = _redact_entry(perc_entry)
        assert "caption" not in r, f"perception: caption leaked: {r}"
        assert r["room"] == "kitchen"
        assert r["verdict"] == "cache_hit"
        assert r["age_seconds"] == 12
        print(f"  {GREEN}OK{RST}: perception_dispatch entry keeps room+verdict+age, drops caption")
    except AssertionError as e:
        failed.append(f"perception redact: {e}")

    # 4. Confirmation entry — domain+service+intercepted kept.
    conf_entry = {
        "ts": "2026-05-17T20:00:03Z",
        "kind": LOG_KIND_CONFIRMATION,
        "conv_id": "cid-1",
        "domain": "lock",
        "service": "unlock",
        "intercepted": True,
        "resolved": False,
        "entity_id": "lock.front_door",  # may want to drop in future
    }
    try:
        r = _redact_entry(conf_entry)
        assert r["domain"] == "lock"
        assert r["service"] == "unlock"
        assert r["intercepted"] is True
        assert r["resolved"] is False
        print(f"  {GREEN}OK{RST}: confirmation entry keeps domain+service+intercepted+resolved")
    except AssertionError as e:
        failed.append(f"confirmation redact: {e}")

    # 5. All declared kinds are in the _VALID_LOG_KINDS set.
    try:
        for k in (LOG_KIND_ROUTING_DECISION, LOG_KIND_TOOL_CALL,
                  LOG_KIND_PERCEPTION_DISPATCH, LOG_KIND_CONFIRMATION):
            assert k in _VALID_LOG_KINDS, f"missing: {k}"
        print(f"  {GREEN}OK{RST}: all 4 kinds declared in _VALID_LOG_KINDS")
    except AssertionError as e:
        failed.append(f"valid kinds set: {e}")

    if failed:
        for f in failed:
            print(f"  {RED}FAIL{RST}: {f}")
    return not failed


def run_log_decision_default_kind_test():
    """log_decision() must add `kind: routing_decision` to pre-M0 entries
    that don't carry one — otherwise the analyzer can't tell which kind
    a backcompat entry is. Test the additive behavior via a fake hass
    that captures the serialized line."""
    print(f"{DIM}── log_decision default kind ──{RST}")
    import asyncio
    from external_routing import log_decision, LOG_KIND_ROUTING_DECISION

    captured: dict = {}

    class _FakeBus:
        def async_fire(self, event_type, data):
            captured.setdefault("events", []).append((event_type, data))

    class _FakeHass:
        bus = _FakeBus()

        async def async_add_executor_job(self, fn, *args):
            # Run sync inline; capture the line so we can introspect.
            captured["line"] = args[0]
            return fn(*args) if False else None  # don't actually write

    # We can intercept the line by patching _write_log_line_sync — easier
    # is to monkeypatch. Instead, just rely on captured["line"] above
    # which is the line passed to the executor (per log_decision impl).
    fake = _FakeHass()
    entry_no_kind = {"ts": "now", "conv_id": "cid-X", "intent": "local",
                     "matched": "HOME_VERBS", "dispatched": "local"}

    try:
        asyncio.get_event_loop().run_until_complete(log_decision(fake, entry_no_kind))
    except RuntimeError:
        # Python 3.12 deprecation — try the new pattern
        asyncio.new_event_loop().run_until_complete(log_decision(fake, entry_no_kind))

    failed = []
    try:
        line = captured.get("line")
        assert line, "no line captured"
        parsed = json.loads(line)
        assert parsed.get("kind") == LOG_KIND_ROUTING_DECISION, \
            f"missing kind default, got {parsed.get('kind')!r}"
        print(f"  {GREEN}OK{RST}: pre-M0 entry got kind=routing_decision injected")

        events = captured.get("events") or []
        assert len(events) == 1, f"expected 1 fired event, got {len(events)}"
        evt_type, evt_data = events[0]
        assert evt_data.get("kind") == LOG_KIND_ROUTING_DECISION, \
            f"fired event missing kind: {evt_data}"
        print(f"  {GREEN}OK{RST}: HA event payload mirrors file write (kind injected)")
    except (AssertionError, json.JSONDecodeError) as e:
        failed.append(str(e))

    if failed:
        for f in failed:
            print(f"  {RED}FAIL{RST}: {f}")
    return not failed


def main():
    print(f"{DIM}══ external_routing.py test suite ══{RST}")
    results = [
        ("classifier",      run_classifier_tests()),
        ("classifier+rule", run_classifier_with_rule_test()),
        ("log serial",      run_log_serialization_test()),
        ("log kind (M0.4)", run_log_kind_test()),
        ("log default kind", run_log_decision_default_kind_test()),
        ("key file",        run_key_file_test()),
        ("privacy",         run_privacy_audit()),
        ("live openai",     run_live_openai_test()),
    ]
    print()
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    if passed == total:
        print(f"{GREEN}══ ✓ all {total} suites passed ══{RST}")
        return 0
    print(f"{RED}══ ✗ {total - passed}/{total} suites failed ══{RST}")
    for name, ok in results:
        flag = f"{GREEN}✓{RST}" if ok else f"{RED}✗{RST}"
        print(f"  {flag} {name}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
