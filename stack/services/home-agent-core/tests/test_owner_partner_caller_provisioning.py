"""Who may call the owner-attested partner kernel, and who must never.

Verified against the live deployment for the equivalent parent kernel:

    home_agent_api                        EXECUTE = false
    home_agent_binding_committer          EXECUTE = true
    home_agent_parent_relationship_kernel EXECUTE = false   (owns it only)

That shape is the point of having a kernel at all. The runtime API role holds no
identity write privilege, so a compromised API cannot write a fact even though
it can read one; writing goes through a separate credential.
"""

from __future__ import annotations

import pathlib

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0025_owner_partner_caller_provisioning.py"
)


def _constants() -> dict[str, str]:
    """The migration substitutes role names via f-strings, so the literals live
    in module constants rather than in the SQL. Resolve them, or an assertion
    about which role is granted would pass on a placeholder."""

    import re

    source = MIGRATION.read_text()
    header = source[: source.index("def upgrade")]
    return dict(re.findall(r'^([A-Z_]+) = \(?\s*"([^"]+)"', header, re.M))


def _sql() -> str:
    """The executable SQL of upgrade(), with placeholders resolved and comments
    stripped, so assertions grade the code rather than the prose."""

    source = MIGRATION.read_text()
    body = source[source.index("def upgrade"): source.index("def downgrade")]
    body = "\n".join(
        line for line in body.splitlines()
        if not line.lstrip().startswith(("#", "--"))
    )
    for name, value in _constants().items():
        body = body.replace("{" + name + "}", value)
    return body


def test_only_the_committer_is_granted_execute() -> None:
    sql = _sql()
    assert "GRANT EXECUTE ON FUNCTION" in sql
    assert "home_agent_binding_committer" in sql
    assert "home_agent_api" not in sql, (
        "the runtime API role must never gain a path to write a fact"
    )


def test_public_execute_is_revoked_before_the_grant() -> None:
    """PostgreSQL grants EXECUTE to PUBLIC on new functions by default."""

    sql = _sql()
    revoke = sql.index("REVOKE ALL ON FUNCTION")
    grant = sql.index("GRANT EXECUTE ON FUNCTION")
    assert revoke < grant
    assert "FROM PUBLIC" in sql


def test_it_refuses_to_provision_a_caller_that_can_set_role() -> None:
    """The kernel refuses a caller who can SET ROLE into it, so granting
    membership would make it permanently unreachable rather than more
    permissive. Fail loudly instead of provisioning something unusable."""

    sql = _sql()
    assert "owner_partner_e5l_caller_is_kernel_member" in sql
    assert "pg_has_role(" in sql


def test_it_refuses_when_the_caller_role_is_absent() -> None:
    assert "owner_partner_e5l_caller_absent" in _sql()


def test_the_signature_matches_the_kernel_exactly() -> None:
    """A signature drift would silently grant on nothing, leaving the kernel
    unreachable while the migration reports success."""

    kernel = (
        MIGRATION.parent / "0024_owner_partner_commit_kernel.py"
    ).read_text()
    provisioning = MIGRATION.read_text()
    signature = provisioning[
        provisioning.index("SIGNATURE = ("): provisioning.index("def upgrade")
    ]
    arity = signature.count("uuid") + signature.count("text")
    kernel_signature = kernel[
        kernel.index("CREATE FUNCTION identity.commit_owner_partner"):
        kernel.index("RETURNS uuid")
    ]
    kernel_arity = kernel_signature.count("uuid") + kernel_signature.count("text")
    assert arity == kernel_arity, (
        f"provisioning grants on {arity} params, kernel declares {kernel_arity}"
    )


def test_downgrade_revokes_rather_than_dropping() -> None:
    source = MIGRATION.read_text()
    down = source[source.index("def downgrade"):]
    assert "REVOKE EXECUTE" in down
    assert "DROP FUNCTION" not in down, (
        "provisioning must be reversible without destroying the kernel"
    )
