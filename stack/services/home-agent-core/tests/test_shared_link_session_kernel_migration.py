"""Offline DDL contracts; no PostgreSQL runtime acceptance is claimed."""
import importlib.util
from pathlib import Path

DIRECTORY=Path(__file__).resolve().parents[1]/'alembic/versions'
PATH=DIRECTORY/'0046_shared_link_session_kernel.py'

def load(path):
    spec=importlib.util.spec_from_file_location(path.stem,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def commands(monkeypatch,operation):
    module=load(PATH);sql=[];monkeypatch.setattr(module.op,'execute',sql.append)
    getattr(module,operation)();return module,sql

def test_all_seven_predecessors_are_frozen_and_only_revision_changes():
    m=load(PATH);prior=load(DIRECTORY/'0045_shared_link_proof_lookup.py')
    assert len(m.WRAPPERS)==7
    for actual,old in zip(m.WRAPPERS,prior.WRAPPERS+(prior.LOOKUP,)):
        assert actual=={**old,'original_revision':prior.revision,'original_sql':prior.aligned_sql(old)}
        assert m.aligned_sql(actual).replace("a.version_num='"+m.revision+"'","a.version_num='"+prior.revision+"'")==actual['original_sql']
    assert len(m.revision)<=32
    assert m.down_revision==prior.revision
    for forbidden in ('runpy','importlib','read_text(','from app','__import__'):
        assert forbidden not in PATH.read_text()

def test_wrapper_derives_issuer_from_new_session_role_and_retains_transaction_guard():
    m=load(PATH)
    assert m.FUNCTION=='identity.revoke_shared_link_session_bound_v1'
    assert m.SIGNATURE=='text,uuid'
    assert "CASE session_user WHEN 'home_agent_shared_echo_session_ingress' THEN 'home-assistant:echo'" in m.BODY
    assert "WHEN 'home_agent_shared_victoria_session_ingress' THEN 'home-assistant:victoria'" in m.BODY
    assert 'proof_ingress' not in m.BODY
    for guard in ('pg_current_xact_id_if_assigned() IS NOT NULL',"transaction_isolation')<>'serializable'",
        "transaction_read_only')<>'off'",'pg_is_in_recovery()',"a.version_num='"+m.revision+"'",
        'NOT r.rolsuper','NOT r.rolbypassrls','NOT r.rolcanlogin','NOT r.rolinherit',
        'm.admin_option OR m.inherit_option OR NOT m.set_option'):
        assert guard in m.BODY
    assert 'FROM identity.revoke_shared_link_session_v1(\n   v_issuer,p_session,p_revocation_id)' in m.BODY
    for forbidden in ('require_shared_link_owner','require_shared_link_session_active','shared_issuers','INSERT INTO','UPDATE identity.'):
        assert forbidden not in m.BODY

def test_all_drift_and_collision_checks_precede_role_or_wrapper_mutation(monkeypatch):
    m,sql=commands(monkeypatch,'upgrade')
    first=next(i for i,s in enumerate(sql) if s.startswith('CREATE ROLE'))
    assert first==9
    assert all('shared_link_combined_wrapper_drift' in s for s in sql[1:8])
    assert 'shared_link_session_kernel_collision' in sql[8]
    assert m.body_source(m.BODY) in sql[-1]
    assert all('a.grantee<>p.proowner' in s and 'has_schema_privilege(' in s for s in sql[1:8])

def test_roles_are_dormant_and_no_caller_is_granted_execution(monkeypatch):
    m,sql=commands(monkeypatch,'upgrade')
    roles=[s for s in sql if s.startswith('CREATE ROLE')]
    assert len(roles)==3
    assert all('NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 0' in s for s in roles)
    for role in (m.ECHO_ROLE,m.VICTORIA_ROLE):
        assert not any(s.startswith('GRANT') and f'TO {role}' in s for s in sql)
    assert f'REVOKE ALL ON FUNCTION {m.FUNCTION}({m.SIGNATURE}) FROM PUBLIC;' in sql
    assert any('aclexplode(p.proacl)' in s and 'a.grantee<>p.proowner' in s for s in sql)
    assert not any('LOGIN PASSWORD' in s or 'BYPASSRLS;' in s for s in sql)

def test_tombstone_and_child_policies_are_issuer_scoped_with_minimal_privileges(monkeypatch):
    m,sql=commands(monkeypatch,'upgrade')
    assert set(m.READS)=={'public.alembic_version','privacy.shared_link_session_revocations','identity.shared_link_challenges','identity.shared_link_ceremonies'}
    for table in ('privacy.shared_link_session_revocations','identity.shared_link_challenges'):
        boundary=m._boundary(table)
        assert f'issuer_id=({m.ISSUER})' in boundary
        assert any(f'ON {table} AS RESTRICTIVE FOR ALL' in s and boundary in s for s in sql)
    assert len([s for s in sql if s.startswith('GRANT INSERT')])==1
    assert len([s for s in sql if s.startswith('GRANT UPDATE')])==1
    assert f'GRANT UPDATE (state,ended_at,revision) ON identity.shared_link_ceremonies TO {m.KERNEL_ROLE};' in sql
    assert all(not s.startswith('GRANT DELETE') for s in sql)
    assert set(m.HELPERS)=={'identity.revoke_shared_link_session_v1(text,text,uuid)','privacy.lock_identity_semantic_write_fence()'}

def test_parent_update_is_pending_to_cancelled_and_victoria_requires_own_child(monkeypatch):
    m,sql=commands(monkeypatch,'upgrade')
    boundary=m._boundary('identity.shared_link_ceremonies')
    assert "='home-assistant:echo' OR EXISTS" in boundary
    assert 'child.ceremony_id=shared_link_ceremonies.ceremony_id' in boundary
    assert f'child.issuer_id=({m.ISSUER})' in boundary
    policies=[s for s in sql if 'FOR UPDATE TO' in s]
    assert len(policies)==2
    for policy in policies:
        assert "AND state='pending') WITH CHECK" in policy
        assert "AND state='cancelled' AND ended_at IS NOT NULL AND revision>0" in policy
    assert any('AS RESTRICTIVE FOR UPDATE' in s for s in policies)

def test_downgrade_verifies_eight_bodies_and_preserves_tombstone_data(monkeypatch):
    m,sql=commands(monkeypatch,'downgrade')
    assert all('shared_link_combined_wrapper_drift' in s for s in sql[1:9])
    assert sql[9:12]==[f'SET LOCAL ROLE {m.KERNEL_ROLE};',f'DROP FUNCTION {m.FUNCTION}({m.SIGNATURE});','RESET ROLE;']
    assert len([s for s in sql if s.startswith('DROP ROLE')])==3
    for i,wrapper in enumerate(m.WRAPPERS):
        assert m.body_source(wrapper['original_sql']) in sql[-7+i]
    assert not any('DROP TABLE' in s or 'DELETE FROM' in s or 'TRUNCATE' in s or 'CASCADE' in s for s in sql)
