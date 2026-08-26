"""The signed E4 documents already carry their tables' column sets.

Steps 21-23 sign writer-freeze, privacy, and cutover documents, and the
semantic cutover kernel reads four tables built from them. Nothing in the
application writes those tables — the only code that populates them is a test
fixture. The missing writer is therefore a key-driven insert rather than a
mapping exercise, but only for as long as the two sides agree.

These tests pin that agreement, so a change to either side fails here instead
of failing in the ceremony with Home Assistant already stopped.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
OPERATOR = ROOT / "stack/home-agent-deploy/operator"
CORE = ROOT / "stack/services/home-agent-core"
SEEDER = CORE / "tests/seed_phase3_identity_semantic_cutover_e4_success.py"
CUTOVER_MIGRATION = (
    CORE / "alembic/versions/0014_identity_semantic_cutover_e4.py"
)


def _operator_module(name: str) -> ModuleType:
    sys.path.insert(0, str(OPERATOR))
    try:
        spec = importlib.util.spec_from_file_location(
            f"home_agent_e4_contract_{name}", OPERATOR / f"{name}.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(OPERATOR))


def _seeded_insert_columns() -> dict[str, tuple[str, ...]]:
    """Column lists the fixture inserts, keyed by table.

    The fixture builds its SQL from adjacent string literals, so the quotes and
    whitespace are removed before matching.
    """

    source = SEEDER.read_text(encoding="utf-8")
    flattened = re.sub(r"\s+", "", source.replace('"', ""))
    found = {}
    for table, columns in re.findall(
        r"INSERTINTOoperations\.(\w+)\(([^)]*)\)VALUES", flattened
    ):
        found[table] = tuple(column for column in columns.split(",") if column)
    return found


def _document_key_sets() -> dict[str, frozenset[str]]:
    freeze = _operator_module("phase3_writer_freeze_evidence")
    privacy = _operator_module("phase3_privacy_cutover_evidence")
    cutover = _operator_module("phase3_semantic_cutover_packet")
    return {
        "legacy_identity_writer_evidence": freeze.EVIDENCE_KEYS,
        "enforced_legacy_identity_writer_freezes": freeze.FREEZE_KEYS,
        "privacy_cutover_check_receipts": privacy.RECEIPT_KEYS,
        "semantic_authority_cutovers": cutover.CANDIDATE_KEYS,
    }


def test_every_e4_evidence_table_is_written_only_by_a_test_fixture() -> None:
    """Record the gap this contract exists because of.

    If a production writer ever lands, this test should be updated to point at
    it — the assertion is a marker, not an endorsement.
    """

    application = ROOT / "stack/services/home-agent-core/app"
    writers = [
        path.name
        for path in application.rglob("*.py")
        if "INSERT INTO operations." in path.read_text(encoding="utf-8")
    ]
    # Today: the finalizer admission and the cutover admission, both in the one
    # module. Neither writes the four evidence tables below.
    assert writers == ["identity_admission_writer.py"], writers
    admission = (application / "identity_admission_writer.py").read_text(
        encoding="utf-8"
    )
    for table in _document_key_sets():
        assert f"INSERT INTO operations.{table}" not in admission


@pytest.mark.parametrize("table", sorted(_document_key_sets()))
def test_signed_document_carries_its_table_columns(table: str) -> None:
    keys = _document_key_sets()[table]
    seeded = _seeded_insert_columns()
    assert table in seeded, f"{table} is not inserted by the fixture"
    assert set(seeded[table]) == set(keys), {
        "only_in_fixture": sorted(set(seeded[table]) - set(keys)),
        "only_in_document": sorted(set(keys) - set(seeded[table])),
    }


def test_modelled_tables_agree_with_the_documents() -> None:
    """Three of the four are modelled in app.schema; check those directly.

    `enforced_legacy_identity_writer_freezes` is deliberately excluded: it is
    created by migrations 0014/0015 and has no model in app/schema.py at all,
    which is itself a symptom of nothing in the application ever writing it.
    """

    sys.path.insert(0, str(CORE))
    try:
        from app import schema
    finally:
        sys.path.remove(str(CORE))

    tables = {table.name: table for table in schema.metadata.sorted_tables}
    assert "enforced_legacy_identity_writer_freezes" not in tables
    for name, keys in _document_key_sets().items():
        table = tables.get(name)
        if table is None:
            continue
        columns = {column.name for column in table.columns}
        optional = {
            column.name
            for column in table.columns
            if column.nullable
            or column.default is not None
            or column.server_default is not None
        }
        assert set(keys) <= columns, sorted(set(keys) - columns)
        assert columns - set(keys) <= optional, sorted(
            columns - set(keys) - optional
        )


def test_the_cutover_kernel_requires_all_four() -> None:
    """The commit kernel reads every one of them, so a partial writer is useless."""

    migration = CUTOVER_MIGRATION.read_text(encoding="utf-8")
    for table in _document_key_sets():
        assert f"operations.{table}" in migration, table
    # And the admission's foreign keys point at two of them, so those rows must
    # exist before an admission can even be written.
    assert "fk_identity_cutover_admission_candidate" in migration
    assert "fk_identity_cutover_admission_freeze" in migration
