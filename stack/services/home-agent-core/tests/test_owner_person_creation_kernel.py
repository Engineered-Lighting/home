"""Creating a person and deciding their privacy state are one act.

The legacy per-item import was retired and `store.create_person` left orphaned.
Reading what it did explains why: it INSERTed into identity.people and stopped,
so a person could exist with no auditable provenance and no privacy state
decided, and how the system may treat them was deferred to whoever wrote the
next row.

These tests pin the properties that make this kernel a different thing.
"""

from __future__ import annotations

import pathlib

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0027_owner_person_creation_kernel.py"
)


def _sql() -> str:
    """upgrade()'s executable SQL: comments stripped, f-string constants
    resolved. Both steps were learned from tests that passed for the wrong
    reason -- one matching a comment, one matching an unresolved placeholder."""

    import re

    source = MIGRATION.read_text()
    header = source[: source.index("def upgrade")]
    constants = dict(re.findall(r'^([A-Z_]+) = \(?\s*"([^"]+)"', header, re.M))
    body = source[source.index("def upgrade"): source.index("def downgrade")]
    body = "\n".join(
        line for line in body.splitlines()
        if not line.lstrip().startswith(("#", "--"))
    )
    for name, value in constants.items():
        body = body.replace("{" + name + "}", value)
    return body


def test_status_is_a_literal_not_a_parameter() -> None:
    """Nobody may be created already erased, or in a state no code expects."""

    sql = _sql()
    signature = sql[sql.index("CREATE FUNCTION"): sql.index("RETURNS uuid")]
    assert "target_status" not in signature
    assert "'active', target_privacy_scope" in sql


def test_an_owner_created_person_cannot_look_like_a_reviewed_import() -> None:
    """The reviewed-import path had a verifier this one does not, so claiming
    its provenance would launder an unverified person into a verified one."""

    assert "legacy_source" not in _sql()


def test_provenance_is_written_in_the_same_transaction() -> None:
    sql = _sql()
    assert "INSERT INTO privacy.artifact_registry" in sql
    assert "'owner_person_attestation'" in sql
    artifact = sql.index("INSERT INTO privacy.artifact_registry")
    person = sql.index("INSERT INTO identity.people")
    assert artifact < person, "the artifact must exist before it is referenced"


def test_auto_expire_must_carry_its_schedule() -> None:
    """An auto-expiring person with no expiry never expires, which is the
    opposite of what was asked for."""

    sql = _sql()
    assert "owner_person_e5n_expiry_missing" in sql
    assert "owner_person_e5n_expiry_unexpected" in sql
    assert "target_directive = 'auto_expire'" in sql


def test_the_directive_vocabulary_is_closed() -> None:
    sql = _sql()
    for directive in ("do_not_track", "ignored", "silent", "private", "auto_expire"):
        assert f"'{directive}'" in sql
    assert "owner_person_e5n_directive_invalid" in sql


def test_a_blank_display_name_is_refused() -> None:
    """The name is how a human recognises this person in a later confirmation;
    whitespace is not a name."""

    sql = _sql()
    assert "owner_person_e5n_display_name_invalid" in sql
    assert "btrim(target_display_name) = ''" in sql
    assert "btrim(target_display_name)" in sql, "the stored name must be trimmed"


def test_it_creates_no_principal_and_no_binding() -> None:
    """Being known to the household is not having an account. Conflating them
    is how someone ends up with authority nobody granted."""

    sql = _sql()
    assert "identity.principals" not in sql
    assert "ha_user_bindings" in sql, "it still reads the attester's binding"
    assert "INSERT INTO identity.ha_user_bindings" not in sql


def test_the_caller_cannot_set_role_into_the_kernel() -> None:
    sql = _sql()
    assert "owner_person_e5n_role_invalid" in sql
    assert "pg_has_role(" in sql


def test_it_runs_serializable_and_takes_the_fence_before_writing() -> None:
    sql = _sql()
    assert "<> 'serializable'" in sql
    fence = sql.index("lock_identity_semantic_write_fence")
    first_write = sql.index("INSERT INTO privacy.artifact_registry")
    assert fence < first_write


def test_replay_returns_the_row_without_repairing_it() -> None:
    sql = _sql()
    assert "RETURN e5n_existing;" in sql
    window = sql[sql.index("SELECT person.person_id"): sql.index("RETURN e5n_existing;")]
    for mutating in ("INSERT ", "UPDATE ", "DELETE "):
        assert mutating not in window


def test_the_artifact_widening_is_column_scoped_and_reversible() -> None:
    """has_table_privilege cannot see column grants and reported false for
    roles that hold them, so this was verified at column level. The widening is
    deliberate but must not outlive the feature."""

    source = MIGRATION.read_text()
    up = source[source.index("def upgrade"): source.index("def downgrade")]
    down = source[source.index("def downgrade"):]
    assert "artifact_registry TO" in up
    assert "artifact_registry FROM" in down, "downgrade must revoke it"
    # Column-scoped, not a blanket table privilege.
    assert "artifact_id, artifact_kind, store" in up


def test_the_erasure_interlock_covers_the_attester() -> None:
    assert "owner_person_e5n_attester_blocked" in _sql()
