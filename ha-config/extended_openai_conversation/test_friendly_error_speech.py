"""Addendum 28 follow-up — regression test for _friendly_error_speech.

Background (2026-05-18): user reported "the AI just speaks gibberish
non stop when i ask it questions" after a multi-entity tool call
("turn on all my lights everywhere") truncated mid-stream. The integration
raised ParseArgumentsFailed with the raw 642-char JSON in its __str__;
the conversation entity's error handler formatted it as
f"Something went wrong: {err}" and shipped it to TTS, which read the
entire JSON aloud = "gibberish".

Fix: _friendly_error_speech() in conversation.py maps known error
classes to short TTS-safe strings and caps any unknown error at 180
chars. This suite locks in that mapping by feeding fake exceptions
into the helper (no HA imports needed — we only test the pure-Python
mapping logic).

Runner contract (mirrors test_world_state.py):
  PYTHONIOENCODING=utf-8 py -3 test_friendly_error_speech.py
Exit 0 on green, non-zero on fail.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Import _friendly_error_speech in isolation. conversation.py has heavy
# HA imports we can't satisfy on a workstation, so we cherry-pick just
# the helper by reading the file + exec'ing the helper definition.
THIS = Path(__file__).parent
CONV_PY = THIS / "conversation.py"
src = CONV_PY.read_text(encoding="utf-8")

# Slice from "_FRIENDLY_ERROR_SPEECH" to the next top-level statement
# AFTER the helper. We anchor on the "# Module-import sentinel" marker
# which appears immediately after _friendly_error_speech in conversation.py.
start = src.find("_FRIENDLY_ERROR_SPEECH = {")
end_marker = src.find("# Module-import sentinel", start)
if start < 0 or end_marker < 0:
    print(f"FAIL — couldn't locate slice bounds (start={start}, end_marker={end_marker})")
    sys.exit(1)
slice_src = src[start:end_marker]

ns: dict = {}
exec(slice_src, ns, ns)
_friendly_error_speech = ns["_friendly_error_speech"]
_FRIENDLY_ERROR_SPEECH = ns["_FRIENDLY_ERROR_SPEECH"]


# Stand-ins for the actual exception classes. Only the TYPE NAME matters
# to the helper, plus optional `.function` attribute for FunctionNotFound.
def _make_exc(name: str, message: str, **attrs):
    """Return an Exception subclass instance with the requested class
    name + str() output, and any extra attributes set on the instance."""
    cls = type(name, (Exception,), {"__str__": lambda self: message})
    inst = cls(message)
    for k, v in attrs.items():
        setattr(inst, k, v)
    return inst


# ──────────────────────────────────────────────────────────────────
# Test cases
# ──────────────────────────────────────────────────────────────────
def test_parse_arguments_failed_does_not_leak_raw_json() -> tuple[bool, str]:
    """The 642-char-JSON regression. The error's str() carries the raw
    tool-args; the helper MUST NOT include any of it in the speech."""
    raw_json = (
        '{"list": [{"domain": "light", "service": "turn_on", '
        '"service_data": {"entity_id": ['
        + ", ".join(f'"light.entity_{i}"' for i in range(20))
        + "]}}]}"
    )
    long_msg = f"failed to parse arguments `{raw_json}`. Increase maximum token to avoid the issue."
    assert len(long_msg) > 500, "fixture must reproduce the >500-char case"
    err = _make_exc("ParseArgumentsFailed", long_msg)
    speech = _friendly_error_speech(err)
    if "{" in speech or "}" in speech or "entity_id" in speech or "light.entity_" in speech:
        return False, f"speech leaked JSON fragments: {speech!r}"
    if len(speech) > 180:
        return False, f"speech too long ({len(speech)} chars): {speech!r}"
    if "smaller pieces" not in speech.lower() and "break" not in speech.lower():
        return False, f"speech missing 'try breaking up' guidance: {speech!r}"
    return True, ""


def test_token_length_exceeded_returns_friendly() -> tuple[bool, str]:
    err = _make_exc(
        "TokenLengthExceededError",
        "token length(`500`) exceeded. Increase maximum token to avoid the issue.",
    )
    speech = _friendly_error_speech(err)
    if len(speech) > 180:
        return False, f"too long: {speech!r}"
    if "ran out of room" not in speech.lower() and "specific" not in speech.lower():
        return False, f"missing guidance: {speech!r}"
    return True, ""


def test_function_not_found_includes_function_name() -> tuple[bool, str]:
    err = _make_exc(
        "FunctionNotFound",
        "function 'invoke_script' does not exist",
        function="invoke_script",
    )
    speech = _friendly_error_speech(err)
    if "invoke_script" not in speech:
        return False, f"function name missing: {speech!r}"
    if "agent tools" not in speech.lower():
        return False, f"missing 'agent tools' hint: {speech!r}"
    return True, ""


def test_function_not_found_without_attribute_still_safe() -> tuple[bool, str]:
    """If a FunctionNotFound somehow lacks .function, fall through to
    the str() path instead of crashing."""
    err = _make_exc("FunctionNotFound", "function not found at all")
    if hasattr(err, "function"):
        # If our test helper added it by default, delete it.
        delattr(err, "function")
    try:
        speech = _friendly_error_speech(err)
    except Exception as e:
        return False, f"raised {type(e).__name__}: {e}"
    if not speech or len(speech) > 180:
        return False, f"bad speech: {speech!r}"
    return True, ""


def test_short_unknown_error_passes_through() -> tuple[bool, str]:
    """Other HomeAssistantError subclasses (EntityNotFound, CallServiceError)
    have human-friendly __str__ already. Short messages pass through
    with the 'I had trouble' prefix."""
    err = _make_exc("EntityNotFound", "Unable to find entity light.attic")
    speech = _friendly_error_speech(err)
    if "light.attic" not in speech:
        return False, f"short error didn't pass through: {speech!r}"
    if len(speech) > 180:
        return False, f"too long: {speech!r}"
    return True, ""


def test_long_unknown_error_falls_back_to_generic() -> tuple[bool, str]:
    """Any unknown error whose str() is > 180 chars must NOT pass
    through — falls back to the generic 'try a simpler request' message."""
    long_msg = "X" * 500
    err = _make_exc("MysteryError", long_msg)
    speech = _friendly_error_speech(err)
    if "X" * 50 in speech:
        return False, f"long error leaked: {speech[:80]!r}..."
    if len(speech) > 180:
        return False, f"fallback too long: {speech!r}"
    if "simpler" not in speech.lower():
        return False, f"fallback missing 'simpler': {speech!r}"
    return True, ""


def test_empty_error_message_falls_back() -> tuple[bool, str]:
    """str(err) == "" must not produce 'I had trouble with that: '."""
    err = _make_exc("SilentError", "")
    speech = _friendly_error_speech(err)
    if speech.endswith(": "):
        return False, f"trailing colon-space leak: {speech!r}"
    if not speech or len(speech) > 180:
        return False, f"bad speech: {speech!r}"
    return True, ""


def test_speech_is_tts_safe_no_backticks_or_braces() -> tuple[bool, str]:
    """Every mapped message must be free of characters that read
    awkwardly via TTS (backticks, braces, brackets)."""
    bad_chars = "{}[]`"
    for name, msg in _FRIENDLY_ERROR_SPEECH.items():
        for c in bad_chars:
            if c in msg:
                return False, f"{name!r} contains TTS-hostile char {c!r}: {msg!r}"
    return True, ""


def test_every_known_mapping_is_short() -> tuple[bool, str]:
    """Sanity: hand-written mappings must not regress past 180 chars."""
    for name, msg in _FRIENDLY_ERROR_SPEECH.items():
        if len(msg) > 180:
            return False, f"{name!r} exceeds 180 chars: {len(msg)}"
    return True, ""


# ──────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────
TESTS = [
    ("parse_arguments_failed_does_not_leak_raw_json", test_parse_arguments_failed_does_not_leak_raw_json),
    ("token_length_exceeded_returns_friendly", test_token_length_exceeded_returns_friendly),
    ("function_not_found_includes_function_name", test_function_not_found_includes_function_name),
    ("function_not_found_without_attribute_still_safe", test_function_not_found_without_attribute_still_safe),
    ("short_unknown_error_passes_through", test_short_unknown_error_passes_through),
    ("long_unknown_error_falls_back_to_generic", test_long_unknown_error_falls_back_to_generic),
    ("empty_error_message_falls_back", test_empty_error_message_falls_back),
    ("speech_is_tts_safe_no_backticks_or_braces", test_speech_is_tts_safe_no_backticks_or_braces),
    ("every_known_mapping_is_short", test_every_known_mapping_is_short),
]


def main() -> int:
    print("── _friendly_error_speech (Addendum 28 follow-up) ──")
    passed = 0
    failed: list[tuple[str, str]] = []
    for name, fn in TESTS:
        try:
            ok, reason = fn()
        except Exception as e:
            ok, reason = False, f"{type(e).__name__}: {e}"
        if ok:
            print(f"  PASS  {name}")
            passed += 1
        else:
            print(f"  FAIL  {name} — {reason}")
            failed.append((name, reason))
    total = len(TESTS)
    if failed:
        print(f"\n{len(failed)}/{total} FAILED")
        for n, r in failed:
            print(f"  - {n}: {r}")
        return 1
    print(f"\n{passed}/{total} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
