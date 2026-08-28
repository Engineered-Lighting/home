"""The owner-attested partner kernel must not quietly become a confirmation.

parent_of was committed by E5f with authority 'explicit_related_party' because a
second party confirmed it in a browser. A partnership the owner asserts alone is
a weaker claim. The whole value of the authority axis is that the difference
survives in the record, so these tests pin it.
"""

from __future__ import annotations

import pathlib

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0024_owner_partner_commit_kernel.py"
)


def _source() -> str:
    return MIGRATION.read_text()


def _sql() -> str:
    """The executable SQL only.

    Matching the whole file would let the module docstring and the explanatory
    comments satisfy assertions about the code, which is exactly backwards: a
    comment saying "deliberately no GRANT EXECUTE" must not make a test for
    GRANT EXECUTE pass.
    """

    source = _source()
    body = source[source.index("def upgrade"): source.index("def downgrade")]
    return "\n".join(
        line for line in body.splitlines()
        if not line.lstrip().startswith(("#", "--"))
    )


def test_owner_assertions_are_recorded_as_administrator_authority() -> None:
    sql = _sql()
    assert "'authorized_administrator'" in sql
    # Never as a confirmation that did not happen.
    assert "'explicit_related_party'" not in sql, (
        "an owner assertion recorded as explicit_related_party forges a "
        "second-party confirmation"
    )


def test_the_authority_is_a_literal_not_a_parameter() -> None:
    """If it were a parameter, a caller could choose its own provenance."""

    source = _source()
    signature = source[source.index("CREATE FUNCTION"): source.index("RETURNS uuid")]
    assert "authority" not in signature, (
        "authority must not be caller-supplied"
    )


def test_the_caller_cannot_set_role_into_the_kernel() -> None:
    source = _source()
    assert "session_user <> '{CALLER_ROLE}'" in source
    assert "current_user <> '{KERNEL_ROLE}'" in source
    assert "pg_catalog.pg_has_role(" in source, (
        "a caller able to SET ROLE into the kernel bypasses every check"
    )


def test_it_runs_serializable_and_takes_the_write_fence() -> None:
    source = _source()
    assert "transaction_isolation'\n               <> 'serializable'" in source \
        or "<> 'serializable'" in source
    assert "privacy.lock_identity_semantic_write_fence()" in source
    fence = source.index("lock_identity_semantic_write_fence")
    first_insert = source.index("INSERT INTO knowledge.")
    assert fence < first_insert, (
        "the fence must be taken before the first application write"
    )


def test_erasure_and_directives_are_checked_after_the_fence() -> None:
    """Checking before the fence would let a concurrent erasure slip behind."""

    source = _source()
    fence = source.index("lock_identity_semantic_write_fence")
    blocked = source.index("privacy.identity_person_is_blocked")
    assert fence < blocked
    assert "directive.enabled" in source


def test_replay_returns_the_same_receipt_and_never_repairs() -> None:
    source = _source()
    assert "RETURN e5k_existing;" in source
    body = source[source.index("SELECT receipt.receipt_id"): source.index("RETURN e5k_existing;")]
    for mutating in ("UPDATE ", "INSERT ", "DELETE "):
        assert mutating not in body, (
            f"replay path contains {mutating.strip()}: replay must be an "
            "exact-match proof, never a repair"
        )


def test_both_directions_or_neither() -> None:
    """A half-recorded partnership satisfies the uniqueness index while being
    false one way round."""

    source = _source()
    assert "FOR e5k_index IN 1..2 LOOP" in source
    assert "edge_count = 2" in source
    assert "owner_partner_e5k_edges_invalid" in source


def test_it_refuses_a_reflexive_or_third_party_assertion() -> None:
    source = _source()
    assert "owner_partner_e5k_reflexive" in source
    # The subject is the bound account holder; speaking for two other people is
    # a different contract and is not this revision.
    assert "e5k_binding.person_id = target_partner_person_id" in source
    assert "binding.revoked_at IS NULL" in source


def test_the_kernel_is_dormant() -> None:
    """It must not become reachable as a side effect of the migration."""

    sql = _sql()
    assert "GRANT EXECUTE" not in sql, (
        "the kernel must stay unreachable until a reviewed change provisions "
        "its caller, as E5d left E5f dormant"
    )
    assert "NOLOGIN VALID UNTIL '1970-01-01'" in _sql()


def test_receipts_are_a_separate_ledger_from_confirmed_ones() -> None:
    source = _source()
    assert "operations.partner_relationship_authority_receipts" in source
    assert "'owner-partner-attestation-v1'" in source
    # Owner-attested receipts must not be written into E5f's confirmed table.
    assert "INSERT INTO operations.parent_relationship_authority_receipts" \
        not in source


def test_receipts_are_owner_only_at_the_row_level() -> None:
    source = _source()
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "session_user = 'home_agent_owner'" in source


def test_downgrade_drops_in_dependency_order() -> None:
    source = _source()
    down = source[source.index("def downgrade"):]
    edges = down.index("receipt_edges")
    receipts = down.index("authority_receipts;")
    assert edges < receipts, "edges reference receipts and must drop first"


def _insert_columns(sql: str, table: str) -> set[str]:
    """The column list of the kernel's INSERT into `table`."""

    start = sql.index(f"INSERT INTO {table} (")
    columns = sql[sql.index("(", start) + 1: sql.index(")", start)]
    return {c.strip() for c in columns.replace("\n", " ").split(",") if c.strip()}


def _not_null_columns(table: str) -> set[str]:
    """Columns schema.py declares NOT NULL with no server default.

    A server default satisfies the constraint without the kernel naming the
    column, so those are excluded; anything else must be supplied explicitly.
    """

    import re

    schema = (
        pathlib.Path(__file__).resolve().parents[1] / "app" / "schema.py"
    ).read_text()
    block = schema[schema.index(f'{table} = Table('):]
    block = block[: block.index("\n)\n")]
    required = set()
    for match in re.finditer(r'Column\(\s*"([a-z_]+)"(.*?)\n    \)', block, re.S):
        name, rest = match.group(1), match.group(2)
        if "nullable=False" in rest and "server_default" not in rest:
            required.add(name)
    for match in re.finditer(r'Column\("([a-z_]+)"[^\n]*nullable=False[^\n]*\)', block):
        line = match.group(0)
        if "server_default" not in line:
            required.add(match.group(1))
    return required


def test_kernel_supplies_every_required_memory_transaction_column() -> None:
    """Two real defects were shipped past review by not checking this:
    candidate, preview and policy_version are NOT NULL and were omitted, so the
    kernel would have failed at runtime on its first real call."""

    supplied = _insert_columns(_sql(), "knowledge.memory_transactions")
    required = _not_null_columns("memory_transactions")
    missing = required - supplied - {"transaction_id"}
    assert not missing, f"kernel omits NOT NULL columns: {sorted(missing)}"


def test_kernel_supplies_every_required_fact_support_column() -> None:
    """artifact_id is NOT NULL, and an unprovenanced support row would leave an
    owner-asserted fact with no root to point at."""

    supplied = _insert_columns(_sql(), "knowledge.fact_support")
    required = _not_null_columns("fact_support")
    missing = required - supplied
    assert not missing, f"kernel omits NOT NULL columns: {sorted(missing)}"
    assert "NULL, NULL," not in _sql()[
        _sql().index("INSERT INTO knowledge.fact_support"):
    ][:400], "artifact_id must be a real artifact, not NULL"


def test_the_attestation_is_registered_as_an_artifact() -> None:
    sql = _sql()
    assert "INSERT INTO privacy.artifact_registry" in sql
    assert "'owner_attestation'" in sql
    registry = sql.index("INSERT INTO privacy.artifact_registry")
    support = sql.index("INSERT INTO knowledge.fact_support")
    assert registry < support, "the artifact must exist before it is referenced"
