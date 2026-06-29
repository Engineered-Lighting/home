#!/usr/bin/env python3
"""Consistency checks for the RC blocker / residual-risk ledger."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "docs" / "qa" / "home-app-rc-blockers.json"
READINESS_MD = REPO / "docs" / "qa" / "home-app-rc-readiness.md"
SCENARIO_MAP = REPO / "tools" / "scenario-evidence-map.json"
AUDIT_JSON = REPO / "docs" / "qa" / "home-app-feature-audit.json"

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


def ledger() -> dict:
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def blockers() -> list[dict]:
    items = ledger().get("blockers", [])
    assert isinstance(items, list) and items, "ledger must contain blockers"
    return items


def readiness() -> str:
    return READINESS_MD.read_text(encoding="utf-8", errors="replace")


def doc_s_number(doc_id: str) -> int:
    match = re.fullmatch(r"DOC-S(\d+)", doc_id)
    assert match, f"unexpected DOC scenario id: {doc_id}"
    return int(match.group(1))


print("\nRC blocker ledger checks\n")


def test_schema_and_ids_are_stable() -> None:
    data = ledger()
    assert data.get("schema_version") == 1, "unexpected schema version"
    ids = [item.get("id") for item in blockers()]
    assert len(ids) == len(set(ids)), "blocker ids must be unique"
    for item_id in ids:
        assert re.fullmatch(r"RC[BR]-\d{3}", str(item_id)), f"bad blocker id {item_id!r}"


t("schema and ids are stable", test_schema_and_ids_are_stable)


def test_required_fields_are_present() -> None:
    required = {
        "id",
        "kind",
        "title",
        "status",
        "blocks_rc",
        "scope",
        "impact",
        "evidence",
        "readiness_anchor",
        "unblock",
    }
    for item in blockers():
        missing = sorted(required - set(item))
        assert not missing, f"{item.get('id')} missing fields: {', '.join(missing)}"
        assert item["kind"] in {"blocker", "risk"}, f"{item['id']} has bad kind"
        assert item["status"] in {"documented_blocker", "documented_risk"}, \
            f"{item['id']} has bad status"
        assert isinstance(item["blocks_rc"], bool), f"{item['id']} blocks_rc must be boolean"
        assert item["scope"], f"{item['id']} must name affected scope"
        assert str(item["impact"]).strip(), f"{item['id']} must describe impact"
        assert str(item["unblock"]).strip(), f"{item['id']} must describe unblock path"


t("required fields are present", test_required_fields_are_present)


def test_evidence_paths_exist() -> None:
    for item in blockers():
        for rel in item.get("evidence", []):
            path = REPO / rel
            assert path.exists(), f"{item['id']} cites missing evidence path {rel}"


t("evidence paths exist", test_evidence_paths_exist)


def test_readiness_mentions_every_anchor() -> None:
    text = readiness()
    assert "docs/qa/home-app-rc-blockers.json" in text, \
        "readiness log should point to the structured blocker ledger"
    for item in blockers():
        anchor = str(item.get("readiness_anchor", "")).strip()
        assert anchor and anchor in text, f"readiness log missing anchor for {item['id']}"


t("readiness mentions every anchor", test_readiness_mentions_every_anchor)


def test_readiness_names_every_blocker_and_risk() -> None:
    text = readiness()
    for item in blockers():
        assert item["id"] in text, f"readiness log missing blocker/risk id {item['id']}"
        assert item["title"] in text, f"readiness log missing blocker/risk title for {item['id']}"


t("readiness names every blocker and risk",
  test_readiness_names_every_blocker_and_risk)


def test_blocking_items_keep_full_rc_status_not_ready() -> None:
    items = blockers()
    blocking = [item for item in items if item.get("blocks_rc") is True]
    nonblocking = [item for item in items if item.get("blocks_rc") is False]
    assert blocking, "expected at least one full-RC blocker"
    assert nonblocking, "expected at least one documented non-blocking residual risk"
    text = readiness()
    assert "Not release-candidate ready yet within the full requested scope." in text, \
        "full RC status must stay not-ready while blocking items remain"


t("blocking items keep full RC status not ready", test_blocking_items_keep_full_rc_status_not_ready)


def test_local_partial_blocker_matches_scenario_map() -> None:
    scenario = json.loads(SCENARIO_MAP.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT_JSON.read_text(encoding="utf-8"))
    partial_rules = [
        rule for rule in scenario.get("rules", [])
        if rule.get("status") == "local_partial_green"
    ]
    assert partial_rules, "scenario map should still expose local-partial rules"
    matching = [
        item for item in blockers()
        if "local_partial_scenarios" in item.get("scope", [])
    ]
    assert matching, "ledger must explain local-partial scenario blocker"
    assert any("tools/scenario-evidence-map.json" in item.get("evidence", []) for item in matching), \
        "local-partial blocker must cite scenario evidence map"
    expected_ids = sorted(
        [
            scenario_id
            for scenario_id, row in audit["scenario_evidence"]["by_id"].items()
            if row.get("status") == "local_partial_green"
        ],
        key=doc_s_number,
    )
    actual_ids = sorted(matching[0].get("scenario_ids", []), key=doc_s_number)
    assert actual_ids == expected_ids, \
        "local-partial blocker scenario_ids must match generated audit"


t("local-partial blocker matches scenario map", test_local_partial_blocker_matches_scenario_map)


def test_desktop_bundle_risk_reflects_current_msi_smoke() -> None:
    desktop_items = [
        item for item in blockers()
        if "desktop_bundle" in item.get("scope", [])
        and "installer" in item.get("scope", [])
    ]
    assert len(desktop_items) == 1, "expected exactly one desktop bundle/install ledger item"
    item = desktop_items[0]
    assert item["id"] == "RCR-003", "desktop bundle item should be the non-blocking residual risk"
    assert item["kind"] == "risk", "desktop bundle package build now passes, so this should be a risk"
    assert item["blocks_rc"] is False, "desktop bundle risk should not block local-scope RC evidence"
    assert "Windows MSI bundle built" in item["title"], \
        "desktop bundle title should reflect the current MSI evidence"
    assert "administrative extraction" in item["impact"], \
        "desktop bundle risk should record the non-install MSI smoke"
    assert "release executable was locked" not in item["title"], \
        "desktop bundle risk title must not keep stale locked-exe blocker wording"
    text = readiness()
    assert item["readiness_anchor"] in text, \
        "readiness log must mention the current MSI bundle/extraction evidence"
    assert "RCB-003" not in text, \
        "readiness log must not keep stale desktop blocker id"


t("desktop bundle risk reflects current MSI smoke",
  test_desktop_bundle_risk_reflects_current_msi_smoke)


def test_frigate_blocker_cites_watcher_and_flow_evidence() -> None:
    frigate_items = [
        item for item in blockers()
        if "frigate" in item.get("scope", [])
        and "fade_on" in item.get("scope", [])
    ]
    assert len(frigate_items) == 1, "expected exactly one Frigate/fade-on blocker"
    evidence = set(frigate_items[0].get("evidence", []))
    assert "tools/test_living_lights_frigate_occupancy_path.py" in evidence, \
        "Frigate blocker should cite local fade-on flow coverage"
    assert any(path.startswith("tools/reports/") and path.endswith(".md") for path in evidence), \
        "Frigate blocker should cite watcher markdown evidence"


t("Frigate blocker cites watcher and flow evidence", test_frigate_blocker_cites_watcher_and_flow_evidence)


def test_rust_risk_reflects_current_thin_shell_coverage() -> None:
    rust_items = [
        item for item in blockers()
        if "rust" in item.get("scope", [])
        and "tauri" in item.get("scope", [])
    ]
    assert len(rust_items) == 1, "expected exactly one Rust/Tauri residual risk"
    item = rust_items[0]
    assert item["kind"] == "risk", "Rust/Tauri item should be a residual risk, not a blocker"
    assert item["blocks_rc"] is False, "Rust/Tauri thin-shell coverage should not block full RC"
    assert "thin Tauri shell" in item["title"], "Rust/Tauri risk title should reflect current coverage"
    assert "compile/build smoke" not in item["impact"], \
        "Rust/Tauri risk must not keep stale compile-smoke wording"
    evidence = set(item.get("evidence", []))
    assert "app/src-tauri/src/main.rs" in evidence, "Rust/Tauri risk should cite unit-test source"
    assert "tools/run-test-coverage.py" in evidence, "Rust/Tauri risk should cite suite registration"


t("Rust risk reflects current thin-shell coverage",
  test_rust_risk_reflects_current_thin_shell_coverage)


print(f"\n{passes} pass . {fails} fail")
if fails:
    print("\nFailures:")
    for name, message in failures:
        print(f"  - {name}: {message}")

sys.exit(0 if fails == 0 else 1)
