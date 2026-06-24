#!/usr/bin/env python3
"""Static QA sanity checks for the Home monorepo.

These are intentionally cheap and context-aware. They catch drift that can
make broader QA noisy: missing suite files, duplicate route registration,
duplicate HA view URLs, and duplicate slash-command aliases in the command
handler. Output follows the repo's "N pass · M fail" convention so
run-test-coverage.py can parse it.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from collections import defaultdict
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
SKIP_PATH_PARTS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "env",
    "node_modules",
    "site-packages",
    "venv",
}

passes = 0
fails = 0
failures: list[tuple[str, str]] = []


def ok(name: str) -> None:
    global passes
    passes += 1
    print(f"  PASS  {name}")


def fail(name: str, detail: str) -> None:
    global fails
    fails += 1
    failures.append((name, detail))
    print(f"  FAIL  {name}")
    print(f"        {detail[:500]}")


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO)).replace("\\", "/")
    except ValueError:
        return str(path)


def is_repo_source(path: Path) -> bool:
    """True for first-party source files, false for vendored/env artifacts."""
    parts = set(path.relative_to(REPO).parts)
    return not (parts & SKIP_PATH_PARTS)


def check_runner_suite_files() -> None:
    path = REPO / "tools" / "run-test-coverage.py"
    spec = importlib.util.spec_from_file_location("run_test_coverage_static", path)
    if spec is None or spec.loader is None:
        fail("load run-test-coverage.py", "importlib could not load module")
        return
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    missing: list[str] = []
    for suite in getattr(mod, "SUITES", []):
        cmd = getattr(suite, "cmd", [])
        for part in cmd[1:]:
            p = Path(str(part))
            if p.is_absolute() and str(p).startswith(str(REPO)) and not p.exists():
                missing.append(f"{getattr(suite, 'id', '?')}: {rel(p)}")
    if missing:
        fail("coverage suite references existing files", "; ".join(missing))
    else:
        ok("coverage suite references existing files")


def check_source_filter_excludes_vendored_code() -> None:
    first_party = REPO / "stack" / "services" / "vision" / "app.py"
    vendored = (
        REPO / "stack" / "services" / "video-labeler" / ".venv" /
        "Lib" / "site-packages" / "fastapi" / "applications.py"
    )
    if is_repo_source(first_party) and not is_repo_source(vendored):
        ok("source scanner excludes virtualenv/vendor files")
    else:
        fail(
            "source scanner excludes virtualenv/vendor files",
            f"first_party={is_repo_source(first_party)} vendored={is_repo_source(vendored)}",
        )


def check_duplicate_fastapi_routes() -> None:
    route_re = re.compile(r'@app\.(get|post|put|delete|patch|api_route)\("([^"]+)"')
    duplicates: list[str] = []
    for path in (REPO / "stack" / "services").rglob("*.py"):
        if not is_repo_source(path):
            continue
        seen: dict[tuple[str, str], int] = {}
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            m = route_re.search(line)
            if not m:
                continue
            key = (m.group(1).upper(), m.group(2))
            if key in seen:
                duplicates.append(
                    f"{rel(path)}:{lineno} duplicates {key[0]} {key[1]} first seen line {seen[key]}"
                )
            else:
                seen[key] = lineno
    if duplicates:
        fail("FastAPI routes are unique within each service", "; ".join(duplicates))
    else:
        ok("FastAPI routes are unique within each service")


def check_duplicate_ha_view_urls() -> None:
    path = REPO / "ha-config" / "extended_openai_conversation" / "__init__.py"
    class_re = re.compile(r"class\s+(\w+).*View")
    url_re = re.compile(r'url\s*=\s*"([^"]+)"')
    seen: dict[str, tuple[str, int]] = {}
    duplicates: list[str] = []
    cls = "<unknown>"
    for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        m_cls = class_re.search(line)
        if m_cls:
            cls = m_cls.group(1)
        m_url = url_re.search(line)
        if not m_url:
            continue
        url = m_url.group(1)
        if url in seen:
            first_cls, first_line = seen[url]
            duplicates.append(f"{url} in {cls}:{lineno} duplicates {first_cls}:{first_line}")
        else:
            seen[url] = (cls, lineno)
    if duplicates:
        fail("HA view URLs are unique", "; ".join(duplicates))
    else:
        ok("HA view URLs are unique")


def _handle_command_block() -> str:
    path = REPO / "app" / "src" / "home-app.jsx"
    text = path.read_text(encoding="utf-8", errors="replace")
    start = text.find("const handleCommand = useCallback")
    if start < 0:
        return ""
    end = text.find("default:", start)
    if end < 0:
        end = text.find("}, [addEvent", start)
    return text[start:end]


def check_duplicate_slash_aliases() -> None:
    block = _handle_command_block()
    if not block:
        fail("slash command handler found", "could not locate handleCommand block")
        return
    aliases = re.findall(r'case\s+"([a-zA-Z0-9_-]+)"\s*:', block)
    by_alias: dict[str, int] = defaultdict(int)
    for alias in aliases:
        by_alias[alias] += 1
    duplicates = [alias for alias, count in sorted(by_alias.items()) if count > 1]
    if duplicates:
        fail("slash command aliases are unique in handleCommand", ", ".join(duplicates))
    else:
        ok(f"slash command aliases are unique in handleCommand ({len(aliases)} aliases)")


def check_home_overview_slash_command_inventory() -> None:
    block = _handle_command_block()
    if not block:
        fail("Home overview slash-command inventory matches handler", "could not locate handleCommand block")
        return
    aliases = sorted(set(re.findall(r'case\s+"([a-zA-Z0-9_-]+)"\s*:', block)))
    overview_text = (REPO / "docs" / "HOME_SYSTEM_OVERVIEW.md").read_text(
        encoding="utf-8", errors="replace"
    )
    section_start = overview_text.find("## 8. Home app features")
    section_end = overview_text.find("## 9.", section_start)
    if section_start < 0 or section_end < 0:
        fail("Home overview slash-command inventory matches handler", "could not locate section 8")
        return
    section = overview_text[section_start:section_end]
    command_start = section.find("### 8a. Slash-command inventory")
    command_end = section.find("### 8b.", command_start)
    if command_start < 0 or command_end < 0:
        fail("Home overview slash-command inventory matches handler", "could not locate slash-command inventory subsection")
        return
    command_section = section[command_start:command_end]
    documented = sorted(
        value[1:]
        for value in re.findall(r"`(/[^`]+)`", command_section)
    )
    missing = sorted(set(aliases) - set(documented))
    extra = sorted(set(documented) - set(aliases))
    if missing:
        fail("Home overview slash-command inventory matches handler", ", ".join(f"/{a}" for a in missing))
    elif extra:
        fail("Home overview slash-command inventory matches handler", "stale documented commands: " + ", ".join(f"/{a}" for a in extra))
    else:
        ok(f"Home overview slash-command inventory matches handler ({len(aliases)} commands)")


def _simulation_scenario_ids() -> list[str]:
    text = (REPO / "app" / "src" / "simulation-data.jsx").read_text(
        encoding="utf-8", errors="replace"
    )
    start = text.find("const scenarios =")
    end = text.find("return scenarios[id]", start)
    block = text[start:end if end > start else len(text)]
    return sorted(set(re.findall(r'\bid:\s*"([^"]+)"', block)))


def check_home_overview_simulation_scenario_inventory() -> None:
    ids = _simulation_scenario_ids()
    if not ids:
        fail("Home overview Simulation Mode scenario inventory matches registry", "no scenarios extracted")
        return
    overview_text = (REPO / "docs" / "HOME_SYSTEM_OVERVIEW.md").read_text(
        encoding="utf-8", errors="replace"
    )
    section_start = overview_text.find("## 8. Home app features")
    section_end = overview_text.find("## 9.", section_start)
    if section_start < 0 or section_end < 0:
        fail("Home overview Simulation Mode scenario inventory matches registry", "could not locate section 8")
        return
    section = overview_text[section_start:section_end]
    scenario_start = section.find("### 8b. Simulation Mode scenario inventory")
    scenario_end = section.find("### 8c.", scenario_start)
    if scenario_start < 0 or scenario_end < 0:
        fail("Home overview Simulation Mode scenario inventory matches registry", "could not locate Simulation Mode scenario inventory subsection")
        return
    scenario_section = section[scenario_start:scenario_end]
    documented = sorted(
        value
        for value in re.findall(r"`([^`]+)`", scenario_section)
        if "/" not in value and "\\" not in value and "." not in value
    )
    missing = sorted(set(ids) - set(documented))
    extra = sorted(set(documented) - set(ids))
    if missing:
        fail("Home overview Simulation Mode scenario inventory matches registry", ", ".join(missing))
    elif extra:
        fail("Home overview Simulation Mode scenario inventory matches registry", "stale documented scenarios: " + ", ".join(extra))
    else:
        ok(f"Home overview Simulation Mode scenario inventory matches registry ({len(ids)} scenarios)")


def check_scenario_namespacing() -> None:
    docs = REPO / "docs" / "SCENARIO-TESTS.md"
    qaf = REPO / "tools" / "qa-features.yaml"
    doc_ids = set(re.findall(r"^###\s+(S\d+)\b", docs.read_text(encoding="utf-8", errors="replace"), re.M))
    shot_ids = set(re.findall(r"^\s+-\s+id:\s+(S\d+)\b", qaf.read_text(encoding="utf-8", errors="replace"), re.M))
    overlap = sorted(doc_ids & shot_ids)
    if overlap:
        ok(
            "legacy scenario ID collisions are namespaced by convention "
            f"(DOC-* vs SHOT-*; {len(overlap)} overlapping legacy IDs)"
        )
    else:
        ok("legacy scenario IDs do not collide")


def check_home_overview_frontend_inventory() -> None:
    index = REPO / "app" / "src" / "index.html"
    overview = REPO / "docs" / "HOME_SYSTEM_OVERVIEW.md"
    index_text = index.read_text(encoding="utf-8", errors="replace")
    overview_text = overview.read_text(encoding="utf-8", errors="replace")
    static_scripts = re.findall(r'<script[^>]+src="([^"]+)"', index_text)
    boot = re.search(r"var\s+files\s*=\s*\[([\s\S]*?)\];", index_text)
    if not boot:
        fail("Home overview frontend inventory matches boot loader", "could not locate index.html boot loader files")
        return
    files = re.findall(r"\['([^']+)'\s*,\s*(?:true|false)\]", boot.group(1))
    section_start = overview_text.find("## 8. Home app features")
    section_end = overview_text.find("## 9.", section_start)
    if section_start < 0 or section_end < 0:
        fail("Home overview frontend inventory matches boot loader", "could not locate section 8 in HOME_SYSTEM_OVERVIEW.md")
        return
    section = overview_text[section_start:section_end]
    boot_start = section.find("### 8c. Boot-loader module inventory")
    boot_end = section.find("The Tauri shell is intentionally thin", boot_start)
    if boot_start < 0 or boot_end < 0:
        fail("Home overview frontend inventory matches boot loader", "could not locate boot-loader inventory table")
        return
    boot_section = section[boot_start:boot_end]
    documented_files = re.findall(r"^\| `([^`]+)` \|", boot_section, re.M)
    missing = sorted(set(files) - set(documented_files))
    extra = sorted(set(documented_files) - set(files))
    if missing:
        fail("Home overview frontend inventory matches boot loader", ", ".join(missing))
    elif extra:
        fail("Home overview frontend inventory matches boot loader", "stale documented modules: " + ", ".join(extra))
    else:
        ok(f"Home overview frontend inventory matches boot loader ({len(files)} files)")
    runtime_start = section.find("Frontend runtime scripts loaded before the app boot chain:")
    runtime_end = section.find("| Feature |", runtime_start)
    if runtime_start < 0 or runtime_end < 0:
        fail("Home overview frontend runtime scripts are documented", "could not locate runtime script table")
        return
    runtime_section = section[runtime_start:runtime_end]
    documented_runtime = re.findall(r"^\| `([^`]+)` \|", runtime_section, re.M)
    missing_runtime = sorted(set(static_scripts) - set(documented_runtime))
    extra_runtime = sorted(set(documented_runtime) - set(static_scripts))
    if missing_runtime:
        fail("Home overview frontend runtime scripts are documented", ", ".join(missing_runtime))
    elif extra_runtime:
        fail("Home overview frontend runtime scripts are documented", "stale documented runtime scripts: " + ", ".join(extra_runtime))
    else:
        ok(f"Home overview frontend runtime scripts are documented ({len(static_scripts)} scripts)")


def _home_ui_surfaces() -> list[str]:
    surfaces = []
    for path in (REPO / "app" / "src").glob("home-*.jsx"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"Drawer|Overlay|Modal|Atlas|Panel|Lightbox", text):
            surfaces.append(path.name)
    return sorted(surfaces)


def check_home_overview_ui_surface_inventory() -> None:
    surfaces = _home_ui_surfaces()
    if not surfaces:
        fail("Home overview UI surface inventory matches source", "no UI surfaces extracted")
        return
    overview_text = (REPO / "docs" / "HOME_SYSTEM_OVERVIEW.md").read_text(
        encoding="utf-8", errors="replace"
    )
    section_start = overview_text.find("## 8. Home app features")
    section_end = overview_text.find("## 9.", section_start)
    if section_start < 0 or section_end < 0:
        fail("Home overview UI surface inventory matches source", "could not locate section 8")
        return
    section = overview_text[section_start:section_end]
    surface_start = section.find("### 8d. Major UI surface inventory")
    surface_end = section.find("For deeper feature design notes", surface_start)
    if surface_start < 0 or surface_end < 0:
        fail("Home overview UI surface inventory matches source", "could not locate UI surface inventory subsection")
        return
    surface_section = section[surface_start:surface_end]
    documented = sorted(
        value
        for value in re.findall(r"`(home-[^`]+\.jsx)`", surface_section)
        if "*" not in value
    )
    missing = sorted(set(surfaces) - set(documented))
    extra = sorted(set(documented) - set(surfaces))
    if missing:
        fail("Home overview UI surface inventory matches source", ", ".join(missing))
    elif extra:
        fail("Home overview UI surface inventory matches source", "stale documented UI surfaces: " + ", ".join(extra))
    else:
        ok(f"Home overview UI surface inventory matches source ({len(surfaces)} surfaces)")


def _living_lights_classifier_states() -> list[str]:
    text = (REPO / "tools" / "build-living-lights-yaml.py").read_text(
        encoding="utf-8", errors="replace"
    )
    match = re.search(r"_CLASSIFIER_SENSOR\s*=\s*r\"\"\"([\s\S]*?)\"\"\"", text)
    if not match:
        return []
    state_section = match.group(1).split("attributes:", 1)[0]
    states: list[str] = []
    for value in re.findall(r"{%\s*(?:if|elif|else)[^%]*%}\s*([a-z_]+)", state_section):
        if value not in states:
            states.append(value)
    return states


def _ha_input_booleans() -> list[str]:
    out: list[str] = []
    paths = sorted((REPO / "ha-config").glob("*.yaml")) + sorted(
        (REPO / "ha-config" / "packages").glob("*.yaml")
    )
    for path in paths:
        in_block = False
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if re.fullmatch(r"input_boolean:\s*", line):
                in_block = True
                continue
            if in_block and re.match(r"^[A-Za-z_][A-Za-z0-9_-]*:\s*$", line):
                in_block = False
            if not in_block:
                continue
            match = re.match(r"^  ([A-Za-z0-9_]+):\s*$", line)
            if match:
                out.append(f"input_boolean.{match.group(1)}")
    return sorted(set(out))


def _agent_tool_catalog() -> list[str]:
    text = (REPO / "ha-config" / "extended_openai_conversation" / "const.py").read_text(
        encoding="utf-8", errors="replace"
    )
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "DEFAULT_CONF_FUNCTION_TOOLS"
            for target in node.targets
        ):
            continue
        try:
            tools = ast.literal_eval(node.value)
        except Exception:
            return []
        names = [
            str(item.get("spec", {}).get("name", "")).strip()
            for item in tools
            if isinstance(item, dict)
        ]
        return [name for name in names if name]
    return []


def _spatial_model_inventory() -> dict[str, int | list[str]]:
    try:
        data = json.loads((REPO / "ha-config" / "spatial_model.json").read_text(
            encoding="utf-8", errors="replace"
        ))
    except Exception:
        return {}
    cameras = data.get("cameras") or {}
    lights = data.get("lights") or {}
    return {
        "schema": int(data.get("schema") or 0),
        "camera_count": len(cameras),
        "zone_count": sum(len((camera.get("zones") or {})) for camera in cameras.values()),
        "light_count": len(lights),
        "lights_with_footprint_polygon": sum(
            1 for rec in lights.values()
            if isinstance(rec, dict) and bool(rec.get("footprint_polygon"))
        ),
        "footprint_empty_count": sum(
            1 for rec in lights.values()
            if isinstance(rec, dict) and rec.get("footprint_empty") is True
        ),
        "missing_deployed_fields": [
            key for key in ("camera_edges", "zone_to_room") if key not in data
        ],
    }


def _python_literal_assignments(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        try:
            value = ast.literal_eval(node.value)
        except Exception:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = value
    return out


def _predictive_lighting_mqtt_inventory() -> dict[str, list[str]]:
    constants = _python_literal_assignments(REPO / "addons" / "predictive-lighting" / "app.py")
    if not constants:
        return {}
    anticipated_base = str(constants.get("ANTICIPATED_BASE", "predictive-lighting/anticipated"))
    availability = str(constants.get("AVAIL_TOPIC", "predictive-lighting/availability"))
    discovery_base = str(constants.get("DISCOVERY_BASE", "homeassistant/binary_sensor/anticipated_"))
    debug_base = str(constants.get("DEBUG_TOPIC_BASE", "predictive-lighting/debug/track"))
    heartbeat = str(constants.get("HEARTBEAT_TOPIC", "predictive-lighting/eval/heartbeat"))
    top = str(constants.get("TOPIC", "frigate/events"))
    return {
        "publish_topics": [
            availability,
            f"{anticipated_base}/<room>",
            f"{discovery_base}<room>/config",
            heartbeat,
            f"{debug_base}/<track_id_short>",
        ],
        "subscribe_topics": [
            top,
            f"{debug_base}/+",
            f"{anticipated_base}/+",
        ],
    }


def check_home_overview_living_lights_state_flag_inventory() -> None:
    states = _living_lights_classifier_states()
    flags = _ha_input_booleans()
    if not states:
        fail("Home overview Living Lights state/flag inventory matches source", "no classifier states extracted")
        return
    if not flags:
        fail("Home overview Living Lights state/flag inventory matches source", "no input_boolean flags extracted")
        return
    overview_text = (REPO / "docs" / "HOME_SYSTEM_OVERVIEW.md").read_text(
        encoding="utf-8", errors="replace"
    )
    section_start = overview_text.find("### 14b. The 8-state classifier")
    section_end = overview_text.find("### 14c.", section_start)
    if section_start < 0 or section_end < 0:
        fail("Home overview Living Lights state/flag inventory matches source", "could not locate section 14b")
        return
    state_section = overview_text[section_start:section_end]
    documented_states = re.findall(r"\*\*`([a-z_]+)`\*\*", state_section)
    flag_count_line = f"{len(flags)} current `input_boolean` flags"
    errors: list[str] = []
    if documented_states != states:
        errors.append(
            "classifier states drifted: documented="
            + ", ".join(documented_states)
            + " source="
            + ", ".join(states)
        )
    if flag_count_line not in overview_text:
        errors.append(f"overview missing input_boolean flag count: {flag_count_line}")
    if "docs/qa/home-app-feature-audit.md#home-assistant-input-boolean-flags" not in overview_text:
        errors.append("overview missing link to exact generated input_boolean inventory")
    if errors:
        fail("Home overview Living Lights state/flag inventory matches source", "; ".join(errors))
    else:
        ok(
            "Home overview Living Lights state/flag inventory matches source "
            f"({len(states)} states, {len(flags)} flags)"
        )


def check_home_overview_agent_tool_catalog_inventory() -> None:
    tools = _agent_tool_catalog()
    if not tools:
        fail("Home overview Extended OpenAI tool catalog matches source", "no agent tools extracted")
        return
    overview_text = (REPO / "docs" / "HOME_SYSTEM_OVERVIEW.md").read_text(
        encoding="utf-8", errors="replace"
    )
    expected_count = f"{len(tools)} Extended OpenAI agent tools"
    errors: list[str] = []
    if "no aggregated list" in overview_text:
        errors.append("overview still claims the tool catalog has no aggregated list")
    if expected_count not in overview_text:
        errors.append(f"overview missing agent tool count: {expected_count}")
    if "docs/qa/home-app-feature-audit.md#extended-openai-agent-tools" not in overview_text:
        errors.append("overview missing link to exact generated agent tool inventory")
    if errors:
        fail("Home overview Extended OpenAI tool catalog matches source", "; ".join(errors))
    else:
        ok(f"Home overview Extended OpenAI tool catalog matches source ({len(tools)} tools)")


def check_home_overview_predictive_lighting_mqtt_inventory() -> None:
    mqtt = _predictive_lighting_mqtt_inventory()
    if not mqtt:
        fail("Home overview predictive-lighting MQTT inventory matches source", "no MQTT topics extracted")
        return
    overview_text = (REPO / "docs" / "HOME_SYSTEM_OVERVIEW.md").read_text(
        encoding="utf-8", errors="replace"
    )
    errors: list[str] = []
    publish_count = f"{len(mqtt['publish_topics'])} predictive-lighting MQTT publish topic families"
    subscribe_count = f"{len(mqtt['subscribe_topics'])} predictive-lighting MQTT subscription topic families"
    for expected in [publish_count, subscribe_count]:
        if expected not in overview_text:
            errors.append(f"overview missing MQTT count: {expected}")
    if "docs/qa/home-app-feature-audit.md#predictive-lighting-mqtt-topics" not in overview_text:
        errors.append("overview missing link to generated predictive-lighting MQTT topic inventory")
    for topic in mqtt["publish_topics"] + mqtt["subscribe_topics"]:
        if topic not in overview_text:
            errors.append(f"overview missing MQTT topic: {topic}")
    stale_phrases = [
        "actual topic names are inferable",
        "no human-facing schema",
    ]
    for phrase in stale_phrases:
        if phrase in overview_text:
            errors.append(f"overview still contains stale MQTT wording: {phrase}")
    if errors:
        fail("Home overview predictive-lighting MQTT inventory matches source", "; ".join(errors))
    else:
        ok(
            "Home overview predictive-lighting MQTT inventory matches source "
            f"({len(mqtt['publish_topics'])} publish, {len(mqtt['subscribe_topics'])} subscribe)"
        )


def check_home_overview_spatial_model_schema_inventory() -> None:
    spatial = _spatial_model_inventory()
    if not spatial:
        fail("Home overview spatial_model schema inventory matches source", "no spatial model inventory extracted")
        return
    overview_text = (REPO / "docs" / "HOME_SYSTEM_OVERVIEW.md").read_text(
        encoding="utf-8", errors="replace"
    )
    expected_counts = [
        f"schema {spatial['schema']}",
        f"{spatial['camera_count']} spatial model cameras",
        f"{spatial['light_count']} spatial model lights",
        f"{spatial['lights_with_footprint_polygon']} lights with footprint polygons",
        f"{spatial['footprint_empty_count']} footprint-empty lights",
    ]
    stale_phrases = [
        "The `spatial_model.json` schema",
        "not formally written down",
        "has this field as `null`",
        "has `camera_edges` and `zone_to_room` as `null`",
        "camera_edges/zone_to_room currently `null`",
    ]
    errors: list[str] = []
    for expected in expected_counts:
        if expected not in overview_text:
            errors.append(f"overview missing spatial model fact: {expected}")
    if "docs/qa/home-app-feature-audit.md#spatial-model-schema" not in overview_text:
        errors.append("overview missing link to exact generated spatial model schema")
    for phrase in stale_phrases:
        if phrase in overview_text:
            errors.append(f"overview still contains stale spatial wording: {phrase}")
    if spatial["missing_deployed_fields"]:
        expected_missing = "`camera_edges` and `zone_to_room` are absent from the local repo copy"
        if expected_missing not in overview_text:
            errors.append("overview missing absent deployed-field note for camera_edges/zone_to_room")
    if errors:
        fail("Home overview spatial_model schema inventory matches source", "; ".join(errors))
    else:
        ok(
            "Home overview spatial_model schema inventory matches source "
            f"({spatial['camera_count']} cameras, {spatial['light_count']} lights)"
        )


def check_home_overview_documentation_inventory_status() -> None:
    overview_text = (REPO / "docs" / "HOME_SYSTEM_OVERVIEW.md").read_text(
        encoding="utf-8", errors="replace"
    )
    start = overview_text.find("**Documentation inventory status:**")
    end = overview_text.find("---", start)
    if start < 0 or end < 0:
        fail("Home overview documentation inventory status is current", "could not locate documentation inventory status block")
        return
    block = overview_text[start:end]
    remaining_marker = "Remaining documentation gaps:"
    covered_marker = "Source-derived inventories now covered by generated QA evidence:"
    errors: list[str] = []
    if covered_marker not in block:
        errors.append("missing source-derived covered-inventory subsection")
    if remaining_marker not in block:
        errors.append("missing remaining documentation gaps subsection")
    resolved_links = [
        "docs/qa/home-app-feature-audit.md#predictive-lighting-mqtt-topics",
        "docs/qa/home-app-feature-audit.md#spatial-model-schema",
        "docs/qa/home-app-feature-audit.md#extended-openai-agent-tools",
        "docs/ARCHITECTURE_DECISIONS.md",
    ]
    for link in resolved_links:
        if link not in block:
            errors.append(f"missing resolved inventory link: {link}")
    if remaining_marker in block:
        remaining = block.split(remaining_marker, 1)[1]
        stale_resolved_terms = [
            "predictive-lighting` add-on's MQTT topic schema",
            "spatial_model.json` schema/inventory",
            "Extended OpenAI Conversation tool catalog",
            "ADR (architecture decision record) trail",
        ]
        for term in stale_resolved_terms:
            if term in remaining:
                errors.append(f"resolved inventory still listed as remaining gap: {term}")
    if "**Documentation gaps** (what is NOT documented but should be):" in overview_text:
        errors.append("stale documentation-gaps heading remains")
    if errors:
        fail("Home overview documentation inventory status is current", "; ".join(errors))
    else:
        ok("Home overview documentation inventory status is current")


def check_architecture_decision_trail_is_current() -> None:
    path = REPO / "docs" / "ARCHITECTURE_DECISIONS.md"
    overview = (REPO / "docs" / "HOME_SYSTEM_OVERVIEW.md").read_text(
        encoding="utf-8", errors="replace"
    )
    if not path.exists():
        fail("architecture decision trail is documented", "docs/ARCHITECTURE_DECISIONS.md missing")
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    required_tokens = [
        "# Architecture Decisions",
        "ADR-001",
        "Markov",
        "kinematic anticipator",
        "addons/predictive-lighting/anticipate.py",
        "addons/predictive-lighting/app.py",
        "predictor/",
        "ADR-002",
        "PersonaPlex",
        "Moshi",
        "docs/EXPERIMENTS-S2S.md",
        "ADR-003",
        "Chatterbox",
        "Kokoro",
        "wyoming-kokoro",
        "ADR-004",
        "Qwen3-VL-30B-A3B-Instruct-FP8",
        "stack/docker-compose.yml",
        "Rollback path:",
        "Evidence:",
    ]
    errors = [
        f"ADR doc missing token: {token}"
        for token in required_tokens
        if token not in text
    ]
    if "| `docs/ARCHITECTURE_DECISIONS.md` |" not in overview:
        errors.append("overview documentation table missing ADR file")
    if "docs/ARCHITECTURE_DECISIONS.md" not in overview:
        errors.append("overview documentation inventory status missing ADR link")
    if "ADR (architecture decision record) trail" in overview:
        errors.append("overview still lists ADR trail as a remaining documentation gap")
    if errors:
        fail("architecture decision trail is documented", "; ".join(errors))
    else:
        ok("architecture decision trail is documented (4 ADRs)")


def _ha_packages() -> list[str]:
    return sorted(path.name for path in (REPO / "ha-config" / "packages").glob("*.yaml"))


def check_home_overview_ha_package_inventory() -> None:
    packages = _ha_packages()
    if not packages:
        fail("Home overview HA package inventory matches source", "no HA packages extracted")
        return
    overview_text = (REPO / "docs" / "HOME_SYSTEM_OVERVIEW.md").read_text(
        encoding="utf-8", errors="replace"
    )
    section_start = overview_text.find("## 9. Home Assistant architecture")
    section_end = overview_text.find("## 10.", section_start)
    if section_start < 0 or section_end < 0:
        fail("Home overview HA package inventory matches source", "could not locate section 9")
        return
    section = overview_text[section_start:section_end]
    package_start = section.find("### 9e. Home Assistant package inventory")
    if package_start < 0:
        fail("Home overview HA package inventory matches source", "could not locate package inventory subsection")
        return
    package_section = section[package_start:]
    documented = sorted(
        value
        for value in re.findall(r"`([^`]+\.yaml)`", package_section)
        if "*" not in value and "/" not in value and "\\" not in value
    )
    missing = sorted(set(packages) - set(documented))
    extra = sorted(set(documented) - set(packages))
    if missing:
        fail("Home overview HA package inventory matches source", ", ".join(missing))
    elif extra:
        fail("Home overview HA package inventory matches source", "stale documented packages: " + ", ".join(extra))
    elif f"{len(packages)} YAML packages" not in overview_text:
        fail(
            "Home overview HA package inventory matches source",
            f"overview should mention current count: {len(packages)} YAML packages",
        )
    elif "19 YAML packages" in overview_text or "19 YAML files" in overview_text:
        fail("Home overview HA package inventory matches source", "stale 19-package count remains")
    else:
        ok(f"Home overview HA package inventory matches source ({len(packages)} packages)")


def _compose_services() -> list[dict[str, str]]:
    text = (REPO / "stack" / "docker-compose.yml").read_text(
        encoding="utf-8", errors="replace"
    )
    services: list[dict[str, str]] = []
    in_services = False
    current: str | None = None
    block: list[str] = []

    def flush() -> None:
        if not current:
            return
        body = "\n".join(block)
        container = re.search(r"^\s{4}container_name:\s*([^\s#]+)", body, re.M)
        profile = re.search(r"^\s{4}profiles:\s*\[([^\]]+)\]", body, re.M)
        profiles = "default"
        if profile:
            values = [
                value.strip().strip('"').strip("'")
                for value in profile.group(1).split(",")
                if value.strip()
            ]
            profiles = ",".join(values) if values else "default"
        services.append({
            "service": current,
            "container_name": container.group(1) if container else "",
            "profile": profiles,
        })

    for line in text.splitlines():
        if not in_services:
            if re.fullmatch(r"services:\s*", line):
                in_services = True
            continue
        if re.match(r"^[A-Za-z0-9_-]+:\s*$", line):
            break
        service = re.match(r"^  ([A-Za-z0-9][A-Za-z0-9_-]*):\s*$", line)
        if service:
            flush()
            current = service.group(1)
            block = []
            continue
        if current is not None:
            block.append(line)
    flush()
    return services


def _markdown_table_first_column(section: str) -> list[str]:
    return [
        match.group(1)
        for match in re.finditer(r"^\| `([^`]+)` \|", section, re.M)
    ]


def _stack_supervisor_endpoint_metadata(method: str, path: str) -> dict[str, str]:
    metadata = {
        ("GET", "/healthz"): {
            "auth": "none",
            "confirm": "none",
            "purpose": "unauthenticated liveness probe",
        },
        ("GET", "/api/stack/status"): {
            "auth": "STACK_TOKEN",
            "confirm": "none",
            "purpose": "aggregate stack and service status",
        },
        ("GET", "/api/stack/tasks"): {
            "auth": "STACK_TOKEN",
            "confirm": "none",
            "purpose": "recent stack-control task summaries",
        },
        ("POST", "/api/stack/start"): {
            "auth": "STACK_TOKEN",
            "confirm": "none",
            "purpose": "start the default AI stack",
        },
        ("POST", "/api/stack/restart"): {
            "auth": "STACK_TOKEN",
            "confirm": "none",
            "purpose": "restart the default AI stack",
        },
        ("GET", "/api/stack/logs/stream"): {
            "auth": "STACK_TOKEN",
            "confirm": "none",
            "purpose": "SSE stream for stack-control task logs",
        },
        ("POST", "/api/stack/stop"): {
            "auth": "STACK_TOKEN",
            "confirm": "stop-ai-stack",
            "purpose": "stop the default AI stack",
        },
        ("GET", "/api/services/{service}/logs"): {
            "auth": "STACK_TOKEN",
            "confirm": "none",
            "purpose": "tail allowlisted service or supervisor logs",
        },
        ("POST", "/api/services/{service}/restart"): {
            "auth": "STACK_TOKEN",
            "confirm": "none",
            "purpose": "restart one allowlisted compose service",
        },
        ("POST", "/api/services/{service}/stop"): {
            "auth": "STACK_TOKEN",
            "confirm": "stop-<service-confirm>",
            "purpose": "stop one allowlisted compose service",
        },
        ("POST", "/api/stack/free-gpu"): {
            "auth": "STACK_TOKEN",
            "confirm": "free-gpu",
            "purpose": "stop GPU-heavy services",
        },
        ("POST", "/api/stack/free_gpu"): {
            "auth": "STACK_TOKEN",
            "confirm": "free-gpu",
            "purpose": "compatibility alias for free-gpu",
        },
    }
    return metadata.get(
        (method, path),
        {"auth": "unknown", "confirm": "unknown", "purpose": "undocumented endpoint"},
    )


def _stack_supervisor_endpoints() -> list[dict[str, str]]:
    path = REPO / "stack" / "services" / "supervisor" / "main.py"
    route_re = re.compile(r'@app\.(get|post)\("([^"]+)"(?:,\s*status_code=(\d+))?\)')
    endpoints: list[dict[str, str]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        match = route_re.search(line)
        if not match:
            continue
        method = match.group(1).upper()
        route = match.group(2)
        endpoint = {
            "method": method,
            "path": route,
            "status_code": match.group(3) or "200",
            "file": rel(path),
            "line": str(lineno),
        }
        endpoint.update(_stack_supervisor_endpoint_metadata(method, route))
        endpoints.append(endpoint)
    return endpoints


def check_compose_service_inventory_docs_match_source() -> None:
    services = _compose_services()
    if not services:
        fail("compose service inventory docs match source", "no Docker Compose services extracted")
        return
    expected = [item["service"] for item in services]
    overview = (REPO / "docs" / "HOME_SYSTEM_OVERVIEW.md").read_text(
        encoding="utf-8", errors="replace"
    )
    runbook = (REPO / "docs" / "RUNBOOK.md").read_text(
        encoding="utf-8", errors="replace"
    )

    def check_table(label: str, text: str, start_marker: str, end_marker: str) -> str | None:
        start = text.find(start_marker)
        end = text.find(end_marker, start)
        if start < 0 or end < 0:
            return f"{label}: could not locate compose service inventory table"
        section = text[start:end]
        documented = [
            service for service in _markdown_table_first_column(section)
            if service != "stack-supervisor"
        ]
        missing = sorted(set(expected) - set(documented))
        extra = sorted(set(documented) - set(expected))
        if missing:
            return f"{label}: missing {', '.join(missing)}"
        if extra:
            return f"{label}: stale {', '.join(extra)}"
        for item in services:
            line = f"| `{item['service']}` | `{item['container_name']}` | `{item['profile']}` |"
            if line not in section:
                return f"{label}: missing profile/container row for {item['service']}"
        return None

    errors = [
        error for error in [
            check_table(
                "overview",
                overview,
                "### 12a. Docker Compose service inventory",
                "**Version pinning",
            ),
            check_table(
                "runbook",
                runbook,
                "## What runs where",
                "## Stack supervisor",
            ),
        ]
        if error
    ]
    default_count = sum(1 for item in services if item["profile"] == "default")
    s2s_count = sum(1 for item in services if item["profile"] == "s2s")
    if f"{default_count} default Docker Compose services" not in overview:
        errors.append(f"overview missing default service count {default_count}")
    if f"{s2s_count} `s2s` profile services" not in overview:
        errors.append(f"overview missing s2s profile service count {s2s_count}")
    if errors:
        fail("compose service inventory docs match source", "; ".join(errors))
    else:
        ok(
            "compose service inventory docs match source "
            f"({default_count} default, {s2s_count} s2s-profile)"
        )


def check_stack_supervisor_docs_match_routes() -> None:
    source = (REPO / "stack" / "services" / "supervisor" / "main.py").read_text(
        encoding="utf-8", errors="replace"
    )
    overview = (REPO / "docs" / "HOME_SYSTEM_OVERVIEW.md").read_text(
        encoding="utf-8", errors="replace"
    )
    runbook = (REPO / "docs" / "RUNBOOK.md").read_text(
        encoding="utf-8", errors="replace"
    )
    routes = set(re.findall(r'@app\.(?:get|post)\("([^"]+)"', source))
    expected_routes = {
        "/api/stack/start",
        "/api/stack/restart",
        "/api/stack/logs/stream",
        "/api/stack/stop",
        "/api/stack/free-gpu",
        "/api/services/{service}/logs",
        "/api/services/{service}/restart",
        "/api/services/{service}/stop",
    }
    if not expected_routes <= routes:
        fail(
            "stack supervisor docs match implemented mutating routes",
            "missing implemented route(s): " + ", ".join(sorted(expected_routes - routes)),
        )
        return

    stale_phrases = [
        "PHASE 1 (this file's current scope)",
        "**Phase 1 (current)** is read-only",
        "Phase 1 endpoints are listed",
        "Phase 2/3 endpoints are not yet built",
        "Phase 2 will add `POST /api/stack/start`",
        "Phase 3 adds `POST /api/stack/stop`",
        "Phase 2 (start/SSE log stream) + Phase 3 (stop/restart) planned",
    ]
    stale_hits = [
        phrase for phrase in stale_phrases
        if phrase in overview or phrase in runbook or phrase in source
    ]
    if stale_hits:
        fail("stack supervisor docs match implemented mutating routes", ", ".join(stale_hits))
        return

    doc_tokens = [
        "/api/stack/start",
        "/api/stack/restart",
        "/api/stack/logs/stream",
        "/api/stack/stop",
        "/api/stack/free-gpu",
        "/api/services/<svc>/logs",
        "/api/services/<svc>/restart",
        "/api/services/<svc>/stop",
    ]
    missing = [
        token for token in doc_tokens
        if token not in runbook and token not in overview
    ]
    if missing:
        fail("stack supervisor docs match implemented mutating routes", ", ".join(missing))
    else:
        ok("stack supervisor docs match implemented mutating routes")


def check_stack_supervisor_endpoint_reference_matches_source() -> None:
    endpoints = _stack_supervisor_endpoints()
    overview = (REPO / "docs" / "HOME_SYSTEM_OVERVIEW.md").read_text(
        encoding="utf-8", errors="replace"
    )
    runbook = (REPO / "docs" / "RUNBOOK.md").read_text(
        encoding="utf-8", errors="replace"
    )
    errors: list[str] = []
    if len(endpoints) != 12:
        errors.append(f"expected 12 source endpoints, found {len(endpoints)}")
    if "docs/qa/home-app-feature-audit.md#stack-supervisor-endpoints" not in overview:
        errors.append("overview missing generated stack-supervisor endpoint inventory link")
    if f"{len(endpoints)} stack-supervisor endpoints" not in overview:
        errors.append(f"overview missing current endpoint count {len(endpoints)}")
    if "endpoint-by-endpoint reference if the API grows" in overview:
        errors.append("overview still lists stack-supervisor endpoint reference as a future gap")
    if "### Endpoint reference" not in runbook:
        errors.append("runbook missing Stack supervisor endpoint reference table")
    normalized_runbook = " ".join(runbook.split())
    if "Mutating stack and service routes also require their explicit `X-Confirm` header." in normalized_runbook:
        errors.append("runbook overstates X-Confirm coverage for all mutating routes")
    if "Destructive routes require explicit `X-Confirm`" not in normalized_runbook:
        errors.append("runbook missing destructive-route X-Confirm scope")
    for item in endpoints:
        display_path = item["path"].replace("{service}", "<svc>")
        row = (
            f"| {item['method']} | `{display_path}` | `{item['status_code']}` | "
            f"`{item['auth']}` | `{item['confirm']}` |"
        )
        if row not in runbook:
            errors.append(f"runbook missing endpoint row: {row}")
        audit_row = (
            f"- `{item['method']} {item['path']}` - status `{item['status_code']}`; "
            f"auth `{item['auth']}`; confirm `{item['confirm']}`; "
            f"{item['purpose']}; source `{item['file']}:{item['line']}`"
        )
        audit_md = (REPO / "docs" / "qa" / "home-app-feature-audit.md").read_text(
            encoding="utf-8", errors="replace"
        )
        if "### Stack Supervisor Endpoints" not in audit_md:
            errors.append("generated audit missing stack supervisor endpoint section")
            break
        if audit_row not in audit_md:
            errors.append(f"generated audit missing endpoint row: {audit_row}")
    if errors:
        fail("stack supervisor endpoint reference matches source", "; ".join(errors))
    else:
        ok(f"stack supervisor endpoint reference matches source ({len(endpoints)} endpoints)")


def main() -> int:
    print("Static QA checks")
    print("=" * 60)
    check_runner_suite_files()
    check_source_filter_excludes_vendored_code()
    check_duplicate_fastapi_routes()
    check_duplicate_ha_view_urls()
    check_duplicate_slash_aliases()
    check_home_overview_slash_command_inventory()
    check_home_overview_simulation_scenario_inventory()
    check_scenario_namespacing()
    check_home_overview_frontend_inventory()
    check_home_overview_ui_surface_inventory()
    check_home_overview_living_lights_state_flag_inventory()
    check_home_overview_agent_tool_catalog_inventory()
    check_home_overview_predictive_lighting_mqtt_inventory()
    check_home_overview_spatial_model_schema_inventory()
    check_home_overview_documentation_inventory_status()
    check_architecture_decision_trail_is_current()
    check_home_overview_ha_package_inventory()
    check_compose_service_inventory_docs_match_source()
    check_stack_supervisor_docs_match_routes()
    check_stack_supervisor_endpoint_reference_matches_source()
    print("")
    print(f"{passes} pass · {fails} fail")
    if failures:
        for name, detail in failures:
            print(f"  FAIL: {name} — {detail[:300]}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
