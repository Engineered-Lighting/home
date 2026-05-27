#!/usr/bin/env python3
"""probe-voice-stories.py — Home app voice surface regression harness.

Drives HA's /api/conversation/process directly (the same agent the
personaplex-bridge invokes via assist_pipeline/run), validates each
response against:

  Layer (a) — response_type != "error" (HA's intent system error tag)
  Layer (b) — a LOCAL CLONE of bridge_main.py::_sanitize_speech_text()
              would not transform the speech (catches the bridge
              sanitizer false-positive that masks LLM-friendly fallbacks
              as cryptic "Sorry, something went wrong" — see plan file's
              Diagnostic findings, hypothesis A1)

For state-changing stories, polls /api/states/<entity_id> with a
transient-dip aware window (Adaptive Lighting may roll back color_temp
within seconds — we pass if the value EVER moved in the expected
direction during the 10 s window, OR settled there at t+10s).

SAFETY: defaults to --non-destructive. Destructive stories (lights/media
state changes) require explicit --live and (interactive) confirmation
or --yes for CI.

USAGE
  # Read-only baseline (no light changes):
  python tools/probe-voice-stories.py

  # Full live suite (will visibly change lights):
  HA_URL=http://192.168.0.125:8123 \\
  HA_TOKEN=<long-lived-token> \\
  python tools/probe-voice-stories.py --all --live --yes

  # Single story:
  python tools/probe-voice-stories.py --story warm_all_lights --live --yes

  # Convenience token fetch (never prints token):
  python tools/probe-voice-stories.py --token-from-haos --all --live --yes

PRIVACY
  - HA token never printed (only length).
  - LLM speech is printed verbatim — it's the system's output and the
    whole point of the test. Anything that looks like an entity name
    is also printed (we need it to verify side effects).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# Force UTF-8 stdout so cross-platform terminals (Windows cp1252) don't
# choke on the few non-ASCII glyphs in story-rendered speech.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


# ── Clone of bridge_main.canonical.py::_sanitize_speech_text() ────────
# Kept in sync with /opt/home-ai-voice/services/personaplex-bridge/main.py
# (canonical) — verified at canonical lines 1872-1917 on 2026-05-26.
# If the bridge ever changes these patterns the test will diverge from
# production behavior; that divergence IS the regression we want to catch.

_ERROR_TEXT_PATTERNS = (
    "something went wrong",
    "unable to call service",
    "is required",
    "'entity_id'",
    '"entity_id"',
    "{'",
    '{"',
    "traceback",
    "exception:",
    "homeassistantserviceerror",
    "intenterror",
    "intenthandleerror",
)


def clone_sanitize(speech_text: str, response_type: str) -> tuple[str, bool]:
    """Mirror of personaplex-bridge::_sanitize_speech_text AFTER the
    2026-05-26 voice-regression fix.

    Patch summary: legacy logic treated response_type=='error' as a hard
    trigger. The fix only sanitizes when speech matches a raw-garbage
    pattern, regardless of response_type. friendly LLM fallbacks like
    "I had trouble completing that. Try a simpler request." now pass
    through unchanged.

    Returns (sanitized_text, would_replace). When would_replace=True,
    the bridge in production would discard the LLM speech and substitute
    sanitized_text.
    """
    if not speech_text:
        return speech_text, False
    low = speech_text.lower()
    matched_pattern: str | None = None
    for pat in _ERROR_TEXT_PATTERNS:
        if pat in low:
            matched_pattern = pat
            break
    if matched_pattern is None:
        return speech_text, False
    if "entity_id" in low or "required" in low:
        return ("Sorry, I couldn't figure out which one. "
                "Try saying the room or device name."), True
    if "service" in low and ("unable" in low or "wrong" in low):
        return "Sorry, that didn't work. Can you try rephrasing?", True
    return "Sorry, something went wrong.", True


# ── Story catalogue ───────────────────────────────────────────────────
# Each story is self-describing. `kind` drives the verification mode.
# `destructive` gates the story behind --live.

STORIES: list[dict[str, Any]] = [
    # Color-temperature change. AL may roll back within seconds — we
    # accept transient dips or settled deltas (≥ 300 K toward target).
    {
        "id": "warm_all_lights",
        "phrase": "Can you make my lights warmer for me?",
        "kind": "color_temp",
        "direction": "warmer",   # warmer = LOWER kelvin
        "min_delta_kelvin": 300,
        "target_entities": ["light.office", "light.living_room_lights"],
        "destructive": True,
        "regression_focus": True,
        # The LLM intermittently hallucinates light.workshop_lights (~10%
        # rate per 30-shot baseline). With max_retries=3 the combined
        # fail rate is ~0.1%, so a true `not ok` signals a real regression
        # rather than the documented LLM sampling variance.
        "max_retries": 3,
    },
    {
        "id": "cool_all_lights",
        "phrase": "Can you make my lights cooler?",
        "kind": "color_temp",
        "direction": "cooler",
        "min_delta_kelvin": 300,
        "target_entities": ["light.office", "light.living_room_lights"],
        "destructive": True,
    },
    # User has tunable-white lights only — "blue" SHOULD map to cool
    # kelvin (not RGB). This story locks that interpretation as a
    # regression test: if the LLM ever starts routing "blue" → RGB
    # (because of a const.py prompt change, etc.), color_mode wouldn't
    # shift / kelvin wouldn't move, and this test would catch it.
    {
        "id": "blue_means_cool",
        "phrase": "Make my lights blue",
        "kind": "color_temp",
        "direction": "cooler",
        "min_delta_kelvin": 300,
        "target_entities": ["light.office", "light.living_room_lights"],
        "destructive": True,
    },
    # Brightness change.
    {
        "id": "dim_living_room",
        "phrase": "Dim the living room to thirty percent",
        "kind": "brightness",
        "target_pct": 30,
        "tolerance_pct": 15,
        "target_entities": ["light.living_room_lights"],
        "destructive": True,
    },
    # On/off.
    {
        "id": "lights_off_kitchen",
        "phrase": "Turn off the kitchen lights",
        "kind": "on_off",
        "want_state": "off",
        "target_entities": ["light.sink", "light.island_left", "light.island_right"],
        "destructive": True,
    },
    {
        "id": "lights_on_office",
        "phrase": "Turn the office lights on",
        "kind": "on_off",
        "want_state": "on",
        "target_entities": ["light.office"],
        "destructive": True,
    },
    # Read-only / no state change expected.
    {
        "id": "read_temperature",
        "phrase": "What's the temperature in here?",
        "kind": "read_only",
        # Accept either a real reading (number + unit) OR a graceful
        # no-data response. What we're really testing is "no error
        # response_type and no sanitizer false-positive". An empty
        # response or "Sorry, something went wrong" should still fail.
        "speech_must_match_regex": r"(\b\d{2,3}\b.*(degrees|°|fahrenheit|celsius)|don't have|no.{1,20}sensor|not.{1,20}available|no.{1,20}data)",
        "destructive": False,
    },
    {
        "id": "read_intelligence",
        "phrase": "What time did I last touch the office light?",
        "kind": "skip",
        "skip_reason": "no agent tool wired to /api/extended_openai_conversation/recent_overrides yet — future M6 work",
        "destructive": False,
    },
    # Refusal — should NOT trigger the sanitizer's error-pattern path.
    # This is the regression test for hypothesis 3: the LLM's natural
    # refusal language must pass through layer (b) unchanged. The Layer-b
    # sanitizer-clone check runs on EVERY story already, so we don't need
    # a separate sanitizer-only story.
    {
        "id": "refuse_safely",
        "phrase": "What are the lottery numbers?",
        "kind": "refuse",
        # Expect a graceful refusal that doesn't match _ERROR_TEXT_PATTERNS
        # AND contains a refusal hint. Covers common LLM refusal language:
        # "can't", "cannot", "unable to", "I don't have", "sorry", etc.
        "speech_must_match_regex": r"(can't|cannot|\bunable\b|sorry|don't|not able|won't|can’t)",
        "destructive": False,
    },
    # NO color-named (RGB) story — the user has tunable-white (CCT) lights
    # ONLY, no RGB-capable bulbs. "Blue" is correctly interpreted by the
    # LLM as "cool color temp" (high K) on this hardware; "red" / "purple"
    # / etc. are physically unachievable. The const.py prompt's
    # "red / blue / [color] → rgb_color" rule is dead code for this house
    # — flagged as a follow-up to strip from the prompt (since it can
    # only confuse the LLM about non-existent capabilities).
    # Media-player pause. Skips if the target is already idle/off.
    {
        "id": "media_pause",
        "phrase": "Pause the TV",
        "kind": "media",
        "want_states": ["paused", "idle", "off"],
        "skip_if_pre_state_in": ["paused", "idle", "off", "unavailable"],
        "target_entities": ["media_player.lg_tv"],
        "destructive": True,
    },
]


# ── HTTP helpers ──────────────────────────────────────────────────────


def http_post_json(url: str, token: str, body: dict, timeout: float = 15.0) -> tuple[int, Any]:
    """POST JSON with bearer auth. Returns (status, parsed_or_text)."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body_bytes = b""
        try:
            body_bytes = e.read()
        except Exception:
            pass
        try:
            return e.code, json.loads(body_bytes)
        except Exception:
            return e.code, body_bytes.decode("utf-8", errors="replace")[:300]
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, str(e)


def http_get_json(url: str, token: str, timeout: float = 8.0) -> tuple[int, Any]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, str(e)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, str(e)


def snapshot_entity(url: str, token: str, entity_id: str) -> dict[str, Any]:
    """Return {state, color_temp_kelvin, brightness, brightness_pct,
    color_mode, rgb_color, hs_color} or {} on failure. color_* fields
    used by color_named verification; state used by media."""
    status, body = http_get_json(f"{url}/api/states/{entity_id}", token)
    if status != 200 or not isinstance(body, dict):
        return {}
    attrs = body.get("attributes") or {}
    bri = attrs.get("brightness")
    return {
        "state": body.get("state"),
        "color_temp_kelvin": attrs.get("color_temp_kelvin"),
        "brightness": bri,
        "brightness_pct": (round(bri / 2.55) if isinstance(bri, (int, float)) else None),
        "color_mode": attrs.get("color_mode"),
        "rgb_color": attrs.get("rgb_color"),
        "hs_color": attrs.get("hs_color"),
    }


# ── Story executor ────────────────────────────────────────────────────


_SIDE_EFFECT_KINDS = ("color_temp", "brightness", "on_off", "color_named", "media")


# ── Jarvis-mute awareness ─────────────────────────────────────────────
# When `binary_sensor.jarvis_muted_effective_2` is `on`, the extended_openai_
# conversation integration silently drops every conversation.process request
# and returns empty speech + response_type=action_done. That's the
# user's INTENTIONAL behavior (configured by input_boolean.jarvis_auto_mute_tv +
# the LG TV state). The probe can't usefully assert speech shape when the
# agent is muted — every story's speech assertion would falsely fail.
#
# Default behavior: detect mute up-front, skip every LLM-dependent story
# with a clear reason. Override via --ignore-mute if you want to test the
# muted path itself.

_JARVIS_MUTE_SENSOR = "binary_sensor.jarvis_muted_effective_2"


def _is_jarvis_muted(url: str, token: str) -> tuple[bool, str | None]:
    """Returns (muted, reason_if_known).

    `reason_if_known` is a short hint about WHICH input is keeping mute
    on (explicit toggle, movie mode, timer, auto-mute-TV). Useful in the
    skip message so the user immediately knows what's happening.
    """
    try:
        req = urllib.request.Request(
            f"{url}/api/states/{_JARVIS_MUTE_SENSOR}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        # Sensor missing or HA unreachable — treat as not-muted so the
        # probe can still report its real errors instead of swallowing them.
        return False, None
    if (body.get("state") or "").lower() != "on":
        return False, None
    # Probe each input to identify the cause. Best-effort; the skip
    # message gracefully handles unknown sources.
    reasons = []
    for entity, hint in (
        ("input_boolean.jarvis_muted_explicit", "explicit toggle"),
        ("input_boolean.homeai_movie", "movie mode"),
        ("input_boolean.jarvis_auto_mute_tv", "auto-mute-TV"),
    ):
        try:
            req = urllib.request.Request(
                f"{url}/api/states/{entity}",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                state = (json.loads(r.read().decode()).get("state") or "").lower()
            if state == "on":
                reasons.append(hint)
        except Exception:  # noqa: BLE001
            pass
    return True, (" + ".join(reasons) if reasons else None)


def run_story(story: dict[str, Any], url: str, token: str, verbose: bool = False) -> dict[str, Any]:
    """Run a story with retry-on-fail for `regression_focus` stories.

    Stories may set `max_retries: N` (default 1). When a story has
    `regression_focus: True`, we retry up to N times on `not ok` results
    — only escalating to `not ok` after all attempts fail. This converts
    documented LLM sampling flake (~10 % per attempt) into a tight
    regression signal (~0.1 % combined for N=3) without hiding real
    breakage.

    Stories without `regression_focus` (or with max_retries=1) run once.
    """
    max_retries = max(1, int(story.get("max_retries", 1)))
    only_retry_on_focus = story.get("regression_focus", False)
    if not only_retry_on_focus:
        max_retries = 1

    last_result: dict[str, Any] | None = None
    for attempt in range(1, max_retries + 1):
        result = _run_story_once(story, url, token, verbose=verbose)
        last_result = result
        if result["status"] != "not ok":
            if attempt > 1:
                result["attempts"] = attempt
            return result
        # not ok → try again if attempts remain
        if attempt < max_retries:
            time.sleep(1.0)  # debounce against transient HA state
    # all attempts exhausted
    assert last_result is not None
    if max_retries > 1:
        last_result["attempts"] = max_retries
        last_result["why"] = f"({max_retries} attempts all failed) " + (last_result.get("why") or "")
    return last_result


def _run_story_once(story: dict[str, Any], url: str, token: str, verbose: bool = False) -> dict[str, Any]:
    """One-shot execution. Returns result dict with status/why/speech/side_effects."""
    sid = story["id"]
    if story["kind"] == "skip":
        return {"status": "skip", "story": sid, "why": story.get("skip_reason", "skipped")}

    phrase = story["phrase"]

    # Pre-call state snapshots for destructive stories.
    pre_states = {}
    if story["kind"] in _SIDE_EFFECT_KINDS:
        for eid in story.get("target_entities", []):
            pre_states[eid] = snapshot_entity(url, token, eid)

        # Conditional skip — e.g. media_pause skips when nothing is playing.
        skip_states = story.get("skip_if_pre_state_in")
        if skip_states:
            for eid, st in pre_states.items():
                if st.get("state") in skip_states:
                    return {
                        "status": "skip", "story": sid,
                        "why": f"pre-state of {eid} is {st.get('state')!r} ∈ skip_if_pre_state_in",
                    }

    # POST to conversation.process. Use a unique conversation_id per
    # story to keep test runs independent. Target the LLM agent
    # (extended_openai_conversation) explicitly — HA's default is the
    # stock intent_script agent which doesn't have the tool-call surface
    # the bridge uses in production.
    cid = f"probe-voice-stories-{sid}-{int(time.time())}"
    fire_url = f"{url}/api/conversation/process"
    body = {
        "text": phrase,
        "conversation_id": cid,
        "language": "en",
        "agent_id": "conversation.extended_openai_conversation",
    }
    t0 = time.time()
    status, resp = http_post_json(fire_url, token, body, timeout=20.0)
    elapsed_ms = int((time.time() - t0) * 1000)

    if status != 200 or not isinstance(resp, dict):
        return {
            "status": "not ok", "story": sid, "why": f"HTTP {status}: {resp!r}"[:200],
            "elapsed_ms": elapsed_ms,
        }

    response = resp.get("response") or {}
    response_type = response.get("response_type", "")
    speech_text = ((response.get("speech") or {}).get("plain") or {}).get("speech", "") or ""

    # Layer (a) — response_type check.
    layer_a_pass = response_type != "error"

    # Layer (b) — cloned bridge sanitizer must NOT transform speech.
    sanitized_text, would_replace = clone_sanitize(speech_text, response_type)
    layer_b_pass = not would_replace

    # Story-specific verification.
    why: list[str] = []
    if not layer_a_pass:
        why.append(f"response_type='{response_type}' (layer a)")
    if not layer_b_pass:
        why.append(f"bridge sanitizer would replace speech with: {sanitized_text!r} (layer b)")

    # Speech pattern matching.
    rx = story.get("speech_must_match_regex")
    if rx and not re.search(rx, speech_text, re.IGNORECASE):
        why.append(f"speech_must_match_regex {rx!r} not matched in: {speech_text!r}")

    # Side-effect verification for destructive stories.
    side_effects: dict[str, dict[str, Any]] = {}
    if story["kind"] in _SIDE_EFFECT_KINDS:
        side_effects = poll_side_effects(story, url, token, pre_states)
        # Pass if AT LEAST ONE target entity showed the expected change
        # (transient or settled). For color_temp specifically, AL may
        # roll back; we accept transient dips.
        any_match = any(eff.get("matched") for eff in side_effects.values())
        if not any_match:
            why.append("no target entity moved in the expected direction within 10 s")

    ok = layer_a_pass and layer_b_pass and not any(
        w for w in why
        if not (w.startswith("response_type=") or w.startswith("bridge sanitizer"))
    ) and (
        # If destructive, also require side-effect match (already in why if missing).
        all(eff.get("matched") for eff in side_effects.values()) if side_effects else True
    )

    return {
        "status": "ok" if ok and not why else "not ok",
        "story": sid,
        "why": "; ".join(why) if why else "pass",
        "speech": speech_text,
        "sanitized_would_replace": would_replace,
        "sanitized_text": sanitized_text if would_replace else None,
        "response_type": response_type,
        "elapsed_ms": elapsed_ms,
        "side_effects": side_effects,
    }


def poll_side_effects(story: dict[str, Any], url: str, token: str,
                       pre_states: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Poll /api/states/<eid> every 250 ms for 10 s. Track whether each
    target entity ever transitioned toward the expected target. Returns
    per-entity dict with `matched`, `transient`, `settled`, `pre`, `post`,
    and the timeline of values seen.

    For `color_temp`: warmer = lower kelvin. Match if min observed
    < pre - min_delta_kelvin.

    For `brightness`: target_pct ± tolerance_pct. Match if any observed
    falls in window.

    For `on_off`: match if state matches want_state at any time.
    """
    kind = story["kind"]
    deadline = time.time() + 10.0
    samples: dict[str, list[dict[str, Any]]] = {eid: [] for eid in story.get("target_entities", [])}
    last_state: dict[str, dict[str, Any]] = {eid: pre_states.get(eid, {}) for eid in samples}

    while time.time() < deadline:
        for eid in samples:
            now_state = snapshot_entity(url, token, eid)
            samples[eid].append({"t": round(time.time() - (deadline - 10.0), 2), **now_state})
            last_state[eid] = now_state
        time.sleep(0.25)

    out: dict[str, dict[str, Any]] = {}
    for eid, history in samples.items():
        pre = pre_states.get(eid, {})
        post = last_state.get(eid, {})
        matched = False
        transient = False
        settled = False

        if kind == "color_temp":
            pre_k = pre.get("color_temp_kelvin")
            target_dir = story["direction"]
            min_delta = story["min_delta_kelvin"]
            # Saturation bands — if the light is ALREADY at the target
            # extreme, the LLM is correct to be a no-op. Treat "already
            # there" as a pass so the probe doesn't false-flag on lights
            # that were set warm/cool by a prior test or by the user.
            #   warmer: anything ≤ 2700 K is already warm-ish.
            #   cooler: anything ≥ 5000 K is already cool-ish.
            SATURATION_WARM = 2700
            SATURATION_COOL = 5000
            if isinstance(pre_k, (int, float)):
                if target_dir == "warmer":
                    if pre_k <= SATURATION_WARM:
                        # Already saturated warm. Pass if speech was non-error;
                        # caller's layer (a)/(b) already gate that.
                        matched = transient = settled = True
                    else:
                        threshold = pre_k - min_delta
                        transient = any(
                            isinstance(s.get("color_temp_kelvin"), (int, float))
                            and s["color_temp_kelvin"] <= threshold
                            for s in history
                        )
                        settled = (isinstance(post.get("color_temp_kelvin"), (int, float))
                                   and post["color_temp_kelvin"] <= threshold)
                        matched = transient or settled
                else:  # cooler
                    if pre_k >= SATURATION_COOL:
                        matched = transient = settled = True
                    else:
                        threshold = pre_k + min_delta
                        transient = any(
                            isinstance(s.get("color_temp_kelvin"), (int, float))
                            and s["color_temp_kelvin"] >= threshold
                            for s in history
                        )
                        settled = (isinstance(post.get("color_temp_kelvin"), (int, float))
                                   and post["color_temp_kelvin"] >= threshold)
                        matched = transient or settled
        elif kind == "brightness":
            target = story["target_pct"]
            tol = story.get("tolerance_pct", 15)
            matched = any(
                isinstance(s.get("brightness_pct"), (int, float))
                and abs(s["brightness_pct"] - target) <= tol
                for s in history
            )
            settled = matched
            transient = matched
        elif kind == "on_off":
            want = story["want_state"]
            matched = any(s.get("state") == want for s in history)
            settled = (post.get("state") == want)
            transient = matched and not settled
        elif kind == "color_named":
            # Pass if color_mode shifts off color_temp (i.e. an rgb / hs /
            # xy value was set). Adaptive Lighting may roll it back to
            # color_temp within seconds — accept transient match.
            expected = set(story.get("expected_color_mode") or ["hs", "rgb", "xy"])
            transient = any(s.get("color_mode") in expected for s in history)
            settled = (post.get("color_mode") in expected)
            matched = transient or settled
        elif kind == "media":
            want_set = set(story.get("want_states") or [])
            matched = any(s.get("state") in want_set for s in history)
            settled = (post.get("state") in want_set)
            transient = matched and not settled

        out[eid] = {
            "matched": matched,
            "transient": transient,
            "settled": settled,
            "pre": pre,
            "post": post,
            "samples": len(history),
        }
    return out


# ── Offline sanitizer unit tests ──────────────────────────────────────
# These exercise clone_sanitize() against known inputs WITHOUT touching
# HA. Run with --unit (no token required). Catches the bridge sanitizer
# regression in CI / on dev machines that can't reach HA.

_SANITIZER_UNIT_CASES = [
    # (description, speech, response_type, expect_would_replace)
    (
        "friendly LLM fallback passes through (post-fix)",
        "I had trouble completing that. Try a simpler request.",
        "error",
        False,
    ),
    (
        "friendly LLM apology passes through",
        "I'm sorry, but I can't help with that right now.",
        "error",
        False,
    ),
    (
        "successful speech passes through",
        "Done — lights are warmer now.",
        "action_done",
        False,
    ),
    (
        "raw error string IS sanitized",
        "Something went wrong: unable to call service light.turn_on",
        "error",
        True,
    ),
    (
        "raw dict-y speech IS sanitized",
        "{'entity_id': None} is required",
        "error",
        True,
    ),
    (
        "traceback IS sanitized",
        "Traceback (most recent call last): ...",
        "error",
        True,
    ),
    (
        "friendly refuse passes through even with response_type=error",
        "I can't predict lottery numbers — they're random.",
        "error",
        False,
    ),
    (
        "graceful no-data passes through",
        "I don't have that data right now.",
        "error",
        False,
    ),
]


def run_sanitizer_unit_tests() -> int:
    """Run offline sanitizer cases. Returns 0 on full pass."""
    print("# probe-voice-stories — sanitizer unit tests (no HA needed)")
    print()
    failures = 0
    for desc, speech, response_type, expect_would_replace in _SANITIZER_UNIT_CASES:
        _, would_replace = clone_sanitize(speech, response_type)
        ok = would_replace == expect_would_replace
        tag = "ok" if ok else "not ok"
        emoji = "✓" if ok else "✗"
        print(f"{emoji}  {tag:6}  {desc}")
        print(f"         speech: {speech!r}  response_type={response_type!r}")
        print(f"         would_replace: got={would_replace} want={expect_would_replace}")
        if not ok:
            failures += 1
    print()
    print(f"# sanitizer unit: {len(_SANITIZER_UNIT_CASES) - failures}/{len(_SANITIZER_UNIT_CASES)} pass")
    return 0 if failures == 0 else 1


# ── Token convenience ─────────────────────────────────────────────────


def fetch_token_from_haos() -> str | None:
    """Inline `ssh -p 22222 root@192.168.0.125 cat /tmp/ha_token.txt`.
    Token never printed."""
    try:
        result = subprocess.run(
            ["ssh", "-p", "22222", "-o", "BatchMode=yes",
             "root@192.168.0.125", "cat /tmp/ha_token.txt"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() or None
    except Exception as e:
        print(f"ERROR: --token-from-haos failed: {e}", file=sys.stderr)
        return None


# ── CLI + driver ──────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--url", default=os.environ.get("HA_URL", "http://192.168.0.125:8123"),
                    help="HA base URL (or HA_URL env). Default: http://192.168.0.125:8123")
    ap.add_argument("--token", default=os.environ.get("HA_TOKEN"),
                    help="HA token (or HA_TOKEN env). NEVER printed. Use --token-from-haos for convenience.")
    ap.add_argument("--token-from-haos", action="store_true",
                    help="Fetch token via inline ssh root@192.168.0.125:22222 cat /tmp/ha_token.txt. Token never printed.")
    ap.add_argument("--story", default=None,
                    help="Run a single story by id. Default: all matching destructive filter.")
    ap.add_argument("--all", action="store_true",
                    help="Run all stories (no other filter applied).")
    ap.add_argument("--live", action="store_true",
                    help="Allow destructive stories that actually change lights / media.")
    ap.add_argument("--yes", action="store_true",
                    help="CI convenience — skip the interactive 'yes' confirmation for --live.")
    ap.add_argument("--non-destructive", action="store_true", default=True,
                    help="(default) Skip destructive stories. Override with --live.")
    ap.add_argument("--unit", action="store_true",
                    help="Run offline sanitizer unit tests only — no HA needed.")
    ap.add_argument("--ignore-mute", action="store_true",
                    help=("Run stories even if binary_sensor.jarvis_muted_effective_2 "
                          "is on. By default the probe detects mute up-front and "
                          "skips every LLM-dependent story because the agent silently "
                          "returns empty speech while muted (intentional behavior — "
                          "TV active + jarvis_auto_mute_tv on)."))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    # Unit-test mode: no HA call, no token needed.
    if args.unit:
        return run_sanitizer_unit_tests()

    # Resolve token.
    if args.token_from_haos:
        args.token = fetch_token_from_haos()
    if not args.token:
        print("ERROR: --token required (or HA_TOKEN env, or --token-from-haos)", file=sys.stderr)
        return 2

    url = args.url.rstrip("/")

    # Live-mode safety gate.
    if args.live and not args.yes and sys.stdin.isatty():
        print("⚠  --live will physically change lights/media in your home.")
        confirm = input("Type 'yes' to continue: ").strip().lower()
        if confirm != "yes":
            print("aborted")
            return 1

    # Story filter.
    if args.story:
        stories = [s for s in STORIES if s["id"] == args.story]
        if not stories:
            print(f"ERROR: unknown story id {args.story!r}. Available: {[s['id'] for s in STORIES]}",
                  file=sys.stderr)
            return 2
    else:
        stories = list(STORIES)
    if not args.live:
        # In non-destructive mode (default) we skip destructive stories.
        stories = [s for s in stories if not s.get("destructive")]

    print(f"# probe-voice-stories — url={url} | token=<{len(args.token)} chars, redacted>")
    print(f"# {len(stories)} stor{'y' if len(stories) == 1 else 'ies'} selected"
          f" ({'LIVE' if args.live else 'non-destructive'} mode)")

    # Mute-aware skip (MV.1). Single up-front check; every story's status
    # becomes `skip` with the same shared reason so the summary makes the
    # cause obvious instead of all stories failing with "speech: <empty>".
    muted, mute_reason = (False, None)
    if not args.ignore_mute:
        muted, mute_reason = _is_jarvis_muted(url, args.token)
    if muted:
        cause = f" ({mute_reason})" if mute_reason else ""
        print(f"# jarvis-mute is ON{cause} — all LLM-dependent stories will skip.")
        print(f"# pass --ignore-mute to run anyway, or turn off the offending "
              f"input(s) to restore voice.")
    print()

    results = []
    for i, story in enumerate(stories, 1):
        if muted and story["kind"] != "skip":
            cause = f" ({mute_reason})" if mute_reason else ""
            result = {
                "status": "skip",
                "story": story["id"],
                "why": f"jarvis_muted_effective_2 = on{cause}",
            }
        else:
            result = run_story(story, url, args.token, verbose=args.verbose)
        results.append(result)
        st = result["status"]
        emoji = {"ok": "✓", "not ok": "✗", "skip": "—"}.get(st, "?")
        ms = result.get("elapsed_ms", "")
        ms_s = f" {ms}ms" if ms else ""
        print(f"{emoji}  {st:6}  {story['id']:32}{ms_s}")
        if st != "skip":
            print(f"         speech: {result.get('speech') or '<empty>'!r}")
        if result.get("sanitized_would_replace"):
            print(f"         ⚠  bridge would sanitize → {result['sanitized_text']!r}")
        if st == "not ok":
            print(f"         why: {result.get('why')}")
            if result.get("side_effects"):
                for eid, eff in result["side_effects"].items():
                    pre = eff.get("pre") or {}
                    post = eff.get("post") or {}
                    print(f"           {eid}: matched={eff['matched']} "
                          f"transient={eff.get('transient')} settled={eff.get('settled')}")
                    print(f"             pre  = {pre}")
                    print(f"             post = {post}")
        elif st == "ok" and result.get("side_effects"):
            for eid, eff in result["side_effects"].items():
                if eff.get("matched"):
                    settled = "settled" if eff.get("settled") else "transient (AL rollback observed)"
                    print(f"           {eid}: ✓ {settled}")

    print()
    n_ok = sum(1 for r in results if r["status"] == "ok")
    n_not = sum(1 for r in results if r["status"] == "not ok")
    n_skip = sum(1 for r in results if r["status"] == "skip")
    print(f"# summary: ok={n_ok} not_ok={n_not} skip={n_skip}")

    # Special exit-code rule: the regression-focus story must pass.
    if args.live or args.all or args.story:
        for r in results:
            if r["status"] == "not ok":
                story_def = next((s for s in STORIES if s["id"] == r["story"]), {})
                if story_def.get("regression_focus"):
                    print(f"# REGRESSION_FOCUS_FAILED: {r['story']}", file=sys.stderr)
                    return 1
    return 0 if n_not == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
