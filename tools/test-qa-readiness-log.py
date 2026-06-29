#!/usr/bin/env python3
"""Consistency checks for the RC readiness log.

The readiness log is the human-facing release-candidate checkpoint. This
test pins it to the generated QA audit and the executable suite registry so
counts and caveats cannot drift quietly.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

REPO = Path(__file__).resolve().parent.parent
AUDIT_JSON = REPO / "docs" / "qa" / "home-app-feature-audit.json"
READINESS_MD = REPO / "docs" / "qa" / "home-app-rc-readiness.md"
RUN_TEST_COVERAGE = REPO / "tools" / "run-test-coverage.py"


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


def audit() -> dict:
    return json.loads(AUDIT_JSON.read_text(encoding="utf-8"))


def readiness() -> str:
    return READINESS_MD.read_text(encoding="utf-8", errors="replace")


def suite_registry() -> list:
    spec = importlib.util.spec_from_file_location("run_test_coverage_readiness", RUN_TEST_COVERAGE)
    assert spec and spec.loader, "unable to import run-test-coverage.py"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return list(getattr(mod, "SUITES", []))


def compact_number(value: int) -> str:
    return f"{value:,}"


def doc_s_number(doc_id: str) -> int:
    match = re.fullmatch(r"DOC-S(\d+)", doc_id)
    assert match, f"unexpected DOC scenario id: {doc_id}"
    return int(match.group(1))


def compact_doc_id_list(ids: list[str]) -> str:
    numbers = sorted(doc_s_number(doc_id) for doc_id in ids)
    groups: list[tuple[int, int]] = []
    for number in numbers:
        if groups and number == groups[-1][1] + 1:
            groups[-1] = (groups[-1][0], number)
        else:
            groups.append((number, number))

    labels = []
    for start, end in groups:
        if start == end:
            labels.append(f"DOC-S{start}")
        else:
            labels.append(f"DOC-S{start} through DOC-S{end}")
    return ", ".join(labels)


def assert_contains(text: str, needle: str) -> None:
    assert needle in text, f"readiness log missing: {needle!r}"


print("\nQA readiness log consistency\n")


def test_readiness_inventory_counts_match_audit() -> None:
    data = audit()
    text = readiness()
    counts = data["registry_counts"]
    inventory = data["inventory"]
    spatial = inventory["spatial_model"]
    mqtt = inventory["predictive_lighting_mqtt"]
    supervisor_endpoints = inventory["stack_supervisor_endpoints"]
    expected_lines = [
        f"- {counts['total_entries']} canonical QA entries.",
        f"- {counts['doc_scenarios']} documented scenarios.",
        f"- {counts['agent_scenarios']} live agent scenarios.",
        f"- {counts['screenshot_recipes']} screenshot recipes.",
        f"- {counts['feature_probes']} feature probes.",
        f"- {counts['test_suites']} quick/unit suites.",
        f"- {len(inventory['ha_packages'])} Home Assistant packages.",
        f"- {len(inventory['compose_services'])} Docker Compose services.",
        f"- {len(inventory['living_lights_classifier_states'])} Living Lights classifier states.",
        f"- {len(inventory['ha_input_booleans'])} Home Assistant input_boolean flags.",
        f"- {len(inventory['agent_tools'])} Extended OpenAI agent tools.",
        f"- {spatial['camera_count']} spatial model cameras.",
        f"- {spatial['light_count']} spatial model lights.",
        f"- {len(mqtt['publish_topics'])} predictive-lighting MQTT publish topic families.",
        f"- {len(mqtt['subscribe_topics'])} predictive-lighting MQTT subscription topic families.",
        f"- {len(supervisor_endpoints)} stack-supervisor endpoints.",
    ]
    for line in expected_lines:
        assert_contains(text, line)


t("readiness inventory counts match generated audit",
  test_readiness_inventory_counts_match_audit)


def test_readiness_scenario_counts_match_audit() -> None:
    data = audit()
    text = readiness()
    summary = data["scenario_evidence"]["counts"]
    expected = (
        "Scenario evidence map: "
        f"{summary.get('simulation_green', 0)} documented scenarios simulation-covered, "
        f"{summary.get('local_contract_green', 0)} local-contract-covered, "
        f"{summary.get('local_partial_green', 0)} locally partial, and "
        f"{summary.get('live_required', 0)} live-required with no sufficient local substitute."
    )
    assert_contains(text, expected)
    assert data["scenario_evidence"]["live_required_ids"] == [], \
        "generated audit should have no unmapped/live-required doc scenarios"
    assert "_none_" in (REPO / "docs" / "qa" / "home-app-feature-audit.md").read_text(encoding="utf-8"), \
        "markdown audit should render an explicit empty live-required list"


t("readiness scenario evidence counts match generated audit",
  test_readiness_scenario_counts_match_audit)


def test_feature_audit_markdown_exposes_backend_inventory() -> None:
    data = audit()
    markdown = (REPO / "docs" / "qa" / "home-app-feature-audit.md").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "### Home Assistant Views" in markdown, \
        "markdown audit should expose HA view inventory, not just counts"
    assert "### First-Party FastAPI Routes" in markdown, \
        "markdown audit should expose FastAPI route inventory, not just counts"
    for item in data["inventory"]["ha_views"]:
        line = f"- `{item['url']}` — `{item['file']}:{item['line']}`"
        assert line in markdown, f"markdown audit missing HA view: {line}"
    for item in data["inventory"]["fastapi_routes"]:
        line = f"- `{item['method']} {item['path']}` — `{item['file']}:{item['line']}`"
        assert line in markdown, f"markdown audit missing FastAPI route: {line}"


t("feature audit markdown exposes backend route and HA view inventory",
  test_feature_audit_markdown_exposes_backend_inventory)


def test_feature_audit_markdown_exposes_frontend_script_inventory() -> None:
    data = audit()
    markdown = (REPO / "docs" / "qa" / "home-app-feature-audit.md").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "### Frontend Scripts" in markdown, \
        "markdown audit should expose frontend script inventory, not just counts"
    expected = ", ".join(f"`{item}`" for item in data["inventory"]["frontend_scripts"])
    assert expected in markdown, "markdown audit missing frontend script inventory"


t("feature audit markdown exposes frontend script inventory",
  test_feature_audit_markdown_exposes_frontend_script_inventory)


def test_feature_audit_markdown_exposes_static_inventory_lists() -> None:
    data = audit()
    markdown = (REPO / "docs" / "qa" / "home-app-feature-audit.md").read_text(
        encoding="utf-8", errors="replace"
    )
    expected_sections = {
        "### Slash Commands": ", ".join(
            f"`/{item}`" for item in data["inventory"]["slash_commands"]
        ),
        "### Simulation Scenarios": ", ".join(
            f"`{item}`" for item in data["inventory"]["simulation_scenarios"]
        ),
        "### UI Surfaces": ", ".join(
            f"`{item}`" for item in data["inventory"]["ui_surfaces"]
        ),
        "### Home Assistant Packages": ", ".join(
            f"`{item}`" for item in data["inventory"]["ha_packages"]
        ),
        "### Living Lights Classifier States": ", ".join(
            f"`{item}`" for item in data["inventory"]["living_lights_classifier_states"]
        ),
    }
    for heading, expected in expected_sections.items():
        assert heading in markdown, f"markdown audit missing section {heading}"
        assert expected in markdown, f"markdown audit missing inventory list for {heading}"
    assert "### Docker Compose Services" in markdown, \
        "markdown audit should expose Docker Compose service inventory"
    for item in data["inventory"]["compose_services"]:
        line = f"- `{item['service']}` — `{item['container_name']}` (`{item['profile']}`)"
        assert line in markdown, f"markdown audit missing compose service row: {line}"
    assert "### Home Assistant Input Boolean Flags" in markdown, \
        "markdown audit should expose HA input_boolean flags"
    for item in data["inventory"]["ha_input_booleans"]:
        line = f"- `{item['entity']}` — `{item['file']}:{item['line']}`"
        assert line in markdown, f"markdown audit missing input_boolean row: {line}"


    assert "### Extended OpenAI Agent Tools" in markdown, \
        "markdown audit should expose Extended OpenAI agent tools"
    for item in data["inventory"]["agent_tools"]:
        line = f"- `{item['name']}` - `{item['function_type']}` -> `{item['function_target']}`"
        assert line in markdown, f"markdown audit missing agent tool row: {line}"
    assert "### Spatial Model Schema" in markdown, \
        "markdown audit should expose spatial_model schema"
    spatial = data["inventory"]["spatial_model"]
    expected_spatial_lines = [
        f"- Schema version: `{spatial['schema']}`; source: `{spatial['source']}`; grid target columns: `{spatial['target_cols']}`",
        (
            f"- Current inventory: `{spatial['camera_count']}` cameras, "
            f"`{spatial['zone_count']}` Frigate zones, "
            f"`{spatial['light_camera_count']}` light-camera bindings, "
            f"`{spatial['light_count']}` lights, "
            f"`{spatial['lights_with_footprint_polygon']}` lights with footprint polygons, "
            f"`{spatial['footprint_empty_count']}` footprint-empty lights"
        ),
    ]
    for line in expected_spatial_lines:
        assert line in markdown, f"markdown audit missing spatial model row: {line}"
    for camera in spatial["cameras"]:
        zones = ", ".join(f"`{zone}`" for zone in camera["zones"]) or "_none_"
        line = (
            f"- `{camera['name']}` - detect `{camera['detect_w']}x{camera['detect_h']}`, "
            f"grid `{camera['grid_cols']}x{camera['grid_rows']}`, "
            f"{camera['zone_count']} zones: {zones}"
        )
        assert line in markdown, f"markdown audit missing spatial model camera row: {line}"
    assert "### Predictive Lighting MQTT Topics" in markdown, \
        "markdown audit should expose predictive-lighting MQTT topics"
    mqtt = data["inventory"]["predictive_lighting_mqtt"]
    assert "Publish topic families:" in markdown, \
        "markdown audit should label predictive-lighting publish topics"
    assert "Subscription topic families:" in markdown, \
        "markdown audit should label predictive-lighting subscription topics"
    for item in mqtt["publish_topics"]:
        line = (
            f"- `{item['topic']}` - payload {item['payload']}; "
            f"retain `{item['retain']}`; qos `{item['qos']}`; {item['purpose']}"
        )
        assert line in markdown, f"markdown audit missing MQTT publish row: {line}"
    for item in mqtt["subscribe_topics"]:
        line = (
            f"- `{item['topic']}` - payload {item['payload']}; "
            f"qos `{item['qos']}`; {item['purpose']}"
        )
        assert line in markdown, f"markdown audit missing MQTT subscription row: {line}"
    assert "### Stack Supervisor Endpoints" in markdown, \
        "markdown audit should expose stack supervisor endpoints"
    for item in data["inventory"]["stack_supervisor_endpoints"]:
        line = (
            f"- `{item['method']} {item['path']}` - status `{item['status_code']}`; "
            f"auth `{item['auth']}`; confirm `{item['confirm']}`; "
            f"{item['purpose']}; source `{item['file']}:{item['line']}`"
        )
        assert line in markdown, f"markdown audit missing stack supervisor endpoint row: {line}"


t("feature audit markdown exposes static inventory lists",
  test_feature_audit_markdown_exposes_static_inventory_lists)


def test_feature_audit_markdown_exposes_non_doc_qa_entries() -> None:
    data = audit()
    markdown = (REPO / "docs" / "qa" / "home-app-feature-audit.md").read_text(
        encoding="utf-8", errors="replace"
    )
    expected_sections = {
        "### Live Agent Scenarios": lambda item: item["tier"] == "live_agent",
        "### Screenshot Recipes": lambda item: item["tier"] == "browser_screenshot",
        "### Feature Probes": lambda item: item["test_id"].startswith("PROBE-"),
        "### Quick/Unit Suites": lambda item: item["tier"] == "unit_contract",
    }
    for heading, predicate in expected_sections.items():
        assert heading in markdown, f"markdown audit missing section {heading}"
        rows = [item for item in data["entries"] if predicate(item)]
        assert rows, f"expected at least one row for {heading}"
        for item in rows:
            line = (
                f"- `{item['test_id']}` - {item['name']} "
                f"(`{item['tier']}`, `{item['safety_level']}`, `{item['latest_result']}`; "
                f"source: `{item['source']}`)"
            )
            assert line in markdown, f"markdown audit missing registry row: {line}"


t("feature audit markdown exposes non-doc QA entry inventories",
  test_feature_audit_markdown_exposes_non_doc_qa_entries)


def test_feature_audit_markdown_exposes_documented_scenario_evidence() -> None:
    data = audit()
    markdown = (REPO / "docs" / "qa" / "home-app-feature-audit.md").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "## Documented Scenario Evidence" in markdown, \
        "markdown audit should expose per-scenario evidence, not just counts"
    scenario_entries = [
        item for item in data["entries"]
        if item["tier"] == "scenario_doc"
    ]
    assert len(scenario_entries) == data["registry_counts"]["doc_scenarios"], \
        "scenario entry inventory should match registry count"
    by_id = data["scenario_evidence"]["by_id"]
    for item in scenario_entries:
        evidence = by_id[item["test_id"]]
        evidence_files = evidence.get("evidence", [])
        evidence_text = ", ".join(f"`{rel}`" for rel in evidence_files) if evidence_files else "_none_"
        remaining = str(evidence.get("remaining", "")).strip() or "_not recorded_"
        line = (
            f"- `{item['test_id']}` - {item['name']} "
            f"(`{evidence.get('status', 'unknown')}`; evidence: {evidence_text}; "
            f"remaining: {remaining})"
        )
        assert line in markdown, f"markdown audit missing scenario evidence row: {line}"


t("feature audit markdown exposes documented scenario evidence",
  test_feature_audit_markdown_exposes_documented_scenario_evidence)


def test_readiness_names_local_partial_scenarios() -> None:
    data = audit()
    text = readiness()
    by_id = data["scenario_evidence"]["by_id"]
    partial_ids = [
        scenario_id
        for scenario_id, row in by_id.items()
        if row.get("status") == "local_partial_green"
    ]
    assert len(partial_ids) == data["scenario_evidence"]["counts"].get("local_partial_green", 0), \
        "local-partial ID inventory should match generated count"
    expected = f"- Local-partial scenario IDs: {compact_doc_id_list(partial_ids)}."
    assert_contains(text, expected)


t("readiness names the local-partial scenario IDs",
  test_readiness_names_local_partial_scenarios)


def test_readiness_suite_counts_match_registry_and_gate_lines() -> None:
    data = audit()
    text = readiness()
    suites = suite_registry()
    quick_suites = [suite for suite in suites if not getattr(suite, "slow", False)]
    assert data["registry_counts"]["test_suites"] == len(suites), \
        "audit test_suites count must match run-test-coverage registry"
    assert len(quick_suites) == len(suites), \
        "readiness text says there are 0 slow suites, so every suite must be quick"
    quick = re.search(r"--quick --json`: pass in 3 consecutive runs, (\d+) suites, ([\d,]+) assertions", text)
    full = re.search(r"--json`: pass, (\d+) suites, ([\d,]+) assertions", text)
    assert quick, "quick gate result line missing or malformed"
    assert full, "full gate result line missing or malformed"
    quick_count, quick_assertions = int(quick.group(1)), quick.group(2)
    full_count, full_assertions = int(full.group(1)), full.group(2)
    assert quick_count == len(quick_suites), \
        f"quick suite count drift: readiness={quick_count}, registry={len(quick_suites)}"
    assert full_count == len(suites), \
        f"full suite count drift: readiness={full_count}, registry={len(suites)}"
    assert quick_assertions == full_assertions, \
        "quick and full assertion counts should match while there are no slow suites"
    assert int(quick_assertions.replace(",", "")) > 0, "assertion count should be nonzero"


t("readiness gate lines match suite registry",
  test_readiness_suite_counts_match_registry_and_gate_lines)


def test_readiness_suite_narrative_matches_registry() -> None:
    text = readiness()
    suites = suite_registry()
    assert "quick/unit suite count from 29 to 61" not in text, \
        "readiness log contains stale suite-growth wording"
    expected = f"bringing the current quick/unit suite count to {len(suites)}."
    assert_contains(text, expected)


t("readiness suite narrative matches current registry",
  test_readiness_suite_narrative_matches_registry)


def test_readiness_preserves_local_partial_caveat() -> None:
    data = audit()
    text = readiness()
    partial = data["scenario_evidence"]["counts"].get("local_partial_green", 0)
    assert partial > 0, "expected at least one local-partial scenario"
    assert "Local-partial scenarios still require live/non-production validation" in text, \
        "readiness must not imply local-partial scenarios are end-to-end green"
    assert "still need live/non-production agent scoring and HA state verification" in text, \
        "residual risk for DOC-S58 through DOC-S68 must stay explicit"
    assert "Not release-candidate ready yet within the full requested scope." in text, \
        "readiness status must not overclaim full RC readiness"


t("readiness preserves local-partial/live-validation caveat",
  test_readiness_preserves_local_partial_caveat)


def test_readiness_lists_required_gate_commands() -> None:
    text = readiness()
    for command in [
        r"py -3 tools\run-test-coverage.py --quick --json",
        r"py -3 tools\run-test-coverage.py --json",
        r"node tools\check-jsx.js home-app.jsx home-worldstate.jsx home-lights.jsx home-ai-stack.jsx",
        r"py -3 tools\qa-static-checks.py",
        r"py -3 tools\qa-registry-audit.py",
        "cargo check",
        "cargo test",
        "cargo tauri build --no-bundle",
    ]:
        assert_contains(text, command)


t("readiness lists required local gate commands",
  test_readiness_lists_required_gate_commands)


def test_readiness_reflects_static_check_count() -> None:
    text = readiness()
    assert "`py -3 tools\\qa-static-checks.py`: pass, 21 checks." in text, \
        "readiness log must report the current static QA check count"
    assert "`py -3 tools\\qa-static-checks.py`: pass, 20 checks." not in text, \
        "readiness log must not keep stale static QA check count"
    assert "`py -3 tools\\qa-static-checks.py`: pass, 19 checks." not in text, \
        "readiness log must not keep stale static QA check count"
    assert "`py -3 tools\\qa-static-checks.py`: pass, 18 checks." not in text, \
        "readiness log must not keep stale static QA check count"
    assert "`py -3 tools\\qa-static-checks.py`: pass, 17 checks." not in text, \
        "readiness log must not keep stale static QA check count"
    assert "`py -3 tools\\qa-static-checks.py`: pass, 16 checks." not in text, \
        "readiness log must not keep stale static QA check count"
    assert "`py -3 tools\\qa-static-checks.py`: pass, 15 checks." not in text, \
        "readiness log must not keep stale static QA check count"
    assert "`py -3 tools\\qa-static-checks.py`: pass, 14 checks." not in text, \
        "readiness log must not keep stale static QA check count"
    assert "`py -3 tools\\qa-static-checks.py`: pass, 13 checks." not in text, \
        "readiness log must not keep stale static QA check count"
    assert "`py -3 tools\\qa-static-checks.py`: pass, 12 checks." not in text, \
        "readiness log must not keep stale static QA check count"
    assert "`py -3 tools\\qa-static-checks.py`: pass, 11 checks." not in text, \
        "readiness log must not keep stale static QA check count"
    assert "`py -3 tools\\qa-static-checks.py`: pass, 10 checks." not in text, \
        "readiness log must not keep stale static QA check count"
    assert "`py -3 tools\\qa-static-checks.py`: pass, 9 checks." not in text, \
        "readiness log must not keep stale static QA check count"
    assert "`py -3 tools\\qa-static-checks.py`: pass, 8 checks." not in text, \
        "readiness log must not keep stale static QA check count"
    assert "`py -3 tools\\qa-static-checks.py`: pass, 7 checks." not in text, \
        "readiness log must not keep older stale static QA check count"


t("readiness reflects static QA check count",
  test_readiness_reflects_static_check_count)


def test_readiness_reflects_rust_tauri_unit_coverage() -> None:
    text = readiness()
    suites = suite_registry()
    suite_ids = {suite.id for suite in suites}
    assert "cargo-test-tauri" in suite_ids, \
        "cargo-test-tauri should stay in the standard local coverage gate"
    assert "`cargo test` in `app/src-tauri`: pass, 5 Rust unit tests." in text, \
        "readiness log must report the current Rust unit-test count"
    assert "Rust unit coverage is currently limited to Tauri shell/bootstrap contracts" in text, \
        "readiness log should preserve the narrowed Rust residual-risk wording"
    assert "0 Rust tests" not in text, "readiness log must not contain stale 0-test wording"
    assert "compile smoke only" not in text, "readiness log must not contain stale compile-smoke wording"


t("readiness reflects Rust/Tauri unit coverage",
  test_readiness_reflects_rust_tauri_unit_coverage)


def test_audit_warning_state_is_reflected() -> None:
    data = audit()
    text = readiness()
    assert data.get("warnings") == [], f"audit warnings are not empty: {data.get('warnings')}"
    assert_contains(text, "`py -3 tools\\qa-registry-audit.py`: pass, 0 warnings.")


t("audit warning state is reflected in readiness log",
  test_audit_warning_state_is_reflected)


print(f"\n{passes} pass . {fails} fail")
if fails:
    print("\nFailures:")
    for name, message in failures:
        print(f"  - {name}: {message}")

sys.exit(0 if fails == 0 else 1)
