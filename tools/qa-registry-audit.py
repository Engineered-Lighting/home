#!/usr/bin/env python3
"""Generate the canonical QA registry audit for the Home app.

This script reconciles the existing QA sources without requiring PyYAML or
browser automation. It is intentionally static: the output tells us what is
registered, what is discovered in source, and what still needs scenario/test
coverage. The generated markdown is a human-readable checkpoint under
docs/qa/; the JSON is for future runners.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parent.parent
DOCS_QA = REPO / "docs" / "qa"
SCENARIO_EVIDENCE_MAP = REPO / "tools" / "scenario-evidence-map.json"
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


@dataclass
class RegistryEntry:
    feature_id: str
    story_id: str
    test_id: str
    legacy_id: str
    tier: str
    safety_level: str
    automation_status: str
    latest_result: str
    linked_bug_ids: list[str]
    name: str
    source: str


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def git(args: list[str]) -> str:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        ).stdout.strip()
    except Exception as exc:
        return f"(git unavailable: {exc})"


def is_repo_source(path: Path) -> bool:
    """True for first-party files; false for virtualenv/vendor artifacts."""
    parts = set(path.relative_to(REPO).parts)
    return not (parts & SKIP_PATH_PARTS)


SCENARIO_STATUS_TO_REGISTRY = {
    "simulation_green": ("simulation_covered", "simulation_green"),
    "local_contract_green": ("local_contract_covered", "local_green_live_pending"),
    "local_partial_green": ("local_partial_covered", "local_partial_live_pending"),
    "live_required": ("live_required", "not_run_live_required"),
}


def expand_doc_range(start: str, end: str) -> list[str]:
    m1 = re.fullmatch(r"DOC-S(\d+)", start)
    m2 = re.fullmatch(r"DOC-S(\d+)", end)
    if not m1 or not m2:
        raise ValueError(f"bad range endpoints: {start}, {end}")
    a = int(m1.group(1))
    b = int(m2.group(1))
    if b < a:
        raise ValueError(f"range end before start: {start}, {end}")
    return [f"DOC-S{i}" for i in range(a, b + 1)]


def expand_scenario_evidence_rules(data: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    expanded: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for idx, rule in enumerate(data.get("rules", []), 1):
        ids: list[str] = []
        try:
            if "range" in rule:
                ids.extend(expand_doc_range(rule["range"][0], rule["range"][1]))
            ids.extend(rule.get("ids", []))
        except Exception as exc:
            errors.append(f"rule {idx}: {exc}")
            continue
        for sid in ids:
            if sid in expanded:
                errors.append(f"{sid} mapped more than once")
            expanded[sid] = rule
    return expanded, errors


def parse_doc_scenarios(evidence_by_id: dict[str, dict[str, Any]] | None = None) -> list[RegistryEntry]:
    text = read(REPO / "docs" / "SCENARIO-TESTS.md")
    out: list[RegistryEntry] = []
    for match in re.finditer(r"^###\s+(S\d+)\s+[--\u2014]\s+(.+)$", text, re.M):
        legacy = match.group(1)
        name = match.group(2).strip()
        test_id = f"DOC-{legacy}"
        evidence = (evidence_by_id or {}).get(test_id, {})
        status = evidence.get("status")
        automation_status, latest_result = SCENARIO_STATUS_TO_REGISTRY.get(
            status, ("documented", "unknown")
        )
        out.append(RegistryEntry(
            feature_id=f"doc.{legacy.lower()}",
            story_id=f"story.{test_id.lower()}",
            test_id=test_id,
            legacy_id=legacy,
            tier="scenario_doc",
            safety_level="mixed",
            automation_status=automation_status,
            latest_result=latest_result,
            linked_bug_ids=[],
            name=name,
            source="docs/SCENARIO-TESTS.md",
        ))
    return out


def parse_qa_features() -> dict[str, list[RegistryEntry]]:
    text = read(REPO / "tools" / "qa-features.yaml")
    current = ""
    found: dict[str, list[RegistryEntry]] = {"agent": [], "screenshots": []}
    pending: dict[str, str] | None = None
    for line in text.splitlines():
        top = re.match(r"^([a-zA-Z_][\w-]*):\s*$", line)
        if top:
            current = top.group(1)
            pending = None
            continue
        item = re.match(r"^\s*-\s+id:\s*([A-Za-z0-9_.+-]+)\s*$", line)
        if item and current in {"agent_scenarios", "screenshots"}:
            pending = {"id": item.group(1), "name": ""}
            continue
        name = re.match(r'^\s+name:\s*"?([^"]+)"?\s*$', line)
        if pending is not None and name:
            pending["name"] = name.group(1).strip()
            legacy = pending["id"]
            if current == "agent_scenarios":
                test_id = legacy
                found["agent"].append(RegistryEntry(
                    feature_id=f"agent.{legacy.lower()}",
                    story_id=f"story.{legacy.lower()}",
                    test_id=test_id,
                    legacy_id=legacy,
                    tier="live_agent",
                    safety_level="write_gated" if legacy in {"U3", "U15"} else "read_only",
                    automation_status="configured",
                    latest_result="not_run",
                    linked_bug_ids=[],
                    name=pending["name"],
                    source="tools/qa-features.yaml",
                ))
            else:
                test_id = f"SHOT-{legacy}"
                found["screenshots"].append(RegistryEntry(
                    feature_id=f"screenshot.{legacy.lower()}",
                    story_id=f"story.{test_id.lower()}",
                    test_id=test_id,
                    legacy_id=legacy,
                    tier="browser_screenshot",
                    safety_level="read_only",
                    automation_status="configured",
                    latest_result="not_run",
                    linked_bug_ids=[],
                    name=pending["name"],
                    source="tools/qa-features.yaml",
                ))
            pending = None
    return found


def parse_probe_entries() -> list[RegistryEntry]:
    path = REPO / "tools" / "feature-validation-probes.json"
    try:
        data = json.loads(read(path))
    except Exception:
        return []
    out: list[RegistryEntry] = []
    for item in data.get("features", []):
        legacy = str(item.get("id", "unknown"))
        tiers = sorted(k for k in item.keys() if k.startswith("tier"))
        out.append(RegistryEntry(
            feature_id=f"probe.{legacy.lower()}",
            story_id=f"story.probe.{legacy.lower()}",
            test_id=f"PROBE-{legacy}",
            legacy_id=legacy,
            tier=",".join(tiers) if tiers else "probe",
            safety_level="read_only",
            automation_status="configured",
            latest_result="not_run",
            linked_bug_ids=[],
            name=str(item.get("name", legacy)),
            source="tools/feature-validation-probes.json",
        ))
    return out


def parse_suite_entries() -> list[RegistryEntry]:
    path = REPO / "tools" / "run-test-coverage.py"
    spec = importlib.util.spec_from_file_location("run_test_coverage_registry", path)
    if spec is None or spec.loader is None:
        return []
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    out: list[RegistryEntry] = []
    for suite in getattr(mod, "SUITES", []):
        sid = getattr(suite, "id", "")
        out.append(RegistryEntry(
            feature_id=f"suite.{sid}",
            story_id=f"story.suite.{sid}",
            test_id=f"SUITE-{sid}",
            legacy_id=sid,
            tier="unit_contract",
            safety_level="read_only",
            automation_status="automated",
            latest_result="quick_green_pending_current_run",
            linked_bug_ids=[],
            name=getattr(suite, "description", "") or sid,
            source="tools/run-test-coverage.py",
        ))
    return out


def load_scenario_evidence(doc_ids: set[str]) -> dict[str, Any]:
    try:
        data = json.loads(read(SCENARIO_EVIDENCE_MAP))
    except Exception as exc:
        return {
            "by_id": {},
            "counts": {},
            "warnings": [f"scenario evidence map could not be loaded: {exc}"],
            "live_required_ids": [],
            "local_evidence_ids": [],
        }
    by_id, errors = expand_scenario_evidence_rules(data)
    warnings = list(errors)
    statuses = set(data.get("status_definitions", {}).keys())
    mapped_ids = set(by_id.keys())
    missing = sorted(doc_ids - mapped_ids)
    extra = sorted(mapped_ids - doc_ids)
    if missing:
        warnings.append("scenario evidence map missing: " + ", ".join(missing))
    if extra:
        warnings.append("scenario evidence map has unknown ids: " + ", ".join(extra))
    for sid, rule in sorted(by_id.items()):
        status = rule.get("status")
        if status not in statuses:
            warnings.append(f"{sid} uses unknown scenario evidence status {status!r}")
        if not str(rule.get("remaining", "")).strip():
            warnings.append(f"{sid} has no remaining-work note")
        if status != "live_required" and not rule.get("evidence"):
            warnings.append(f"{sid} is {status} but cites no local evidence")
        for rel in rule.get("evidence", []):
            if not (REPO / rel).exists():
                warnings.append(f"{sid} cites missing local evidence file: {rel}")

    counts = Counter(str(rule.get("status", "unknown")) for sid, rule in by_id.items() if sid in doc_ids)
    return {
        "by_id": by_id,
        "counts": dict(sorted(counts.items())),
        "warnings": warnings,
        "live_required_ids": sorted(
            sid for sid, rule in by_id.items()
            if sid in doc_ids and rule.get("status") == "live_required"
        ),
        "local_evidence_ids": sorted(
            sid for sid, rule in by_id.items()
            if sid in doc_ids and rule.get("status") != "live_required"
        ),
    }


def extract_slash_commands() -> list[str]:
    text = read(REPO / "app" / "src" / "home-app.jsx")
    start = text.find("const handleCommand = useCallback")
    if start < 0:
        return []
    end = text.find("\n      default:", start)
    if end < 0:
        end = text.find("\n  },", start)
    block = text[start:end]
    return sorted(set(re.findall(r'case\s+"([^"]+)":', block)))


def extract_simulation_scenarios() -> list[str]:
    text = read(REPO / "app" / "src" / "simulation-data.jsx")
    start = text.find("const scenarios =")
    end = text.find("return scenarios[id]", start)
    block = text[start:end if end > start else len(text)]
    return sorted(set(re.findall(r'\bid:\s*"([^"]+)"', block)))


def extract_frontend_scripts() -> list[str]:
    text = read(REPO / "app" / "src" / "index.html")
    static_scripts = re.findall(r'<script[^>]+src="([^"]+)"', text)
    boot_scripts = extract_boot_loader_scripts(text)
    return list(dict.fromkeys(static_scripts + boot_scripts))


def extract_boot_loader_scripts(index_html: str) -> list[str]:
    match = re.search(r"var\s+files\s*=\s*\[([\s\S]*?)\];", index_html)
    if not match:
        return []
    return re.findall(r"\[\s*['\"]([^'\"]+)['\"]\s*,\s*(?:true|false)\s*\]", match.group(1))


def extract_fastapi_routes() -> list[dict[str, str]]:
    routes: list[dict[str, str]] = []
    route_re = re.compile(r'@app\.(get|post|put|delete|patch|api_route)\("([^"]+)"')
    for path in (REPO / "stack" / "services").rglob("*.py"):
        if not is_repo_source(path):
            continue
        for lineno, line in enumerate(read(path).splitlines(), 1):
            m = route_re.search(line)
            if m:
                routes.append({
                    "file": str(path.relative_to(REPO)).replace("\\", "/"),
                    "line": str(lineno),
                    "method": m.group(1).upper(),
                    "path": m.group(2),
                })
    return routes


def stack_supervisor_endpoint_metadata(method: str, path: str) -> dict[str, str]:
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


def extract_stack_supervisor_endpoints() -> list[dict[str, str]]:
    path = REPO / "stack" / "services" / "supervisor" / "main.py"
    route_re = re.compile(r'@app\.(get|post)\("([^"]+)"(?:,\s*status_code=(\d+))?\)')
    endpoints: list[dict[str, str]] = []
    for lineno, line in enumerate(read(path).splitlines(), 1):
        match = route_re.search(line)
        if not match:
            continue
        method = match.group(1).upper()
        route = match.group(2)
        endpoint = {
            "method": method,
            "path": route,
            "status_code": match.group(3) or "200",
            "file": str(path.relative_to(REPO)).replace("\\", "/"),
            "line": str(lineno),
        }
        endpoint.update(stack_supervisor_endpoint_metadata(method, route))
        endpoints.append(endpoint)
    return endpoints


def extract_ha_views() -> list[dict[str, str]]:
    path = REPO / "ha-config" / "extended_openai_conversation" / "__init__.py"
    out: list[dict[str, str]] = []
    for lineno, line in enumerate(read(path).splitlines(), 1):
        m = re.search(r'url\s*=\s*"([^"]+)"', line)
        if m:
            out.append({"file": str(path.relative_to(REPO)).replace("\\", "/"),
                        "line": str(lineno), "url": m.group(1)})
    return out


def extract_ha_packages() -> list[str]:
    return sorted(path.name for path in (REPO / "ha-config" / "packages").glob("*.yaml"))


def extract_compose_services() -> list[dict[str, str]]:
    text = read(REPO / "stack" / "docker-compose.yml")
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


def extract_ui_surfaces() -> list[str]:
    surfaces = []
    for path in (REPO / "app" / "src").glob("home-*.jsx"):
        text = read(path)
        if re.search(r"Drawer|Overlay|Modal|Atlas|Panel|Lightbox", text):
            surfaces.append(path.name)
    return sorted(surfaces)


def extract_living_lights_classifier_states() -> list[str]:
    text = read(REPO / "tools" / "build-living-lights-yaml.py")
    match = re.search(r"_CLASSIFIER_SENSOR\s*=\s*r\"\"\"([\s\S]*?)\"\"\"", text)
    if not match:
        return []
    state_section = match.group(1).split("attributes:", 1)[0]
    states: list[str] = []
    for value in re.findall(r"{%\s*(?:if|elif|else)[^%]*%}\s*([a-z_]+)", state_section):
        if value not in states:
            states.append(value)
    return states


def extract_ha_input_booleans() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    paths = sorted((REPO / "ha-config").glob("*.yaml")) + sorted(
        (REPO / "ha-config" / "packages").glob("*.yaml")
    )
    for path in paths:
        in_block = False
        for lineno, line in enumerate(read(path).splitlines(), 1):
            if re.fullmatch(r"input_boolean:\s*", line):
                in_block = True
                continue
            if in_block and re.match(r"^[A-Za-z_][A-Za-z0-9_-]*:\s*$", line):
                in_block = False
            if not in_block:
                continue
            match = re.match(r"^  ([A-Za-z0-9_]+):\s*$", line)
            if match:
                out.append({
                    "entity": f"input_boolean.{match.group(1)}",
                    "file": str(path.relative_to(REPO)).replace("\\", "/"),
                    "line": str(lineno),
                })
    return sorted(out, key=lambda item: item["entity"])


def extract_agent_tools() -> list[dict[str, str]]:
    text = read(REPO / "ha-config" / "extended_openai_conversation" / "const.py")
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
        out: list[dict[str, str]] = []
        for index, item in enumerate(tools, 1):
            if not isinstance(item, dict):
                continue
            spec = item.get("spec", {})
            function = item.get("function", {})
            if not isinstance(spec, dict) or not isinstance(function, dict):
                continue
            name = str(spec.get("name", "")).strip()
            function_type = str(function.get("type", "")).strip()
            target = (
                function.get("name")
                or ("value_template" if "value_template" in function else None)
                or function.get("path")
                or function.get("command")
                or ""
            )
            if name:
                out.append({
                    "index": str(index),
                    "name": name,
                    "function_type": function_type,
                    "function_target": str(target).strip(),
                })
        return out
    return []


def extract_spatial_model_inventory() -> dict[str, Any]:
    path = REPO / "ha-config" / "spatial_model.json"
    try:
        data = json.loads(read(path))
    except Exception:
        return {}
    cameras = data.get("cameras") or {}
    lights = data.get("lights") or {}
    light_camera = data.get("light_camera") or {}
    camera_rows: list[dict[str, Any]] = []
    for name, camera in sorted(cameras.items()):
        zones = camera.get("zones") or {}
        camera_rows.append({
            "name": name,
            "detect_w": camera.get("detect_w"),
            "detect_h": camera.get("detect_h"),
            "grid_cols": camera.get("grid_cols"),
            "grid_rows": camera.get("grid_rows"),
            "zone_count": len(zones),
            "zones": sorted(zones),
        })
    optional_deployed_fields = ["camera_edges", "zone_to_room"]
    return {
        "schema": data.get("schema"),
        "source": data.get("source"),
        "target_cols": (data.get("grid") or {}).get("target_cols"),
        "top_level_keys": sorted(data.keys()),
        "camera_count": len(cameras),
        "zone_count": sum(len((camera.get("zones") or {})) for camera in cameras.values()),
        "light_camera_count": len(light_camera),
        "light_count": len(lights),
        "lights_with_footprint": sum(
            1 for rec in lights.values()
            if isinstance(rec, dict) and bool(rec.get("footprint"))
        ),
        "lights_with_footprint_polygon": sum(
            1 for rec in lights.values()
            if isinstance(rec, dict) and bool(rec.get("footprint_polygon"))
        ),
        "footprint_empty_count": sum(
            1 for rec in lights.values()
            if isinstance(rec, dict) and rec.get("footprint_empty") is True
        ),
        "optional_deployed_fields_missing": [
            key for key in optional_deployed_fields if key not in data
        ],
        "cameras": camera_rows,
    }


def _python_literal_assignments(path: Path) -> dict[str, Any]:
    text = read(path)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    out: dict[str, Any] = {}
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


def extract_predictive_lighting_mqtt_topics() -> dict[str, Any]:
    constants = _python_literal_assignments(REPO / "addons" / "predictive-lighting" / "app.py")
    if not constants:
        return {}
    top = str(constants.get("TOPIC", "frigate/events"))
    anticipated_base = str(constants.get("ANTICIPATED_BASE", "predictive-lighting/anticipated"))
    availability = str(constants.get("AVAIL_TOPIC", "predictive-lighting/availability"))
    discovery_base = str(constants.get("DISCOVERY_BASE", "homeassistant/binary_sensor/anticipated_"))
    debug_base = str(constants.get("DEBUG_TOPIC_BASE", "predictive-lighting/debug/track"))
    heartbeat = str(constants.get("HEARTBEAT_TOPIC", "predictive-lighting/eval/heartbeat"))
    heartbeat_interval = constants.get("HEARTBEAT_INTERVAL_S", "")
    publish_topics = [
        {
            "topic": availability,
            "payload": "`online` / `offline`",
            "retain": "true",
            "qos": "0",
            "purpose": "availability + Last Will and Testament for anticipated sensors",
        },
        {
            "topic": f"{anticipated_base}/<room>",
            "payload": "`ON` / `OFF`",
            "retain": "true",
            "qos": "0",
            "purpose": "per-room anticipated occupancy state consumed by HA MQTT discovery sensors",
        },
        {
            "topic": f"{discovery_base}<room>/config",
            "payload": "HA MQTT discovery JSON",
            "retain": "true",
            "qos": "0",
            "purpose": "binary_sensor.anticipated_<room> discovery config",
        },
        {
            "topic": heartbeat,
            "payload": "JSON: ts, events_seen, transitions_logged, anticipated_publishes",
            "retain": "true",
            "qos": "0",
            "purpose": f"eval watchdog heartbeat every {heartbeat_interval}s",
        },
        {
            "topic": f"{debug_base}/<track_id_short>",
            "payload": "debug JSON",
            "retain": "false",
            "qos": "0",
            "purpose": "per-event diagnostic stream for offline anticipator evaluation",
        },
    ]
    subscribe_topics = [
        {
            "topic": top,
            "payload": "Frigate event JSON",
            "qos": "0",
            "purpose": "primary person/zone event stream",
        },
        {
            "topic": f"{debug_base}/+",
            "payload": "debug JSON",
            "qos": "0",
            "purpose": "self-subscription for eval_logger capture",
        },
        {
            "topic": f"{anticipated_base}/+",
            "payload": "`ON` / `OFF`",
            "qos": "0",
            "purpose": "self-subscription for eval_logger capture",
        },
    ]
    return {
        "constants": {
            key: constants.get(key)
            for key in [
                "TOPIC",
                "AVAIL_TOPIC",
                "ANTICIPATED_BASE",
                "DISCOVERY_BASE",
                "DEBUG_TOPIC_BASE",
                "HEARTBEAT_TOPIC",
                "HEARTBEAT_INTERVAL_S",
            ]
        },
        "publish_topics": publish_topics,
        "subscribe_topics": subscribe_topics,
    }


def build_audit() -> dict[str, Any]:
    qa = parse_qa_features()
    raw_doc_entries = parse_doc_scenarios()
    scenario_evidence = load_scenario_evidence({e.test_id for e in raw_doc_entries})
    doc_entries = parse_doc_scenarios(scenario_evidence["by_id"])
    entries: list[RegistryEntry] = []
    entries.extend(doc_entries)
    entries.extend(qa["agent"])
    entries.extend(qa["screenshots"])
    entries.extend(parse_probe_entries())
    entries.extend(parse_suite_entries())

    id_counts = Counter(e.test_id for e in entries)
    collisions = sorted(k for k, n in id_counts.items() if n > 1)

    inventory = {
        "slash_commands": extract_slash_commands(),
        "simulation_scenarios": extract_simulation_scenarios(),
        "frontend_scripts": extract_frontend_scripts(),
        "fastapi_routes": extract_fastapi_routes(),
        "ha_views": extract_ha_views(),
        "ha_packages": extract_ha_packages(),
        "compose_services": extract_compose_services(),
        "ui_surfaces": extract_ui_surfaces(),
        "living_lights_classifier_states": extract_living_lights_classifier_states(),
        "ha_input_booleans": extract_ha_input_booleans(),
        "agent_tools": extract_agent_tools(),
        "spatial_model": extract_spatial_model_inventory(),
        "predictive_lighting_mqtt": extract_predictive_lighting_mqtt_topics(),
        "stack_supervisor_endpoints": extract_stack_supervisor_endpoints(),
    }
    warnings = []
    if collisions:
        warnings.append(f"duplicate canonical test IDs: {', '.join(collisions)}")
    if len(doc_entries) < 90:
        warnings.append("docs/SCENARIO-TESTS.md parsed fewer scenarios than expected")
    if not inventory["slash_commands"]:
        warnings.append("no slash commands discovered")
    if not inventory["simulation_scenarios"]:
        warnings.append("no simulation scenarios discovered")
    if not inventory["ha_packages"]:
        warnings.append("no Home Assistant packages discovered")
    if not inventory["compose_services"]:
        warnings.append("no Docker Compose services discovered")
    if not inventory["living_lights_classifier_states"]:
        warnings.append("no Living Lights classifier states discovered")
    if not inventory["ha_input_booleans"]:
        warnings.append("no Home Assistant input_boolean flags discovered")
    if not inventory["agent_tools"]:
        warnings.append("no Extended OpenAI agent tools discovered")
    if not inventory["spatial_model"]:
        warnings.append("no spatial_model.json inventory discovered")
    if not inventory["predictive_lighting_mqtt"]:
        warnings.append("no predictive-lighting MQTT topic inventory discovered")
    if not inventory["stack_supervisor_endpoints"]:
        warnings.append("no stack supervisor endpoint inventory discovered")
    warnings.extend(scenario_evidence["warnings"])

    return {
        "schema_version": 1,
        "generated_by": "tools/qa-registry-audit.py",
        "baseline": {
            "git_status_short": git(["status", "--short"]),
            "git_diff_stat": git(["diff", "--stat"]),
        },
        "registry_counts": {
            "doc_scenarios": len(doc_entries),
            "agent_scenarios": len(qa["agent"]),
            "screenshot_recipes": len(qa["screenshots"]),
            "feature_probes": len(parse_probe_entries()),
            "test_suites": len(parse_suite_entries()),
            "total_entries": len(entries),
        },
        "scenario_evidence": scenario_evidence,
        "namespace_rules": {
            "DOC-S*": "docs/SCENARIO-TESTS.md scenarios",
            "SHOT-S*": "tools/qa-features.yaml screenshot recipes",
            "U*": "live agent scenarios",
            "PROBE-*": "feature-validation-probes.json feature probes",
            "SUITE-*": "run-test-coverage.py suites",
        },
        "entries": [asdict(e) for e in entries],
        "inventory": inventory,
        "warnings": warnings,
    }


def natural_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def append_entry_section(lines: list[str], title: str, rows: list[dict[str, Any]]) -> None:
    lines.extend(["", f"### {title}", ""])
    if not rows:
        lines.append("_none_")
        return
    for item in sorted(rows, key=lambda row: natural_key(str(row.get("test_id", "")))):
        lines.append(
            f"- `{item['test_id']}` - {item['name']} "
            f"(`{item['tier']}`, `{item['safety_level']}`, `{item['latest_result']}`; "
            f"source: `{item['source']}`)"
        )


def format_scenario_evidence_row(item: dict[str, Any], evidence: dict[str, Any]) -> str:
    evidence_files = evidence.get("evidence", [])
    evidence_text = ", ".join(f"`{rel}`" for rel in evidence_files) if evidence_files else "_none_"
    remaining = str(evidence.get("remaining", "")).strip() or "_not recorded_"
    return (
        f"- `{item['test_id']}` - {item['name']} "
        f"(`{evidence.get('status', 'unknown')}`; evidence: {evidence_text}; "
        f"remaining: {remaining})"
    )


def write_markdown(audit: dict[str, Any]) -> str:
    counts = audit["registry_counts"]
    inv = audit["inventory"]
    spatial = inv.get("spatial_model") or {}
    mqtt_topics = inv.get("predictive_lighting_mqtt") or {}
    supervisor_endpoints = inv.get("stack_supervisor_endpoints") or []
    entries = audit["entries"]
    scenario_evidence = audit.get("scenario_evidence", {})
    scenario_counts = scenario_evidence.get("counts", {})
    lines = [
        "# Home App Feature QA Audit",
        "",
        "Generated by `tools/qa-registry-audit.py`.",
        "",
        "## Registry Summary",
        "",
        f"- Documented scenarios: {counts['doc_scenarios']}",
        f"- Live agent scenarios: {counts['agent_scenarios']}",
        f"- Screenshot recipes: {counts['screenshot_recipes']}",
        f"- Feature probes: {counts['feature_probes']}",
        f"- Quick/unit suites: {counts['test_suites']}",
        f"- Total canonical entries: {counts['total_entries']}",
        "",
        "## Scenario Evidence Summary",
        "",
        f"- Simulation-covered documented scenarios: {scenario_counts.get('simulation_green', 0)}",
        f"- Local-contract-covered documented scenarios: {scenario_counts.get('local_contract_green', 0)}",
        f"- Locally partial documented scenarios: {scenario_counts.get('local_partial_green', 0)}",
        f"- Live-required documented scenarios with no sufficient local substitute: {scenario_counts.get('live_required', 0)}",
        "",
        "Status semantics:",
        "",
        "- `simulation_green`: covered by local Simulation Mode tests without live HA/Frigate/device effects.",
        "- `local_contract_green`: core behavior is covered by local unit/mock/static tests; live end-to-end execution is still not claimed.",
        "- `local_partial_green`: some important local contracts are covered, but the scenario remains materially live/manual.",
        "- `live_required`: requires a live or non-production integration run.",
        "",
        "Live-required scenario IDs:",
        "",
        ", ".join(f"`{sid}`" for sid in scenario_evidence.get("live_required_ids", [])) or "_none_",
        "",
        "## Documented Scenario Evidence",
        "",
    ]
    scenario_rows = [
        item for item in entries
        if item["tier"] == "scenario_doc"
    ]
    for item in sorted(scenario_rows, key=lambda row: natural_key(str(row.get("test_id", "")))):
        lines.append(format_scenario_evidence_row(
            item,
            scenario_evidence.get("by_id", {}).get(item["test_id"], {}),
        ))
    lines.extend([
        "",
        "## Namespace Rules",
        "",
    ])
    for key, value in audit["namespace_rules"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend([
        "",
        "## Canonical QA Entries",
        "",
        "The generated JSON contains the full registry; this markdown lists the non-doc executable/manual entry groups so release reviewers can scan the configured QA surface without opening JSON.",
    ])
    append_entry_section(lines, "Live Agent Scenarios", [
        item for item in entries if item["tier"] == "live_agent"
    ])
    append_entry_section(lines, "Screenshot Recipes", [
        item for item in entries if item["tier"] == "browser_screenshot"
    ])
    append_entry_section(lines, "Feature Probes", [
        item for item in entries if item["test_id"].startswith("PROBE-")
    ])
    append_entry_section(lines, "Quick/Unit Suites", [
        item for item in entries if item["tier"] == "unit_contract"
    ])
    lines.extend([
        "",
        "## Static Inventory",
        "",
        f"- Slash commands discovered: {len(inv['slash_commands'])}",
        f"- Simulation scenarios discovered: {len(inv['simulation_scenarios'])}",
        f"- Frontend scripts discovered: {len(inv['frontend_scripts'])}",
        f"- FastAPI routes discovered: {len(inv['fastapi_routes'])}",
        f"- HA views discovered: {len(inv['ha_views'])}",
        f"- HA packages discovered: {len(inv['ha_packages'])}",
        f"- Docker Compose services discovered: {len(inv['compose_services'])}",
        f"- Major UI surfaces discovered: {len(inv['ui_surfaces'])}",
        f"- Living Lights classifier states discovered: {len(inv['living_lights_classifier_states'])}",
        f"- Home Assistant input_boolean flags discovered: {len(inv['ha_input_booleans'])}",
        f"- Extended OpenAI agent tools discovered: {len(inv['agent_tools'])}",
        f"- Spatial model cameras discovered: {spatial.get('camera_count', 0)}",
        f"- Spatial model lights discovered: {spatial.get('light_count', 0)}",
        f"- Predictive Lighting MQTT publish topic families discovered: {len(mqtt_topics.get('publish_topics', []))}",
        f"- Predictive Lighting MQTT subscription topic families discovered: {len(mqtt_topics.get('subscribe_topics', []))}",
        f"- Stack supervisor endpoints discovered: {len(supervisor_endpoints)}",
        "",
        "### Slash Commands",
        "",
        ", ".join(f"`/{c}`" for c in inv["slash_commands"]) or "_none_",
        "",
        "### Simulation Scenarios",
        "",
        ", ".join(f"`{c}`" for c in inv["simulation_scenarios"]) or "_none_",
        "",
        "### Frontend Scripts",
        "",
        ", ".join(f"`{c}`" for c in inv["frontend_scripts"]) or "_none_",
        "",
        "### UI Surfaces",
        "",
        ", ".join(f"`{c}`" for c in inv["ui_surfaces"]) or "_none_",
        "",
        "### Home Assistant Packages",
        "",
        ", ".join(f"`{c}`" for c in inv["ha_packages"]) or "_none_",
        "",
        "### Docker Compose Services",
        "",
    ])
    if inv["compose_services"]:
        lines.extend(
            f"- `{item['service']}` — `{item['container_name']}` (`{item['profile']}`)"
            for item in inv["compose_services"]
        )
    else:
        lines.append("_none_")
    lines.extend([
        "",
        "### Living Lights Classifier States",
        "",
        ", ".join(f"`{state}`" for state in inv["living_lights_classifier_states"]) or "_none_",
        "",
        "### Home Assistant Input Boolean Flags",
        "",
    ])
    if inv["ha_input_booleans"]:
        lines.extend(
            f"- `{item['entity']}` — `{item['file']}:{item['line']}`"
            for item in inv["ha_input_booleans"]
        )
    else:
        lines.append("_none_")
    lines.extend([
        "",
        "### Extended OpenAI Agent Tools",
        "",
    ])
    if inv["agent_tools"]:
        lines.extend(
            f"- `{item['name']}` - `{item['function_type']}` -> `{item['function_target']}`"
            for item in inv["agent_tools"]
        )
    else:
        lines.append("_none_")
    lines.extend([
        "",
        "### Spatial Model Schema",
        "",
    ])
    if spatial:
        top_keys = ", ".join(f"`{key}`" for key in spatial["top_level_keys"])
        missing_fields = ", ".join(
            f"`{key}`" for key in spatial["optional_deployed_fields_missing"]
        ) or "_none_"
        lines.extend([
            f"- Schema version: `{spatial.get('schema')}`; source: `{spatial.get('source')}`; grid target columns: `{spatial.get('target_cols')}`",
            f"- Top-level keys: {top_keys}",
            (
                f"- Current inventory: `{spatial.get('camera_count')}` cameras, "
                f"`{spatial.get('zone_count')}` Frigate zones, "
                f"`{spatial.get('light_camera_count')}` light-camera bindings, "
                f"`{spatial.get('light_count')}` lights, "
                f"`{spatial.get('lights_with_footprint_polygon')}` lights with footprint polygons, "
                f"`{spatial.get('footprint_empty_count')}` footprint-empty lights"
            ),
            f"- Optional/deployed-only fields absent locally: {missing_fields}",
        ])
        for camera in spatial["cameras"]:
            zones = ", ".join(f"`{zone}`" for zone in camera["zones"]) or "_none_"
            lines.append(
                f"- `{camera['name']}` - detect `{camera['detect_w']}x{camera['detect_h']}`, "
                f"grid `{camera['grid_cols']}x{camera['grid_rows']}`, "
                f"{camera['zone_count']} zones: {zones}"
            )
    else:
        lines.append("_none_")
    lines.extend([
        "",
        "### Predictive Lighting MQTT Topics",
        "",
    ])
    if mqtt_topics:
        lines.extend([
            "Publish topic families:",
            "",
        ])
        for item in mqtt_topics["publish_topics"]:
            lines.append(
                f"- `{item['topic']}` - payload {item['payload']}; "
                f"retain `{item['retain']}`; qos `{item['qos']}`; {item['purpose']}"
            )
        lines.extend([
            "",
            "Subscription topic families:",
            "",
        ])
        for item in mqtt_topics["subscribe_topics"]:
            lines.append(
                f"- `{item['topic']}` - payload {item['payload']}; "
                f"qos `{item['qos']}`; {item['purpose']}"
            )
    else:
        lines.append("_none_")
    lines.extend([
        "",
        "### Stack Supervisor Endpoints",
        "",
    ])
    if supervisor_endpoints:
        for item in supervisor_endpoints:
            lines.append(
                f"- `{item['method']} {item['path']}` - status `{item['status_code']}`; "
                f"auth `{item['auth']}`; confirm `{item['confirm']}`; "
                f"{item['purpose']}; source `{item['file']}:{item['line']}`"
            )
    else:
        lines.append("_none_")
    lines.extend([
        "",
        "### Home Assistant Views",
        "",
    ])
    if inv["ha_views"]:
        lines.extend(
            f"- `{item['url']}` — `{item['file']}:{item['line']}`"
            for item in inv["ha_views"]
        )
    else:
        lines.append("_none_")
    lines.extend([
        "",
        "### First-Party FastAPI Routes",
        "",
    ])
    if inv["fastapi_routes"]:
        for item in inv["fastapi_routes"]:
            lines.append(
                f"- `{item['method']} {item['path']}` — `{item['file']}:{item['line']}`"
            )
    else:
        lines.append("_none_")
    lines.extend([
        "",
        "## Warnings",
        "",
    ])
    if audit["warnings"]:
        lines.extend(f"- {w}" for w in audit["warnings"])
    else:
        lines.append("- No canonical ID collisions or extractor failures detected.")
    lines.extend([
        "",
        "## Baseline Snapshot",
        "",
        "### git status --short",
        "",
        "```text",
        audit["baseline"]["git_status_short"] or "(clean)",
        "```",
        "",
        "### git diff --stat",
        "",
        "```text",
        audit["baseline"]["git_diff_stat"] or "(no tracked diff)",
        "```",
        "",
        "## Notes",
        "",
        "- `DOC-S*` and `SHOT-S*` intentionally preserve overlapping legacy `S*` IDs without collision.",
        "- Registry warnings are non-fatal until the inventory is fully mapped to runnable tests.",
        "- High-risk live write tests remain gated out of default QA.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    DOCS_QA.mkdir(parents=True, exist_ok=True)
    audit = build_audit()
    (DOCS_QA / "home-app-feature-audit.json").write_text(
        json.dumps(audit, indent=2), encoding="utf-8"
    )
    (DOCS_QA / "home-app-feature-audit.md").write_text(
        write_markdown(audit), encoding="utf-8"
    )
    fail_count = len(audit["warnings"])
    print("QA registry audit")
    print("=" * 60)
    print(f"entries: {audit['registry_counts']['total_entries']}")
    print(f"slash commands: {len(audit['inventory']['slash_commands'])}")
    print(f"simulation scenarios: {len(audit['inventory']['simulation_scenarios'])}")
    print(f"ha packages: {len(audit['inventory']['ha_packages'])}")
    print(f"compose services: {len(audit['inventory']['compose_services'])}")
    print(f"agent tools: {len(audit['inventory']['agent_tools'])}")
    spatial = audit["inventory"].get("spatial_model") or {}
    print(
        "spatial model: "
        f"{spatial.get('camera_count', 0)} cameras, "
        f"{spatial.get('light_count', 0)} lights"
    )
    mqtt_topics = audit["inventory"].get("predictive_lighting_mqtt") or {}
    print(
        "predictive mqtt: "
        f"{len(mqtt_topics.get('publish_topics', []))} publish families, "
        f"{len(mqtt_topics.get('subscribe_topics', []))} subscription families"
    )
    print(f"stack supervisor endpoints: {len(audit['inventory']['stack_supervisor_endpoints'])}")
    print(f"warnings: {fail_count}")
    print("1 pass · 0 fail" if fail_count == 0 else f"0 pass · {fail_count} fail")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
