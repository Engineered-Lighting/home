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

from datetime import datetime
import importlib.util
import json
from pathlib import Path
import re
import sys
from types import ModuleType
import uuid

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


def test_the_evidence_tables_have_exactly_one_production_writer() -> None:
    """Only the evidence writer fills them, and it fills all four."""

    application = ROOT / "stack/services/home-agent-core/app"
    writers = sorted(
        path.name
        for path in application.rglob("*.py")
        if "INSERT INTO operations." in path.read_text(encoding="utf-8")
        or "INSERT INTO {insert.table}" in path.read_text(encoding="utf-8")
    )
    assert writers == [
        "identity_admission_writer.py",
        "identity_evidence_writer.py",
    ], writers
    written = {
        insert.table.removeprefix("operations.")
        for operation in _writer().OPERATIONS.values()
        for insert in operation.inserts
    }
    assert written == set(_document_key_sets())


def test_the_gate_fixture_drives_the_writer_rather_than_its_own_sql() -> None:
    """The E4 phase must prove the production path, not a parallel one.

    While the fixture carried its own INSERT statements, the gate proved the
    commit kernel against rows a test had written, and the absence of a
    production writer was invisible from both ends. The fixture now calls
    app.identity_evidence_writer, so the same phase exercises the real carrier.
    """

    seeder = SEEDER.read_text(encoding="utf-8")
    for table in _document_key_sets():
        assert f"INSERT INTO operations.{table}" not in seeder, table
    assert "from app import identity_evidence_writer" in seeder
    assert "identity_evidence_writer.parse_request" in seeder
    assert "identity_evidence_writer.execute" in seeder
    # All three arms, in the order the schema forces.
    freeze = seeder.index('("freeze", freeze)')
    privacy = seeder.index('("privacy", privacy)')
    cutover = seeder.index('("cutover", cutover)')
    assert freeze < privacy < cutover


def _writer() -> ModuleType:
    core = str(CORE)
    sys.path.insert(0, core)
    try:
        spec = importlib.util.spec_from_file_location(
            "home_agent_e4_evidence_writer",
            CORE / "app/identity_evidence_writer.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(core)


@pytest.mark.parametrize("table", sorted(_document_key_sets()))
def test_the_writer_inserts_exactly_the_signed_keys(table: str) -> None:
    """The writer's column set is the document's key set, for every table.

    This is the property that makes the writer a carrier rather than a mapping
    layer. If either side drifts, it must fail here.
    """

    writer = _writer()
    inserts = {
        insert.table.removeprefix("operations."): insert
        for operation in writer.OPERATIONS.values()
        for insert in operation.inserts
    }
    assert table in inserts, table
    columns = {name for name, _ in inserts[table].columns}
    assert columns == set(_document_key_sets()[table]), {
        "only_in_writer": sorted(columns - set(_document_key_sets()[table])),
        "only_in_document": sorted(set(_document_key_sets()[table]) - columns),
    }


def test_the_writer_expects_the_packets_the_ceremony_signs() -> None:
    """Each arm's packet key set is the producer's own."""

    writer = _writer()
    freeze = _operator_module("phase3_writer_freeze_evidence")
    privacy = _operator_module("phase3_privacy_cutover_evidence")
    cutover = _operator_module("phase3_semantic_cutover_packet")
    assert writer.OPERATIONS["freeze"].packet_keys == freeze.PACKET_KEYS
    assert writer.OPERATIONS["privacy"].packet_keys == privacy.PACKET_KEYS
    assert writer.OPERATIONS["cutover"].packet_keys == cutover.PACKET_KEYS


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


def _packet(writer, operation_name: str):
    """A minimally well-formed packet for one arm."""

    operation = writer.OPERATIONS[operation_name]
    uid = "018f3f7a-8b4d-7abc-8def-0123456789ab"

    def row(insert):
        values = {}
        for name, convert in insert.columns:
            if convert is writer._uuid_value:
                values[name] = uid
            elif convert is writer._integer_value:
                values[name] = 1
            elif convert is writer._boolean_value:
                values[name] = False
            elif convert is writer._timestamp_value:
                values[name] = "2026-08-25T00:00:00.000000Z"
            else:
                values[name] = "x"
        return values

    packet = {key: "x" for key in operation.packet_keys}
    for insert in operation.inserts:
        packet[insert.document_key] = [row(insert)] if insert.many else row(insert)
    return operation, packet


def _request(writer, operation, packet) -> bytes:
    import base64 as _b64

    raw = json.dumps(packet, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return json.dumps(
        {
            "contract": operation.contract,
            "document_b64": _b64.b64encode(raw).decode("ascii"),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


@pytest.mark.parametrize("arm", ["freeze", "privacy", "cutover"])
def test_writer_accepts_its_own_signed_shape(arm: str) -> None:
    writer = _writer()
    operation, packet = _packet(writer, arm)
    request = writer.parse_request(_request(writer, operation, packet), operation)
    assert request.operation.name == arm
    expected_rows = sum(1 for _ in operation.inserts)
    assert len(request.rows) >= expected_rows
    # Values reach PostgreSQL already typed, never as raw JSON strings.
    for insert, row in request.rows:
        assert set(row) == {name for name, _ in insert.columns}
        for name, convert in insert.columns:
            if convert is writer._uuid_value:
                assert isinstance(row[name], uuid.UUID)
            elif convert is writer._timestamp_value:
                assert isinstance(row[name], datetime)


@pytest.mark.parametrize("arm", ["freeze", "privacy", "cutover"])
def test_writer_refuses_a_document_that_is_not_the_signed_shape(arm: str) -> None:
    writer = _writer()
    operation, packet = _packet(writer, arm)
    insert = operation.inserts[0]
    column = insert.columns[0][0]

    def mutate(change):
        broken = json.loads(json.dumps(packet))
        target = broken[insert.document_key]
        target = target[0] if insert.many else target
        change(target)
        return broken

    for broken in (
        mutate(lambda row: row.pop(column)),
        mutate(lambda row: row.update({"unexpected": "x"})),
        mutate(lambda row: row.update({column: None})),
        {**packet, "contract": "x", "unexpected_packet_key": "x"},
    ):
        with pytest.raises(writer.EvidenceWriterError):
            writer.parse_request(_request(writer, operation, broken), operation)

    # A wrong contract on the envelope is refused before the document is read.
    other = writer.OPERATIONS["privacy" if arm != "privacy" else "freeze"]
    with pytest.raises(writer.EvidenceWriterError):
        writer.parse_request(_request(writer, other, packet), operation)


def test_writer_connects_only_as_the_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _writer()
    password = "b" * 64
    good = f"postgresql+psycopg://home_agent_owner:{password}@postgres:5432/home_agent"
    monkeypatch.setenv("HOME_AGENT_DATABASE_URL", good)
    assert writer.database_url() == good
    for bad in (
        "",
        f"postgresql+psycopg://home_agent_identity_migration:{password}@postgres:5432/home_agent",
        f"postgresql+psycopg://home_agent_owner:{password}@postgres:5432/other",
        f"postgresql://home_agent_owner:{password}@postgres:5432/home_agent",
    ):
        monkeypatch.setenv("HOME_AGENT_DATABASE_URL", bad)
        with pytest.raises(writer.EvidenceWriterError):
            writer.database_url()


def test_writer_is_reachable_only_through_fixed_image_arms() -> None:
    entrypoint = (CORE / "docker-entrypoint.sh").read_text(encoding="utf-8")
    for arm, operation in (
        ("identity-evidence-freeze", "freeze"),
        ("identity-evidence-privacy", "privacy"),
        ("identity-evidence-cutover", "cutover"),
    ):
        assert f"{arm})" in entrypoint
        assert f"exec python -m app.identity_evidence_writer {operation}" in entrypoint


def test_writer_replay_is_verified_rather_than_assumed() -> None:
    source = (CORE / "app/identity_evidence_writer.py").read_text(encoding="utf-8")
    # A replayed step must find exactly the row it meant to write; a different
    # document reusing an identity must not be mistaken for success.
    assert "ON CONFLICT DO NOTHING" in source
    assert "dict(stored) != row" in source
    assert 'isolation_level="SERIALIZABLE"' in source
    assert "hide_parameters=True" in source
    assert "print(error" not in source
    assert "str(error)" not in source
