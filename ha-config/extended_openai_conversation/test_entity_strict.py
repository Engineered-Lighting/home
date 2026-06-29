#!/usr/bin/env python3
"""M4 tests for entity.py strict-mode tool wrapping.

Standalone runner — mocks the HA imports so we can exercise
_adjust_schema (which already exists for the structured_output path)
without a live HA. Verifies the wrap produces an OpenAI-strict-mode
compatible spec for our actual tool shapes (execute_services with
nested objects, optional params, arrays).

Run: PYTHONIOENCODING=utf-8 py -3 test_entity_strict.py
"""

from __future__ import annotations

import sys
import types
from pathlib import Path


# ── Mock HA imports so entity.py loads standalone ────────────────────
def _mk(name):
    return types.ModuleType(name)


class _vol:
    @staticmethod
    def Schema(*a, **k):
        return None

    @staticmethod
    def Required(x, **k):
        return x


sys.modules.setdefault("voluptuous", _vol)
sys.modules.setdefault("orjson", _mk("orjson"))
sys.modules.setdefault("voluptuous_openapi", _mk("voluptuous_openapi"))
voluptuous_openapi = sys.modules["voluptuous_openapi"]
voluptuous_openapi.convert = lambda *a, **k: {}

# openai SDK surface
openai = _mk("openai")
openai_types = _mk("openai.types")
openai_types_chat = _mk("openai.types.chat")
class _AsyncClient: pass
class _AsyncStream: pass
class _ChatCompletionToolParam(dict):
    def __init__(self, **kwargs): super().__init__(kwargs)
class _ChatCompletionAssistantMessageParam: pass
class _ChatCompletionChunk: pass
class _ChatCompletionMessageParam: pass
openai.AsyncClient = _AsyncClient
openai.AsyncStream = _AsyncStream
openai_types_chat.ChatCompletionToolParam = _ChatCompletionToolParam
openai_types_chat.ChatCompletionAssistantMessageParam = _ChatCompletionAssistantMessageParam
openai_types_chat.ChatCompletionChunk = _ChatCompletionChunk
openai_types_chat.ChatCompletionMessageParam = _ChatCompletionMessageParam
sys.modules["openai"] = openai
sys.modules["openai.types"] = openai_types
sys.modules["openai.types.chat"] = openai_types_chat

# homeassistant surface
ha = _mk("homeassistant")
ha_components = _mk("homeassistant.components")
ha_components_conversation = _mk("homeassistant.components.conversation")
class _ChatLog: pass
class _AssistantContent: pass
class _AssistantContentDeltaDict: pass
class _ToolResultContentDeltaDict: pass
ha_components_conversation.ChatLog = _ChatLog
ha_components_conversation.AssistantContent = _AssistantContent
ha_components_conversation.AssistantContentDeltaDict = _AssistantContentDeltaDict
ha_components_conversation.ToolResultContentDeltaDict = _ToolResultContentDeltaDict
ha_config_entries = _mk("homeassistant.config_entries")
class _ConfigSubentry: pass
ha_config_entries.ConfigSubentry = _ConfigSubentry
ha_helpers = _mk("homeassistant.helpers")
ha_helpers_dr = _mk("homeassistant.helpers.device_registry")
ha_helpers_dr.async_get = lambda hass: None
ha_helpers_entity = _mk("homeassistant.helpers.entity")
class _Entity: pass
ha_helpers_entity.Entity = _Entity
ha_helpers_llm = _mk("homeassistant.helpers.llm")
class _LLMContext: pass
class _APIInstance: pass
class _ToolInput: pass
ha_helpers_llm.LLMContext = _LLMContext
ha_helpers_llm.APIInstance = _APIInstance
ha_helpers_llm.ToolInput = _ToolInput
ha_util = _mk("homeassistant.util")
ha_util.slugify = lambda s: (s or "").lower()

ha.components = ha_components
ha_components.conversation = ha_components_conversation
ha.config_entries = ha_config_entries
ha.helpers = ha_helpers
ha_helpers.device_registry = ha_helpers_dr
ha_helpers.entity = ha_helpers_entity
ha_helpers.llm = ha_helpers_llm
ha.util = ha_util

for k, v in {
    "homeassistant": ha,
    "homeassistant.components": ha_components,
    "homeassistant.components.conversation": ha_components_conversation,
    "homeassistant.config_entries": ha_config_entries,
    "homeassistant.helpers": ha_helpers,
    "homeassistant.helpers.device_registry": ha_helpers_dr,
    "homeassistant.helpers.entity": ha_helpers_entity,
    "homeassistant.helpers.llm": ha_helpers_llm,
    "homeassistant.util": ha_util,
}.items():
    sys.modules[k] = v

# Stub the package context for entity.py's relative imports.
pkg = _mk("extended_openai_conversation_test_pkg")
pkg.__path__ = []
sys.modules["extended_openai_conversation_test_pkg"] = pkg
pkg_const = _mk("extended_openai_conversation_test_pkg.const")
for c in (
    "CONF_CHAT_MODEL", "CONF_CONTEXT_THRESHOLD", "CONF_CONTEXT_TRUNCATE_STRATEGY",
    "CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION", "CONF_MAX_TOKENS",
    "CONF_REASONING_EFFORT", "CONF_SERVICE_TIER", "CONF_SHORTEN_TOOL_CALL_ID",
    "CONF_TEMPERATURE", "CONF_TOP_P",
    "DEFAULT_CHAT_MODEL", "DEFAULT_CONTEXT_THRESHOLD", "DEFAULT_CONTEXT_TRUNCATE_STRATEGY",
    "DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION", "DEFAULT_MAX_TOKENS",
    "DEFAULT_REASONING_EFFORT", "DEFAULT_SERVICE_TIER", "DEFAULT_SHORTEN_TOOL_CALL_ID",
    "DEFAULT_TEMPERATURE", "DEFAULT_TOP_P", "DOMAIN",
):
    setattr(pkg_const, c, c.lower())
sys.modules["extended_openai_conversation_test_pkg.const"] = pkg_const
pkg_exc = _mk("extended_openai_conversation_test_pkg.exceptions")
class FunctionNotFound(Exception): pass
class ParseArgumentsFailed(Exception): pass
class TokenLengthExceededError(Exception): pass
pkg_exc.FunctionNotFound = FunctionNotFound
pkg_exc.ParseArgumentsFailed = ParseArgumentsFailed
pkg_exc.TokenLengthExceededError = TokenLengthExceededError
sys.modules["extended_openai_conversation_test_pkg.exceptions"] = pkg_exc
pkg_funcs = _mk("extended_openai_conversation_test_pkg.functions")
pkg_funcs.get_function = lambda t: None
sys.modules["extended_openai_conversation_test_pkg.functions"] = pkg_funcs
pkg_helpers = _mk("extended_openai_conversation_test_pkg.helpers")
pkg_helpers.get_model_config = lambda m: {
    "supports_max_completion_tokens": False,
    "supports_max_tokens": True,
    "supports_top_p": True,
    "supports_temperature": True,
}
sys.modules["extended_openai_conversation_test_pkg.helpers"] = pkg_helpers

# Now load entity.py
import importlib.util
ENTITY_PATH = Path(__file__).parent / "entity.py"
spec = importlib.util.spec_from_file_location(
    "extended_openai_conversation_test_pkg.entity", ENTITY_PATH,
)
entity = importlib.util.module_from_spec(spec)
sys.modules["extended_openai_conversation_test_pkg.entity"] = entity
spec.loader.exec_module(entity)


# ── Test harness ─────────────────────────────────────────────────────
passes = 0
fails = 0
failures: list[tuple[str, str]] = []


def t(name, body):
    global passes, fails
    try:
        body()
        passes += 1
        print(f"  PASS  {name}")
    except AssertionError as e:
        fails += 1
        failures.append((name, str(e)))
        print(f"  FAIL  {name}: {e}")
    except Exception as e:  # noqa: BLE001
        fails += 1
        failures.append((name, f"{type(e).__name__}: {e}"))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")


def section(name):
    print(f"\n[{name}]")


# ── Tests ────────────────────────────────────────────────────────────
section("_adjust_schema strict-mode wrap")


def _simple_object_schema_gets_strict_and_addl_false():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }
    entity._adjust_schema(schema)
    assert schema["strict"] is True
    assert schema["additionalProperties"] is False
t("simple object → strict + additionalProperties:false", _simple_object_schema_gets_strict_and_addl_false)


def _optional_props_become_required_with_nullable():
    schema = {
        "type": "object",
        "properties": {
            "domain": {"type": "string"},
            "area":   {"type": "string"},  # optional
        },
        "required": ["domain"],
    }
    entity._adjust_schema(schema)
    assert "area" in schema["required"], f"area not promoted to required: {schema['required']}"
    assert schema["properties"]["area"]["type"] == ["string", "null"], \
        f"area not made nullable: {schema['properties']['area']}"
    # domain was already required → stays as plain string
    assert schema["properties"]["domain"]["type"] == "string"
t("optional props promoted to required with nullable type", _optional_props_become_required_with_nullable)


def _nested_object_schemas_also_strict():
    schema = {
        "type": "object",
        "properties": {
            "list": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string"},
                        "service": {"type": "string"},
                    },
                    "required": ["domain", "service"],
                },
            },
        },
        "required": ["list"],
    }
    entity._adjust_schema(schema)
    inner = schema["properties"]["list"]["items"]
    assert inner["strict"] is True, "nested object missing strict"
    assert inner["additionalProperties"] is False, "nested object missing additionalProperties:false"
t("nested object in array gets strict recursively", _nested_object_schemas_also_strict)


def _array_with_no_items_no_crash():
    schema = {"type": "array"}
    entity._adjust_schema(schema)
    # Just verify it didn't crash; the rest is the no-op behavior.
t("array without items → no crash", _array_with_no_items_no_crash)


def _object_with_no_properties_no_crash():
    schema = {"type": "object"}
    entity._adjust_schema(schema)
    assert schema["strict"] is True
    assert schema["additionalProperties"] is False
t("object without properties → strict + additionalProperties only", _object_with_no_properties_no_crash)


section("strict mode applied to real tool specs (smoke)")


def _execute_services_spec_strict_compatible():
    """Run _adjust_schema on the execute_services parameters from
    const.py. Verify the result is strict-mode-compliant."""
    spec = {
        "type": "object",
        "properties": {
            "delay": {
                "type": "object",
                "properties": {
                    "hours": {"type": "integer", "minimum": 0},
                    "minutes": {"type": "integer", "minimum": 0},
                    "seconds": {"type": "integer", "minimum": 0},
                },
            },
            "list": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string"},
                        "service": {"type": "string"},
                        "service_data": {
                            "type": "object",
                            "properties": {
                                "entity_id": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "area_id": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                        },
                    },
                    "required": ["domain", "service", "service_data"],
                },
            },
        },
    }
    entity._adjust_schema(spec)
    assert spec["strict"] is True
    assert spec["additionalProperties"] is False
    # delay was optional → promoted to required + nullable
    assert "delay" in spec["required"]
    # list was optional → promoted to required + nullable
    assert "list" in spec["required"]
    # Nested objects ALSO strict
    list_items = spec["properties"]["list"]["items"]
    assert list_items["strict"] is True
    assert list_items["additionalProperties"] is False
t("execute_services spec → all nested objects strict-compliant", _execute_services_spec_strict_compatible)


def _registry_tool_with_optional_args():
    """find_entity has optional area + domain. After _adjust_schema,
    they should be in required with nullable types."""
    spec = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "area": {"type": "string"},
            "domain": {"type": "string"},
            "max_results": {"type": "integer"},
        },
        "required": ["query"],
    }
    entity._adjust_schema(spec)
    for opt in ("area", "domain", "max_results"):
        assert opt in spec["required"], f"{opt} not promoted to required"
        assert "null" in spec["properties"][opt]["type"], \
            f"{opt} type not nullable: {spec['properties'][opt]['type']}"
    assert spec["properties"]["query"]["type"] == "string", "required prop type unchanged"
t("find_entity-shape spec: optional area/domain become nullable+required", _registry_tool_with_optional_args)


# ── Summary ──────────────────────────────────────────────────────────
print(f"\n{passes} pass · {fails} fail")
if fails:
    print("\nFailures:")
    for n, m in failures:
        print(f"  - {n}: {m}")
sys.exit(0 if fails == 0 else 1)
