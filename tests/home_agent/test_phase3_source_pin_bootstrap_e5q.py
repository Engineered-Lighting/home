from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
OPERATOR = ROOT / "stack/home-agent-deploy/operator"


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def load_plan() -> ModuleType:
    path = OPERATOR / "phase3_activation_source_plan.py"
    spec = importlib.util.spec_from_file_location("phase3_activation_source_plan", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pin_normalization_accepts_only_commit_and_run_literal_changes() -> None:
    plan = load_plan()

    original = (
        b'CONTRACT = "unchanged"\n'
        b'ACCEPTED_COMMIT = "1111111111111111111111111111111111111111"\n'
        b'ACCEPTED_POSTGRES_RUN_ID = "123456"\n'
        b'VALUE = "still executable"\n'
    )
    repinned = (
        b'CONTRACT = "unchanged"\n'
        b'ACCEPTED_COMMIT = "2222222222222222222222222222222222222222"\n'
        b'ACCEPTED_POSTGRES_RUN_ID = "987654321"\n'
        b'VALUE = "still executable"\n'
    )
    changed = repinned.replace(b"still executable", b"changed executable")

    assert plan.source_plan_matches_accepted_pin_only(original, repinned) is True
    assert plan.source_plan_matches_accepted_pin_only(original, changed) is False


def test_pin_normalization_rejects_missing_duplicate_and_malformed_literals() -> None:
    plan = load_plan()

    valid = (
        b'ACCEPTED_COMMIT = "1111111111111111111111111111111111111111"\n'
        b'ACCEPTED_POSTGRES_RUN_ID = "123456"\n'
    )
    invalid_values = (
        b"",
        valid + b'ACCEPTED_POSTGRES_RUN_ID = "999999"\n',
        valid.replace(b"123456", b"0"),
        valid.replace(b"1" * 40, b"z" * 40),
        valid + b"\0",
    )
    for value in invalid_values:
        with pytest.raises(plan.SourcePlanError):
            plan.normalize_source_plan_pins(value)


def test_source_plan_excludes_only_its_pin_file_from_the_plain_git_diff() -> None:
    source = read(
        "stack/home-agent-deploy/operator/phase3_activation_source_plan.py"
    )

    assert "SOURCE_PIN_BOOTSTRAP_CONTRACT" in source
    assert "source_plan_matches_accepted_pin_only" in source
    assert "if path != SOURCE_PLAN_RELATIVE_PATH" in source
    assert 'git", "show", f"{ACCEPTED_COMMIT}:{SOURCE_PLAN_RELATIVE_PATH}"' in source
    assert '"source_pin_bootstrap_installed": trusted' in source


def test_source_pin_bootstrap_is_hosted_gate_input() -> None:
    workflow = read(".github/workflows/home-agent-e1-postgres.yml")
    runner = read("tools/run-home-agent-e1-postgres-gate.py")

    assert "E5o/E5p/E5q PostgreSQL gate" in workflow
    assert "E5o/E5p/E5q authority gate" in workflow
    assert "test_phase3_source_pin_bootstrap_e5q.py" in workflow
    assert "test_phase3_source_pin_bootstrap_e5q.py" in runner
