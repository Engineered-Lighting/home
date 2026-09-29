#!/usr/bin/env python3
"""Standalone model-tool-surface containment checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path


path = Path(__file__).with_name("const.py")
spec = importlib.util.spec_from_file_location("action_containment_const", path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def main() -> int:
    disabled = module.is_model_action_tool_disabled
    exposed = module.DEFAULT_CONF_FUNCTION_TOOLS
    exposed_names = {
        (tool.get("spec") or {}).get("name") for tool in exposed if isinstance(tool, dict)
    }
    assert module.MODEL_TOOL_CATALOG_ENABLED is False
    assert module.MODEL_PRIVATE_CONTEXT_ENABLED is False
    assert module.EXTERNAL_REASONING_ROUTING_ENABLED is False
    assert exposed == []
    assert "Marcelo" not in module.CONTAINED_DEFAULT_PROMPT
    assert "camera" in module.CONTAINED_DEFAULT_PROMPT
    assert not (exposed_names & module.DISABLED_MODEL_ACTION_SPEC_NAMES)

    # Renaming a public spec does not bypass the backing-function check.
    assert disabled({
        "spec": {"name": "totally_safe_reader"},
        "function": {"type": "native", "name": "execute_service"},
    })
    assert disabled({
        "spec": {"name": "totally_safe_reader"},
        "function": {"type": "bash", "command": "true"},
    })
    assert disabled({
        "spec": {"name": "totally_safe_reader"},
        "function": {"type": "rest", "resource": "https://example.invalid"},
    })
    assert disabled({
        "spec": {"name": "totally_safe_reader"},
        "function": {"type": "write_file", "path": "unsafe"},
    })
    assert disabled({
        "spec": {"name": "totally_safe_reader"},
        "function": {"type": "script", "sequence": [{"service": "lock.unlock"}]},
    })
    assert disabled({
        "spec": {"name": "totally_safe_reader"},
        "function": {
            "type": "composite",
            "sequence": [{"function": {"type": "bash", "command": "unsafe"}}],
        },
    })
    assert disabled({
        "spec": {"name": "renamed_capture"},
        "function": {"type": "gesture_training", "name": "start_guided_capture"},
    })
    assert disabled({
        "spec": {"name": "Execute-Services"},
        "function": {"type": "template", "value_template": "renamed"},
    })
    assert disabled({
        "spec": {"name": "totally_safe_reader"},
        "function": {"type": "living_lights", "name": "set_presence_override"},
    })
    # Read tools are not action dispatchers, but the separate global catalog
    # lock keeps them away from the MVP model context too.
    assert not disabled({
        "spec": {"name": "get_history"},
        "function": {"type": "native", "name": "get_history"},
    })
    assert "confirmed" in module.DEFAULT_PROMPT
    assert "grants no authority" in module.DEFAULT_PROMPT
    print("19 pass · 0 fail")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
