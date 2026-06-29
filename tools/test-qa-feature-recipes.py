#!/usr/bin/env python3
"""Static contracts for live/manual QA feature recipes.

The recipes in tools/qa-features.yaml are intentionally not executed by the
local RC gate because they can depend on live HA, Frigate, browser, or device
state. This suite keeps those deferred recipes structured, privacy-aware, and
correctly represented in the generated QA audit.
"""

from __future__ import annotations

import json
import ipaddress
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

try:
    import yaml
except Exception as exc:  # noqa: BLE001
    yaml = None
    YAML_IMPORT_ERROR = exc
else:
    YAML_IMPORT_ERROR = None


REPO = Path(__file__).resolve().parent.parent
QA_FEATURES = REPO / "tools" / "qa-features.yaml"
FEATURE_PROBES = REPO / "tools" / "feature-validation-probes.json"
AUDIT_JSON = REPO / "docs" / "qa" / "home-app-feature-audit.json"
READINESS_MD = REPO / "docs" / "qa" / "home-app-rc-readiness.md"

passes = 0
fails = 0
failures: list[tuple[str, str]] = []


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


def qa_features() -> dict:
    assert yaml is not None, f"PyYAML import failed: {YAML_IMPORT_ERROR}"
    data = yaml.safe_load(QA_FEATURES.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "qa-features.yaml must parse as a mapping"
    return data


def audit() -> dict:
    return json.loads(AUDIT_JSON.read_text(encoding="utf-8"))


def feature_probes() -> dict:
    data = json.loads(FEATURE_PROBES.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "feature-validation-probes.json must parse as a mapping"
    return data


def readiness() -> str:
    return READINESS_MD.read_text(encoding="utf-8", errors="replace")


def ids(items: list[dict]) -> list[str]:
    return [str(item.get("id", "")) for item in items]


def assert_unique(values: list[str], label: str) -> None:
    duplicates = sorted({value for value in values if values.count(value) > 1})
    assert not duplicates, f"duplicate {label}: {', '.join(duplicates)}"


def numeric_suffix(value: str) -> int:
    match = re.fullmatch(r"[A-Z]+(\d+)", value)
    assert match, f"bad id format: {value}"
    return int(match.group(1))


def is_local_or_private_http_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.hostname or ""
    if host in {"localhost", "homeassistant.local"} or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


print("\nQA feature recipe contracts\n")


def test_registry_namespace_contract() -> None:
    data = qa_features()
    registry = data.get("registry")
    assert isinstance(registry, dict), "registry block missing"
    assert registry.get("schema_version") == 1, "unexpected registry schema version"
    canonical = registry.get("canonical_ids", {})
    assert canonical == {
        "documented_scenarios": "DOC-S*",
        "screenshot_recipes": "SHOT-S*",
        "live_agent_scenarios": "U*",
        "feature_probes": "PROBE-*",
        "quick_suites": "SUITE-*",
    }, "canonical id namespaces drifted"
    required = set(registry.get("required_fields", []))
    expected = {
        "feature_id",
        "story_id",
        "test_id",
        "legacy_id",
        "tier",
        "safety_level",
        "automation_status",
        "latest_result",
        "linked_bug_ids",
    }
    assert expected <= required, "registry required_fields missing canonical audit keys"


t("registry namespace contract is explicit", test_registry_namespace_contract)


def test_live_agent_scenarios_are_complete_and_ordered() -> None:
    agents = qa_features().get("agent_scenarios", [])
    assert len(agents) == 17, f"expected 17 live agent scenarios, got {len(agents)}"
    agent_ids = ids(agents)
    assert_unique(agent_ids, "live agent ids")
    assert agent_ids == [f"U{i}" for i in range(1, 18)], "live agent ids must be U1..U17"
    for item in agents:
        assert str(item.get("name", "")).strip(), f"{item.get('id')} missing name"
        assert str(item.get("prompt", "")).strip(), f"{item.get('id')} missing prompt"
        assert isinstance(item.get("expect"), dict) and item["expect"], \
            f"{item.get('id')} missing non-empty expect block"


t("live agent scenarios are complete and ordered", test_live_agent_scenarios_are_complete_and_ordered)


def test_live_agent_write_scenarios_are_safely_gated() -> None:
    agents = {item["id"]: item for item in qa_features().get("agent_scenarios", [])}
    write_ids = sorted(
        sid for sid, item in agents.items()
        if item.get("side_effects") is True
    )
    write_ids = sorted(
        write_ids,
        key=numeric_suffix,
    )
    assert write_ids == ["U3", "U15"], f"unexpected side-effect scenarios: {write_ids}"
    for sid in write_ids:
        item = agents[sid]
        assert item.get("requires_tool") == "execute_services", f"{sid} must use execute_services"
        assert item.get("safe_entity") == "light.office", f"{sid} must stay on safe light.office"
        expect = item.get("expect", {})
        assert "execute_services" in expect.get("tool_call_must_include", []), \
            f"{sid} must assert execute_services was called"
    unlock = agents["U11"]
    assert unlock.get("side_effects") is not True, "unlock-door scenario must not be side-effecting"
    blocked = unlock.get("expect", {}).get("tool_call_must_not_include", [])
    assert "lock_unlock" in blocked and "execute_services_lock" in blocked, \
        "unlock-door scenario must forbid lock actuation tools"


t("live agent write scenarios are safely gated", test_live_agent_write_scenarios_are_safely_gated)


def test_live_agent_integration_requirements_are_visible() -> None:
    data = qa_features()
    expected_tools = set(data.get("preflight", {}).get("expected_tools", []))
    for tool in {"recap", "find_clips", "get_history", "describe_clip", "areas_in_home", "find_entity"}:
        assert tool in expected_tools, f"preflight expected_tools missing {tool}"
    agents = data.get("agent_scenarios", [])
    required_services = {item.get("requires_service") for item in agents if item.get("requires_service")}
    assert {"ha", "frigate", "vision_sidecar"} <= required_services, \
        "live agents must declare HA, Frigate, and vision-sidecar dependencies"
    external = next(item for item in agents if item["id"] == "U12")
    assert external.get("expect", {}).get("routing_log_dispatched") == "external", \
        "general-knowledge scenario should pin external routing"


t("live agent integration requirements are visible", test_live_agent_integration_requirements_are_visible)


def test_screenshot_recipes_are_complete_and_ordered() -> None:
    shots = qa_features().get("screenshots", [])
    assert len(shots) == 18, f"expected 18 screenshot recipes, got {len(shots)}"
    shot_ids = ids(shots)
    assert_unique(shot_ids, "screenshot ids")
    assert shot_ids == [f"S{i}" for i in range(1, 19)], "screenshot ids must be S1..S18"
    saves: list[str] = []
    for item in shots:
        sid = item["id"]
        assert str(item.get("name", "")).strip(), f"{sid} missing name"
        assert isinstance(item.get("drive"), list) and item["drive"], f"{sid} missing drive steps"
        assert isinstance(item.get("expected_elements"), list) and item["expected_elements"], \
            f"{sid} missing expected_elements"
        saved = [step.get("save_as") for step in item["drive"] if step.get("action") == "screenshot"]
        assert saved, f"{sid} has no screenshot step"
        assert any(str(name).startswith(f"{sid}-") and str(name).endswith(".png") for name in saved), \
            f"{sid} screenshot filename must start with {sid}- and end with .png"
        saves.extend(str(name) for name in saved)
    assert_unique(saves, "screenshot output filenames")


t("screenshot recipes are complete and ordered", test_screenshot_recipes_are_complete_and_ordered)


def test_screenshot_recipe_references_are_acyclic() -> None:
    shots = qa_features().get("screenshots", [])
    seen: set[str] = set()
    for item in shots:
        sid = item["id"]
        for step in item.get("drive", []):
            ref = step.get("id") if step.get("action") == "continue_from" else None
            if ref:
                assert ref in seen, f"{sid} continue_from references unavailable recipe {ref}"
                assert numeric_suffix(ref) < numeric_suffix(sid), \
                    f"{sid} continue_from must reference an earlier recipe"
        seen.add(sid)


t("screenshot recipe references are acyclic", test_screenshot_recipe_references_are_acyclic)


def test_screenshot_drive_actions_are_runner_safe() -> None:
    shots = qa_features().get("screenshots", [])
    allowed_actions = {
        "open_application",
        "wait",
        "screenshot",
        "click_input",
        "type",
        "key",
        "continue_from",
        "mouse_move",
        "left_click",
        "click_tray_header",
        "click_tab",
        "trigger_api_calls",
    }
    allowed_keys = {"Return", "Escape"}
    for item in shots:
        sid = item["id"]
        for index, step in enumerate(item.get("drive", []), 1):
            action = step.get("action")
            assert action in allowed_actions, f"{sid} step {index} uses unknown action {action!r}"
            if action == "open_application":
                assert step.get("app") == "home", f"{sid} step {index} may only open the Home app"
            elif action == "wait":
                seconds = step.get("seconds")
                assert isinstance(seconds, (int, float)) and 0 < seconds <= 15, \
                    f"{sid} step {index} wait must be bounded to 0-15s"
            elif action == "screenshot":
                save_as = str(step.get("save_as", ""))
                assert save_as.endswith(".png"), f"{sid} step {index} screenshot must save a PNG"
                assert "/" not in save_as and "\\" not in save_as, \
                    f"{sid} step {index} screenshot must not write outside the artifact dir"
            elif action == "type":
                text = str(step.get("text", ""))
                assert text.startswith("/"), f"{sid} step {index} typed screenshot command must be slash/sim scoped"
                assert "\n" not in text and "\r" not in text, \
                    f"{sid} step {index} typed command must stay single-line"
            elif action == "key":
                assert step.get("text") in allowed_keys, \
                    f"{sid} step {index} key must be one of {sorted(allowed_keys)}"
            elif action == "continue_from":
                assert re.fullmatch(r"S\d+", str(step.get("id", ""))), \
                    f"{sid} step {index} continue_from must reference a screenshot id"
            elif action == "mouse_move":
                assert str(step.get("target_row", "")).startswith("/"), \
                    f"{sid} step {index} mouse_move target_row must name a slash command"
            elif action == "left_click":
                assert str(step.get("target_row") or step.get("target") or "").strip(), \
                    f"{sid} step {index} left_click must name a target"
            elif action == "click_tab":
                assert step.get("name") in {"ai", "infra", "trace", "diag"}, \
                    f"{sid} step {index} click_tab target drifted"
            elif action == "trigger_api_calls":
                assert sid == "S14", "only the Lab activity screenshot may trigger synthetic API activity"
                assert int(step.get("count", 0)) <= 2, "synthetic API activity must stay bounded"
                assert step.get("vision") is True, "synthetic Lab activity recipe must explicitly request vision activity"


t("screenshot drive actions are runner-safe", test_screenshot_drive_actions_are_runner_safe)


def test_privacy_modes_cover_strict_clear_and_redact() -> None:
    privacy = qa_features().get("privacy", {})
    assert privacy.get("default_mode") == "strict", "privacy default must be strict"
    modes = privacy.get("modes", {})
    assert set(modes) == {"strict", "clear", "redact"}, "privacy modes must be strict/clear/redact"
    strict_targets = set(modes["strict"].get("blur_targets", []))
    assert {
        "identity-pill",
        "perception-event-text",
        "person-name-in-bubble",
        "world-state-drawer-person-row",
    } <= strict_targets, "strict mode must blur identity and perception surfaces"
    assert modes["clear"].get("blur_targets") == [], "clear mode must not blur after opt-in"
    replacements = modes["redact"].get("replacements", {})
    assert replacements.get("person_pattern") == "<person-{n}>", "redact person pattern drifted"
    assert replacements.get("room_pattern") == "<room-{n}>", "redact room pattern drifted"


t("privacy modes cover strict, clear, and redact", test_privacy_modes_cover_strict_clear_and_redact)


def test_feature_probe_matrix_is_complete_and_local_backed() -> None:
    data = feature_probes()
    probes = data.get("features", [])
    assert len(probes) == 22, f"expected 22 feature probes, got {len(probes)}"
    probe_ids = [str(item.get("id", "")) for item in probes]
    assert_unique(probe_ids, "feature probe ids")
    known_kinds = {
        "grep_count",
        "grep_count_min",
        "http_jq",
        "http_jq_post",
        "node_run",
        "python_test",
    }
    for item in probes:
        pid = item["id"]
        assert str(item.get("name", "")).strip(), f"{pid} missing name"
        assert "tier1" in item, f"{pid} missing tier1 marker"
        tiers = {
            key: value
            for key, value in item.items()
            if key.startswith("tier") and isinstance(value, dict)
        }
        assert any(key != "tier1" for key in tiers), f"{pid} missing higher-tier evidence"
        local_exec = [
            key for key, spec in tiers.items()
            if spec.get("kind") in {"node_run", "python_test"}
        ]
        assert local_exec, f"{pid} must cite at least one local executable tier"
        for tier, spec in tiers.items():
            kind = spec.get("kind")
            assert kind in known_kinds, f"{pid}.{tier} uses unknown probe kind {kind!r}"
            if kind in {"node_run", "python_test"}:
                ref = spec.get("cmd") or spec.get("file")
                assert ref, f"{pid}.{tier} missing executable reference"


t("feature probe matrix is complete and locally backed",
  test_feature_probe_matrix_is_complete_and_local_backed)


def test_live_feature_probe_endpoints_are_read_only_and_scoped() -> None:
    data = feature_probes()
    http_specs: list[tuple[str, str, dict]] = []
    for item in data.get("features", []):
        pid = item["id"]
        for tier, spec in item.items():
            if tier.startswith("tier") and isinstance(spec, dict) and str(spec.get("kind", "")).startswith("http"):
                http_specs.append((pid, tier, spec))
    for service in data.get("service_probes", []):
        http_specs.append((f"service:{service.get('id')}", "service_probe", service))

    assert http_specs, "expected at least one live HTTP probe to be represented"
    for pid, tier, spec in http_specs:
        url = str(spec.get("url", ""))
        assert is_local_or_private_http_url(url), f"{pid}.{tier} points outside local/private scope: {url}"
        kind = spec.get("kind")
        method = str(spec.get("method", "GET")).upper()
        if kind == "http_jq_post":
            assert pid == "F-3", f"only F-3 may use HTTP POST in the live probe matrix, got {pid}"
            parsed = urlparse(url)
            body = spec.get("body", {})
            assert parsed.path == "/describe_clip", "F-3 POST must target the vision describe_clip read-only endpoint"
            assert str(body.get("camera", "")).startswith("camera."), "F-3 POST must use HA camera entity id"
            assert "frame_count" not in body, "F-3 probe must use the sidecar's `frames` parameter, not stale frame_count"
            assert 1 <= int(body.get("frames", 0)) <= 8, "F-3 frames must stay bounded"
        else:
            assert method == "GET", f"{pid}.{tier} live probe must stay read-only GET"

    routing = data.get("routing_log_probe", {})
    assert routing.get("ssh_host") == "root@homeassistant.local", "routing log probe host drifted"
    assert routing.get("path") == "/config/external_routing.log", "routing log probe must tail the routing log only"
    assert int(routing.get("tail_count", 0)) <= 100, "routing log probe tail should stay bounded"


t("live feature probe endpoints are read-only and scoped",
  test_live_feature_probe_endpoints_are_read_only_and_scoped)


def test_audit_mirrors_live_agent_recipes() -> None:
    agents = {item["id"]: item for item in qa_features().get("agent_scenarios", [])}
    entries = {
        entry["test_id"]: entry
        for entry in audit()["entries"]
        if entry.get("tier") == "live_agent"
    }
    assert set(entries) == set(agents), "audit live-agent ids differ from qa-features.yaml"
    for sid, entry in entries.items():
        assert entry["name"] == agents[sid]["name"], f"{sid} audit name drifted"
        assert entry["source"] == "tools/qa-features.yaml", f"{sid} audit source drifted"
        assert entry["automation_status"] == "configured", f"{sid} automation_status drifted"
        assert entry["latest_result"] == "not_run", f"{sid} latest_result must remain not_run"
        expected_safety = "write_gated" if sid in {"U3", "U15"} else "read_only"
        assert entry["safety_level"] == expected_safety, f"{sid} safety_level drifted"


t("audit mirrors live agent recipes", test_audit_mirrors_live_agent_recipes)


def test_audit_mirrors_screenshot_recipes() -> None:
    shots = {f"SHOT-{item['id']}": item for item in qa_features().get("screenshots", [])}
    entries = {
        entry["test_id"]: entry
        for entry in audit()["entries"]
        if entry.get("tier") == "browser_screenshot"
    }
    assert set(entries) == set(shots), "audit screenshot ids differ from qa-features.yaml"
    for test_id, entry in entries.items():
        assert entry["name"] == shots[test_id]["name"], f"{test_id} audit name drifted"
        assert entry["source"] == "tools/qa-features.yaml", f"{test_id} audit source drifted"
        assert entry["safety_level"] == "read_only", f"{test_id} safety_level drifted"
        assert entry["automation_status"] == "configured", f"{test_id} automation_status drifted"
        assert entry["latest_result"] == "not_run", f"{test_id} latest_result must remain not_run"


t("audit mirrors screenshot recipes", test_audit_mirrors_screenshot_recipes)


def test_readiness_preserves_live_recipe_scope_boundary() -> None:
    text = readiness()
    assert "17 live agent scenarios" in text, "readiness log missing live-agent count"
    assert "18 screenshot recipes" in text, "readiness log missing screenshot count"
    assert "registered in `tools/qa-features.yaml`, but are out of scope" in text, \
        "readiness log must preserve live/manual out-of-scope boundary"
    assert "Live/device scenarios are not verified in this pass by design" in text, \
        "readiness residual risk must preserve live/device caveat"


t("readiness preserves live recipe scope boundary", test_readiness_preserves_live_recipe_scope_boundary)


print(f"\n{passes} pass . {fails} fail")
if fails:
    print("\nFailures:")
    for name, message in failures:
        print(f"  - {name}: {message}")

sys.exit(0 if fails == 0 else 1)
