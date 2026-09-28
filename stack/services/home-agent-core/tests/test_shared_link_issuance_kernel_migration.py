"""Offline containment contracts; PostgreSQL acceptance remains separate."""
import importlib.util
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "alembic/versions/0040_shared_link_issuance_kernel.py"


def migration():
    spec = importlib.util.spec_from_file_location("shared_link_issuance_kernel_migration", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def commands(monkeypatch, operation):
    module = migration()
    statements = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    getattr(module, operation)()
    return module, statements


def test_frozen_signature_delegates_every_argument_without_new_authority():
    module = migration()
    assert module.revision == "0040_shared_link_issue_kernel_v1"
    assert module.down_revision == "0039_shared_link_issue_v1"
    assert module.FUNCTION == "identity.issue_shared_link_ceremony_v1"
    assert module.SIGNATURE == "uuid,uuid,uuid,uuid,text,text,text,text,text,uuid,uuid,text,text,text,text"
    assert "from app" not in PATH.read_text(encoding="utf-8")
    assert "RETURN QUERY SELECT issued.* FROM identity.begin_shared_link_v1(" in module.BODY
    for parameter in ("p_ceremony", "p_principal", "p_person", "p_binding", "p_echo_subject",
                      "p_owner", "p_request", "p_echo_session", "p_victoria_session",
                      "p_echo_child", "p_victoria_child", "p_echo_challenge", "p_victoria_challenge",
                      "p_key_id", "p_key_fingerprint"):
        assert module.BODY.count(parameter) == 2
    assert "INSERT INTO" not in module.BODY and "UPDATE identity." not in module.BODY


def test_roles_are_dormant_and_collisions_abort_before_creation(monkeypatch):
    module, statements = commands(monkeypatch, "upgrade")
    assert "shared_issuance_kernel_migration_authority_invalid" in statements[0]
    assert "shared_issuance_kernel_role_collision" in statements[1]
    for role in (module.KERNEL_ROLE, module.COORDINATOR_ROLE):
        creation = next(s for s in statements if s.startswith(f"CREATE ROLE {role} "))
        for flag in ("NOLOGIN", "NOINHERIT", "NOSUPERUSER", "NOBYPASSRLS", "NOCREATEDB",
                     "NOCREATEROLE", "NOREPLICATION", "CONNECTION LIMIT 0", "1970-01-01"):
            assert flag in creation
    assert not any(s.startswith("GRANT ") and f"TO {module.COORDINATOR_ROLE}" in s for s in statements)
    assert not any(s.startswith("GRANT EXECUTE") and module.FUNCTION in s for s in statements)


def test_role_guard_rejects_impersonation_memberships_and_privileged_flags():
    module = migration()
    guard = module.BODY.split("RETURN QUERY")[0]
    assert f"session_user<>'{module.COORDINATOR_ROLE}'" in guard
    assert f"current_user<>'{module.KERNEL_ROLE}'" in guard
    for flag in ("rolsuper", "rolbypassrls", "rolinherit", "rolcreatedb", "rolcreaterole", "rolreplication"):
        assert guard.count(f"NOT r.{flag}") == 2
    assert "NOT r.rolcanlogin" in guard
    assert "r.oid IN (m.member,m.roleid)" in guard
    assert "ON r.oid=m.member WHERE r.rolname=current_user" in guard
    assert "member_role.rolname<>'home_agent_owner' OR m.admin_option OR m.inherit_option OR NOT m.set_option" in guard


def test_transaction_and_exact_revision_are_checked_before_dispatch():
    module = migration()
    for fragment in ("SECURITY DEFINER SET search_path=pg_catalog SET row_security=on",
                     "current_setting('transaction_isolation')<>'serializable'",
                     "current_setting('transaction_read_only')<>'off'", "pg_is_in_recovery()",
                     "pg_current_xact_id_if_assigned() IS NOT NULL",
                     "(SELECT count(*) FROM public.alembic_version)<>1",
                     f"a.version_num='{module.revision}'"):
        assert fragment in module.BODY
        assert module.BODY.index(fragment) < module.BODY.index("RETURN QUERY")


def test_negative_evidence_is_visible_without_subject_filter(monkeypatch):
    module, statements = commands(monkeypatch, "upgrade")
    for table in ("identity.edge_privacy_user_blocks", "identity.privacy_directives",
                  "privacy.shared_link_session_revocations"):
        assert f"CREATE POLICY shared_issue_kernel_select ON {table} FOR SELECT TO {module.KERNEL_ROLE} USING ({module.PAIR});" in statements
        assert table not in module.SUPPRESSION
    assert "ha_user_id,person_id" == module.READS["identity.edge_privacy_user_blocks"]


def test_table_writes_are_only_issuance_and_row_lock_columns(monkeypatch):
    module, statements = commands(monkeypatch, "upgrade")
    assert set(module.INSERTS) == {"privacy.shared_owner_generations", "identity.shared_link_ceremonies", "identity.shared_link_challenges"}
    for table, column in module.LOCKS.items():
        assert f"GRANT UPDATE ({column}) ON {table} TO {module.KERNEL_ROLE};" in statements
        assert f"CREATE POLICY shared_issue_kernel_lock ON {table} FOR UPDATE TO {module.KERNEL_ROLE} USING ({module.PAIR}) WITH CHECK (false);" in statements
        assert f"CREATE POLICY shared_issue_kernel_no_update ON {table} AS RESTRICTIVE FOR UPDATE TO {module.KERNEL_ROLE} USING ({module.PAIR}) WITH CHECK (false);" in statements
    for table in module.READS:
        if table != "public.alembic_version":
            assert f"CREATE POLICY shared_issue_kernel_boundary ON {table} AS RESTRICTIVE FOR ALL TO {module.KERNEL_ROLE} USING ({module.PAIR}) WITH CHECK ({module.PAIR});" in statements
    grants = [s for s in statements if s.startswith("GRANT ")]
    assert not any("DELETE" in s or "TRUNCATE" in s or "GRANT ALL" in s for s in grants)
    assert not any("shared_source_grants" in s or "shared_auth_proofs" in s for s in grants)
    for table, columns in module.INSERTS.items():
        assert f"GRANT INSERT ({columns}) ON {table} TO {module.KERNEL_ROLE};" in statements
        assert f"CREATE POLICY shared_issue_kernel_insert ON {table} FOR INSERT TO {module.KERNEL_ROLE} WITH CHECK ({module.PAIR});" in statements


def test_erasure_suppression_and_nested_helper_privileges_are_explicit(monkeypatch):
    module, statements = commands(monkeypatch, "upgrade")
    for table, expression in module.SUPPRESSION.items():
        assert f"CREATE POLICY shared_issue_kernel_erasure ON {table} AS RESTRICTIVE FOR ALL TO {module.KERNEL_ROLE} USING ({expression}) WITH CHECK ({expression});" in statements
    for helper in ("privacy.lock_identity_semantic_write_fence()", "privacy.identity_person_is_blocked(uuid)",
                   "privacy.identity_principal_is_blocked(uuid)", "identity.require_shared_link_session_active_v1(text,text)",
                   "identity.require_shared_link_owner_v1(text,uuid,uuid,uuid)"):
        assert f"GRANT EXECUTE ON FUNCTION {helper} TO {module.KERNEL_ROLE};" in statements
    assert not any("revoke_shared_link_session" in s or "confirm_shared_link" in s for s in statements)


def test_ownership_transfer_and_default_acl_removal_are_ordered(monkeypatch):
    module, statements = commands(monkeypatch, "upgrade")
    temporary = statements.index(f"GRANT CREATE ON SCHEMA identity TO {module.KERNEL_ROLE};")
    transfer = statements.index(f"ALTER FUNCTION {module.FUNCTION}({module.SIGNATURE}) OWNER TO {module.KERNEL_ROLE};")
    revoke = statements.index(f"REVOKE CREATE ON SCHEMA identity FROM {module.KERNEL_ROLE};")
    as_kernel = statements.index(f"SET LOCAL ROLE {module.KERNEL_ROLE};")
    public = statements.index(f"REVOKE ALL ON FUNCTION {module.FUNCTION}({module.SIGNATURE}) FROM PUBLIC;")
    assert temporary < transfer < revoke < as_kernel < public
    assert "aclexplode(p.proacl)" in statements[public + 1]
    assert "a.grantee<>p.proowner" in statements[public + 1]
    assert statements[-1] == "RESET ROLE;"


def test_downgrade_only_removes_new_boundary_and_preserves_history(monkeypatch):
    module, statements = commands(monkeypatch, "downgrade")
    assert f"version_num='{module.revision}'" in statements[0]
    assert "current_user<>'home_agent_owner' OR session_user<>'home_agent_owner'" in statements[0]
    assert statements[1:6] == [f"SET LOCAL ROLE {module.KERNEL_ROLE};",
                               f"DROP FUNCTION {module.FUNCTION}({module.SIGNATURE});",
                               f"DROP FUNCTION {module.LOOKUP_FUNCTION}({module.LOOKUP_SIGNATURE});",
                               f"DROP FUNCTION {module.RECONCILE_FUNCTION}({module.RECONCILE_SIGNATURE});", "RESET ROLE;"]
    combined = "\n".join(statements)
    for forbidden in ("CASCADE", "DROP TABLE", "DELETE FROM", "TRUNCATE", "ALTER TABLE"):
        assert forbidden not in combined
    for table, columns in module.READS.items():
        selected = f"SELECT ({columns})" if columns else "SELECT"
        assert f"REVOKE {selected} ON {table} FROM {module.KERNEL_ROLE};" in statements
    assert statements[-2:] == [f"DROP ROLE {module.COORDINATOR_ROLE};", f"DROP ROLE {module.KERNEL_ROLE};"]


def test_owner_resolver_has_identical_guards_and_complete_lineage_check():
    module = migration()
    assert module.LOOKUP_FUNCTION == "identity.resolve_shared_link_owner_v1"
    assert module.LOOKUP_SIGNATURE == "text"
    body = module.LOOKUP_BODY
    assert module.BOUNDARY_GUARDS in body and module.BOUNDARY_GUARDS in module.BODY
    assert "SECURITY DEFINER SET search_path=pg_catalog SET row_security=on" in body
    assert "RETURNS TABLE(principal_id uuid,person_id uuid,legacy_binding_id uuid)" in body
    assert "INTO STRICT anchor" in body and "WHEN NO_DATA_FOUND OR TOO_MANY_ROWS" in body
    assert "b.ha_user_id=p_echo_subject AND b.revoked_at IS NULL" in body
    assert "p.kind='ha_user' AND p.status='active' AND person.status='active'" in body
    assert body.index("pg_current_xact_id_if_assigned") < body.index("PERFORM privacy.lock_identity_semantic_write_fence()") < body.index("INTO STRICT anchor")
    assert body.index("PERFORM identity.require_shared_link_owner_v1(") < body.index("RETURN QUERY")
    assert "p_echo_subject,anchor.principal_id,anchor.person_id,anchor.binding_id" in body
    assert "INSERT INTO" not in body and "UPDATE identity." not in body


def test_owner_resolver_is_dormant_and_default_acl_recipients_removed(monkeypatch):
    module, statements = commands(monkeypatch, "upgrade")
    signature = f"{module.LOOKUP_FUNCTION}({module.LOOKUP_SIGNATURE})"
    assert module.LOOKUP_BODY in statements
    assert f"ALTER FUNCTION {signature} OWNER TO {module.KERNEL_ROLE};" in statements
    assert f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC;" in statements
    cleanup = next(s for s in statements if "aclexplode" in s and signature in s)
    assert "a.grantee<>p.proowner" in cleanup
    assert not any(s.startswith("GRANT ") and module.LOOKUP_FUNCTION in s for s in statements)


def test_reconciliation_is_inspection_only_and_keeps_original_historical_times():
    module = migration()
    body = module.RECONCILE_BODY
    assert module.RECONCILE_FUNCTION == "identity.inspect_shared_link_issuance_v1"
    assert module.RECONCILE_SIGNATURE == module.SIGNATURE
    assert module.BOUNDARY_GUARDS in body
    for forbidden in ("begin_shared_link_v1", "INSERT INTO", "UPDATE identity.", "UPDATE privacy.", "DELETE FROM", "clock_timestamp"):
        assert forbidden not in body
    assert "EXCEPTION WHEN NO_DATA_FOUND THEN" in body and "WHEN TOO_MANY_ROWS THEN" in body
    assert "-- Absence is unknown, never permission to dispatch again.\n   RETURN;" in body
    assert "prior.created_at,prior.expires_at,echo_revision,victoria_revision" in body
    assert "prior.expires_at<>prior.created_at+interval '5 minutes'" in body


def test_reconciliation_rechecks_complete_authority_and_exact_parent_and_children():
    body = migration().RECONCILE_BODY
    for fragment in ("require_shared_link_owner_v1(p_echo_subject,p_principal,p_person,p_binding)",
                     "require_shared_link_session_active_v1('home-assistant:echo',p_echo_session)",
                     "require_shared_link_session_active_v1('home-assistant:victoria',p_victoria_session)",
                     "admitted.key_id=p_key_id AND admitted.key_fingerprint=p_key_fingerprint",
                     "admitted.state='active'", "generation.state<>'active'", "prior.state<>'pending'",
                     "prior.revision<>1", "prior.ended_at IS NOT NULL",
                     "prior.authorization_generation<>generation.authorization_generation"):
        assert fragment in body
    for field, parameter in (("ceremony_id", "p_ceremony"), ("owner_commitment", "p_owner"),
                             ("request_commitment", "p_request"), ("principal_id", "p_principal"),
                             ("person_id", "p_person"), ("legacy_binding_id", "p_binding"),
                             ("initiating_session_commitment", "p_echo_session")):
        assert f"prior.{field} IS DISTINCT FROM {parameter}" in body
    for site in ("echo", "victoria"):
        assert f"issuer.issuer_id='home-assistant:{site}' AND issuer.state='active' FOR SHARE" in body
        assert f"child.issuer_id='home-assistant:{site}' AND child.challenge_id=p_{site}_child" in body
        assert f"child.session_commitment=p_{site}_session AND child.challenge_commitment=p_{site}_challenge" in body
        assert f"child.registration_revision={site}_revision" in body
    assert body.count("child.authorization_generation=prior.authorization_generation") == 2
    assert body.count("child.created_at=prior.created_at AND child.expires_at=prior.expires_at FOR SHARE") == 2


def test_reconciliation_default_acl_closed_and_kernel_owned(monkeypatch):
    module, statements = commands(monkeypatch, "upgrade")
    signature = f"{module.RECONCILE_FUNCTION}({module.RECONCILE_SIGNATURE})"
    assert module.RECONCILE_BODY in statements
    assert f"ALTER FUNCTION {signature} OWNER TO {module.KERNEL_ROLE};" in statements
    assert f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC;" in statements
    assert any("aclexplode" in s and signature in s and "a.grantee<>p.proowner" in s for s in statements)
    assert not any(s.startswith("GRANT ") and module.RECONCILE_FUNCTION in s for s in statements)
