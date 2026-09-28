"""Offline frozen-kernel checks; actual PostgreSQL execution remains required."""
import importlib.util
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "alembic/versions/0033_shared_auth_proof_ingress.py"


def migration():
    spec = importlib.util.spec_from_file_location("shared_proof_ingress_migration", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def commands(monkeypatch, operation):
    module = migration()
    statements = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    getattr(module, operation)()
    return module, statements


def test_revision_and_frozen_signature_are_explicit():
    module = migration()
    assert module.revision == "0033_shared_auth_proof_v1"
    assert module.down_revision == "0032_shared_identity_v1"
    assert module.SIGNATURE == "uuid, text, text, text, timestamptz, bigint"
    assert "from app" not in PATH.read_text(encoding="utf-8")
    for parameter in ("p_proof_id uuid", "p_subject text", "p_session_commitment text",
                      "p_challenge_commitment text", "p_authenticated_at timestamptz",
                      "p_registration_revision bigint"):
        assert parameter in module.BODY


def test_roles_start_dormant_and_preexisting_roles_are_not_trusted(monkeypatch):
    module, statements = commands(monkeypatch, "upgrade")
    assert "shared_proof_role_collision" in statements[0]
    assert "current_user <> 'home_agent_owner'" in statements[0]
    assert "session_user <> 'home_agent_owner'" in statements[0]
    for role in module.ROLES:
        statement = next(s for s in statements if s.startswith(f"CREATE ROLE {role} "))
        for restriction in ("NOLOGIN", "NOINHERIT", "NOSUPERUSER", "NOBYPASSRLS",
                            "NOCREATEROLE", "NOCREATEDB", "NOREPLICATION", "CONNECTION LIMIT 0"):
            assert restriction in statement
    assert not any("GRANT EXECUTE" in s for s in statements)
    assert "aclexplode(p.proacl)" in "\n".join(statements)
    assert "REVOKE ALL ON FUNCTION" in "\n".join(statements)


def test_provenance_column_has_no_invented_backfill_or_default(monkeypatch):
    _, statements = commands(monkeypatch, "upgrade")
    column = next(s for s in statements if "ADD COLUMN registration_revision" in s)
    assert "registration_revision IS NULL OR registration_revision > 0" in column
    assert "DEFAULT" not in column and "NOT NULL" not in column
    assert not any(s.strip().startswith("UPDATE ") for s in statements)


def test_issuer_is_bound_to_session_identity_not_a_function_argument():
    body = migration().BODY
    assert "CASE session_user" in body
    assert "WHEN 'home_agent_shared_echo_proof_ingress' THEN 'home-assistant:echo'" in body
    assert "WHEN 'home_agent_shared_victoria_proof_ingress' THEN 'home-assistant:victoria'" in body
    assert "p_issuer" not in body
    assert "pg_catalog.pg_auth_members" in body
    assert "shared_proof_role_boundary_invalid" in body
    assert "SECURITY DEFINER" in body and "SET search_path = pg_catalog" in body
    assert "SET row_security = on" in body


def test_transaction_revision_and_current_registration_gate_every_replay():
    body = migration().BODY
    replay = body.index("SELECT p.* INTO v_existing")
    for guard in ("transaction_isolation", "shared_proof_schema_revision_invalid",
                  "i.state = 'active'", "i.registration_revision = p_registration_revision",
                  "pg_catalog.clock_timestamp()", "NOT pg_catalog.isfinite(p_authenticated_at)",
                  "p_authenticated_at + interval '5 minutes' <= v_now"):
        assert guard in body[:replay]
    assert "'serializable'" in body
    assert "transaction_timestamp" not in body
    assert body.index("pg_current_xact_id_if_assigned") < body.index("FOR SHARE")
    assert body.index("FOR SHARE") < body.rindex("pg_catalog.clock_timestamp()") < replay


def test_replay_is_exact_unconsumed_unexpired_and_never_upserts():
    body = migration().BODY
    for field in ("proof_id", "issuer_id", "subject", "session_commitment",
                  "challenge_commitment", "authenticated_at", "registration_revision"):
        assert f"v_existing.{field} IS DISTINCT FROM" in body
    assert "v_existing.consumed_at IS NOT NULL" in body
    assert "v_existing.expires_at <= v_now" in body
    assert "shared_proof_replay_conflict" in body
    assert "ON CONFLICT" not in body and "UPDATE identity." not in body
    assert "INSERT INTO identity.shared_auth_proofs" in body
    for forbidden in ("shared_owner_links", "shared_source_grants", "ha_user_bindings"):
        assert forbidden not in body


def test_table_authority_is_select_insert_and_narrow_registration_lock(monkeypatch):
    module, statements = commands(monkeypatch, "upgrade")
    grants = [s for s in statements if s.startswith("GRANT ")]
    assert not any("DELETE" in s or "ALL" in s for s in grants)
    updates = [s for s in grants if "UPDATE" in s]
    assert updates == [f"GRANT UPDATE (registration_revision) ON identity.shared_issuers TO {module.KERNEL_ROLE};"]
    insert = next(s for s in grants if s.startswith("GRANT INSERT"))
    assert "consumed_at" not in insert
    assert module.KERNEL_ROLE in insert
    policies = [s for s in statements if s.startswith("CREATE POLICY")]
    assert len(policies) == 4
    assert all("CASE session_user" in s and f"TO {module.KERNEL_ROLE}" in s for s in policies)
    assert "registration_revision IS NOT NULL AND consumed_at IS NULL" in policies[-1]
    assert "WITH CHECK (false)" in policies[-2]
    create = statements.index(f"GRANT CREATE ON SCHEMA identity TO {module.KERNEL_ROLE};")
    transfer = statements.index(f"ALTER FUNCTION {module.FUNCTION}({module.SIGNATURE}) OWNER TO {module.KERNEL_ROLE};")
    revoke = statements.index(f"REVOKE CREATE ON SCHEMA identity FROM {module.KERNEL_ROLE};")
    assert create < transfer < revoke


def test_downgrade_checks_all_proof_history_before_removing_provenance(monkeypatch):
    module, statements = commands(monkeypatch, "downgrade")
    assert "shared_proof_migration_authority_invalid" in statements[0]
    assert statements[1] == "SET LOCAL row_security = off;"
    assert "ACCESS EXCLUSIVE" in statements[2]
    assert "EXISTS (SELECT 1 FROM identity.shared_auth_proofs)" in statements[3]
    assert "shared_proof_downgrade_requires_empty_storage" in statements[3]
    assert "DROP COLUMN registration_revision" in "\n".join(statements[4:])
    assert "CASCADE" not in "\n".join(statements)
    assert not any(s.startswith("DELETE ") for s in statements)
    for role in module.ROLES:
        assert f"DROP ROLE {role};" in statements
