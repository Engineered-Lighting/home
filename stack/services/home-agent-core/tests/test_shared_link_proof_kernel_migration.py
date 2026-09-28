"""Offline SQL containment checks; not PostgreSQL execution evidence."""
import importlib.util
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / 'alembic/versions/0041_shared_link_proof_kernel.py'


def migration():
    spec = importlib.util.spec_from_file_location('shared_link_proof_kernel_migration', PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def commands(monkeypatch, operation):
    module = migration()
    statements = []
    monkeypatch.setattr(module.op, 'execute', statements.append)
    getattr(module, operation)()
    return module, statements


def test_api_does_not_accept_issuer_or_create_new_link_authority():
    m = migration()
    assert m.revision == '0041_shared_link_proof_kernel_v1'
    assert m.down_revision == '0040_shared_link_issue_kernel_v1'
    assert m.FUNCTION == 'identity.issue_shared_link_auth_proof_v1'
    assert m.SIGNATURE == 'uuid,text,text,text,timestamptz,bigint'
    assert 'v_issuer := CASE session_user' in m.BODY
    assert 'v_issuer,p_proof_id,p_subject,p_session,p_challenge,p_authenticated_at,p_registration_revision' in m.BODY
    assert 'p_issuer' not in m.BODY
    assert 'from app' not in PATH.read_text()
    for forbidden in ('INSERT INTO', 'UPDATE identity.', 'confirm_shared_link', 'begin_shared_link'):
        assert forbidden not in m.BODY


def test_privileged_roles_memberships_and_wrong_caller_are_rejected():
    m = migration()
    for flag in ('rolsuper','rolbypassrls','rolinherit','rolcreatedb','rolcreaterole','rolreplication'):
        assert m.BODY.count(f'NOT r.{flag}') == 2
    for fragment in ('v_issuer IS NULL', f"current_user<>'{m.KERNEL_ROLE}'", 'NOT r.rolcanlogin',
                     'r.oid IN (m.member,m.roleid)', 'ON r.oid=m.member WHERE r.rolname=current_user',
                     "member_role.rolname<>'home_agent_owner' OR m.admin_option OR m.inherit_option OR NOT m.set_option"):
        assert fragment in m.BODY


def test_revision_and_transaction_checked_before_authority_reads():
    m = migration()
    for fragment in ('SECURITY DEFINER SET search_path=pg_catalog SET row_security=on',
                     "current_setting('transaction_isolation')<>'serializable'",
                     "current_setting('transaction_read_only')<>'off'", 'pg_is_in_recovery()',
                     'pg_current_xact_id_if_assigned() IS NOT NULL',
                     '(SELECT count(*) FROM public.alembic_version)<>1', f"a.version_num='{m.revision}'"):
        assert m.BODY.index(fragment) < m.BODY.index('PERFORM identity.require_shared_link_session_active_v1')


def test_every_replay_checks_incoming_parent_and_sibling_sessions():
    body = migration().BODY
    assert "require_shared_link_session_active_v1(v_issuer,p_session)" in body
    assert "require_shared_link_session_active_v1('home-assistant:echo',parent_session)" in body
    assert 'require_shared_link_session_active_v1(sibling.issuer_id,sibling.session_commitment)' in body
    assert "child.session_commitment=p_session AND parent.state='pending'" in body
    assert body.index('LOOP;') < body.index('RETURN QUERY')
    assert body.index('PERFORM identity.require_shared_link_session_active_v1') < body.index('SELECT parent.ceremony_id')


def test_expiry_is_rechecked_after_potential_insert_wait():
    body = migration().BODY
    assert body.index('INTO STRICT receipt') < body.index('receipt.expires_at<=pg_catalog.clock_timestamp()') < body.index('RETURN QUERY')
    assert "RAISE EXCEPTION 'shared_link_challenge_expired' USING ERRCODE='22023'" in body


def test_no_runtime_enablement_or_old_boundary_modification(monkeypatch):
    m, statements = commands(monkeypatch, 'upgrade')
    creates = [s for s in statements if s.startswith('CREATE ROLE')]
    assert len(creates) == 1
    for flag in ('NOLOGIN','NOINHERIT','NOSUPERUSER','NOBYPASSRLS','NOCREATEDB','NOCREATEROLE','NOREPLICATION','CONNECTION LIMIT 0','1970-01-01'):
        assert flag in creates[0]
    assert not any(s.startswith('GRANT ') and (f'TO {m.ECHO_ROLE}' in s or f'TO {m.VICTORIA_ROLE}' in s) for s in statements)
    assert not any('CREATE OR REPLACE' in s or 'ALTER ROLE' in s for s in statements)
    assert not any(s.startswith('GRANT EXECUTE') and m.FUNCTION in s for s in statements)


def test_negative_evidence_and_sibling_sessions_remain_visible(monkeypatch):
    m, statements = commands(monkeypatch, 'upgrade')
    for table in ('identity.edge_privacy_user_blocks','identity.privacy_directives','privacy.shared_link_session_revocations','identity.shared_link_challenges'):
        assert m._boundary(table) == m.PAIR
        assert table not in m.SUPPRESSION
        assert f'CREATE POLICY shared_link_proof_select ON {table} FOR SELECT TO {m.KERNEL_ROLE} USING ({m.PAIR});' in statements
    for table in ('identity.shared_auth_proofs','identity.shared_issuers'):
        assert f'issuer_id=({m.ISSUER})' in m._boundary(table)


def test_restrictive_policies_prevent_public_policy_widening(monkeypatch):
    m, statements = commands(monkeypatch, 'upgrade')
    for table in m.READS:
        if table == 'public.alembic_version':
            continue
        assert f'CREATE POLICY shared_link_proof_boundary ON {table} AS RESTRICTIVE FOR ALL TO {m.KERNEL_ROLE} USING ({m._boundary(table)}) WITH CHECK ({m._boundary(table)});' in statements
    for table in m.LOCKS:
        assert any(s.startswith(f'CREATE POLICY shared_link_proof_no_update ON {table} AS RESTRICTIVE') and 'WITH CHECK (false)' in s for s in statements)
    assert any('shared_link_proof_restrict ON identity.shared_link_challenges AS RESTRICTIVE FOR UPDATE' in s and f'issuer_id=({m.ISSUER})' in s for s in statements)
    assert any('shared_link_proof_insert_restrict ON identity.shared_auth_proofs AS RESTRICTIVE FOR INSERT' in s and 'consumed_at IS NULL' in s for s in statements)


def test_only_proof_insert_child_consumption_and_lock_columns_are_granted(monkeypatch):
    m, statements = commands(monkeypatch, 'upgrade')
    grants = [s for s in statements if s.startswith('GRANT ')]
    inserts = [s for s in grants if s.startswith('GRANT INSERT')]
    assert inserts == [f'GRANT INSERT ({m.PROOF_COLUMNS}) ON identity.shared_auth_proofs TO {m.KERNEL_ROLE};']
    updates = [s for s in grants if s.startswith('GRANT UPDATE')]
    assert len(updates) == len(m.LOCKS) + 1
    assert f'GRANT UPDATE ({m.CHILD_UPDATE}) ON identity.shared_link_challenges TO {m.KERNEL_ROLE};' in updates
    for forbidden in ('DELETE','TRUNCATE','GRANT ALL','shared_source_grants','shared_owner_links'):
        assert not any(forbidden in s for s in grants)
    for table, expression in m.SUPPRESSION.items():
        assert f'CREATE POLICY shared_link_proof_erasure ON {table} AS RESTRICTIVE FOR ALL TO {m.KERNEL_ROLE} USING ({expression}) WITH CHECK ({expression});' in statements


def test_nested_helpers_and_default_acl_are_explicit(monkeypatch):
    m, statements = commands(monkeypatch, 'upgrade')
    for helper in m.HELPERS:
        assert f'GRANT EXECUTE ON FUNCTION {helper} TO {m.KERNEL_ROLE};' in statements
    signature = f'{m.FUNCTION}({m.SIGNATURE})'
    transfer = statements.index(f'ALTER FUNCTION {signature} OWNER TO {m.KERNEL_ROLE};')
    assert statements[transfer-1] == f'GRANT CREATE ON SCHEMA identity TO {m.KERNEL_ROLE};'
    assert statements[transfer+1] == f'REVOKE CREATE ON SCHEMA identity FROM {m.KERNEL_ROLE};'
    assert f'REVOKE ALL ON FUNCTION {signature} FROM PUBLIC;' in statements
    assert any('aclexplode(p.proacl)' in s and 'a.grantee<>p.proowner' in s for s in statements)


def test_downgrade_preserves_history_and_existing_roles(monkeypatch):
    m, statements = commands(monkeypatch, 'downgrade')
    assert f"version_num='{m.revision}'" in statements[0]
    assert statements[1:4] == [f'SET LOCAL ROLE {m.KERNEL_ROLE};', f'DROP FUNCTION {m.FUNCTION}({m.SIGNATURE});', 'RESET ROLE;']
    combined = '\n'.join(statements)
    for forbidden in ('CASCADE','DROP TABLE','DELETE FROM','TRUNCATE','ALTER TABLE',f'DROP ROLE {m.ECHO_ROLE}',f'DROP ROLE {m.VICTORIA_ROLE}'):
        assert forbidden not in combined
    assert statements[-1] == f'DROP ROLE {m.KERNEL_ROLE};'
