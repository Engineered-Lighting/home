#!/usr/bin/env python3
"""Validate tools/scenario-evidence-map.json against docs/SCENARIO-TESTS.md.

This is a truthfulness guard: local contract tests are allowed to support a
scenario, but the map must not omit live/manual residue or silently drop a
documented scenario.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "SCENARIO-TESTS.md"
MAP = REPO / "tools" / "scenario-evidence-map.json"

passes = 0
fails = 0
failures: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global passes, fails
    if cond:
        passes += 1
        print(f"  PASS  {name}")
    else:
        fails += 1
        failures.append((name, detail))
        print(f"  FAIL  {name}")
        if detail:
            print(f"        {detail[:800]}")


def scenario_ids() -> set[str]:
    text = DOC.read_text(encoding="utf-8", errors="replace")
    ids = set()
    for match in re.finditer(r"^###\s+(S\d+)\s+[-\u2014]\s+(.+)$", text, re.M):
        ids.add(f"DOC-{match.group(1)}")
    return ids


def expand_range(start: str, end: str) -> list[str]:
    m1 = re.fullmatch(r"DOC-S(\d+)", start)
    m2 = re.fullmatch(r"DOC-S(\d+)", end)
    if not m1 or not m2:
        raise ValueError(f"bad range endpoints: {start}, {end}")
    a = int(m1.group(1))
    b = int(m2.group(1))
    if b < a:
        raise ValueError(f"range end before start: {start}, {end}")
    return [f"DOC-S{i}" for i in range(a, b + 1)]


def expand_rules(data: dict) -> tuple[dict[str, dict], list[str]]:
    expanded: dict[str, dict] = {}
    errors: list[str] = []
    for idx, rule in enumerate(data.get("rules", []), 1):
        ids: list[str] = []
        try:
            if "range" in rule:
                ids.extend(expand_range(rule["range"][0], rule["range"][1]))
            ids.extend(rule.get("ids", []))
        except Exception as exc:
            errors.append(f"rule {idx}: {exc}")
            continue
        for sid in ids:
            if sid in expanded:
                errors.append(f"{sid} mapped more than once")
            expanded[sid] = rule
    return expanded, errors


def main() -> int:
    print("Scenario evidence map checks")
    print("=" * 60)
    doc_ids = scenario_ids()
    data = json.loads(MAP.read_text(encoding="utf-8"))
    expanded, expansion_errors = expand_rules(data)
    statuses = set(data.get("status_definitions", {}).keys())
    mapped_ids = set(expanded.keys())

    check("schema version is current", data.get("schema_version") == 1)
    check(
        "status vocabulary includes expected truth states",
        {"simulation_green", "local_contract_green", "local_partial_green", "live_required"} <= statuses,
    )
    check("rules expand without duplicates or malformed ranges", not expansion_errors, "; ".join(expansion_errors))
    check(
        "every documented scenario has an evidence-map entry",
        doc_ids == mapped_ids,
        f"missing={sorted(doc_ids - mapped_ids)} extra={sorted(mapped_ids - doc_ids)}",
    )
    bad_status = sorted(sid for sid, rule in expanded.items() if rule.get("status") not in statuses)
    check("every mapped scenario uses a known status", not bad_status, ", ".join(bad_status))
    missing_remaining = sorted(sid for sid, rule in expanded.items() if not str(rule.get("remaining", "")).strip())
    check("every mapped scenario records remaining live/manual work", not missing_remaining, ", ".join(missing_remaining))
    no_evidence = sorted(
        sid for sid, rule in expanded.items()
        if rule.get("status") != "live_required" and not rule.get("evidence")
    )
    check("non-live-required mappings cite local evidence", not no_evidence, ", ".join(no_evidence))
    missing_files: list[str] = []
    for sid, rule in sorted(expanded.items()):
        for rel in rule.get("evidence", []):
            if not (REPO / rel).exists():
                missing_files.append(f"{sid}: {rel}")
    check("all cited local evidence files exist", not missing_files, "; ".join(missing_files))

    print("")
    print(f"{passes} pass · {fails} fail")
    if failures:
        for name, detail in failures:
            print(f"  FAIL: {name} — {detail[:300]}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
