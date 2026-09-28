"""Offline migration guards; actual PostgreSQL permissions need the pinned gate."""
import importlib.util
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "alembic/versions/0047_personal_preference_authority.py"


def load():
    spec = importlib.util.spec_from_file_location(PATH.stem, PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def commands(monkeypatch, operation):
    module = load()
    statements = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    getattr(module, operation)()
    return module, statements


def test_existing_identity_functions_change_only_the_revision_guard():
    module = load()
    old = module.previous()
    assert module.down_revision == old.revision
    assert len(module.revision) <= 32
    wrappers = module.wrappers(old)
    assert len(wrappers) == 8
    for wrapper, sql in wrappers:
        advanced = module.advance(sql)
        assert advanced.replace(
            f"a.version_num='{module.revision}'", f"a.version_num='{old.revision}'"
        ) == sql
    assert wrappers[-1] == (old.KERNEL, old.BODY)


def test_existing_functions_are_verified_before_any_mutation(monkeypatch):
    module, statements = commands(monkeypatch, "upgrade")
    assert "shared_link_combined_migration_authority_invalid" in statements[0]
    assert all("shared_link_combined_wrapper_drift" in sql for sql in statements[1:9])
    assert statements[9].startswith("CREATE ROLE ")
    assert module.previous().body_source(module.BODY) in statements[-1]


def test_new_function_is_dormant_and_cannot_write_identity(monkeypatch):
    module, statements = commands(monkeypatch, "upgrade")
    assert len([s for s in statements if s.startswith("CREATE ROLE ")]) == 1
    role = statements[9]
    for flag in ("NOLOGIN", "NOINHERIT", "NOSUPERUSER", "NOBYPASSRLS", "CONNECTION LIMIT 0"):
        assert flag in role
    assert not any(s.startswith(("GRANT INSERT", "GRANT DELETE")) for s in statements)
    assert not any(s.startswith("GRANT EXECUTE") and module.FUNCTION in s for s in statements)
    assert not any("FOR UPDATE TO" in s or "FOR INSERT TO" in s for s in statements)
    assert not any(word in module.BODY for word in ("INSERT INTO", "UPDATE identity.", "DELETE FROM"))
    assert f"REVOKE ALL ON FUNCTION {module.FUNCTION}({module.SIGNATURE}) FROM PUBLIC;" in statements
    assert any("aclexplode(p.proacl)" in s and "a.grantee<>p.proowner" in s for s in statements)


def test_authority_requires_fixed_source_both_homes_and_live_session():
    body = load().BODY
    for required in (
        "transaction_isolation')<>'serializable'", "pg_is_in_recovery()",
        "privacy.lock_identity_semantic_write_fence()", "b.issuer_id=p_issuer AND b.subject=p_subject",
        "bindings<>2", "privacy.identity_person_is_blocked(anchor.person_id)",
        "r.issuer_id=p_issuer AND r.session_commitment=p_session",
        "g.source_id='core.personal-preferences.v1'", "g.site_id IN ('echo','victoria')",
        "s.registration_revision=g.source_revision", "g.authorization_generation=anchor.authorization_generation",
        "FOR SHARE OF b,l,p,person", "FOR SHARE OF b,i", "FOR SHARE OF g,s,i",
        "grants<>CASE WHEN p_write THEN 4 ELSE 2 END", "interval '60 seconds'",
    ):
        assert required in body
    # The role has only named-column reads; wildcard expansion would fail or
    # silently require broader permission when the table gains a column.
    assert "SELECT l.*" not in body
    assert "SELECT *" not in body


def test_downgrade_verifies_new_kernel_before_removal_and_only_revokes_owned_grants(monkeypatch):
    module, statements = commands(monkeypatch, "downgrade")
    assert all("shared_link_combined_wrapper_drift" in s for s in statements[1:10])
    assert module.previous().body_source(module.BODY) in statements[9]
    assert statements[10] == f"SET LOCAL ROLE {module.ROLE};"
    assert not any(word in s for s in statements for word in (
        "DROP OWNED", "DROP TABLE", "DELETE FROM", "TRUNCATE", "CASCADE"
    ))
    for table, columns in module.READS.items():
        assert f"REVOKE SELECT ({columns}) ON {table} FROM {module.ROLE};" in statements
    for table, column in module.LOCK_COLUMNS.items():
        assert f"REVOKE UPDATE ({column}) ON {table} FROM {module.ROLE};" in statements
    for helper in module.HELPERS:
        assert f"REVOKE EXECUTE ON FUNCTION {helper} FROM {module.ROLE};" in statements


def test_revision_rewrite_refuses_missing_or_ambiguous_guard():
    import pytest
    module = load()
    with pytest.raises(ValueError):
        module.advance("SELECT 1")
    with pytest.raises(ValueError):
        module.advance(f"a.version_num='{module.down_revision}' " * 2)
