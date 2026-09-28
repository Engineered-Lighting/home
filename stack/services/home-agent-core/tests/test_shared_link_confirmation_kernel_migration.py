"""Offline containment contracts; PostgreSQL acceptance is separate."""
import importlib.util
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / 'alembic/versions/0042_shared_link_confirmation_kernel.py'


def migration():
    spec = importlib.util.spec_from_file_location('shared_link_confirmation_kernel_migration', PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def commands(monkeypatch, operation):
    module = migration()
    statements = []
    monkeypatch.setattr(module.op, 'execute', statements.append)
    getattr(module, operation)()
    return module, statements


def test_api_delegates_all_arguments_without_new_capability_grants():
    m = migration()
    assert m.revision == '0042_shared_link_confirm_krnl_v1'
    assert m.down_revision == '0041_shared_link_proof_kernel_v1'
    assert m.FUNCTION == 'identity.confirm_shared_link_ceremony_v1'
    assert m.SIGNATURE == 'uuid,text,text,bigint,text,text,uuid,uuid,uuid,uuid,uuid'
    assert 'RETURNS TABLE(link_id uuid,authorization_generation bigint,revision bigint,confirmed_at timestamptz)' in m.BODY
    for arg in ('p_ceremony','p_echo_subject','p_session','p_expected_revision','p_confirmation','p_digest','p_proposal','p_receipt','p_link','p_echo_binding','p_victoria_binding'):
        assert m.BODY.count(arg) == 2
    assert 'RETURN QUERY SELECT confirmed.* FROM identity.confirm_shared_link_v1(' in m.BODY
    for forbidden in ('INSERT INTO','UPDATE identity.','UPDATE privacy.','from app','shared_source_grants'):
        assert forbidden not in m.BODY


def test_transaction_exact_revision_and_role_guards_precede_dispatch():
    m = migration()
    for fragment in ('SECURITY DEFINER SET search_path=pg_catalog SET row_security=on',
                     f"session_user<>'{m.COORDINATOR_ROLE}'", f"current_user<>'{m.KERNEL_ROLE}'",
                     "current_setting('transaction_isolation')<>'serializable'",
                     "current_setting('transaction_read_only')<>'off'", 'pg_is_in_recovery()',
                     'pg_current_xact_id_if_assigned() IS NOT NULL',
                     '(SELECT count(*) FROM public.alembic_version)<>1', f"a.version_num='{m.revision}'",
                     'r.oid IN (m.member,m.roleid)', 'ON r.oid=m.member WHERE r.rolname=current_user',
                     "member_role.rolname<>'home_agent_owner' OR m.admin_option OR m.inherit_option OR NOT m.set_option"):
        assert m.BODY.index(fragment) < m.BODY.index('RETURN QUERY')
    for flag in ('rolsuper','rolbypassrls','rolinherit','rolcreatedb','rolcreaterole','rolreplication'):
        assert m.BODY.count(f'NOT r.{flag}') == 2
    assert 'NOT r.rolcanlogin' in m.BODY


def test_new_kernel_dormant_existing_roles_and_boundaries_unchanged(monkeypatch):
    m, sql = commands(monkeypatch, 'upgrade')
    creates = [s for s in sql if s.startswith('CREATE ROLE')]
    assert len(creates) == 1
    for flag in ('NOLOGIN','NOINHERIT','NOSUPERUSER','NOBYPASSRLS','NOCREATEDB','NOCREATEROLE','NOREPLICATION','CONNECTION LIMIT 0','1970-01-01'):
        assert flag in creates[0]
    assert 'role_collision' in sql[1]
    assert not any(s.startswith('GRANT ') and f'TO {m.COORDINATOR_ROLE}' in s for s in sql)
    assert not any(s.startswith('GRANT EXECUTE') and m.FUNCTION in s for s in sql)
    assert not any('CREATE OR REPLACE' in s or 'ALTER ROLE' in s for s in sql)


def test_exact_writes_are_confirmation_records_and_consumption(monkeypatch):
    m, sql = commands(monkeypatch, 'upgrade')
    assert set(m.INSERTS) == {'identity.shared_link_proposals','identity.shared_link_receipts','identity.shared_owner_links','identity.shared_subject_bindings'}
    assert m.UPDATES == {'identity.shared_auth_proofs':'consumed_at',
                         'privacy.shared_owner_generations':'authorization_generation,revision,updated_at',
                         'identity.shared_link_ceremonies':'state,ended_at,confirmation_receipt_id,revision'}
    assert not set(m.UPDATES).intersection(m.LOCKS)
    for table, cols in m.UPDATES.items():
        assert f'GRANT UPDATE ({cols}) ON {table} TO {m.KERNEL_ROLE};' in sql
    for table, cols in m.INSERTS.items():
        assert f'GRANT INSERT ({cols}) ON {table} TO {m.KERNEL_ROLE};' in sql
    grants = [s for s in sql if s.startswith('GRANT ')]
    for forbidden in ('DELETE','TRUNCATE','GRANT ALL','shared_source_grants','shared_sources'):
        assert not any(forbidden in s for s in grants)


def test_public_policies_cannot_widen_boundary_or_lock_only_updates(monkeypatch):
    m, sql = commands(monkeypatch, 'upgrade')
    for table in m.READS:
        if table == 'public.alembic_version':
            continue
        assert f'CREATE POLICY shared_confirm_boundary ON {table} AS RESTRICTIVE FOR ALL TO {m.KERNEL_ROLE} USING ({m.PAIR}) WITH CHECK ({m.PAIR});' in sql
    for table, cols in m.LOCKS.items():
        assert f'GRANT UPDATE ({cols}) ON {table} TO {m.KERNEL_ROLE};' in sql
        assert f'CREATE POLICY shared_confirm_no_update ON {table} AS RESTRICTIVE FOR UPDATE TO {m.KERNEL_ROLE} USING ({m.PAIR}) WITH CHECK (false);' in sql


def test_negative_evidence_visible_and_positive_graph_erasure_filtered(monkeypatch):
    m, sql = commands(monkeypatch, 'upgrade')
    for table in ('identity.edge_privacy_user_blocks','identity.privacy_directives','privacy.shared_link_session_revocations'):
        assert table not in m.SUPPRESSION
        assert f'CREATE POLICY shared_confirm_select ON {table} FOR SELECT TO {m.KERNEL_ROLE} USING ({m.PAIR});' in sql
    for table in ('identity.shared_link_proposals','identity.shared_link_receipts','identity.shared_owner_links'):
        assert 'identity_person_is_blocked(person_id)' in m.SUPPRESSION[table]
        assert 'identity_principal_is_blocked(principal_id)' in m.SUPPRESSION[table]
    assert m.SUPPRESSION['identity.shared_subject_bindings'] == 'EXISTS (SELECT 1 FROM identity.shared_owner_links linked WHERE linked.link_id=shared_subject_bindings.link_id)'
    for table, expression in m.SUPPRESSION.items():
        assert f'CREATE POLICY shared_confirm_erasure ON {table} AS RESTRICTIVE FOR ALL TO {m.KERNEL_ROLE} USING ({expression}) WITH CHECK ({expression});' in sql


def test_helpers_and_default_function_acl_explicit(monkeypatch):
    m, sql = commands(monkeypatch, 'upgrade')
    for helper in m.HELPERS:
        assert f'GRANT EXECUTE ON FUNCTION {helper} TO {m.KERNEL_ROLE};' in sql
    signature = f'{m.FUNCTION}({m.SIGNATURE})'
    transfer = sql.index(f'ALTER FUNCTION {signature} OWNER TO {m.KERNEL_ROLE};')
    assert sql[transfer-1] == f'GRANT CREATE ON SCHEMA identity TO {m.KERNEL_ROLE};'
    assert sql[transfer+1] == f'REVOKE CREATE ON SCHEMA identity FROM {m.KERNEL_ROLE};'
    assert f'REVOKE ALL ON FUNCTION {signature} FROM PUBLIC;' in sql
    assert any('aclexplode(p.proacl)' in s and 'a.grantee<>p.proowner' in s for s in sql)


def test_session_trigger_read_dependencies_are_available():
    m = migration()
    # The pending-parent branch joins sibling sessions to tombstones. Final
    # consumption currently returns early, but the kernel includes the complete
    # trigger dependency rather than depending on that control-flow shortcut.
    assert {'ceremony_id','issuer_id','session_commitment'} <= set(m.READS['identity.shared_link_challenges'].split(','))
    assert {'issuer_id','session_commitment'} <= set(m.READS['privacy.shared_link_session_revocations'].split(','))
    assert m.READS['identity.shared_link_ceremonies'] is None
    assert 'identity.require_shared_link_session_active_v1(text,text)' in m.HELPERS
    assert 'privacy.lock_identity_semantic_write_fence()' in m.HELPERS


def test_downgrade_removes_only_new_boundary(monkeypatch):
    m, sql = commands(monkeypatch, 'downgrade')
    assert f"version_num='{m.revision}'" in sql[0]
    assert sql[1:4] == [f'SET LOCAL ROLE {m.KERNEL_ROLE};', f'DROP FUNCTION {m.FUNCTION}({m.SIGNATURE});', 'RESET ROLE;']
    for forbidden in ('CASCADE','DROP TABLE','DELETE FROM','TRUNCATE','ALTER TABLE',f'DROP ROLE {m.COORDINATOR_ROLE}'):
        assert forbidden not in '\n'.join(sql)
    for table, cols in m.UPDATES.items():
        assert f'REVOKE UPDATE ({cols}) ON {table} FROM {m.KERNEL_ROLE};' in sql
    for table, cols in m.INSERTS.items():
        assert f'REVOKE INSERT ({cols}) ON {table} FROM {m.KERNEL_ROLE};' in sql
    assert sql[-1] == f'DROP ROLE {m.KERNEL_ROLE};'
