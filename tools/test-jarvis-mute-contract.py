#!/usr/bin/env python3
"""Source-contract tests for the Jarvis mute lifecycle.

These checks intentionally avoid live Home Assistant, Voice PE, TTS, or
device effects. They pin the local wiring that supports DOC-S21:

  - HA package exposes the composite mute helpers.
  - conversation.py reads the composite sensor, applies mute commands,
    drops muted turns silently, and clears manual/timer mute on resume.
  - the Home app slash commands/header pill operate the same helpers.
  - the live voice-story probe skips LLM speech checks when muted.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

REPO = Path(__file__).resolve().parent.parent


passes = 0
fails = 0
failures: list[tuple[str, str]] = []


def read(rel_path: str) -> str:
    return (REPO / rel_path).read_text(encoding="utf-8", errors="replace")


def require_tokens(source: str, tokens: list[str], label: str) -> None:
    missing = [token for token in tokens if token not in source]
    assert not missing, f"{label} missing tokens: {missing}"


def t(name: str, fn) -> None:
    global passes, fails
    try:
        fn()
        passes += 1
        print(f"  PASS  {name}")
    except AssertionError as exc:
        fails += 1
        failures.append((name, str(exc)))
        print(f"  FAIL  {name}: {exc}")
    except Exception as exc:  # noqa: BLE001
        fails += 1
        failures.append((name, f"{type(exc).__name__}: {exc}"))
        print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")


def parsed_source(rel_path: str) -> tuple[str, ast.Module]:
    source = read(rel_path)
    return source, ast.parse(source, filename=str(REPO / rel_path))


def class_methods(source: str, tree: ast.Module, class_name: str) -> dict[str, str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            methods: dict[str, str] = {}
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    segment = ast.get_source_segment(source, item)
                    methods[item.name] = segment or ""
            return methods
    raise AssertionError(f"class not found: {class_name}")


print("\nJarvis mute contract\n")


def test_ha_package_defines_composite_mute_entities() -> None:
    source = read("ha-config/homeai_jarvis_mute.yaml")
    require_tokens(
        source,
        [
            "jarvis_muted_explicit",
            "jarvis_auto_mute_tv",
            "jarvis_mute_until",
            "jarvis_mute_reason",
            "unique_id: jarvis_muted_effective_v2",
            "input_boolean.homeai_movie",
            "media_player.lg_tv",
            "jarvis_refresh_muted_effective_1m",
            "homeassistant.update_entity",
            "binary_sensor.jarvis_muted_effective_2",
        ],
        "homeai_jarvis_mute.yaml",
    )
    state_lines = [line for line in source.splitlines() if line.strip().startswith("state:")]
    assert state_lines, "template binary_sensor state expression missing"
    state_expr = "\n".join(state_lines)
    require_tokens(
        state_expr,
        [
            "input_boolean.jarvis_muted_explicit",
            "input_boolean.homeai_movie",
            "input_datetime.jarvis_mute_until",
            "input_boolean.jarvis_auto_mute_tv",
            "media_player.lg_tv",
        ],
        "mute template expression",
    )


t("HA package exposes composite mute helpers", test_ha_package_defines_composite_mute_entities)


def test_conversation_gate_reads_composite_and_drops_muted_turns() -> None:
    source, tree = parsed_source("ha-config/extended_openai_conversation/conversation.py")
    methods = class_methods(source, tree, "ExtendedOpenAIAgentEntity")
    is_muted = methods.get("_is_muted", "")
    async_process = methods.get("async_process", "")
    require_tokens(
        is_muted,
        [
            "binary_sensor.jarvis_muted_effective_2",
            "return False",
            'state == "on"',
        ],
        "ExtendedOpenAIAgentEntity._is_muted",
    )
    require_tokens(
        async_process,
        [
            "_parse_mute_command",
            "_is_muted()",
            "_is_resume_command",
            "_clear_mute_state",
            "_apply_mute",
            "JARVIS_MUTED_DROP",
            'async_set_speech("")',
        ],
        "ExtendedOpenAIAgentEntity.async_process",
    )
    assert "continue_conversation=False" in async_process.replace(" ", ""), \
        "muted/resume/mute branches must close the Voice PE conversation"


t("conversation gate reads composite sensor and drops muted turns",
  test_conversation_gate_reads_composite_and_drops_muted_turns)


def test_conversation_apply_and_clear_mute_services() -> None:
    source, tree = parsed_source("ha-config/extended_openai_conversation/conversation.py")
    methods = class_methods(source, tree, "ExtendedOpenAIAgentEntity")
    apply_mute = methods.get("_apply_mute", "")
    clear_mute = methods.get("_clear_mute_state", "")
    require_tokens(
        apply_mute,
        [
            "input_boolean",
            "turn_on",
            "input_boolean.jarvis_muted_explicit",
            "input_text",
            "input_text.jarvis_mute_reason",
            "input_boolean.homeai_movie",
            "input_datetime.jarvis_mute_until",
            "media_player",
            "media_stop",
            "extended_openai_conversation.tts_cancel",
        ],
        "ExtendedOpenAIAgentEntity._apply_mute",
    )
    require_tokens(
        clear_mute,
        [
            "input_boolean",
            "turn_off",
            "input_boolean.jarvis_muted_explicit",
            "input_datetime",
            "input_datetime.jarvis_mute_until",
            "2000-01-01T00:00:00",
        ],
        "ExtendedOpenAIAgentEntity._clear_mute_state",
    )


t("conversation apply/clear paths call the expected HA helpers",
  test_conversation_apply_and_clear_mute_services)


def test_conversation_mute_patterns_are_anchored() -> None:
    source = read("ha-config/extended_openai_conversation/conversation.py")
    require_tokens(
        source,
        [
            "_RESUME_PATTERNS = re.compile",
            "_MUTE_PATTERNS = [",
            'r"^\\s*(hey\\s+)?(jarvis,?\\s+)?"',
            "stop(\\s+(it|please|now|jarvis))?",
            "transient_unmute",
        ],
        "conversation mute parser",
    )


t("conversation mute/resume parser remains whole-utterance anchored",
  test_conversation_mute_patterns_are_anchored)


def test_home_app_mute_commands_and_header_pill_are_wired() -> None:
    source = read("app/src/home-app.jsx")
    require_tokens(
        source,
        [
            'cmd: "/mute"',
            'cmd: "/unmute"',
            'case "mute":',
            'case "unmute":',
            'client.callApi("GET", "states/binary_sensor.jarvis_muted_effective_2")',
            'ev?.data?.entity_id !== "binary_sensor.jarvis_muted_effective_2"',
            "muteState?.muted",
            "onUnmuteClick",
            'entity_id: "input_boolean.homeai_movie"',
            'entity_id: "input_boolean.jarvis_muted_explicit"',
            'entity_id: "input_datetime.jarvis_mute_until"',
            'entity_id: "input_text.jarvis_mute_reason"',
        ],
        "home-app.jsx mute UI",
    )
    assert source.count(
        'client.callService("input_boolean", "turn_off", { entity_id: "input_boolean.jarvis_muted_explicit" })'
    ) >= 2, "both /unmute and header pill must clear manual mute"
    assert source.count('datetime: "2000-01-01T00:00:00"') >= 2, \
        "both /unmute and header pill must clear timed mute"


t("Home app slash commands and header pill target mute helpers",
  test_home_app_mute_commands_and_header_pill_are_wired)


def test_s87_mute_pill_reason_and_auto_signal_contract() -> None:
    package = read("ha-config/homeai_jarvis_mute.yaml")
    require_tokens(
        package,
        [
            "attributes:",
            "reason: >-",
            "mute_until_iso: >-",
            "%}movie",
            "%}tv",
            "%}timer",
            "input_text.jarvis_mute_reason",
        ],
        "jarvis muted effective attributes",
    )

    source = read("app/src/home-app.jsx")
    require_tokens(
        source,
        [
            'type="button"',
            "click to clear manual/timer mute",
            'muteState.reason || "active"',
            'reason: s?.attributes?.reason || ""',
            'until: s?.attributes?.mute_until_iso || ""',
        ],
        "HomeHeader mute pill reason UI",
    )
    start = source.index("const handleUnmuteClick")
    end = source.index("/* ── conversation.finished", start)
    handler = source[start:end]
    require_tokens(
        handler,
        [
            'entity_id: "input_boolean.jarvis_muted_explicit"',
            'entity_id: "input_datetime.jarvis_mute_until"',
        ],
        "header mute-pill click handler",
    )
    assert 'entity_id: "input_boolean.homeai_movie"' not in handler, \
        "header pill must not clear movie-mode auto mute"
    assert 'entity_id: "input_boolean.jarvis_auto_mute_tv"' not in handler, \
        "header pill must not disable TV auto mute"

    unmute_start = source.index('case "unmute":')
    unmute_end = source.index('case "model":', unmute_start)
    unmute_branch = source[unmute_start:unmute_end]
    assert 'entity_id: "input_boolean.homeai_movie"' not in unmute_branch, \
        "/unmute must not clear movie-mode auto mute"
    assert 'entity_id: "input_boolean.jarvis_auto_mute_tv"' not in unmute_branch, \
        "/unmute must not disable TV auto mute"


t("S87 mute pill reason and auto-signal click contract",
  test_s87_mute_pill_reason_and_auto_signal_contract)


def test_typed_home_app_commands_get_one_turn_mute_bypass() -> None:
    source = read("app/src/home-app.jsx")
    start = source.index("const sendToHA = useCallback")
    end = source.index("/* ── Stop / cancel an in-flight run", start)
    send_to_ha = source[start:end]
    require_tokens(
        send_to_ha,
        [
            "const pipelineText = muteState?.muted",
            "`just this once, ${text}`",
            "muted · running typed command once; voice stays quiet",
            "text: pipelineText",
            "muteState?.muted",
        ],
        "sendToHA typed mute bypass",
    )
    assert "addEvent({ kind: \"user\", text });" in send_to_ha, \
        "visible user bubble should preserve the user's original text"


t("typed Home-app commands use one-turn mute bypass",
  test_typed_home_app_commands_get_one_turn_mute_bypass)


def test_voice_story_probe_skips_when_mute_sensor_on() -> None:
    source = read("tools/probe-voice-stories.py")
    require_tokens(
        source,
        [
            '_JARVIS_MUTE_SENSOR = "binary_sensor.jarvis_muted_effective_2"',
            'f"{url}/api/states/{_JARVIS_MUTE_SENSOR}"',
            '"input_boolean.jarvis_muted_explicit"',
            '"input_boolean.homeai_movie"',
            '"input_boolean.jarvis_auto_mute_tv"',
            "--ignore-mute",
            "if not args.ignore_mute:",
            'if muted and story["kind"] != "skip":',
            '"why": f"jarvis_muted_effective_2 = on{cause}"',
        ],
        "probe-voice-stories.py mute handling",
    )


t("voice story probe detects mute and skips LLM speech assertions",
  test_voice_story_probe_skips_when_mute_sensor_on)


print(f"\n{passes} pass . {fails} fail")
if fails:
    print("\nFailures:")
    for name, message in failures:
        print(f"  - {name}: {message}")

sys.exit(0 if fails == 0 else 1)
