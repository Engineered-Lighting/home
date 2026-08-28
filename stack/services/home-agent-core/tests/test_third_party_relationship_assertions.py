"""Asserting about two other people is a weaker claim, and must record as one.

E5k's attester was always one end of the edge, asserting about their own life.
A third-party assertion has two people who are not the attester and neither of
whom consented. The authority axis says who had the standing; assertion_scope
says how close they stood. Flattening the two would make it impossible to find
third-party claims later without re-deriving the graph.
"""

from __future__ import annotations

import pathlib

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0026_third_party_relationship_assertions.py"
)


def _sql() -> str:
    """upgrade()'s executable SQL, comments stripped and f-string constants
    resolved.

    Both steps matter and both were learned by writing tests that passed for
    the wrong reason: an assertion matching a comment grades the prose, and one
    matching "{CALLER_ROLE}" grades an unresolved placeholder rather than the
    role actually granted.
    """

    import re

    source = MIGRATION.read_text()
    constants = dict(
        re.findall(r'^([A-Z_]+) = \(?\s*"([^"]+)"', source[: source.index("def upgrade")], re.M)
    )
    body = source[source.index("def upgrade"): source.index("def downgrade")]
    body = "\n".join(
        line for line in body.splitlines()
        if not line.lstrip().startswith(("#", "--"))
    )
    for name, value in constants.items():
        body = body.replace("{" + name + "}", value)
    return body


def test_scope_is_derived_not_supplied() -> None:
    """A caller that could label its own assertion 'self' would erase the
    distinction the column exists to record."""

    sql = _sql()
    signature = sql[sql.index("CREATE OR REPLACE FUNCTION"): sql.index("RETURNS uuid")]
    assert "assertion_scope" not in signature
    assert "WHEN e5m_subject = e5m_binding.person_id THEN 'self'" in sql
    assert "ELSE 'third_party'" in sql


def test_scope_is_constrained_to_two_values() -> None:
    assert "assertion_scope IN ('self', 'third_party')" in _sql()


def test_the_default_subject_preserves_the_original_contract() -> None:
    """An omitted subject must still mean 'about me', so existing callers keep
    working and cannot accidentally assert about someone else."""

    sql = _sql()
    assert "target_subject_person_id uuid DEFAULT NULL" in sql
    assert "coalesce(target_subject_person_id," in sql


def test_parent_of_is_not_written_symmetrically() -> None:
    """partner_of is symmetric; parent_of is not. Writing the inverse would
    assert that a child is a parent of their parent."""

    sql = _sql()
    assert "e5m_symmetric := target_predicate = 'partner_of'" in sql
    assert "CASE WHEN e5m_symmetric THEN 2 ELSE 1 END" in sql
    assert "WHERE e5m_symmetric OR edge.ordinal = 0" in sql


def test_edge_count_matches_the_predicate() -> None:
    """The receipt must not claim two edges when one was written."""

    sql = _sql()
    assert "(predicate = 'partner_of' AND edge_count = 2)" in sql
    assert "(predicate = 'parent_of' AND edge_count = 1)" in sql


def test_both_endpoints_are_checked_not_just_the_far_one() -> None:
    """In a third-party assertion neither person is the attester, so a check
    that assumed one end was the account holder would leave a gap."""

    sql = _sql()
    assert "person.person_id IN (e5m_subject, target_partner_person_id)" in sql
    assert ") <> 2 THEN" in sql
    assert "privacy.identity_person_is_blocked(e5m_subject)" in sql


def test_the_perspective_stays_the_attester() -> None:
    """These are the owner's beliefs about the household, not impersonal truth,
    so the perspective principal must remain theirs."""

    assert "e5m_binding.principal_id" in _sql()


def test_the_guards_survive_the_replacement() -> None:
    """CREATE OR REPLACE rewrites the whole body: every guard has to be
    restated, and dropping one would be invisible in the diff."""

    sql = _sql()
    for guard in (
        "owner_partner_e5k_role_invalid",
        "owner_partner_e5k_transaction_invalid",
        "owner_partner_e5k_identifiers_invalid",
        "owner_partner_e5k_digest_invalid",
        "owner_partner_e5k_binding_missing",
        "owner_partner_e5k_reflexive",
        "owner_partner_e5k_privacy_blocked",
        "owner_partner_e5k_already_recorded",
        "lock_identity_semantic_write_fence",
    ):
        assert guard in sql, f"replacement dropped guard: {guard}"


def test_replay_still_returns_without_writing() -> None:
    sql = _sql()
    body = sql[sql.index("SELECT receipt.receipt_id"): sql.index("RETURN e5m_existing;")]
    for mutating in ("INSERT ", "UPDATE ", "DELETE "):
        assert mutating not in body


def test_the_new_signature_is_granted_to_the_committer() -> None:
    """A changed signature is a different function: without a new GRANT the
    kernel would be unreachable while the migration reported success."""

    sql = _sql()
    assert "GRANT EXECUTE ON FUNCTION" in sql
    assert "uuid, uuid, text) TO home_agent_binding_committer" in sql
